# -*- coding: utf-8 -*-
"""MySQL 同步 API 化的纯逻辑层：契约、设备认证、逐表策略、SQL 与游标。

不依赖 fastapi/pymysql，便于在缺 Web 依赖的环境里做离线单测；服务端路由
（sync_api.py）只负责 HTTP 与事务，所有可验证的规则集中在本模块。生产部
署时本文件与 app.py 同目录（/opt/aftersale-web/）。
"""
import hashlib
import hmac
import json
import os
from datetime import datetime

# 契约版本由服务端声明，客户端必须先校验一致再同步；升级语义时递增。
# 步骤 4 加表属向后兼容增量，版本保持不变以免本地与生产错配。
CONTRACT_VERSION = "1.0.0-alpha.1"

# 售后 LWW 字段与业务键：与 database/merge_back.py 的 _AFTERSALE_COLS /
# aftersale_db.RECORD_KEY_COLS 同口径，服务端不得各维护一份而错配。
AFTERSALE_COLS = (
    "created_at", "occurred_at", "creator", "issue_type", "table_no",
    "room_name", "region", "problem", "cause", "resolved",
    "is_initiative", "is_our_problem", "solution", "resolver",
    "response_time", "snk_code", "device_code", "cycle_start", "updated_at",
)
RECORD_KEY_COLS = ("created_at", "creator", "table_no", "problem")

# 跑视频表：以 database/ledger_db.py::RECORD_FIELDS / RECORD_KEY_COLS 为权威。
# merge_back.merge_ledger 曾把已含 updated_at 的 RECORD_FIELDS 又追加一次
# updated_at（重复列名），那是既有实现疑点；新链路不复制该行为。
LEDGER_COLS = (
    "category", "kind", "room_name", "video_name", "frame",
    "description", "repro", "new_program", "remark", "signer",
    "occurred_at", "created_at", "updated_at",
)
LEDGER_KEY_COLS = ("created_at", "signer", "category", "kind", "video_name")

# 运维/映射表列清单：从 database/schema.py::TABLE_COLUMNS 提取，顺序即
# INSERT 列顺序。id 是显式主键（billiard_tables 等没有 AUTO_INCREMENT），
# 必须随行一起写入，否则服务端无法与客户端对齐行标识。
BILLIARD_COLS = (
    "id", "name", "roomName", "onlineStatusName", "remark", "cameraPassExt",
    "snk_code", "code", "city", "deviceVersion", "status", "createTime",
    "roomAddress", "sales", "sales_transfer",
)
XQZG_COLS = (
    "id", "file_path", "table_id", "club_name", "pic_total", "normal_count",
    "normal_total", "except_count", "operation_rate", "untreated_count",
    "operation_count", "accuracy_count", "already_count", "rubbish_count",
    "error_rate", "device_code", "target_directory", "status", "todesk_id",
    "todesk_status", "todesk_action", "normal_files", "except_files",
    "untreated_files", "operation_files", "accuracy_files", "already_files",
    "rubbish_files", "version_files",
)
KD_COLS = (
    "id", "file_path", "table_id", "club_name", "pic_total", "normal_count",
    "normal_total", "except_count", "operation_rate", "untreated_count",
    "operation_count", "accuracy_count", "already_count", "rubbish_count",
    "error_rate", "device_code", "target_directory", "status", "normal_files",
    "except_files", "untreated_files", "operation_files", "accuracy_files",
    "already_files", "rubbish_files", "version_files",
)
SUBMISSION_COLS = (
    "id", "created_at", "device_code", "table_id", "club_name", "category",
    "file_name", "file_path_date", "collect_ok", "upload_zip", "upload_ok",
)
MAPPING_COLS = ("device_code", "local_dir", "source", "created_at", "updated_at")
HEALTH_COLS = (
    "name", "roomName", "onlineStatusName", "health", "resolved_health",
    "resolved_at", "device_code", "updated_at",
)

