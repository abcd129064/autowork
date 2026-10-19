# -*- coding: utf-8 -*-
"""兜底增量合并回写 MySQL（恢复后 LWW 合并）

MySQL 从 DEGRADED 恢复为 ONLINE 时，把降级期间写入本地 SQLite 的增量
按「时间戳 LWW（last-write-wins）」合并回 MySQL：
- aftersale_records：有 updated_at，按业务键 (created_at,creator,table_no,problem)
  匹配；MySQL 已有且 updated_at 更新 → 跳过；MySQL 不存在或较旧 → 写入。
- device_mapping：有 updated_at，按 device_code 主键 LWW。
- 运维表（billiard_tables/xqzg_status/kd_status/submission_log）：无 updated_at，
  退化为 SQLite 优先（最新 API 快照覆盖，可接受）。

与 push_*（镜像推送，P0 守卫已拦主模式）的区别：合并是恢复后显式回写兜底增量，
非镜像推送，不受守卫影响。

合并只认时间戳，不比对内容：两端可能各自改过同一行，逐字段合并的规则比重写
冲突更难解释，也无法用简单 SQL 表达。代价是时钟不同步时会误判，但降级窗口
很短，按「较新者胜」处理足够安全。

对外 API：
- merge_back(progress_cb) -> (ok, msg, count)
- merge_aftersale(mysql_conn, progress_cb) -> int
- merge_device_mapping(mysql_conn, progress_cb) -> int
- merge_ops_tables(mysql_conn, progress_cb) -> int
"""

import sqlite3
from datetime import datetime

from database import aftersale_db
from database import backend
from database import ledger_db
from database.sqlite_io import read_sqlite_table
from database.table_db import DB_PATH

# 售后业务键与 aftersale_db.RECORD_KEY_COLS 共用同一来源，避免两边各维护
# 一份键定义后错配。字段列表必须含 updated_at，其余列与 MySQL 表同名同序
_AFTERSALE_COLS = (
    "created_at", "occurred_at", "creator", "issue_type", "table_no",
    "room_name", "region", "problem", "cause", "resolved",
    "is_initiative", "is_our_problem", "solution", "resolver",
    "response_time", "snk_code", "device_code", "cycle_start", "updated_at",
)
# 运维表没有 updated_at，无法做 LWW，退化为 SQLite 优先覆盖：这些表是外部
# 快照，本地刚抓到的版本一定更新。submission_log 例外，保持追加语义
_OPS_TABLES = {
    "billiard_tables": ("id",),
    "xqzg_status": ("id",),
    "kd_status": ("id", "file_path"),
    "submission_log": ("id",),  # INSERT IGNORE 语义
}


def _read_rows_as_tuples(table: str, columns: tuple) -> list:
    """从本地 SQLite 读全部行，按请求列顺序返回 tuple 列表（缺列补空串）

    列交集 + 缺列补空语义由 database.sqlite_io.read_sqlite_table 提供
    （T01 数据层基础设施，只读），此处仅做 dict → tuple 适配以兼容
    合并逻辑期望的 tuple 行形态。
    """
    # 连接必须在 finally 里关闭：SQLite 句柄不释放会锁住数据库文件，
    # Windows 上尤其明显，下一次写入会直接报 database is locked
    sl = sqlite3.connect(DB_PATH)
    try:
        records = read_sqlite_table(sl, table, columns)
    finally:
        sl.close()
    return [tuple("" if r[c] is None else r[c] for c in columns)
            for r in records]


