# -*- coding: utf-8 -*-
"""MySQL 连接与连通性测试工具（镜像推送机制 B 已下线）

历史职责（SQLite → MySQL 单向镜像推送）已随架构评审整体移除：当前为
MySQL 主库 + SQLite 兜底双后端模式，读写直连 MySQL，不再有镜像推送路径。
已删除的推送设施包括 push_all / push_table / push_aftersale /
_ensure_schema / _read_sqlite / _AFTERSALE_KEY_COLS 及各 _DDL_* /
push 辅助函数（对应回归测试 tests/test_mysql_sync_removed.py）。

本模块仅保留连接配置读取与连通性测试：
- _load_mysql_config(): 从 settings.json 读取 mysql_sync 配置（敏感字段
  透明解密；未启用返回 {}）
- _get_pymysql(): 延迟导入 pymysql（未安装返回 None）
- _connect(): 创建 MySQL 连接（供 test_connection 使用）
- test_connection(): 测试连接是否可达（MysqlTestWorker 使用）
"""

import json
import os


def _load_mysql_config() -> dict:
    """从 settings.json 读取 mysql_sync 配置（敏感字段透明解密）"""
    from core.app_paths import get_app_dir
    from core.secrets import decrypt_settings
    path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            settings = decrypt_settings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    cfg = settings.get("mysql_sync", {})
    if not cfg.get("enabled", False):
        return {}
    return cfg


def _get_pymysql():
    """延迟导入 pymysql，未安装时返回 None"""
    try:
        import pymysql
        return pymysql
    except ImportError:
        return None


def _connect(cfg: dict = None, use_database: bool = True):
    """创建 MySQL 连接；cfg 缺省时从 settings.json 读取

    use_database=False 时不指定 database（用于首次建库/连通性探测）
    """
    pymysql = _get_pymysql()
    if pymysql is None:
        raise RuntimeError("pymysql 未安装，请执行 pip install pymysql")
    if cfg is None:
        cfg = _load_mysql_config()
    if not cfg:
        raise RuntimeError("MySQL 同步未启用或配置缺失")
    kwargs = {
        "host": cfg.get("host", "127.0.0.1"),
        "port": int(cfg.get("port", 3306)),
        "user": cfg.get("user", "root"),
        "password": cfg.get("password", ""),
        "charset": "utf8mb4",
        # 连接超时 3s：连通性测试/恢复探测快速反馈，避免卡 10s
        "connect_timeout": 3,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if use_database:
        kwargs["database"] = cfg.get("database", "autowork")
    return pymysql.connect(**kwargs)


def test_connection(cfg: dict = None) -> tuple:
    """测试 MySQL 连接是否可达

    Args:
        cfg: 显式连接配置；缺省时从 settings.json 读取

    Returns:
        (ok: bool, message: str)
    """
    pymysql = _get_pymysql()
    if pymysql is None:
        return False, "pymysql 未安装，请执行 pip install pymysql"
    try:
        # 先无 database 连接，测试基础连通性
        conn = _connect(cfg, use_database=False)
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            ver = cur.fetchone()
        conn.close()
        return True, f"连接成功，MySQL {ver.get('VERSION()', '')}"
    except Exception as e:
        return False, f"连接失败: {type(e).__name__}: {e}"