MAX_UPSERT_ROWS = 500

# 逐表配置是唯一事实源。strategy 决定写入冲突语义：
#   lww        —— 有 updated_at，业务键匹配、时间戳严格更新才覆盖
#   overwrite —— 无时间戳，按主键覆盖（最新快照优先，列取交集）
#   append    —— INSERT IGNORE，只补新增，永不覆盖（submission_log 追加语义）
#   upsert_kd —— 按 (file_path,device_code) 增量 upsert，绝不 DELETE 整日数据
# 破坏性整表替换（SaveKd / sync_health_alerts）刻意不暴露端点：3 个清库
# 入口的语义留在客户端评审后再迁移，服务端先只提供安全的行级操作。
TABLE_SPECS = {
    "aftersale_records": {
        "cols": AFTERSALE_COLS, "key": RECORD_KEY_COLS, "strategy": "lww",
        "watermark": "last_sync_api_aftersale_records", "has_deleted": True,
        "order_col": "updated_at", "tie_col": "id", "tie_numeric": True,
    },
    "ledger_records": {
        "cols": LEDGER_COLS, "key": LEDGER_KEY_COLS, "strategy": "lww",
        "watermark": "last_sync_api_ledger_records", "has_deleted": False,
        "order_col": "updated_at", "tie_col": "id", "tie_numeric": True,
    },
    "device_mapping": {
        "cols": MAPPING_COLS, "key": ("device_code",), "strategy": "lww",
        "watermark": "last_sync_api_device_mapping", "has_deleted": False,
        "order_col": "updated_at", "tie_col": "device_code", "tie_numeric": False,
    },
    "health_alerts": {
        "cols": HEALTH_COLS, "key": ("name",), "strategy": "overwrite",
        "watermark": "last_sync_api_health_alerts", "has_deleted": False,
        "order_col": "updated_at", "tie_col": "name", "tie_numeric": False,
    },
    "billiard_tables": {
        "cols": BILLIARD_COLS, "key": ("id",), "strategy": "overwrite",
        "watermark": "last_sync_api_billiard_tables", "has_deleted": False,
        "order_col": "id", "tie_col": "id", "tie_numeric": True,
    },
    "xqzg_status": {
        "cols": XQZG_COLS, "key": ("id",), "strategy": "overwrite",
        "watermark": "last_sync_api_xqzg_status", "has_deleted": False,
        "order_col": "id", "tie_col": "id", "tie_numeric": True,
    },
    "submission_log": {
        "cols": SUBMISSION_COLS, "key": ("id",), "strategy": "append",
        "watermark": "last_sync_api_submission_log", "has_deleted": False,
        "order_col": "id", "tie_col": "id", "tie_numeric": True,
    },
    "kd_status": {
        "cols": KD_COLS, "key": ("file_path", "device_code"),
        "strategy": "upsert_kd",
        "watermark": "last_sync_api_kd_status", "has_deleted": False,
        "order_col": "id", "tie_col": "id", "tie_numeric": True,
    },
}

SUPPORTED_TABLES = tuple(TABLE_SPECS)
WATERMARK_KEY = TABLE_SPECS["aftersale_records"]["watermark"]  # 兼容旧调用方

# 设备 scope 按表隔离；health/contract 用首表 read scope 作最低门槛。
READ_SCOPE = "aftersale_records:read"
WRITE_SCOPE = "aftersale_records:write"


def read_scope(table: str) -> str:
    return f"{table}:read"


def write_scope(table: str) -> str:
    return f"{table}:write"


def watermark_key(table: str) -> str:
    spec = TABLE_SPECS.get(table)
    return spec["watermark"] if spec else ""


# 对齐桌面端 query_page 的 1~500 夹逼口径；导出走独立不分页端点。
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 200

