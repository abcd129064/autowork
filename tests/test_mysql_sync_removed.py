# -*- coding: utf-8 -*-
"""镜像推送机制 B 拆除回归测试

验证 SQLite → MySQL 单向镜像推送（机制 B）已随架构评审整体下线：
- database.mysql_sync 不再暴露 push_all / push_table / push_aftersale /
  _ensure_schema / _read_sqlite / _AFTERSALE_KEY_COLS 及各 _DDL_* /
  push 辅助函数等推送属性
- workers.mysql_sync_worker 不再有 MysqlSyncWorker（MysqlTestWorker 保留）
- mysql_sync.test_connection 仍存在（连接测试保留）

说明：workers.mysql_sync_worker 模块顶层 import PySide6，而隔离 venv
（测试环境）未安装 PySide6，直接 import 会失败。故对 worker 侧断言采用
源码级检查（无 PySide6 依赖）；若运行环境恰好有 PySide6，则额外做强断言。
"""

import os

import database.mysql_sync as mysql_sync

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKER_SRC_PATH = os.path.join(_PROJECT_ROOT, "workers", "mysql_sync_worker.py")
_WORKERS_INIT_PATH = os.path.join(_PROJECT_ROOT, "workers", "__init__.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ==================== 推送 API 已拆除 ====================

def test_push_apis_removed():
    """对外推送入口已全部移除"""
    for name in ("push_all", "push_table", "push_aftersale",
                 "_ensure_schema", "_read_sqlite", "_AFTERSALE_KEY_COLS"):
        assert not hasattr(mysql_sync, name), f"机制 B 残留: {name}"


def test_schema_ddl_removed():
    """镜像推送用 DDL 常量已移除（DDL 单一来源收敛到 database.schema）"""
    for name in ("_DDL_BILLIARD_TABLES", "_DDL_XQZG_STATUS", "_DDL_KD_STATUS",
                 "_DDL_SUBMISSION_LOG", "_DDL_DEVICE_MAPPING", "_DDL_SYNC_META",
                 "_DDL_AFTERSALE_RECORDS", "_ALL_DDL", "_CREATE_DATABASE"):
        assert not hasattr(mysql_sync, name), f"机制 B 残留 DDL: {name}"


def test_push_helpers_removed():
    """推送辅助函数与列常量已移除"""
    for name in ("_push_replace", "_push_upsert", "_push_insert_ignore",
                 "_BT_COLS", "_XQZG_COLS", "_KD_COLS", "_SUB_COLS", "_DM_COLS"):
        assert not hasattr(mysql_sync, name), f"机制 B 残留辅助: {name}"


def test_legacy_meta_readers_removed():
    """镜像推送配套的元数据读取入口已移除"""
    for name in ("is_enabled", "get_last_push_time", "ensure_schema"):
        assert not hasattr(mysql_sync, name), f"机制 B 残留: {name}"


def test_connection_helpers_kept():
    """连接配置读取与连通性测试保留（MysqlTestWorker 依赖）"""
    assert hasattr(mysql_sync, "test_connection")
    assert hasattr(mysql_sync, "_connect")
    assert hasattr(mysql_sync, "_load_mysql_config")
    assert hasattr(mysql_sync, "_get_pymysql")


# ==================== worker 侧拆除（源码级，无 PySide6 依赖） ====================

def test_sync_worker_class_removed_from_source():
    """mysql_sync_worker.py 不再定义 MysqlSyncWorker"""
    src = _read(_WORKER_SRC_PATH)
    assert "class MysqlSyncWorker" not in src
    assert "class MysqlTestWorker" in src


def test_sync_worker_not_exported_from_package():
    """workers/__init__.py 不再导出 MysqlSyncWorker"""
    src = _read(_WORKERS_INIT_PATH)
    assert "MysqlSyncWorker" not in src
    assert "MysqlTestWorker" in src


# ==================== 环境具备 PySide6 时的强断言 ====================

def test_sync_worker_removed_strong_when_pyside6_available():
    """PySide6 可用时做强断言：模块对象上确实无 MysqlSyncWorker"""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return  # 隔离 venv 无 PySide6，跳过强断言
    import importlib
    import sys
    mod = importlib.import_module("workers.mysql_sync_worker")
    assert not hasattr(mod, "MysqlSyncWorker")
    assert hasattr(mod, "MysqlTestWorker")
    assert "workers" in sys.modules
