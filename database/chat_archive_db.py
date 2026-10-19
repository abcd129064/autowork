# -*- coding: utf-8 -*-
"""企微售后群消息归档数据层（SQLite / MySQL 双后端，自动跟随 MySQL 测试开关）

背景与目标见 docs/企微售后群消息归档-剪贴板采集器设计与落地2026-10-06.md
与 docs/售后群消息归档可行性评估2026-10-05.md（§五 分层架构的「存储」层）。

职责：
- insert_messages：把采集侧解析出的入站契约消息幂等写入 chat_archive_messages
  （msg_id 是采集指纹，重复导入同一批消息只计 skipped，不产生重复行）
- get_cursor / set_cursor：采集断点（chat_archive_cursor），断档自愈的唯一依据
- latest_msg_ts：按来源/群取已归档的最大时间，供增量采集判断「有没有新消息」
- query_messages / count_messages：报告页的消息流查询
- query_pivot：群 × 时间段 × 问题类型 三维透视（**按 room_id 分组**，
  room_name 仅作展示——群改名会撕裂、重名会合并，见 database/aftersale_db.py
  query_rank 的 TRIM(room_name) 教训）
- stats_by_day / stats_by_room：报告页的时序与分群统计

设计约束：
- 表结构单一来源为 database/schema.py 的 CHAT_ARCHIVE_TABLES，本模块只写 DML，
  不写 DDL（建表由 table_db._ensure_initialized / _ensure_mysql_tables 负责）。
- 只使用双方言都成立的 SQL：不用 INSERT OR IGNORE（backend 只转换
  INSERT OR REPLACE），不用 SQLite 专有函数；分页用 LIMIT/OFFSET。
- chat_archive_cursor 必须豁免 database/data_retention.py 的清理
  （该模块表清单为硬编码白名单，本表不在其中），回归见
  tests/test_chat_archive_db.py::test_cursor_table_exempt_from_retention。
"""
import json
from datetime import datetime

from database import table_db

# 采集来源标识（入站契约 source 字段；来源可插拔，下游零分支）
# 存的是字符串不是枚举：库里已有历史行，改名等于让旧数据失去来源标签
SOURCE_CLIP = "wecom_clip"      # 企微客户端剪贴板采集器（本仓库自研）
SOURCE_MANUAL = "manual"        # 人工导出/手工粘贴导入
SOURCE_OFFICIAL = "official"    # 企微官方会话内容存档 SDK（需超管开通）

# 消息字段（与 chat_archive_messages DDL 一致；created_at 由本模块统一维护）
# 拼 INSERT 的列名与占位符都按这个元组展开，schema.py 改列时必须同步这里，
# 否则写入会「列数对不上」，而错误信息只会说 SQL 语法错
MESSAGE_FIELDS = (
    "msg_id", "source", "room_id", "room_name",
    "sender_id", "sender_name", "sender_raw", "sender_kind",
    "msg_type", "content", "media_count", "msg_ts",
    "msg_ts_inferred_year", "mentions", "pull_batch", "created_at",
)

# 标签字段（与 chat_archive_tags DDL 一致）
TAG_FIELDS = ("msg_id", "category", "matched_rule", "confidence",
              "tagged_by", "created_at")

# 未打标消息在透视表中的归类名（LEFT JOIN 兜底）
UNCATEGORIZED = "未分类"

# INSERT 分块大小：SQLite 默认 SQLITE_MAX_VARIABLE_NUMBER=999，
# 16 列 × 60 行 = 960 个占位符，留余量
_INSERT_CHUNK = 60
# 已存在 msg_id 的 IN 查询分块（单列，取 400）
_LOOKUP_CHUNK = 400


def _now_str() -> str:
    """统一的入库时间串；与 msg_ts 同格式，两者能直接按字典序比较"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value) -> str:
    """None → 空串：表列都是 NOT NULL 默认空，写入侧不再逐列判空"""
    return "" if value is None else str(value)


def _to_int(value, default: int = 0) -> int:
    """宽松转 int：入站契约里的数字可能来自 JSON 文本，转不动就落默认值，
    不能因为一条消息的 media_count 异常让整批导入失败"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_to_dict(cursor, row) -> dict:
    """按 cursor.description 生成列名；不手写列序，SELECT * 也能安全取字段"""
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# ==================== 消息写入 ====================

