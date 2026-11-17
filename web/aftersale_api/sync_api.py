# -*- coding: utf-8 -*-
"""/api/sync 路由：MySQL 同步 API 化的服务端 HTTP / 事务层。

规则全部下沉到 sync_core（可离线单测），本模块只做认证、参数归一与事务。
health/contract 用首表 read scope 作最低门槛，读写端点按表 scope 隔离。
生产部署时本文件与 app.py 同目录。frp 不在本链路依赖内。
"""
import os
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder

# 本地按包导入（web.aftersale_api.*）、生产按顶层模块（uvicorn app:app）
# 两种形态都要能加载；相对导入失败即回退同目录绝对导入。
try:
    from . import sync_core as core
except ImportError:
    import sync_core as core

router = APIRouter(prefix="/api/sync", tags=["sync"])

# DB 连接工厂由 app.py 注入，避免本模块反向 import app 形成循环。
_db_factory = None


def set_db_factory(factory):
    global _db_factory
    _db_factory = factory


def _db():
    if _db_factory is None:
        raise RuntimeError("sync_api db factory not configured")
    return _db_factory()


def _authenticate(device_id: str, token: str, scope: str) -> str:
    if os.getenv("SYNC_AUTH_ENABLED", "true").lower() != "true":
        return "auth-disabled"
    ok, reason = core.verify_device(device_id, token, core.load_devices(), scope)
    if not ok:
        # 对外统一文案，避免「未知设备/口令错误」的差别泄露设备清单；
        # 具体原因留服务端日志，便于排障。
        print(f"[sync] auth failed: device={device_id!r} reason={reason}")
        raise HTTPException(401, "authentication failed")
    return device_id


def require_sync_device(x_sync_device: str = Header(default=""),
                        x_sync_token: str = Header(default="")) -> str:
    """只读设备认证：缺省开启，设备清单缺省即拒绝（读不到文件 = 全部 401）。

    每次请求重读清单文件，让「置 enabled=false」的吊销立即生效；生产必须
    保持 SYNC_AUTH_ENABLED=true，关闭开关只用于本地联调。
    """
    return _authenticate(x_sync_device, x_sync_token, core.READ_SCOPE)


def require_sync_writer(x_sync_device: str = Header(default=""),
                        x_sync_token: str = Header(default="")) -> str:
    """写设备认证：scope 与只读设备分离，未授权设备连端点都进不来。"""
    return _authenticate(x_sync_device, x_sync_token, core.WRITE_SCOPE)


def _table_from(request: Request) -> str:
    # 表名来自路由参数，随后必须过 is_supported_table 白名单才能进 SQL
    return str(request.path_params.get("table") or "")


def require_sync_table_read(request: Request,
                            x_sync_device: str = Header(default=""),
                            x_sync_token: str = Header(default="")) -> str:
    """按表读 scope 隔离：设备没被授予 ledger_records:read 就读不了跑视频。"""
    return _authenticate(x_sync_device, x_sync_token,
                         core.read_scope(_table_from(request)))


def require_sync_table_write(request: Request,
                              x_sync_device: str = Header(default=""),
                              x_sync_token: str = Header(default="")) -> str:
    return _authenticate(x_sync_device, x_sync_token,
                         core.write_scope(_table_from(request)))


def _provisioned(table: str):
    """生产 schema 未就绪的表按 409 拒绝，而不是让 SELECT 抛 500。

    开通状态来自 .env 显式清单（core.available_tables），补表后改配置重启
    即可放开，代码不用动；与设备 scope 是两道独立的门。
    """
    if table not in core.available_tables():
        raise HTTPException(409, "table not provisioned on server")


@router.get("/health")
def health(device: str = Depends(require_sync_device)):
    available = list(core.available_tables())
    return {"ok": True,
            "contract_version": core.CONTRACT_VERSION,
            "server_time": datetime.now().isoformat(timespec="seconds"),
            "tables": available,
            "declared_tables": list(core.SUPPORTED_TABLES),
            "provisioning": {"declared": len(core.SUPPORTED_TABLES),
                             "available": len(available),
                             "pending": [t for t in core.SUPPORTED_TABLES
                                         if t not in available]}}


@router.get("/contract")
def get_contract(device: str = Depends(require_sync_device)):
    """客户端启动时校验：版本不一致或能力未开放时不应继续同步。

    tables/table_specs 只列生产已开通的表，并附 provisioning 明细，避免
    客户端对未建表发起拉取；未配置收窄清单时退化成完整 8 表声明。
    """
    c = core.contract()
    available = set(core.available_tables())
    c["tables"] = [t for t in c["tables"] if t in available]
    c["table_specs"] = {t: s for t, s in c["table_specs"].items()
                        if t in available}
    c["provisioning"] = {
        "declared": len(core.SUPPORTED_TABLES),
        "available": len(c["tables"]),
        "pending": [t for t in core.SUPPORTED_TABLES if t not in available],
    }
    return c


