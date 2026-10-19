# -*- coding: utf-8 -*-
"""企微客户端「拖选 + PageUp + Ctrl+C」剪贴板文本 → 结构化消息（解析器 + 归档导入管线）

背景
----
企业微信 PC 端复制出来的聊天记录是**结构化纯文本**（自带发送人与时间），
比截屏 OCR 精确得多，因此本路线不需要任何视觉/OCR 依赖。本模块是
docs/企微售后群消息归档-剪贴板采集器设计与落地2026-10-06.md 里
「来源可插拔」架构中的数据侧：把任意采集来源的文本解析成统一的入站契约
ChatMessage，再幂等导入 chat_archive_* 归档表。下游（打标 / 报告 / 展示）
对来源零分支。

文本格式（2026-10-06 实测于用户的企微版本，真实样本在仓库 tests/ 目录下的“复制测试”本地目录，未入库）：

    江苏~朱华国@微信@微信联系人 8/12 12:55:45\r\n
    [视频]\r\n
    \r\n                                   ← 消息之间的空行分隔
    孙跃源 9/22 18:14:31\r\n
    更换电池后，要按f2进入bios界面，\r\n   ← 正文可以多行
    1点开Power Management...\r\n
    \r\n

已知格式特性（都是实测，不是猜的）：
  * 行尾是 CRLF；文件无 BOM。
  * 时间戳**只有 月/日**，没有年份 —— 年份必须由采集时刻推断，见 infer_years。
  * 外部联系人（微信好友）的显示名带后缀：@微信@微信联系人。
  * @提及 用 **U+2005 FOUR-PER-E M SPACE** 与前文/后文分隔，且可能渲染成
    显示名（@沈喆）**或**原始 openim id（@25984982603779408@openim）。
  * 非文本消息的表现不统一：视频 → 正文 [视频]；图片/文件 → **正文为空**。

职责边界
--------
- 纯函数部分（解析/指纹/去重/年份推断）零第三方依赖、零 IO，可直接单测。
- 导入部分**延迟导入** database.chat_archive_db（避免纯解析场景被迫拉起
  数据库栈），并在无 DB 环境下给出明确报错而不是静默失败。

用法（人工核对剪贴板文本时）：
    python core/wecom_clip.py <剪贴板文本文件> [--room-id X] [--json]
"""
import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

# ---------------------------------------------------------------- 常量

#: 消息头： <发送人> <M/D> <H:MM:SS>
HEADER_RE = re.compile(
    r"^(?P<sender>.+?)[ \t]+"
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})[ \t]+"
    # 秒固定两位、时只限一位：企微对个位数小时不补零，补零就匹配不上；
    # 行尾再放一点空白余量，拖选复制很容易把行尾空格一起带进来
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})[ \t]*$"
)

#: 外部联系人的显示名后缀（按长度降序匹配，先长后短）
EXTERNAL_SUFFIXES = ("@微信@微信联系人", "@微信联系人", "@微信")

#: @提及 的分隔符（U+2005 FOUR-PER-EM SPACE）
MENTION_SEP = "\u2005"

#: 整条正文就是一个 [xxx] 占位符 → 非文本消息
PLACEHOLDER_RE = re.compile(r"^\[(?P<kind>[^\[\]]{1,12})\]$")

#: 占位符 → 归一化类型
PLACEHOLDER_KIND = {
    "视频": "video",
    "图片": "image",
    "文件": "file",
    "语音": "voice",
    "链接": "link",
    "位置": "location",
    "名片": "contact_card",
    "动画表情": "sticker",
    "表情": "sticker",
    "聊天记录": "chat_history",
    "合并转发": "chat_history",
    "小程序": "miniapp",
    "视频号": "channels",
    "音乐": "music",
    "收藏": "favorite",
    "转账": "transfer",
    "红包": "redpacket",
}

#: 采集来源标识（与 database/chat_archive_db.py 的 SOURCE_CLIP 一致）
SOURCE = "wecom_clip"

#: 采集产物文件名模式（tools 侧 collect 脚本落盘的批次文件）
BATCH_FILE_RE = re.compile(r"^batch_(?P<idx>\d+)\.txt$")