def existing_msg_ids(msg_ids) -> set:
    """返回给定 msg_id 中已存在于归档库的子集（幂等导入的前置查询）

    刻意不用 INSERT OR IGNORE / ON CONFLICT：backend 的方言转换只覆盖
    INSERT OR REPLACE 与 ON CONFLICT(col) DO UPDATE，用前置查询最稳，
    且能给出准确的 skipped 计数（重复采集同一段历史时报告「新增 0 条」
    比「新增 N 条」更难误导）。
    """
    ids = [str(x) for x in msg_ids if str(x or "").strip()]
    if not ids:
        return set()
    conn = table_db.get_conn()
    found = set()
    # 分块查询只为绕开 SQLite 的变量数上限，MySQL 侧同样按块执行，
    # 单块 400 个占位符在两边都远低于限制
    for i in range(0, len(ids), _LOOKUP_CHUNK):
        chunk = ids[i:i + _LOOKUP_CHUNK]
        marks = ", ".join(["?"] * len(chunk))
        cur = conn.execute(
            f"SELECT msg_id FROM chat_archive_messages "
            f"WHERE msg_id IN ({marks})", chunk)
        found.update(r[0] for r in cur.fetchall())
    return found


def normalize_row(row: dict, default_room_id: str = "",
                  default_room_name: str = "", default_batch: str = "") -> dict:
    """把入站契约消息（dict）规整成 chat_archive_messages 的一行

    接受 ChatMessage 转出的 dict；缺失字段取默认空值。mentions 统一序列化
    为 JSON 数组文本（写入侧唯一来源，读取侧 json.loads 兼容空串）。

    序列化只在写入这一处做：各采集器各自 dump 迟早出现「有时是数组有时是
    逗号串」的脏数据，读取侧再想统一就晚了。
    """
    out = {}
    for f in MESSAGE_FIELDS:
        out[f] = _clean(row.get(f))
    # 默认值兜底：采集侧偶尔取不到群标识/群名（窗口切换瞬间），用调用方给的
    # 上一层默认值补齐，否则这批数据会因 room_id 为空而无法按群查询
    if not out["room_id"]:
        out["room_id"] = _clean(default_room_id)
    if not out["room_name"]:
        out["room_name"] = _clean(default_room_name)
    if not out["pull_batch"]:
        out["pull_batch"] = _clean(default_batch)
    mentions = row.get("mentions")
    if isinstance(mentions, (list, tuple)):
        out["mentions"] = json.dumps(list(mentions), ensure_ascii=False)
    elif mentions:
        out["mentions"] = _clean(mentions)
    else:
        out["mentions"] = ""
    out["media_count"] = _to_int(row.get("media_count"), 0)
    out["msg_ts_inferred_year"] = 1 if row.get("msg_ts_inferred_year") else 0
    out["created_at"] = _now_str()
    return out


def insert_messages(rows, default_room_id: str = "",
                    default_room_name: str = "", default_batch: str = "") -> dict:
    """幂等写入归档消息，返回 {"inserted": n, "skipped": n, "invalid": n}

    一次前置查询拿全「已存在」的 msg_id，再只插新行：逐行试插虽然也幂等，
    但几千条消息就是几千次往返，且拿不到准确的 skipped 计数。

    - msg_id 为空的行计入 invalid 并跳过（指纹是去重的唯一依据）
    - 已存在的 msg_id 计入 skipped
    - 同一批次内部重复的 msg_id 只写一次
    """
    normalized = []
    invalid = 0
    seen = set()
    for row in rows or []:
        item = normalize_row(row, default_room_id, default_room_name,
                             default_batch)
        if not item["msg_id"]:
            invalid += 1
            continue
        # 批内重复计入 invalid 而不是静默丢弃：同一批里出现重复 msg_id
        # 说明采集侧把同一段扫了两遍，计数能让这种异常在报告里露头
        if item["msg_id"] in seen:
            invalid += 1
            continue
        seen.add(item["msg_id"])
        normalized.append(item)
    if not normalized:
        return {"inserted": 0, "skipped": 0, "invalid": invalid}
    exists = existing_msg_ids([r["msg_id"] for r in normalized])
    fresh = [r for r in normalized if r["msg_id"] not in exists]
    if not fresh:
        return {"inserted": 0, "skipped": len(normalized), "invalid": invalid}
    conn = table_db.get_conn()
    cols = ", ".join(MESSAGE_FIELDS)
    marks = "(" + ", ".join(["?"] * len(MESSAGE_FIELDS)) + ")"
    # 多行 VALUES 一次写入：逐行 INSERT 在 MySQL 侧每行一次往返，几千条要几十秒
    for i in range(0, len(fresh), _INSERT_CHUNK):
        chunk = fresh[i:i + _INSERT_CHUNK]
        conn.execute(
            f"INSERT INTO chat_archive_messages ({cols}) "
            f"VALUES {', '.join([marks] * len(chunk))}",
            [r[f] for r in chunk for f in MESSAGE_FIELDS])
    # 整批一次提交：sqlite 每条 commit 都是一次 fsync，MySQL 则是一次往返
    conn.commit()
    return {"inserted": len(fresh), "skipped": len(normalized) - len(fresh),
            "invalid": invalid}


