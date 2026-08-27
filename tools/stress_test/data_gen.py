# -*- coding: utf-8 -*-
"""内存级模拟数据生成（不新建物理数据库，不落盘）

资源约束（服务器 1GB 内存 / 20GB 硬盘）下的设计：
- 记录用**生成器**逐条产出（iter_aftersale_records），永不一次性构造 10 万条列表；
- 装载进 **sqlite3 :memory:**（内存库），分批 executemany（默认 5000 条/批），
  批间 gc.collect()，峰值内存 ~ 单批数据 + 库本身；
- 表结构复用 `database/schema.py` 的 DDL（与生产 aftersale_records 完全一致），
  保证压测跑在真实字段/索引语义上；
- 视频负载用可压缩的内存字节流模拟（不生成真实大文件，避免吃掉 20GB 硬盘）。

内存估算：单条工单（20 字段 dict）~ 0.5~0.8KB；10 万条装载进内存库后
常驻约 60~120MB（SQLite 页存储比 Python dict 紧凑），分批生成峰值 < 40MB。
"""

import gc
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database import schema  # noqa: E402  （无 Qt 依赖，可在纯 python 环境导入）

# ---------------- 业务字典（与生产语义一致） ----------------

ISSUE_TYPES = ("球桌问题", "设备故障", "软件异常", "网络问题", "耗材更换")
REGIONS = ("华东", "华北", "华南", "西南", "东北")
ROOMS = ("星牌球房", "乔氏球房", "力欧球房", "健英球房", "威灵格球房")
PROBLEMS = (
    "击球点位偏移，需要重新校准定位器",
    "视频回放卡顿，帧率低于 15fps",
    "球桌灯箱不亮，疑似电源模块故障",
    "上报数据延迟超过 5 分钟",
    "台面摩擦异常，需更换台呢",
    "摄像头画面偏色，需重新白平衡",
    "系统提示设备离线，重启后恢复",
    "积分统计与实际对局不符",
)
CAUSES = ("长期使用磨损", "安装时未校准", "固件版本过旧", "网络抖动丢包",
          "人为操作失误", "环境温湿度异常")
SOLUTIONS = ("重新校准定位器并紧固螺丝", "升级固件至最新版本",
             "更换电源模块", "调整交换机 QoS 策略", "更换台呢并重新绷紧")
RESOLVERS = ("维修员甲", "维修员乙", "技术支持组", "区域工程师")

SCALE_PRESETS = {"10k": 10_000, "50k": 50_000, "100k": 100_000}


def iter_aftersale_records(n: int, seed: int = 20260828):
    """逐条产出售后工单（生成器，不占内存）

    字段与 database/aftersale_db.RECORD_FIELDS 对齐（不含自增 id）。
    """
    rng = random.Random(seed)
    base = datetime(2026, 1, 1)
    for i in range(n):
        created = base + timedelta(minutes=rng.randint(0, 240 * 24 * 60))
        occurred = created - timedelta(minutes=rng.randint(0, 720))
        resolved = "是" if rng.random() < 0.62 else "否"
        yield {
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "occurred_at": occurred.strftime("%Y-%m-%d %H:%M:%S"),
            "creator": f"填写员{rng.randint(1, 20)}",
            "issue_type": ISSUE_TYPES[rng.randrange(len(ISSUE_TYPES))],
            "table_no": f"T{rng.randint(1, 300)}",
            "room_name": ROOMS[rng.randrange(len(ROOMS))],
            "region": REGIONS[rng.randrange(len(REGIONS))],
            "problem": PROBLEMS[rng.randrange(len(PROBLEMS))],
            "cause": CAUSES[rng.randrange(len(CAUSES))],
            "resolved": resolved,
            "is_initiative": "是" if rng.random() < 0.35 else "否",
            "is_our_problem": "是" if rng.random() < 0.45 else "否",
            "solution": SOLUTIONS[rng.randrange(len(SOLUTIONS))] if resolved == "是" else "",
            "resolver": RESOLVERS[rng.randrange(len(RESOLVERS))] if resolved == "是" else "",
            "response_time": (occurred + timedelta(minutes=rng.randint(10, 600))
                              ).strftime("%Y-%m-%d %H:%M:%S"),
            "is_important": 1 if rng.random() < 0.08 else 0,
            "snk_code": f"SNK{rng.randint(10000, 99999)}",
            "device_code": f"DEV{rng.randint(1000, 9999)}",
            "cycle_start": "",      # 装载时按 occurred_at 计算（与 aftersale_db 规则一致）
            "updated_at": created.strftime("%Y-%m-%d %H:%M:%S"),
        }