def _parse_dt(s) -> datetime:
    """解析时间串，非法返回 datetime.min（LWW 判定作最旧处理）。

    返回 datetime.min 而不是 None：调用方直接做大小比较，用最早时间让非法
    时间戳自然落败，省掉每个比较点都判空的分支。
    """
    try:
        return datetime.strptime(str(s or "").strip()[:19],
                                 "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.min


def merge_aftersale(mysql_conn, progress_cb=None) -> int:
    """售后 LWW 合并：按业务键匹配，updated_at 较新者覆盖较旧者。

    逐行先 SELECT 再决定 INSERT/UPDATE，而不是一条 upsert 解决：MySQL 的
    ON DUPLICATE KEY UPDATE 无法表达「本地更旧就保留远端」，硬写会在降级
    窗口里用旧数据覆盖新数据。整批只 commit 一次，避免逐行提交把每次写
    都放大成一次磁盘同步。
    返回合并条数（新增 + 更新）。
    """
    rows = _read_rows_as_tuples("aftersale_records", _AFTERSALE_COLS)
    if not rows:
        return 0
    key_cols = aftersale_db.RECORD_KEY_COLS
    cols_str = ", ".join(f"`{c}`" for c in _AFTERSALE_COLS)
    ph = ", ".join(["%s"] * len(_AFTERSALE_COLS))
    insert_sql = (f"INSERT INTO `aftersale_records` ({cols_str}) "
                  f"VALUES ({ph})")
    # UPDATE 全部非键列（含 updated_at）
    non_key = [c for c in _AFTERSALE_COLS if c not in key_cols]
    set_str = ", ".join(f"`{c}` = %s" for c in non_key)
    key_where = " AND ".join(f"`{c}` = %s" for c in key_cols)
    update_sql = (f"UPDATE `aftersale_records` SET {set_str} "
                  f"WHERE {key_where}")
    sel_sql = (f"SELECT updated_at FROM `aftersale_records` "
               f"WHERE {key_where} LIMIT 1")
    idx = {c: i for i, c in enumerate(_AFTERSALE_COLS)}
    inserted = updated = skipped = 0
    # 行数通常只有降级窗口内的增量，一次性全读进内存即可，不必分批
    with mysql_conn.cursor() as cur:
        for r in rows:
            d = dict(zip(_AFTERSALE_COLS, r))
            keys = [d[c] for c in key_cols]
            cur.execute(sel_sql, keys)
            exist = cur.fetchone()
            local_ts = _parse_dt(d.get("updated_at"))
            if exist:
                remote_ts = _parse_dt(exist[0] if isinstance(exist, tuple)
                                      else exist.get("updated_at"))
                if local_ts > remote_ts:
                    vals = [d[c] for c in non_key] + keys
                    cur.execute(update_sql, vals)
                    updated += 1
                else:
                    skipped += 1  # MySQL 较新，不覆盖
            else:
                cur.execute(insert_sql, [d[c] for c in _AFTERSALE_COLS])
                inserted += 1
    mysql_conn.commit()
    # 售后记录写进 MySQL 后必须失效本地字段候选缓存，否则编辑表单仍显示旧候选；
    # merge_back 已模块级 import aftersale_db，直接调用不会循环导入
    try:
        aftersale_db._invalidate_field_cands_cache()
    except Exception:
        pass  # 缓存失效失败不影响合并结果本身
    if progress_cb:
        progress_cb(f"aftersale: 新增 {inserted}，更新 {updated}，跳过 {skipped}")
    return inserted + updated


def merge_ledger(mysql_conn, progress_cb=None) -> int:
    """跑视频 LWW 合并：按业务键匹配，updated_at 较新者覆盖较旧者。

    与 merge_aftersale 同套路，业务键取 ledger_db.RECORD_KEY_COLS。
    字段列表在 RECORD_FIELDS 之后补 updated_at，因为那个元组只描述业务列。
    """
    cols = tuple(ledger_db.RECORD_FIELDS) + ("updated_at",)
    rows = _read_rows_as_tuples("ledger_records", cols)
    if not rows:
        return 0
    key_cols = ledger_db.RECORD_KEY_COLS
    cols_str = ", ".join(f"`{c}`" for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    insert_sql = (f"INSERT INTO `ledger_records` ({cols_str}) "
                  f"VALUES ({ph})")
    non_key = [c for c in cols if c not in key_cols]
    set_str = ", ".join(f"`{c}` = %s" for c in non_key)
    key_where = " AND ".join(f"`{c}` = %s" for c in key_cols)
    update_sql = (f"UPDATE `ledger_records` SET {set_str} "
                  f"WHERE {key_where}")
    sel_sql = (f"SELECT updated_at FROM `ledger_records` "
               f"WHERE {key_where} LIMIT 1")
    inserted = updated = skipped = 0
    with mysql_conn.cursor() as cur:
        for r in rows:
            d = dict(zip(cols, r))
            keys = [d[c] for c in key_cols]
            cur.execute(sel_sql, keys)
            exist = cur.fetchone()
            local_ts = _parse_dt(d.get("updated_at"))
            if exist:
                remote_ts = _parse_dt(exist[0] if isinstance(exist, tuple)
                                      else exist.get("updated_at"))
                if local_ts > remote_ts:
                    vals = [d[c] for c in non_key] + keys
                    cur.execute(update_sql, vals)
                    updated += 1
                else:
                    skipped += 1  # MySQL 较新，不覆盖
            else:
                cur.execute(insert_sql, [d[c] for c in cols])
                inserted += 1
    mysql_conn.commit()
    if progress_cb:
        progress_cb(f"ledger: 新增 {inserted}，更新 {updated}，跳过 {skipped}")
    return inserted + updated


def merge_device_mapping(mysql_conn, progress_cb=None) -> int:
    """device_mapping LWW 合并：按 device_code 主键，updated_at 较新者覆盖。

    先 SELECT 比较再决定是否 upsert：ON DUPLICATE KEY UPDATE 只能无条件覆盖，
    单独用它会在远端更新时把新映射改回旧值。映射表很小，多一次查询可接受。
    """
    cols = ("device_code", "local_dir", "source", "created_at", "updated_at")
    rows = _read_rows_as_tuples("device_mapping", cols)
    if not rows:
        return 0
    ph = ", ".join(["%s"] * len(cols))
    cols_str = ", ".join(f"`{c}`" for c in cols)
    insert_sql = (f"INSERT INTO `device_mapping` ({cols_str}) "
                  f"VALUES ({ph}) "
                  f"ON DUPLICATE KEY UPDATE local_dir=VALUES(local_dir), "
                  f"source=VALUES(source), updated_at=VALUES(updated_at)")
    sel_sql = "SELECT updated_at FROM `device_mapping` WHERE device_code=%s"
    idx = {c: i for i, c in enumerate(cols)}
    n = 0
    with mysql_conn.cursor() as cur:
        for r in rows:
            d = dict(zip(cols, r))
            cur.execute(sel_sql, (d["device_code"],))
            exist = cur.fetchone()
            local_ts = _parse_dt(d.get("updated_at"))
            if exist:
                remote_ts = _parse_dt(exist[0] if isinstance(exist, tuple)
                                      else exist.get("updated_at"))
                # 远端不旧就跳过，避免把另一端刚改好的映射顶掉
                if local_ts <= remote_ts:
                    continue  # 远端较新，跳过
            cur.execute(insert_sql, [d[c] for c in cols])
            n += 1
    mysql_conn.commit()
    if progress_cb:
        progress_cb(f"device_mapping: {n} 条")
    return n


def merge_ops_tables(mysql_conn, progress_cb=None) -> int:
    """运维表合并（无 updated_at，退化 SQLite 优先：存在则覆盖，不存在则插入）

    submission_log 用 INSERT IGNORE（仅补新增，不覆盖）保留其追加语义。

    列取 SQLite 与 MySQL 的交集：两端表结构可能因版本差异不同步，按交集
    写可以避免某一端多出的列让整条 INSERT 失败。
    """
    total = 0
    for tbl, pk_cols in _OPS_TABLES.items():
        # 读 SQLite 全部列
        sl = sqlite3.connect(DB_PATH)
        try:
            sl_cols = [r[1] for r in sl.execute(
                f"PRAGMA table_info({tbl})").fetchall()]
            if not sl_cols:
                continue
            rows = sl.execute(f"SELECT * FROM {tbl}").fetchall()
        finally:
            sl.close()
        if not rows:
            continue
        # MySQL 列名（从 description 取）
        mcur = mysql_conn.execute(f"SELECT * FROM `{tbl}` LIMIT 0")
        mcols = [d[0] for d in mcur.description]
        common = [c for c in mcols if c in sl_cols]
        if not common:
            continue
        col_str = ", ".join(f"`{c}`" for c in common)
        ph = ", ".join(["%s"] * len(common))
        if tbl == "submission_log":
            sql = (f"INSERT IGNORE INTO `{tbl}` ({col_str}) VALUES ({ph})")
        else:
            # SQLite 优先：存在则覆盖（REPLACE 语义，按主键）
            update_cols = [c for c in common if c not in pk_cols]
            if update_cols:
                upd = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in update_cols)
                sql = (f"INSERT INTO `{tbl}` ({col_str}) VALUES ({ph}) "
                       f"ON DUPLICATE KEY UPDATE {upd}")
            else:
                sql = (f"INSERT IGNORE INTO `{tbl}` ({col_str}) VALUES ({ph})")
        sidx = {c: i for i, c in enumerate(sl_cols)}
        data = [[r[sidx[c]] for c in common] for r in rows]
        with mysql_conn.cursor() as cur:
            n = cur.executemany(sql, data)
        mysql_conn.commit()
        total += (n if n else 0) or len(data)
        if progress_cb:
            progress_cb(f"{tbl}: {len(data)} 条")
    return total


def merge_back(progress_cb=None) -> tuple:
    """MySQL 恢复后合并兜底增量回写（入口）。

    只在 ONLINE 时执行：DEGRADED 说明连接还不稳定，此时合并可能中途失败，
    留下半批已提交的数据，重试反而更难判断进度。连接失败立即 mark_degraded，
    让上层回到降级写入，而不是继续把新数据写进本地 SQLite 造成二次分叉。
    Returns: (ok, msg, count)，count 是四张表合并条数之和。
    """
    if backend.get_state() != backend.STATE_ONLINE:
        return False, "MySQL 非在线，跳过合并", 0
    # 连接由 backend 统一创建，这里不关心 MySQL 配置来自文件还是环境变量
    try:
        mysql_conn = backend.create_mysql_connection()
    except Exception as e:
        backend.mark_degraded()
        return False, f"MySQL 连接失败，跳过合并：{e}", 0
    try:
        # 各表按自己的业务键独立合并，顺序无关；任一表抛错都会让整次合并
        # 报错，由 finally 统一关连接，不会留下未关闭的游标
        n = 0
        n += merge_aftersale(mysql_conn, progress_cb)
        n += merge_ledger(mysql_conn, progress_cb)
        n += merge_device_mapping(mysql_conn, progress_cb)
        n += merge_ops_tables(mysql_conn, progress_cb)
        return True, f"合并完成，共 {n} 条", n
    except Exception as e:
        return False, f"合并失败：{type(e).__name__}: {e}", 0
    # 无论成功还是中途异常都要关闭连接，否则恢复流程反复重试会耗尽连接数
    finally:
        try:
            mysql_conn.close()
        except Exception:
            pass