# ==================== 采集断点（断档自愈依据） ====================

def get_cursor(scope: str) -> dict:
    """读采集断点；不存在时返回各字段为空串的字典（调用方无需判 None）

    空串即「首次采集」的语义：调用方拿 next_cursor 直接拼请求参数，
    多一层 None 判断反而容易漏。
    """
    conn = table_db.get_conn()
    cur = conn.execute(
        "SELECT scope, next_cursor, last_msg_ts, last_success_ts, updated_at "
        "FROM chat_archive_cursor WHERE scope = ?", (_clean(scope),))
    row = cur.fetchone()
    if not row:
        return {"scope": _clean(scope), "next_cursor": "", "last_msg_ts": "",
                "last_success_ts": "", "updated_at": ""}
    return _row_to_dict(cur, row)


def set_cursor(scope: str, next_cursor: str = "", last_msg_ts: str = "",
               last_success_ts: str = "") -> None:
    """写采集断点（存在则更新，不存在则插入）

    刻意用「先 SELECT 再 UPDATE/INSERT」而不是 ON CONFLICT DO UPDATE：
    后者虽然被 backend 转换支持，但 MySQL 侧 VALUES() 语义与 SQLite
    excluded.* 有细微差异，断点表是断档自愈的唯一依据，这里选最稳的写法。
    """
    conn = table_db.get_conn()
    scope = _clean(scope)
    now = _now_str()
    # 没传成功时间就按「本次即成功」记：否则断档自愈会把一次真实的成功采集
    # 当成「从没成功过」，下次启动又从头拉一遍
    last_success_ts = _clean(last_success_ts) or now
    cur = conn.execute("SELECT 1 FROM chat_archive_cursor WHERE scope = ?",
                       (scope,))
    if cur.fetchone():
        conn.execute(
            "UPDATE chat_archive_cursor SET next_cursor = ?, last_msg_ts = ?, "
            "last_success_ts = ?, updated_at = ? WHERE scope = ?",
            (_clean(next_cursor), _clean(last_msg_ts), last_success_ts, now,
             scope))
    else:
        conn.execute(
            "INSERT INTO chat_archive_cursor "
            "(scope, next_cursor, last_msg_ts, last_success_ts, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (scope, _clean(next_cursor), _clean(last_msg_ts), last_success_ts,
             now))
    conn.commit()


def latest_msg_ts(source: str = "", room_id: str = "") -> str:
    """已归档消息的最大 msg_ts（空串 = 该范围内无记录）

    返回空串而不是 None：调用方拿它直接拼「有没有新消息」的判断，
    多一处 None 判空就多一处漏判。
    """
    sql = "SELECT MAX(msg_ts) FROM chat_archive_messages WHERE 1 = 1"
    params = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    if room_id:
        sql += " AND room_id = ?"
        params.append(room_id)
    cur = table_db.get_conn().execute(sql, params)
    row = cur.fetchone()
    return _clean(row[0]) if row and row[0] else ""


# ==================== 查询与统计 ====================

