# -*- coding: utf-8 -*-
"""售后面板性能改造（S1/S3/S4，2026-09-06）数据层回归测试

覆盖：
- S1 load_cycle_mode 进程内缓存：命中返回副本、save_cycle_mode 写盘后失效
- S3 recalc_cycle_starts：幂等重算物化列；周期筛选走物化列且与动态口径
  （_record_cycle）等价；存量空 cycle_start 行自动兜底回填
- S4 get_field_candidates 缓存：命中返回副本、写操作后失效
- P1-2 返工：重算失败自愈——save 写 pending 标志、recalc 成功清除；
  失败残留 → 面板首载 recalc_cycle_starts_on_load 全量追平并清标志；
  干净库首载仅探测不重算

隔离方式同 test_aftersale_batch_ops：tmp SQLite + monkeypatch，
不触碰真实 tables.db；周期模式固定注入保证归属确定性。
"""
import json
import sqlite3

import pytest

import database.backend as backend
import database.table_db as table_db
import database.aftersale_db as adb
from database import schema

TUE = {"type": "tue", "start": "", "span": 7}


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """配置门面隔离：app_dir 指向 tmp + 前后清空门面/adb 模块缓存（防跨用例串扰）"""
    import core.app_paths
    import core.app_settings as fas
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(tmp_path))
    fas.invalidate_cache()
    adb._cycle_mode_cache = None
    yield tmp_path
    fas.invalidate_cache()
    adb._cycle_mode_cache = None