# ---------------------------------------------------------------- 数据契约

@dataclass
class ChatMessage:
    """与 docs/售后群消息归档可行性评估2026-10-05.md 的入站契约对齐。

    msg_id 是稳定指纹：同一 (群, 发送人, 时间, 正文) 在任何一次采集里都得到同一个 id，
    因此跨批次重叠、跨次运行重复采集都靠它去重。
    """

    source: str
    msg_id: str
    room_id: str
    # 剪贴板里只有显示名，没有稳定账号 id，先拿归一化名顶着：
    # 下游按人聚合时够用，等有了 openim id 再换真值
    sender_id: str
    sender_name: str          # 归一化后的显示名（去掉 @微信@微信联系人 之类后缀）
    sender_raw: str           # 剪贴板里的原始显示名，保留以便追溯
    sender_kind: str          # internal | external_wechat
    msg_ts: str               # 'YYYY-MM-DD HH:MM:SS'，本地时间
    msg_ts_inferred_year: bool
    msg_type: str             # text | video | image | file | voice | ... | empty
    content: str
    mentions: List[str] = field(default_factory=list)
    batch_index: int = 0      # 来自第几批剪贴板内容
    line_no: int = 0          # 在原始文本里的起始行号（1 基），便于人工回溯


@dataclass
class ParseWarning:
    """解析过程中发现的可疑点，不进 messages，但要给人工核对看。

    code 供脚本归类统计，detail 是给人读的一句话，line_no 指向原始文本行号。
    """

    code: str
    detail: str
    line_no: int


# ---------------------------------------------------------------- 工具

def normalize_clip_text(raw: str) -> str:
    """统一行尾为 LF、去掉 BOM、去掉每行尾部空白。

    只 strip **行尾**：行首空白在 @提及 场景里是有意义的位置信息（实测样本
    L32 = " @沈喆 \u2005把更换电池的视频发一个" 是带前导空格的），但整体首尾空白仍去掉。
    """
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip("\n")


def split_sender(raw_name: str) -> Tuple[str, str]:
    """把剪贴板里的显示名拆成 (归一化名, 类型)。

    江苏~朱华国@微信@微信联系人 → (江苏~朱华国, external_wechat)
    沈喆                        → (沈喆, internal)
    """
    name = raw_name.strip()
    for suffix in EXTERNAL_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip(), "external_wechat"
    return name, "internal"


def extract_mentions(content: str) -> List[str]:
    """抽出 @提及 的目标。

    实测两种形态：显示名 "@沈喆 \u2005"、原始 id "@25984982603779408@openim\u2005"。
    U+2005 是可靠的分隔符，用它切；没有 U+2005 时退回 @ + 非空白连续串。
    """
    out: List[str] = []
    # 有 U+2005 时它是唯一可靠边界，按它切最干净；
    # 没有就退回正则扫 @，两种形态互不信任
    if MENTION_SEP in content:
        for seg in content.split(MENTION_SEP)[:-1]:
            idx = seg.find("@")   # 用 find：@25984982603779408@openim 要整体保留
            if idx < 0:
                continue
            token = seg[idx + 1:].strip()
            if token:
                out.append(token)
    if out:
        return out
    for m in re.finditer(r"@([^\s@\u2005]{1,40})", content):
        out.append(m.group(1))
    return out


def classify_content(content: str) -> Tuple[str, str]:
    """返回 (msg_type, content)。占位符归一化，空正文标成 empty。"""
    stripped = content.strip()
    if not stripped:
        return "empty", ""
    m = PLACEHOLDER_RE.match(stripped)
    if m:
        # 表里没有的占位符归到 placeholder 而不是 unknown：企微版本会新增占位
        # 类型，认不出来也比整条丢掉强，detail 里还留着原文可查
        kind = PLACEHOLDER_KIND.get(m.group("kind"), "placeholder")
        return kind, stripped
    return "text", content