# 语义契约清单（立项文档 §3 的机器可读版）：未支持的项明确标 pending，
# 不开放端点，避免客户端把半成品当成已支持。
SEMANTICS = {
    # SaveKd 的「清同日其他设备记录」属破坏性语义，本链路刻意不实现/不暴露；
    # 已支持的是安全的行级 upsert，两者不能混为一项宣称。
    "upsert_kd_clear_same_day": "pending",
    "kd_row_upsert": "supported_no_delete",
    "lww_conflict": "supported",
    "sync_meta_watermark": "supported",
    "zero_rows_notice": "supported",
    "query_page_clamp": "supported",
    "export_unpaged": "pending",
    "destructive_full_replace": "not_exposed",
    "db_worker_race_guard": "client_side",
    "mysql_degrade": "client_side",
    "ddl_authority": "pending",
}

CAPABILITIES = {
    "health": True,
    "contract": True,
    "incremental_read": True,
    # 写路径双开关门控：SYNC_WRITE_ENABLED 环境变量 + 每表 write scope。
    "batch_upsert": "gated",
    "conflict_resolution": "per_table_strategy",
    "export": False,
}


def effective_cols(table: str) -> tuple:
    """实际参与行级写入的列： id 不是业务键时由服务端自增，禁止跨端写入。

    billiard_tables / xqzg_status / submission_log 的 id 本身就是业务主键，
    必须随行同步；kd_status 的 id 只是服务端行标识，若允许客户端把它写进
    INSERT/UPDATE 会撞主键或改写无关行。其余表列清单里根本没有 id。
    """
    spec = TABLE_SPECS.get(table)
    if not spec:
        return ()
    if "id" in spec["cols"] and "id" not in spec["key"]:
        return tuple(c for c in spec["cols"] if c != "id")
    return tuple(spec["cols"])