@pytest.fixture
def db(monkeypatch, tmp_path):
    """临时库：建 aftersale_records 表 + 固定周期模式，隔离 settings.json"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(table_db, "DB_PATH", path)
    monkeypatch.setattr(table_db, "_conn", None)
    monkeypatch.setattr(table_db, "_ensure_initialized", lambda c: None)
    monkeypatch.setattr(backend, "is_mysql_test_mode", lambda: False)
    monkeypatch.setattr(adb, "load_cycle_mode", lambda: dict(TUE))
    sl = sqlite3.connect(path)
    sl.executescript(schema.to_sqlite_ddl("aftersale_records"))
    sl.commit()
    sl.close()
    return path


@pytest.fixture
def reset_caches():
    """每个用例前后清空模块级缓存，避免用例间串扰"""
    adb._cycle_mode_cache = None
    adb._field_cands_cache = None
    adb._cycle_recalc_futile = 0
    yield
    adb._cycle_mode_cache = None
    adb._field_cands_cache = None
    adb._cycle_recalc_futile = 0


def _insert_raw(db, rows):
    """直写库插入（cycle_start 不维护，模拟早期导入/外部写入的存量行）"""
    sl = sqlite3.connect(db)
    for occ, cre in rows:
        sl.execute(
            "INSERT INTO aftersale_records "
            "(created_at, occurred_at, creator, issue_type, table_no, "
            "room_name, region, problem, cause, resolved, is_initiative, "
            "is_our_problem, solution, resolver, response_time, snk_code, "
            "device_code, cycle_start) "
            "VALUES (?, ?, 'tester', '硬件问题', 'T1', 'room', '', '问题X', "
            "'', '否', '否', '否', '', '', '', '', '', '')",
            (cre, occ))
    sl.commit()
    sl.close()


# ==================== S1：load_cycle_mode 进程内缓存 ====================

def test_cycle_mode_cache_hit_and_copy(isolated_settings):
    """命中缓存直接返回且为副本：修改返回值不污染缓存"""
    tmp_path = isolated_settings
    cfg_file = tmp_path / "config" / "aftersale.json"
    cfg_file.parent.mkdir(exist_ok=True)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({"aftersale_cycle": {"type": "mon", "span": 7}}, f)
    cfg1 = adb.load_cycle_mode()
    assert cfg1["type"] == "mon"
    cfg1["type"] = "custom"  # 污染返回值
    cfg2 = adb.load_cycle_mode()
    assert cfg2["type"] == "mon"  # 缓存未被污染


def test_cycle_mode_cache_stale_until_save(isolated_settings):
    """缓存期间外部改配置文件不生效；save_cycle_mode 写盘后失效重读"""
    tmp_path = isolated_settings
    path = tmp_path / "config" / "aftersale.json"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"aftersale_cycle": {"type": "tue", "span": 7}}, f)
    assert adb.load_cycle_mode()["type"] == "tue"
    # 外部手改文件：进程内缓存仍返回旧值（与 backend 缓存同策略）
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"aftersale_cycle": {"type": "month", "span": 7}}, f)
    assert adb.load_cycle_mode()["type"] == "tue"
    # save_cycle_mode 合并写成功后缓存失效，重读为新值
    adb.save_cycle_mode({"type": "month"})
    assert adb.load_cycle_mode()["type"] == "month"


# ==================== S3：物化列重算与兜底回填 ====================

def test_recalc_cycle_starts_idempotent(db):
    """重算按当前周期口径填物化列；二次执行 0 更新（幂等）"""
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00"),   # → 2026/08/18
                     ("2026-08-25", "2026-08-25 10:00:00")])  # → 2026/08/25
    n1 = adb.recalc_cycle_starts()
    assert n1 == 2
    sl = sqlite3.connect(db)
    vals = sorted(r[0] for r in sl.execute(
        "SELECT cycle_start FROM aftersale_records"))
    sl.close()
    assert vals == ["2026/08/18", "2026/08/25"]
    assert adb.recalc_cycle_starts() == 0  # 幂等


def test_recalc_cycle_starts_none_dates_to_empty(db):
    """两日期均无法解析的记录物化为空串（不归属任何周期）"""
    _insert_raw(db, [("", ""), ("garbage", "garbage")])
    # 已是空串的行为幂等无更新；带旧物化值的不可解析记录重写为空串
    sl = sqlite3.connect(db)
    sl.execute(
        "UPDATE aftersale_records SET cycle_start = '2026/01/01' "
        "WHERE occurred_at = 'garbage'")
    sl.commit()
    sl.close()
    assert adb.recalc_cycle_starts() == 1
    sl = sqlite3.connect(db)
    vals = [r[0] for r in sl.execute("SELECT cycle_start FROM aftersale_records")]
    sl.close()
    assert vals == ["", ""]


def test_cycle_filter_uses_materialized_column(db):
    """周期筛选 = 物化列等值过滤：重算后命中数与动态口径一致"""
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00"),
                     ("2026-08-20", "2026-08-20 10:00:00"),
                     ("2026-08-25", "2026-08-25 10:00:00")])
    adb.recalc_cycle_starts()
    total, rows, _stats = adb.query_with_stats(1, 50, cycle_start="2026/08/18")
    assert total == 2 and len(rows) == 2


def test_cycle_filter_auto_backfills_legacy_empty_rows(db, reset_caches):
    """存量空 cycle_start 行：首次周期筛选自动兜底回填（等价旧动态口径）"""
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00"),
                     ("2026-08-25", "2026-08-25 10:00:00")])
    # 未显式 recalc：查询触发 _ensure_cycle_materialized 自动回填
    total, rows, stats = adb.query_with_stats(1, 50, cycle_start="2026/08/18")
    assert total == 1 and stats["total"] == 1
    sl = sqlite3.connect(db)
    vals = sorted(r[0] for r in sl.execute(
        "SELECT cycle_start FROM aftersale_records"))
    sl.close()
    assert vals == ["2026/08/18", "2026/08/25"]


def test_cycle_filter_invalid_start_zero_hits(db):
    """非当前模式真实周期起点（tue 模式传周三）→ 0 命中（沿用旧语义）"""
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00")])
    adb.recalc_cycle_starts()
    total, rows, _ = adb.query_with_stats(1, 50, cycle_start="2026/08/19")
    assert total == 0 and rows == []


# ==================== S4：动态候选缓存（写后失效） ====================

def test_field_candidates_cache_and_invalidation(db, reset_caches):
    """命中缓存返回副本；insert/delete 后失效重建"""
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00")])
    # 直写库不走 insert_record，先建缓存（此时问题候选为预置兜底）
    c1 = adb.get_field_candidates()
    assert c1["problems"]  # 预置常见项兜底非空
    c1["problems"].append("污染项")
    c2 = adb.get_field_candidates()
    assert "污染项" not in c2["problems"]  # 副本，缓存未被污染
    # insert_record 成功 → 缓存失效 → 新问题进入候选
    adb.insert_record({"creator": "tester", "problem": "全新问题P",
                        "occurred_at": "2026-08-19"})
    c3 = adb.get_field_candidates()
    assert "全新问题P" in c3["problems"]


def test_field_candidates_invalidate_on_delete(db, reset_caches):
    """delete_record 后缓存失效：候选不再含已删记录的独有问题"""
    rid = adb.insert_record({"creator": "tester", "problem": "待删问题Q",
                             "occurred_at": "2026-08-19"})
    assert "待删问题Q" in adb.get_field_candidates()["problems"]
    adb.delete_record(rid)
    assert "待删问题Q" not in adb.get_field_candidates()["problems"]


# ==================== P1-2 返工：重算失败自愈（pending 标志） ====================

def test_save_sets_and_recalc_clears_pending(db, isolated_settings,
                                             reset_caches):
    """保存周期成功写 pending=true；重算成功清除（False 时移除键，稳态无冗余）"""
    tmp_path = isolated_settings
    adb.save_cycle_mode({"type": "mon"})
    assert adb._load_recalc_pending() is True
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00")])
    adb.recalc_cycle_starts()
    assert adb._load_recalc_pending() is False
    with open(tmp_path / "config" / "aftersale.json", encoding="utf-8") as f:
        data = json.load(f)
    assert "aftersale_cycle_recalc_pending" not in data  # False 时移除键


def test_recalc_failure_self_heals_on_next_load(db, monkeypatch,
                                                isolated_settings,
                                                reset_caches):
    """重算失败 → pending 残留、物化列停留旧口径；下次首载自动全量追平并清标志"""
    tmp_path = isolated_settings
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00")])
    adb.recalc_cycle_starts()  # 旧口径（tue）物化：周三归属 2026/08/18
    # 保存新周期（mon）：成功 → pending=true
    adb.save_cycle_mode({"type": "mon"})
    assert adb._load_recalc_pending() is True

    # 让周期口径真实跟随 config/aftersale.json（fixture 默认固定 tue）
    def _real_mode():
        with open(tmp_path / "config" / "aftersale.json",
                  encoding="utf-8") as f:
            cfg = json.load(f).get("aftersale_cycle") or {}
        return {"type": cfg.get("type") or "tue",
                "start": cfg.get("start") or "",
                "span": int(cfg.get("span") or 7)}
    monkeypatch.setattr(adb, "load_cycle_mode", _real_mode)

    # ① 模拟触发链 A 重算失败：recalc 抛异常 → 标志残留、数据停留旧口径
    real_recalc = adb.recalc_cycle_starts

    def _boom(*a, **k):
        raise RuntimeError("模拟重算失败")
    monkeypatch.setattr(adb, "recalc_cycle_starts", _boom)
    with pytest.raises(RuntimeError):
        adb.recalc_cycle_starts()
    assert adb._load_recalc_pending() is True  # 失败不清标志
    sl = sqlite3.connect(db)
    v1 = [r[0] for r in sl.execute(
        "SELECT cycle_start FROM aftersale_records")]
    sl.close()
    assert v1 == ["2026/08/18"]  # 物化列未追平（仍按 tue）

    # ② 下次面板首载（recalc_cycle_starts_on_load）：pending → 全量重算追平
    calls = []

    def _spy(*a, **k):
        calls.append(1)
        return real_recalc()
    monkeypatch.setattr(adb, "recalc_cycle_starts", _spy)
    n = adb.recalc_cycle_starts_on_load()
    assert calls == [1] and n == 1  # 确实走了全量重算，1 行按 mon 口径追平
    assert adb._load_recalc_pending() is False  # 成功后清除标志
    sl = sqlite3.connect(db)
    v2 = [r[0] for r in sl.execute(
        "SELECT cycle_start FROM aftersale_records")]
    sl.close()
    assert v2 == ["2026/08/17"]  # mon 口径：周三 08-19 归属周一 08-17


def test_on_load_clean_path_skips_recalc(db, monkeypatch, tmp_path,
                                         reset_caches):
    """干净库（无 pending、无空值存量行）：首载仅索引探测，不触发全量重算"""
    import core.app_paths
    monkeypatch.setattr(core.app_paths, "get_app_dir", lambda: str(tmp_path))
    _insert_raw(db, [("2026-08-19", "2026-08-19 10:00:00")])
    adb.recalc_cycle_starts()
    adb._save_recalc_pending(False)  # 确保无待办标志
    calls = []
    monkeypatch.setattr(adb, "recalc_cycle_starts",
                        lambda *a, **k: calls.append(1) or 0)
    assert adb.recalc_cycle_starts_on_load() == 0
    assert calls == []  # 正常路径零额外重算成本