def message_fingerprint(room_id: str, sender_raw: str, month: int, day: int,
                        hour: int, minute: int, second: int, content: str) -> str:
    """稳定 msg_id。刻意**不含年份**——年份是推断出来的，不能进指纹，
    否则同一条消息在不同年份的推断下会得到不同 id，去重就失效了。"""
    blob = "\x1f".join([
        SOURCE, room_id, sender_raw,
        "{:02d}/{:02d}".format(month, day),
        "{:02d}:{:02d}:{:02d}".format(hour, minute, second),
        content,
    ])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def infer_years(dates: List[Tuple[int, int]], collected_at: datetime) -> List[int]:
    """给一串只有 (月, 日) 的消息推断年份。

    规则：
      1. 第一条：若 (月,日) 不晚于采集日，则取采集年；否则取采集年 - 1。
      2. 之后逐条向前走：若 (月,日) 比前一条**变小**，说明跨了一个年关，年份 +1。
    这样既能处理"去年底的记录 + 今年初的记录"混在一段选区里的情况，
    也不需要用户手工输入年份。
    """
    # 空输入直接返回而不是返回空 years 让调用方越界：
    # 日期列表和消息列表必须等长，任何一边少一个都会错位到别人头上
    if not dates:
        return []
    years: List[int] = []
    prev = None
    for month, day in dates:
        if prev is None:
            year = collected_at.year
            if (month, day) > (collected_at.month, collected_at.day):
                year -= 1
        else:
            year = years[-1]
            if (month, day) < prev:
                year += 1
        years.append(year)
        prev = (month, day)
    return years


# ---------------------------------------------------------------- 主解析

def parse_clip_text(raw: str, room_id: str = "",
                    collected_at: Optional[datetime] = None,
                    batch_index: int = 0) -> Tuple[List[ChatMessage], List[ParseWarning]]:
    """把一段剪贴板文本解析成结构化消息。

    返回 (messages, warnings)。warnings 不是错误——它们是**需要人来判断**的可疑点，
    例如正文为空（很可能是图片/文件消息，剪贴板拿不到）、时间倒退（可能是正文里
    恰好写了一行像消息头的东西）。
    """
    collected_at = collected_at or datetime.now()
    warnings: List[ParseWarning] = []
    lines = normalize_clip_text(raw).split("\n")

    # 1) 先找出所有消息头的行号
    heads: List[Tuple[int, "re.Match"]] = []
    for i, ln in enumerate(lines):
        m = HEADER_RE.match(ln)
        if m:
            heads.append((i, m))

    if not heads:
        warnings.append(ParseWarning("no_header", "整段文本里没有一行像消息头", 1))
        return [], warnings

    if heads[0][0] != 0:
        warnings.append(ParseWarning(
            "leading_text",
            "第 1 个消息头之前还有 {} 行文本（选区可能不完整）".format(heads[0][0]), 1))

    # 2) 年份推断需要先拿到全部 (月, 日)：单条消息无法判断跨年，只有看到
    #    相邻两条的月份变小，才能确定中间跨了一个年关。
    dates = [(int(m.group("month")), int(m.group("day"))) for _, m in heads]
    years = infer_years(dates, collected_at)

    # 3) 逐条切分
    messages: List[ChatMessage] = []
    # 一条消息的正文 = 相邻两个消息头之间的行；尾部空行是消息间分隔符，
    # 必须先弹掉，否则每条正文都带一个换行，指纹跟着不稳定
    for idx, (line_no, m) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body_lines = lines[line_no + 1:end]
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        content = "\n".join(body_lines)

        month, day = dates[idx]
        hour = int(m.group("hour"))
        minute = int(m.group("minute"))
        second = int(m.group("second"))
        year = years[idx]

        try:
            ts = datetime(year, month, day, hour, minute, second)
        except ValueError as exc:
            warnings.append(ParseWarning("bad_datetime",
                                         "{}: {}".format(m.group(0), exc), line_no + 1))
            continue

        stamp = "{}/{} {:02d}:{:02d}:{:02d}".format(month, day, hour, minute, second)
        msg_type, content_norm = classify_content(content)
        sender_name, sender_kind = split_sender(m.group("sender"))

        if msg_type == "empty":
            warnings.append(ParseWarning(
                "empty_body",
                "{} {} 正文为空（剪贴板对图片/文件类消息可能就是拿不到正文，需人工确认）"
                .format(sender_name, stamp), line_no + 1))

        messages.append(ChatMessage(
            source=SOURCE,
            msg_id=message_fingerprint(room_id, m.group("sender"), month, day,
                                       hour, minute, second, content),
            room_id=room_id,
            sender_id=sender_name,
            sender_name=sender_name,
            sender_raw=m.group("sender").strip(),
            sender_kind=sender_kind,
            msg_ts=ts.strftime("%Y-%m-%d %H:%M:%S"),
            msg_ts_inferred_year=(year != collected_at.year),
            msg_type=msg_type,
            content=content_norm,
            mentions=extract_mentions(content),
            batch_index=batch_index,
            line_no=line_no + 1,
        ))

    # 4) 时间倒退 = 正文里混进了像消息头的行，或者选区顺序异常
    for a, b in zip(messages, messages[1:]):
        if b.msg_ts < a.msg_ts:
            warnings.append(ParseWarning(
                "time_regression",
                "{} {} 之后出现更早的 {} {}（可能是某条正文里恰好写了一行像消息头的"
                "内容，需要人工核对）".format(a.sender_name, a.msg_ts,
                                              b.sender_name, b.msg_ts),
                b.line_no))

    return messages, warnings