def hash_token(token: str) -> str:
    """设备 token 只以 sha256 摘要入库，明文不落服务端文件。"""
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def load_devices(path: str = None) -> dict:
    """读取设备清单，返回 device_id -> 记录；文件缺失/损坏按空清单处理。

    缺省拒绝优于缺省放行：读不到清单时所有设备都认证失败，而不是放行。
    """
    p = path or os.getenv("SYNC_DEVICES_FILE", "/opt/aftersale-web/sync_devices.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    items = data.get("devices", []) if isinstance(data, dict) else []
    out = {}
    for rec in items:
        if isinstance(rec, dict) and rec.get("device_id") and rec.get("token_hash"):
            out[str(rec["device_id"])] = rec
    return out


def verify_device(device_id: str, token: str, devices: dict, scope: str) -> tuple:
    """校验设备身份与权限，返回 (是否通过, 原因)。

    比较用 compare_digest 防时序侧信道；disabled 即吊销（删设备或置 false
    都能立即生效），scope 精确匹配，避免只读设备拿到写端点。
    """
    if not device_id or not token:
        return False, "missing credentials"
    rec = devices.get(str(device_id))
    if not rec:
        return False, "unknown device"
    if not rec.get("enabled", False):
        return False, "device disabled"
    if not hmac.compare_digest(str(rec.get("token_hash", "")), hash_token(token)):
        return False, "invalid token"
    scopes = rec.get("scopes") or []
    if scope not in scopes:
        return False, "scope denied"
    return True, "ok"


def clamp_page_size(value, default: int = DEFAULT_PAGE_SIZE) -> int:
    """页大小夹逼到 [1, 500]；非法值回默认值，与桌面端 query_page 同口径。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_PAGE_SIZE, min(MAX_PAGE_SIZE, n))


def decode_cursor_for(table: str, cursor: str) -> tuple | None:
    """按表解析 '<排序列>|<并列键>' 游标；空表示全量起点。

    数值并列键（id）必须转 int，否则字典序会把 '10' 排在 '9' 前面导致漏行；
    字符串主键（device_code/name）保持原值。非法游标视为从头拉取，便于重连自愈。
    """
    spec = TABLE_SPECS.get(table)
    if not spec or not cursor:
        return None
    try:
        order, tie = str(cursor).rsplit("|", 1)
        if not order or not tie:
            return None
        if spec["tie_numeric"]:
            tie = int(tie)
            if tie < 0:
                return None
        return order, tie
    except (ValueError, TypeError):
        return None


def decode_cursor(cursor: str) -> tuple | None:
    """兼容旧接口：首表的 updated_at|id 游标。"""
    return decode_cursor_for("aftersale_records", cursor)


def encode_cursor_for(table: str, row: dict) -> str:
    """按表从行生成游标；数值并列键统一转 int 字符串。"""
    spec = TABLE_SPECS[table]
    order = row.get(spec["order_col"], "") or ""
    tie = row.get(spec["tie_col"], 0 if spec["tie_numeric"] else "") or 0
    if spec["tie_numeric"]:
        try:
            tie = int(tie)
        except (TypeError, ValueError):
            tie = 0
    return f"{order}|{tie}"


def encode_cursor(updated_at: str, row_id: int) -> str:
    """兼容旧接口：首表游标（row_id 必须是整数）。"""
    return f"{updated_at or ''}|{int(row_id)}"


def build_query_sql(table: str, cursor=None, size: int = 200,
                    active_only: bool = False) -> tuple:
    """按表生成增量读 SQL（表名/列名只来自 TABLE_SPECS，值全部参数化）。

    有 updated_at 的表按时间游标拉取，才能拿到「旧行被更新」的变更；无时间
    戳的运维表按 id 游标。多取一行由调用方判 has_more，省一次 COUNT。
    """
    spec = TABLE_SPECS.get(table)
    if not spec:
        raise KeyError(f"unsupported table: {table}")
    order, tie = spec["order_col"], spec["tie_col"]
    where, params = [], []
    if active_only and spec["has_deleted"]:
        where.append("deleted = 0")
    if cursor:
        c_order, c_tie = cursor
        if order == tie:
            where.append(f"`{order}` > %s")
            params.append(c_tie)
        else:
            where.append(f"(`{order}` > %s OR (`{order}` = %s AND `{tie}` > %s))")
            params.extend([c_order, c_order, c_tie])
    sql = f"SELECT * FROM `{table}`"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY `{order}` ASC"
    if order != tie:
        sql += f", `{tie}` ASC"
    sql += " LIMIT %s"
    params.append(size + 1)
    return sql, params


def parse_dt(value) -> datetime:
    """时间串解析；非法值返回 datetime.min，让 LWW 比较自然落败。"""
    try:
        return datetime.strptime(str(value or "").strip()[:19],
                                 "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return datetime.min


def lww_decision(client_updated_at, server_updated_at) -> str:
    """LWW 裁决：服务端无行则 insert；客户端时间戳严格更新才 update。"""
    if server_updated_at is None:
        return "insert"
    return ("update" if parse_dt(client_updated_at) > parse_dt(server_updated_at)
            else "skip")


def normalize_upsert_rows(rows, table: str = "aftersale_records") -> tuple:
    """把客户端行归一到该表白名单列顺序，返回 (合法行, 错误列表)。

    只取 TABLE_SPECS 里的列；None 归一为空串。业务键全空无法定位则丢弃。
    kd_status 必须同时带 file_path/device_code，否则部分拉取会误伤其他设备。
    """
    spec = TABLE_SPECS.get(table)
    if not spec:
        return [], ["unsupported table"]
    cols, key_cols = effective_cols(table), spec["key"]
    if not isinstance(rows, list):
        return [], ["rows must be a list"]
    if len(rows) > MAX_UPSERT_ROWS:
        return [], [f"too many rows: {len(rows)} > {MAX_UPSERT_ROWS}"]
    good, errors = [], []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            errors.append(f"row {i}: not an object")
            continue
        row = ["" if raw.get(c) is None else raw.get(c) for c in cols]
        d = dict(zip(cols, row))
        if not any(str(d[c]).strip() for c in key_cols):
            errors.append(f"row {i}: empty business key")
            continue
        if spec["strategy"] == "upsert_kd" and any(
                not str(d[c]).strip() for c in ("file_path", "device_code")):
            errors.append(f"row {i}: missing kd key")
            continue
        good.append(row)
    return good, errors


def build_upsert_sql(table: str = "aftersale_records") -> dict:
    """按表策略生成行级写入 SQL；绝不生成 DELETE/整表替换。

    返回 cols/update_cols 给路由层定位参数下标，避免路由层与核心层各算
    一份列顺序而漂移（kd_status 的 id 被 effective_cols 排除后尤其容易错）。
    """
    spec = TABLE_SPECS.get(table)
    if not spec:
        raise KeyError(f"unsupported table: {table}")
    cols, key_cols, strategy = effective_cols(table), spec["key"], spec["strategy"]
    cols_str = ", ".join(f"`{c}`" for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    update_cols = tuple(c for c in cols if c not in key_cols)
    set_str = ", ".join(f"`{c}` = %s" for c in update_cols)
    key_where = " AND ".join(f"`{c}` = %s" for c in key_cols)
    select_col = "updated_at" if strategy == "lww" else "1"
    insert_verb = "INSERT IGNORE INTO" if strategy == "append" else "INSERT INTO"
    return {
        "select": f"SELECT {select_col} FROM `{table}` WHERE {key_where} LIMIT 1",
        "update": (None if strategy == "append" else
                   f"UPDATE `{table}` SET {set_str} WHERE {key_where}"),
        "insert": f"{insert_verb} `{table}` ({cols_str}) VALUES ({ph})",
        "watermark": ("INSERT INTO `sync_meta` (`key`, `value`) VALUES (%s, %s) "
                      "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)"),
        "watermark_key": spec["watermark"],
        "strategy": strategy, "key": key_cols, "cols": cols,
        "update_cols": update_cols,
    }


def is_supported_table(name: str) -> bool:
    """表名白名单：SQL 里只能拼接已登记的表名，绝不信任客户端输入。"""
    return str(name or "") in SUPPORTED_TABLES


def available_tables(env_value=None) -> tuple:
    """生产实际开通的表，由 .env 的 SYNC_AVAILABLE_TABLES 显式收窄。

    代码声明 8 表能力，但生产库可能只建了部分表（实测 autowork 库只有
    aftersale_records / billiard_tables 两张）。契约若宣称全量而表不存在，
    客户端一拉就 500，等于撒谎。未配置该变量时返回全部，保证本地/单测
    拥有完整能力；配置值里的未知表直接忽略，绝不进 SQL。
    """
    raw = os.getenv("SYNC_AVAILABLE_TABLES", "") if env_value is None else env_value
    if not str(raw or "").strip():
        return tuple(SUPPORTED_TABLES)
    return tuple(t.strip() for t in str(raw).split(",")
                 if t.strip() in SUPPORTED_TABLES)


def contract() -> dict:
    """对外声明契约版本、逐表策略与能力状态，供客户端启动时校验。"""
    return {
        "contract_version": CONTRACT_VERSION,
        "tables": list(SUPPORTED_TABLES),
        "table_specs": {
            t: {"key": list(s["key"]), "cols": len(effective_cols(t)),
                "strategy": s["strategy"], "watermark": s["watermark"],
                "has_deleted": s["has_deleted"],
                "order_col": s["order_col"], "tie_col": s["tie_col"],
                "tie_numeric": s["tie_numeric"]}
            for t, s in TABLE_SPECS.items()},
        "capabilities": dict(CAPABILITIES),
        "semantics": dict(SEMANTICS),
        "page_size": {"min": MIN_PAGE_SIZE, "max": MAX_PAGE_SIZE,
                      "default": DEFAULT_PAGE_SIZE},
        "cursor": "per_table: <order_col>|<tie_col>",
    }