def _msg_where(room_id: str = "", date_from: str = "", date_to: str = "",
               keyword: str = "", source: str = "", msg_type: str = ""):
    """构造消息查询 WHERE 子句（返回 sql 片段 + 参数列表）

    日期过滤用 msg_ts 的字符串比较（定长 'YYYY-MM-DD HH:MM:SS'，字典序
    即时间序），可以吃到 idx_chat_archive_room_ts 索引；不用 substr(msg_ts)
    以免索引失效。
    """
    sql = " WHERE 1 = 1"
    params = []
    if room_id:
        sql += " AND room_id = ?"
        params.append(room_id)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if msg_type:
        sql += " AND msg_type = ?"
        params.append(msg_type)
    if date_from:
        sql += " AND msg_ts >= ?"
        params.append(f"{date_from} 00:00:00")
    if date_to:
        sql += " AND msg_ts <= ?"
        params.append(f"{date_to} 23:59:59")
    if keyword:
        # 两侧带 % 的 LIKE 吃不到索引，只在前端已限定时间/群的场景下用；
        # 全库模糊搜索在 MySQL 侧会退化成全表扫，报告页不给这个入口
        sql += " AND content LIKE ?"
        params.append(f"%{keyword}%")
    return sql, params


def count_messages(room_id: str = "", date_from: str = "", date_to: str = "",
                   keyword: str = "", source: str = "",
                   msg_type: str = "") -> int:
    """按条件统计归档消息条数

    与 query_messages 分开而不是查完再 len()：报告页要先拿到总数才能算页数，
    为算页数把全量行拉进内存，几万条消息就是几十 MB。
    """
    where, params = _msg_where(room_id, date_from, date_to, keyword, source,
                               msg_type)
    cur = table_db.get_conn().execute(
        f"SELECT COUNT(*) FROM chat_archive_messages{where}", params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def query_messages(room_id: str = "", date_from: str = "", date_to: str = "",
                   keyword: str = "", source: str = "", msg_type: str = "",
                   limit: int = 200, offset: int = 0) -> list:
    """按时间倒序分页查询归档消息（报告页消息流）"""
    where, params = _msg_where(room_id, date_from, date_to, keyword, source,
                               msg_type)
    # 排序带上 msg_id 兜底：同一秒常有多条消息，只按 msg_ts 排序时翻页会
    # 出现重复或漏行（两次查询的相对顺序不稳定）
    params = list(params) + [int(limit), int(offset)]
    cur = table_db.get_conn().execute(
        f"SELECT * FROM chat_archive_messages{where} "
        f"ORDER BY msg_ts DESC, msg_id DESC LIMIT ? OFFSET ?", params)
    return [_row_to_dict(cur, r) for r in cur.fetchall()]


def stats_by_room(date_from: str = "", date_to: str = "",
                  source: str = "") -> list:
    """按群统计消息量（room_id 分组；room_name 取该组内字典序最大的名字）

    这里取的是 MAX(room_name)，只是「稳定可复现」而非「时间上最新」的展示名：
    要真取最新名得再按 msg_ts 做子查询，报告页不需要这个精度，故不复用
    after_sale 的窗口函数写法。
    """
    sql = ("SELECT room_id, MAX(room_name) AS room_name, "
           "COUNT(*) AS msg_count, MIN(msg_ts) AS first_ts, "
           "MAX(msg_ts) AS last_ts "
           "FROM chat_archive_messages")
    where, params = _msg_where(date_from=date_from, date_to=date_to,
                               source=source)
    cur = table_db.get_conn().execute(
        f"{sql}{where} GROUP BY room_id ORDER BY msg_count DESC", params)
    return [_row_to_dict(cur, r) for r in cur.fetchall()]


def stats_by_day(room_id: str = "", date_from: str = "", date_to: str = "",
                 source: str = "") -> list:
    """按天统计消息量（substr 取日期前 10 位，SQLite/MySQL 双方言一致）

    不用 DATE()：两个方言对 DATE 的入参与返回类型并不一致，substr 对定长
    "YYYY-MM-DD HH:MM:SS" 是纯字符串操作，行为完全可预期。
    """
    where, params = _msg_where(room_id=room_id, date_from=date_from,
                               date_to=date_to, source=source)
    cur = table_db.get_conn().execute(
        f"SELECT substr(msg_ts, 1, 10) AS day, COUNT(*) AS msg_count "
        f"FROM chat_archive_messages{where} "
        f"GROUP BY substr(msg_ts, 1, 10) ORDER BY day", params)
    return [_row_to_dict(cur, r) for r in cur.fetchall()]


def query_pivot(room_id: str = "", date_from: str = "", date_to: str = "",
                category: str = "", source: str = "") -> list:
    """群 × 时间段 × 问题类型 三维透视（LEFT JOIN 标签，未打标归「未分类」）

    **分组键是 room_id，不是 room_name**：群改名会让同一群在报告里裂成两行，
    重名群会被合并——这正是 database/aftersale_db.py query_rank 的口径缺陷，
    归档报告不复用该写法。
    """
    sql = ("SELECT m.room_id AS room_id, MAX(m.room_name) AS room_name, "
           "COALESCE(NULLIF(t.category, ''), ?) AS category, "
           "COUNT(*) AS msg_count, MIN(m.msg_ts) AS first_ts, "
           "MAX(m.msg_ts) AS last_ts "
           "FROM chat_archive_messages m "
           "LEFT JOIN chat_archive_tags t ON t.msg_id = m.msg_id")
    where, params = _msg_where(room_id=room_id, date_from=date_from,
                               date_to=date_to, source=source)
    # 参数顺序必须与 SQL 里占位符出现顺序严格一致：SELECT 里的 ? 在最前，
    # 其次是 WHERE 片段，最后是 GROUP BY 的那个；顺序错会静默查空结果
    params = [UNCATEGORIZED] + params
    if category:
        # 过滤条件必须重复写 COALESCE：写成 t.category = ? 时未打标的消息
        # 会被 WHERE 过滤掉，与上面「未打标归未分类」的口径自相矛盾
        where += " AND COALESCE(NULLIF(t.category, ''), ?) = ?"
        params.extend([UNCATEGORIZED, category])
    cur = table_db.get_conn().execute(
        f"{sql}{where} GROUP BY m.room_id, "
        f"COALESCE(NULLIF(t.category, ''), ?) "
        f"ORDER BY msg_count DESC", params + [UNCATEGORIZED])
    return [_row_to_dict(cur, r) for r in cur.fetchall()]


# ==================== 人工作业（纠错/重采） ====================

def delete_room(room_id: str) -> int:
    """删除某群全部归档消息（人工纠错/重采用），返回删除条数

    同时清掉这些消息的标签；不动 chat_archive_cursor（断点由人工单独重置）。

    不自动重置断点是有意的：断点还前进着，重采才会从上次的位置继续；
    一并清掉会让采集器把整个群的历史从头再扫一遍。
    """
    conn = table_db.get_conn()
    cur = conn.execute(
        "SELECT msg_id FROM chat_archive_messages WHERE room_id = ?",
        (_clean(room_id),))
    # 先删标签再删消息：两表没有外键级联，顺序反了会留下指向已删消息的孤儿标签
    ids = [r[0] for r in cur.fetchall()]
    if not ids:
        return 0
    for i in range(0, len(ids), _LOOKUP_CHUNK):
        chunk = ids[i:i + _LOOKUP_CHUNK]
        marks = ", ".join(["?"] * len(chunk))
        conn.execute(f"DELETE FROM chat_archive_tags WHERE msg_id IN ({marks})",
                     chunk)
    conn.execute("DELETE FROM chat_archive_messages WHERE room_id = ?",
                 (_clean(room_id),))
    conn.commit()
    return len(ids)


def add_tag(msg_id: str, category: str, matched_rule: str = "",
            confidence: float = 0.0, tagged_by: str = "rule") -> int:
    """给消息打一个标签（同一 msg_id 可多标签），返回新标签 id

    不做去重：规则标签与人工改判要能并存，读取侧按 id 取第一条作准，
    重复打同一条规则标签只是多一行，不影响结论。
    """
    conn = table_db.get_conn()
    cur = conn.execute(
        "INSERT INTO chat_archive_tags "
        "(msg_id, category, matched_rule, confidence, tagged_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_clean(msg_id), _clean(category), _clean(matched_rule),
         float(confidence or 0), _clean(tagged_by), _now_str()))
    conn.commit()
    return int(cur.lastrowid or 0)


def tags_for(msg_id: str) -> list:
    """取某条消息的全部标签（按 id 升序：先打的规则标签排在人工改判之前）"""
    cur = table_db.get_conn().execute(
        "SELECT * FROM chat_archive_tags WHERE msg_id = ? ORDER BY id",
        (_clean(msg_id),))
    return [_row_to_dict(cur, r) for r in cur.fetchall()]