@router.post("/{table}/query")
def incremental_query(table: str, payload: dict = Body(default={}),
                      device: str = Depends(require_sync_table_read)):
    """按表策略的复合游标增量拉取（含软删除墓碑行）。

    游标/排序列由 TABLE_SPECS 决定：有 updated_at 的表按时间游标才能拿到
    「旧行被更新」，无时间戳的运维表按 id 游标；active_only 默认 false，
    软删除行必须下发，否则客户端不知道该删哪条。页大小沿用桌面端 1~500
    夹逼，导出另走不分页端点（尚未开放）。
    """
    if not core.is_supported_table(table):
        raise HTTPException(404, "unsupported table")
    _provisioned(table)
    payload = payload or {}
    size = core.clamp_page_size(payload.get("page_size"))
    cursor = core.decode_cursor_for(table, payload.get("cursor"))
    active_only = bool(payload.get("active_only", False))
    # SQL 全部由 core 按白名单表/列拼装，值一律参数化
    sql, params = core.build_query_sql(table, cursor, size, active_only)

    with _db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = (core.encode_cursor_for(table, rows[-1]) if rows else None)
    return {"rows": jsonable_encoder(rows), "count": len(rows),
            "next_cursor": next_cursor, "has_more": has_more,
            "contract_version": core.CONTRACT_VERSION}


@router.post("/{table}/upsert")
def batch_upsert(table: str, payload: dict = Body(default={}),
                 device: str = Depends(require_sync_table_write)):
    """批量行级写入，冲突语义由 TABLE_SPECS.strategy 逐表决定。

    lww        —— 复刻 merge_back：先 SELECT，仅当 updated_at 严格更新才覆盖
    overwrite  —— 无时间戳的快照表，按主键覆盖（最新快照优先）
    append     —— 追加日志，只 INSERT IGNORE 新增，永不覆盖
    upsert_kd  —— 按 (file_path,device_code) 增量 upsert，绝不 DELETE 整日数据
    破坏性整表替换（SaveKd / sync_health_alerts）刻意不提供端点：3 个清库
    入口留在客户端评审后再迁移。整批一个事务，成功后用 upsert 语义写
    sync_meta 水位（避开 MySQL 1062）。dry_run 只裁决不落库，供灰度比对。
    """
    if not core.is_supported_table(table):
        raise HTTPException(404, "unsupported table")
    _provisioned(table)
    if os.getenv("SYNC_WRITE_ENABLED", "false").lower() != "true":
        raise HTTPException(403, "sync write disabled")
    payload = payload or {}
    rows, errors = core.normalize_upsert_rows(payload.get("rows"), table)
    if errors and not rows:
        raise HTTPException(400, "; ".join(errors[:5]))
    dry_run = bool(payload.get("dry_run", False))
    sql = core.build_upsert_sql(table)
    strategy = sql["strategy"]
    # 列顺序只认 core 返回的 cols/update_cols，路由层不另算一份
    idx = {c: i for i, c in enumerate(sql["cols"])}
    key_idx = [idx[c] for c in sql["key"]]
    update_idx = [idx[c] for c in sql["update_cols"]]
    ts_idx = idx.get("updated_at")  # 只有 lww 表的列清单带 updated_at
    counts = {"insert": 0, "update": 0, "skip": 0}
    watermark = None

    with _db() as conn, conn.cursor() as cur:
        conn.begin()  # DB 配置 autocommit=True，批量写必须显式圈事务
        try:
            for row in rows:
                keys = [row[i] for i in key_idx]
                cur.execute(sql["select"], keys)
                exist = cur.fetchone()
                if strategy == "append":
                    decision = "skip" if exist else "insert"
                elif strategy == "lww":
                    remote_ts = (exist.get("updated_at") if isinstance(exist, dict)
                                 else exist[0] if exist else None)
                    decision = core.lww_decision(row[ts_idx], remote_ts)
                else:  # overwrite / upsert_kd：快照或增量行以本批为准
                    decision = "update" if exist else "insert"
                if not dry_run:
                    if decision == "insert":
                        cur.execute(sql["insert"], row)
                    elif decision == "update":
                        cur.execute(sql["update"],
                                    [row[i] for i in update_idx] + keys)
                counts[decision] += 1
            if not dry_run:
                watermark = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(sql["watermark"],
                            [sql["watermark_key"], watermark])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"applied": counts["insert"] + counts["update"],
            "inserted": counts["insert"], "updated": counts["update"],
            "skipped": counts["skip"], "rejected": errors,
            "dry_run": dry_run, "strategy": strategy,
            "watermark": watermark,
            "contract_version": core.CONTRACT_VERSION}