def aftersale_cycle_start(occurred_at: str) -> str:
    """周期归属（简化版，与 aftersale_db.cycle_start_of 同口径：按天归属）"""
    s = (occurred_at or "")[:10]
    return s


def build_memory_db(n: int, batch: int = 5000, verbose: bool = True) -> sqlite3.Connection:
    """在内存库建表并装载 n 条工单（分批 + 批间回收）

    返回共享的内存 SQLite 连接（check_same_thread=False 便于多线程场景）。
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")   # 压测用，内存库无落盘风险
    # 关键：临时表/排序一律走内存（默认 temp_store=FILE 会在大数据量
    # GROUP BY / ORDER BY 时落临时文件，受限环境报 "unable to open database file"）
    conn.execute("PRAGMA temp_store=MEMORY")
    # DDL 单一来源（含索引语句），与生产 aftersale_records 完全一致
    conn.executescript(schema.to_sqlite_ddl("aftersale_records"))

    cols = [c.name for c in schema.TABLE_COLUMNS["aftersale_records"]
            if c.name != "id"]
    sql = (f"INSERT INTO aftersale_records ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")

    t0 = time.perf_counter()
    buf, loaded = [], 0
    for rec in iter_aftersale_records(n):
        rec["cycle_start"] = aftersale_cycle_start(rec["occurred_at"])
        buf.append(tuple(rec.get(c) for c in cols))
        if len(buf) >= batch:
            conn.executemany(sql, buf)
            conn.commit()
            loaded += len(buf)
            del buf
            buf = []
            gc.collect()
    if buf:
        conn.executemany(sql, buf)
        conn.commit()
        loaded += len(buf)
        del buf
    gc.collect()
    if verbose:
        print(f"    [data] 内存库装载 {loaded} 条，耗时 "
              f"{(time.perf_counter() - t0) * 1000:.0f}ms，"
              f"当前进程 RSS~{_rss()}MB")
    return conn


def build_ops_dataset(n: int, batch: int = 5000) -> sqlite3.Connection:
    """运维面板大表模拟（kd_status 口径：设备状态按日期分区）

    真实库 kd_status 已有 4.6 万行，压测用内存库放大到同等/更高量级。
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA temp_store=MEMORY")   # 大 GROUP BY 临时表走内存
    conn.execute("""CREATE TABLE kd_status (
        id INTEGER PRIMARY KEY, file_path TEXT DEFAULT '', table_id TEXT DEFAULT '',
        device_code TEXT DEFAULT '', category TEXT DEFAULT '', status TEXT DEFAULT '',
        created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kd_path ON kd_status(file_path)")
    rng = random.Random(20260829)
    sql = ("INSERT INTO kd_status (file_path, table_id, device_code, category, "
           "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)")
    buf = []
    for i in range(n):
        day = (datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 240))
               ).strftime("%Y/%m/%d")
        buf.append((day, f"T{rng.randint(1, 300)}", f"DEV{rng.randint(1000, 9999)}",
                    rng.choice(("问题", "未复现", "精度", "使用")),
                    rng.choice(("在线", "离线")),
                    f"{day.replace('/', '-')} {rng.randint(0,23):02d}:00:00",
                    f"{day.replace('/', '-')} {rng.randint(0,23):02d}:30:00"))
        if len(buf) >= batch:
            conn.executemany(sql, buf)
            del buf
            buf = []
            gc.collect()
    if buf:
        conn.executemany(sql, buf)
    conn.commit()
    gc.collect()
    return conn


def make_video_bytes(size_mb: float, compressible: bool = True) -> bytes:
    """生成模拟视频负载（内存字节流，可压缩以贴近真实视频熵）

    不落盘：返回内存 bytes，测完由调用方 del + gc.collect() 释放。
    """
    n = int(size_mb * 1024 * 1024)
    if compressible:
        # 周期性强、可压缩的字节流（贴近编码后视频的统计特征）
        unit = b"\x10\x20\x30\x40\x50\x60\x70\x80" * 64
        reps = max(1, n // len(unit))
        return (unit * reps)[:n]
    return os.urandom(min(n, 8 * 1024 * 1024))  # 随机流上限 8MB，避免耗时


def _rss() -> float:
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return 0.0


def estimate_memory_mb(n: int) -> float:
    """粗估 n 条工单在内存库中的常驻大小（MB），用于压测前资源自检"""
    return round(n * 0.7 / 1024, 1)