def _norm_for_dup(text: str) -> str:
    """去重比较用的正文归一化：抹掉空白和 @ 符号。

    同一条消息在不同批次里可能以不同面貌出现 —— 拖选边界会截掉正文开头的
    @提及 的一部分（实测："@江苏~朱华国 朱老师…" 与 "苏~朱华国 朱老师…"），
    指纹因此不同，不去重就会同一句话留两条。
    """
    return re.sub(r"[\s@]+", "", text or "")


def dedup_messages(messages: List[ChatMessage]) -> List[ChatMessage]:
    """按 msg_id 去重，并按时间升序返回。

    跨批次重叠是常态（每次拖选都会覆盖上一批的一部分），所以去重是必需的。

    第二遍：同一发送人 + 同一秒、且正文归一化后互为子串的，判定为同一条消息，
    只保留正文最完整的那条。**必须两个条件同时满足才合并**，避免把同一秒里
    发的两条不同消息误并。
    """
    seen = set()
    uniq: List[ChatMessage] = []
    for msg in messages:
        if msg.msg_id in seen:
            continue
        seen.add(msg.msg_id)
        uniq.append(msg)

    kept: List[ChatMessage] = []
    index_of = {}
    for msg in uniq:
        slots = index_of.setdefault((msg.sender_raw, msg.msg_ts), [])
        cur = _norm_for_dup(msg.content)
        hit = None
        for i in slots:
            other = _norm_for_dup(kept[i].content)
            if cur == other or (cur and other and (cur in other or other in cur)):
                hit = i
                break
        if hit is None:
            slots.append(len(kept))
            kept.append(msg)
        elif len(cur) > len(_norm_for_dup(kept[hit].content)):
            kept[hit] = msg

    # 输出按时间升序而非输入顺序：跨批次采集天然是乱的，CLI 与调用方都依赖
    # 这里的顺序看消息流，line_no 只作为同一秒内的稳定次序
    kept.sort(key=lambda m: (m.msg_ts, m.line_no))
    return kept


def parse_batches(texts, room_id: str = "",
                  collected_at: Optional[datetime] = None):
    """按批次顺序解析多段剪贴板文本并跨批次去重。

    texts 是 (batch_index, 文本) 序列。返回 (messages, warnings, stats)，
    stats 含每批解析条数与去重前后计数，供采集脚本打印"漏采自检"。
    """
    collected_at = collected_at or datetime.now()
    all_msgs: List[ChatMessage] = []
    warnings: List[ParseWarning] = []
    per_batch = []
    for idx, raw in texts:
        msgs, warns = parse_clip_text(raw, room_id=room_id,
                                      collected_at=collected_at,
                                      batch_index=idx)
        per_batch.append({"batch": idx, "parsed": len(msgs),
                          "warnings": len(warns)})
        all_msgs.extend(msgs)
        warnings.extend(warns)
    unique = dedup_messages(all_msgs)
    stats = {"batches": len(per_batch), "raw": len(all_msgs),
             "unique": len(unique), "per_batch": per_batch}
    return unique, warnings, stats


# ---------------------------------------------------------------- 归档导入管线

def message_to_row(msg: ChatMessage, room_name: str = "",
                   pull_batch: str = "") -> dict:
    """ChatMessage → chat_archive_messages 的一行（dict，字段名与 DDL 一致）"""
    return {
        "msg_id": msg.msg_id,
        "source": msg.source or SOURCE,
        "room_id": msg.room_id,
        "room_name": room_name,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender_name,
        "sender_raw": msg.sender_raw,
        "sender_kind": msg.sender_kind,
        "msg_type": msg.msg_type,
        "content": msg.content,
        "media_count": 1 if msg.msg_type in ("image", "video", "file", "voice")
        else 0,
        "msg_ts": msg.msg_ts,
        "msg_ts_inferred_year": 1 if msg.msg_ts_inferred_year else 0,
        "mentions": list(msg.mentions or []),
        "pull_batch": pull_batch,
    }


def import_messages(messages, room_id: str = "", room_name: str = "",
                    pull_batch: str = "") -> dict:
    """把解析结果幂等写入归档库，并推进采集断点。

    返回 {"inserted", "skipped", "invalid", "cursor"}。断点 scope = room_id，
    last_msg_ts 取本批最大时间（用于下次增量采集判断"有没有新消息"）。
    """
    from database import chat_archive_db as db

    rows = [message_to_row(m, room_name=room_name, pull_batch=pull_batch)
            for m in messages]
    result = db.insert_messages(rows, default_room_id=room_id)
    # 断点取本批最大时间戳而不是最后一条：跨批次采集的顺序本来就是乱的，
    # 用「最后一条」会把断点往回拨，下次又从头采一批重复数据
    latest = ""
    for m in messages:
        if m.msg_ts > latest:
            latest = m.msg_ts
    # 没有 room_id 的采集统一落到 default scope：断点按群推进，群标识缺失时
    # 不能写进空串，否则会和别的无群采集互相覆盖进度
    scope = room_id or "default"
    # 全批都撞库重复时也得断点前移，否则下次还会从同一批重头采；
    # 反过来没有新时间戳也没插进任何一条时，不动断点才是对的
    if latest or result["inserted"]:
        prev = db.get_cursor(scope)
        db.set_cursor(scope,
                      next_cursor=latest or prev.get("next_cursor", ""),
                      last_msg_ts=latest or prev.get("last_msg_ts", ""))
    result["cursor"] = db.get_cursor(scope)
    return result


def import_clip_text(raw: str, room_id: str, room_name: str = "",
                     collected_at: Optional[datetime] = None,
                     batch_index: int = 0, pull_batch: str = "") -> dict:
    """单段剪贴板文本：解析 → 幂等入库 → 推进断点"""
    # 单段文本内部也可能重复：同一条消息被拖选覆盖两次是常事，
    # 只靠 msg_id 去重不够，还要走一遍归一化合并
    messages, warnings = parse_clip_text(raw, room_id=room_id,
                                         collected_at=collected_at,
                                         batch_index=batch_index)
    messages = dedup_messages(messages)
    result = import_messages(messages, room_id=room_id, room_name=room_name,
                             pull_batch=pull_batch)
    result["warnings"] = [asdict(w) for w in warnings]
    result["messages"] = len(messages)
    return result


def load_batches(out_dir: str):
    """读取采集产物目录下的 batch_NN.txt，按批次序号升序返回 [(idx, 文本)]"""
    items = []
    for name in os.listdir(out_dir):
        m = BATCH_FILE_RE.match(name)
        if not m:
            continue
        path = os.path.join(out_dir, name)
        # utf-8-sig 顺便吃掉 BOM；errors="replace" 让个别乱码字节不至于让整批
        # 导入直接失败——能导进去再人工核对，比一条都导不进去强。
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig", errors="replace")
        items.append((int(m.group("idx")), raw))
    items.sort(key=lambda kv: kv[0])
    return items


def import_batches_dir(out_dir: str, room_id: str, room_name: str = "",
                       collected_at: Optional[datetime] = None) -> dict:
    """把一次采集的产物目录整体导入归档库（解析 → 跨批去重 → 幂等入库）

    pull_batch 取产物目录名（out/<时间戳>），便于回溯与漏采核对。
    """
    texts = load_batches(out_dir)
    if not texts:
        # 目录在但一个 batch_NN.txt 都没有，多半是指错目录或采集半途停了；
        # 必须报错返回，否则拿空列表入库会安静地什么都不做
        return {"error": "目录下没有 batch_*.txt：{}".format(out_dir)}
    messages, warnings, stats = parse_batches(texts, room_id=room_id,
                                              collected_at=collected_at)
    batch_label = os.path.basename(os.path.normpath(out_dir))
    result = import_messages(messages, room_id=room_id, room_name=room_name,
                             pull_batch=batch_label)
    result.update(stats)
    result["warnings"] = [asdict(w) for w in warnings]
    result["out_dir"] = out_dir
    return result


# ---------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    # CLI 只解析并打印，不写库：入库必须走 import_* 系列由采集脚本显式调用，
    # 免得一次误敲的命令把半截选区灌进归档表
    ap = argparse.ArgumentParser(description="解析企微剪贴板聊天记录文本（只读，不写库）")
    ap.add_argument("path", help="剪贴板文本文件或采集产物目录")
    ap.add_argument("--room-id", default="", help="来源群标识（剪贴板里没有，需外部给）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON 而不是摘要")
    args = ap.parse_args(argv)

    if not os.path.exists(args.path):
        print("找不到输入：{}".format(args.path), file=sys.stderr)
        return 2

    # 目录 = 一次完整采集（多批）；单文件 = 手工核对剪贴板文本的临时场景。
    if os.path.isdir(args.path):
        messages, warnings, stats = parse_batches(
            load_batches(args.path), room_id=args.room_id)
    else:
        with open(args.path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig", errors="replace")
        messages, warnings = parse_clip_text(raw, room_id=args.room_id)
        # 单文件也要去重：手工核对的选区常和上次导入的批次重叠，
        # 这里的 unique 是去重后的真实条数，比 raw 小是正常的
        messages = dedup_messages(messages)
        stats = {"batches": 1, "raw": len(messages), "unique": len(messages)}

    if args.json:
        print(json.dumps({
            "stats": stats,
            "messages": [asdict(m) for m in messages],
            "warnings": [asdict(w) for w in warnings],
        }, ensure_ascii=False, indent=2))
        return 0

    print("输入：{}".format(args.path))
    print("批次 {} / 去重后 {} 条消息，{} 条告警\n".format(
        stats["batches"], len(messages), len(warnings)))
    print("{:>3} {:<20} {:<15} {:<8} 正文".format("#", "时间", "发送人", "类型"))
    print("-" * 100)
    # 正文压到 44 字再加省略号：终端里一屏要能看全十几条消息，
    # 完整正文用 --json 输出，人工核对细节走那条路
    for i, m in enumerate(messages, 1):
        body = m.content.replace("\n", " / ")
        if len(body) > 44:
            body = body[:44] + "…"
        name = m.sender_name + ("(外)" if m.sender_kind == "external_wechat" else "")
        if m.mentions:
            name += "@"
        print("{:>3} {:<20} {:<15} {:<8} {}".format(i, m.msg_ts, name, m.msg_type, body))

    if warnings:
        print("\n告警：")
        for w in warnings:
            print("  [{}] L{} {}".format(w.code, w.line_no, w.detail))

    types = {}
    for m in messages:
        types[m.msg_type] = types.get(m.msg_type, 0) + 1
    print("\n类型分布：" + ", ".join("{}={}".format(k, v) for k, v in sorted(types.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
