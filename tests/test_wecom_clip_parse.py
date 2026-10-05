# -*- coding: utf-8 -*-
"""企微剪贴板聊天记录解析器（core/wecom_clip.py）回归测试

覆盖：消息头正则、外部联系人后缀拆分、@提及 两种形态（显示名 / openim id）、
占位符分类、年份推断、指纹稳定性、跨批次去重、各类解析告警，以及
「真实样本」——SAMPLE_LINES 是用户 2026-10-06 亲手在企微 PC 端
（拖选 + PageUp + Ctrl+C）复制出来的原文，逐字节内联在此，
保证测试不依赖任何未入库的外部文件。

样本的实测特征（改动解析器前先读这里）：
- 行尾 CRLF，文件无 BOM；
- 时间戳只有 月/日，没有年份 → 年份由 infer_years 按采集时刻推断；
- 外部联系人显示名带后缀 @微信@微信联系人；
- @提及 分隔符是 U+2005（四个半角宽的空白），目标可能是显示名也可能是 openim id；
- 图片消息在剪贴板里**只有消息头、正文为空**（不是 [图片]），
  所以 empty 是正常现象而不是解析错误，只出告警不丢数据。

隔离：纯函数，不碰数据库、不碰桌面、不弹窗。
"""
import os
import shutil
import tempfile
import uuid
from datetime import datetime

import pytest

from core import wecom_clip as wc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_FALLBACK = os.path.join(ROOT, "tools", "_scratch", "pytest_chat_db")


def _writable_dir() -> str:
    """选一个真的能写文件的临时目录（探测失败则 skip，不产生假失败）

    受限文件沙箱下 pytest 的 tmp_path 建不出来（%TEMP% 下的 basetemp 会
    PermissionError [WinError 5]），所以落盘型用例自带可写目录探测。
    """
    candidates = []
    try:
        candidates.append(tempfile.mkdtemp(prefix="aw_wecom_clip_"))
    except OSError:
        pass
    candidates.append(SCRATCH_FALLBACK)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, "_probe.tmp")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(probe)
            return d
        except OSError:
            continue
    pytest.skip("当前环境没有可写的临时目录")


@pytest.fixture
def tmpdir_path():
    """自建临时目录（不依赖 pytest tmp_path），用完删掉"""
    d = os.path.join(_writable_dir(), "case_{}".format(uuid.uuid4().hex[:8]))
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


#: 采集时刻（用户在 2026-10-06 凌晨完成的采集）——决定年份推断的起点
COLLECTED = datetime(2026, 10, 6, 4, 9, 0)

#: 用户真实剪贴板样本，逐行内联（CRLF 由 SAMPLE 拼回）
SAMPLE_LINES = [
    '江苏~朱华国@微信@微信联系人 8/12 12:55:45',
    '[视频]',
    '',
    '江苏~朱华国@微信@微信联系人 8/12 12:56:09',
    '播报有一个贴球[调皮]',
    '',
    '沈喆 8/12 12:58:20',
    '我们手势贴球识别要2秒，这个可能是之前测试版本的配置参数的问题',
    '',
    '沈喆 8/12 12:58:45',
    '等下我们手动更新一下再试试',
    '',
    '江苏~朱华国@微信@微信联系人 9/22 15:10:37',
    '一号台卡死了',
    '',
    '孙跃源 9/22 15:11:01',
    '稍等',
    '',
    '孙跃源 9/22 15:14:53',
    '麻烦重启一下主机，我们远程连接不上',
    '',
    '江苏~朱华国@微信@微信联系人 9/22 15:17:51',
    '现在电源开关一旦断开，就必须到机器后面手动按，以前不是这样的',
    '',
    '江苏~朱华国@微信@微信联系人 9/22 15:18:14',
    '现在在打，要等一下上去',
    '',
    '崔润平@微信@微信联系人 9/22 15:19:05',
    '让小阙换个纽扣电池',
    '',
    '崔润平@微信@微信联系人 9/22 15:19:24',
    ' @沈喆 \u2005把更换电池的视频发一个',
    '',
    '沈喆 9/22 15:22:54',
    '好的',
    '',
    '沈喆 9/22 18:11:44',
    '这个是我们的bios更换电池后的流程，整体视频我们还在剪辑',
    '',
    '沈喆 9/22 18:11:53',
    '',
    '',
    '孙跃源 9/22 18:14:31',
    '更换电池后，要按f2进入bios界面，',
    '1点开Power Management，AC Recovery选项，选择Power On',
    '2.Deep Sleep Control选项，选择Disabled',
    '3.点开POST Behavior,将Adapter Wamings和Keyboard Errors取消勾选',
    '4.最后点击右下角Apply，勾选Save as Custom User Settings，点OK应用修改',
    '',
    '江苏~朱华国@微信@微信联系人 10/5 21:06:58',
    '@25984982603779408@openim\u2005刚才一号台又卡死了',
    '',
    '孙跃源 10/5 21:08:08',
    '稍等，我们看一下',
    '',
    '孙跃源 10/5 21:09:37',
    '是重启了吗',
]

SAMPLE = "\r\n".join(SAMPLE_LINES) + "\r\n"

#: 用户手工拖出来的原始文件（未入库，仅本机存在；存在时用它反查样本保真度）
REAL_SAMPLE_PATH = "tests/复制测试"


def _sample_messages():
    msgs, warns = wc.parse_clip_text(SAMPLE, room_id="after_sales",
                                     collected_at=COLLECTED)
    return msgs, warns


# ==================== 样本保真度 ====================

def test_embedded_sample_matches_real_file_if_present():
    """内联样本必须与用户原始文件逐字节一致（文件不存在则跳过）

    原始文件未入库（.gitignore 之外的临时产物），所以这里用 skip 而不是硬依赖；
    一旦两者不一致，说明内联样本被改动过，解析结果就不再代表真实剪贴板。
    """
    import os
    if not os.path.exists(REAL_SAMPLE_PATH):
        pytest.skip("原始样本 {} 不在本机，跳过保真度核对".format(REAL_SAMPLE_PATH))
    with open(REAL_SAMPLE_PATH, "rb") as fh:
        raw = fh.read().decode("utf-8-sig")
    assert raw.replace("\r\n", "\n").strip("\n") == \
        SAMPLE.replace("\r\n", "\n").strip("\n")


# ==================== 真实样本整体解析 ====================

def test_sample_message_count_and_range():
    msgs, _ = _sample_messages()
    assert len(msgs) == 18
    assert msgs[0].msg_ts == "2026-08-12 12:55:45"
    assert msgs[-1].msg_ts == "2026-10-05 21:09:37"


def test_sample_year_inferred_from_collection_time():
    """样本最早是 8/12、最晚是 10/5，都早于采集日 10/6 → 全部落在 2026 年"""
    msgs, _ = _sample_messages()
    assert {m.msg_ts[:4] for m in msgs} == {"2026"}
    assert not any(m.msg_ts_inferred_year for m in msgs)


def test_sample_sender_kinds():
    msgs, _ = _sample_messages()
    by_name = {}
    for m in msgs:
        by_name.setdefault(m.sender_name, set()).add(m.sender_kind)
    assert by_name["江苏~朱华国"] == {"external_wechat"}
    assert by_name["崔润平"] == {"external_wechat"}
    assert by_name["沈喆"] == {"internal"}
    assert by_name["孙跃源"] == {"internal"}
    # 原始显示名保留后缀，归一化名去掉后缀
    ext = [m for m in msgs if m.sender_name == "江苏~朱华国"][0]
    assert ext.sender_raw == "江苏~朱华国@微信@微信联系人"


def test_sample_only_warning_is_empty_body_image():
    """唯一的告警是「正文为空」——那是图片消息，剪贴板拿不到图片本身"""
    msgs, warns = _sample_messages()
    assert len(warns) == 1
    assert warns[0].code == "empty_body"
    empty = [m for m in msgs if m.msg_type == "empty"]
    assert len(empty) == 1
    assert empty[0].sender_name == "沈喆"
    assert empty[0].msg_ts == "2026-09-22 18:11:53"
    assert empty[0].content == ""


def test_sample_video_placeholder():
    msgs, _ = _sample_messages()
    video = [m for m in msgs if m.msg_type == "video"]
    assert len(video) == 1
    assert video[0].content == "[视频]"
    assert video[0].sender_name == "江苏~朱华国"


def test_sample_multiline_body_kept_whole():
    """孙跃源的 BIOS 流程是 5 行正文，不能被拆成多条消息"""
    msgs, _ = _sample_messages()
    bios = [m for m in msgs if m.msg_ts == "2026-09-22 18:14:31"][0]
    lines = bios.content.split("\n")
    assert len(lines) == 5
    assert lines[0] == "更换电池后，要按f2进入bios界面，"
    assert lines[-1].startswith("4.最后点击右下角Apply")


def test_sample_mentions_both_forms():
    """@提及 两种形态都要能取到：显示名 / 原始 openim id"""
    msgs, _ = _sample_messages()
    by_ts = {m.msg_ts: m for m in msgs}
    # 崔润平 9/22 15:19:24 那条 @沈喆（正文里带前导空格 + U+2005 分隔）
    m1 = by_ts["2026-09-22 15:19:24"]
    assert m1.mentions == ["沈喆"]
    assert m1.content.lstrip().startswith("@沈喆")
    # 江苏~朱华国 10/5 21:06:58 那条 @ 的是原始 openim id，不能被截成 "openim"
    m2 = by_ts["2026-10-05 21:06:58"]
    assert m2.mentions == ["25984982603779408@openim"]


# ==================== 文本归一化 ====================

def test_normalize_removes_bom_and_crlf():
    out = wc.normalize_clip_text("\ufeff甲 8/12 1:00:00\r\n正文\r\n")
    assert "\ufeff" not in out and "\r" not in out
    assert out.split("\n")[0] == "甲 8/12 1:00:00"


def test_normalize_strips_trailing_but_keeps_leading_space():
    """行首空白必须保留：@提及 后紧跟正文时，行首空格是位置信息"""
    out = wc.normalize_clip_text("甲 8/12 1:00:00   \n @某某 \u2005把视频发一个   \n")
    assert out.split("\n")[1] == " @某某 \u2005把视频发一个"


# ==================== 消息头与发送人 ====================

def test_header_regex_matches_real_headers():
    for line in ["江苏~朱华国@微信@微信联系人 8/12 12:55:45",
                 "沈喆 9/22 18:11:53",
                 "孙跃源 10/5 21:09:37"]:
        assert wc.HEADER_RE.match(line), line


def test_header_regex_rejects_body_line():
    """正文里像时间但不符合 月/日 时:分:秒 的行不能被当成消息头"""
    assert wc.HEADER_RE.match("1点开Power Management，AC Recovery选项") is None
    assert wc.HEADER_RE.match("4.最后点击右下角Apply，勾选Save as Custom") is None


def test_split_sender_external_and_internal():
    assert wc.split_sender("江苏~朱华国@微信@微信联系人") == ("江苏~朱华国",
                                                     "external_wechat")
    assert wc.split_sender("沈喆") == ("沈喆", "internal")
    # 后缀按长度降序匹配，长的先命中
    assert wc.split_sender("某人@微信联系人") == ("某人", "external_wechat")
    assert wc.split_sender(" 空格名 ") == ("空格名", "internal")


# ==================== 占位符分类 ====================

@pytest.mark.parametrize("body,kind", [
    ("", "empty"),
    ("   ", "empty"),
    ("[视频]", "video"),
    ("[图片]", "image"),
    ("[文件]", "file"),
    ("[语音]", "voice"),
    ("[位置]", "location"),
    ("[未知的东西]", "placeholder"),
])
def test_classify_content(body, kind):
    got, _ = wc.classify_content(body)
    assert got == kind


def test_classify_content_plain_text_preserved_verbatim():
    kind, text = wc.classify_content("第一行\n第二行")
    assert kind == "text" and text == "第一行\n第二行"


def test_classify_content_not_confused_by_inline_bracket():
    """正文里出现 [调皮] 这类表情文字时仍是文本消息"""
    kind, _ = wc.classify_content("播报有一个贴球[调皮]")
    assert kind == "text"


# ==================== 年份推断 ====================

def test_infer_years_same_year():
    assert wc.infer_years([(8, 12), (10, 5)], COLLECTED) == [2026, 2026]


def test_infer_years_first_before_collection_is_last_year():
    """首条 (12,20) 晚于采集日 (10,6) → 只能是上一年"""
    assert wc.infer_years([(12, 20), (12, 28)], COLLECTED) == [2025, 2025]


def test_infer_years_crossing_new_year_increments():
    """选区同时含去年底和今年初时，跨年关要 +1"""
    assert wc.infer_years([(12, 20), (1, 3)], datetime(2026, 1, 5)) == [2025, 2026]


def test_infer_years_empty():
    assert wc.infer_years([], COLLECTED) == []


def test_parse_marks_inferred_year_flag():
    msgs, _ = wc.parse_clip_text("甲 12/20 10:00:00\n你好\n",
                                 collected_at=COLLECTED)
    assert msgs[0].msg_ts == "2025-12-20 10:00:00"
    assert msgs[0].msg_ts_inferred_year is True


# ==================== 指纹与去重 ====================

def test_fingerprint_stable_and_content_sensitive():
    args = ("after_sales", "沈喆", 9, 22, 18, 14, 31, "更换电池")
    assert wc.message_fingerprint(*args) == wc.message_fingerprint(*args)
    assert wc.message_fingerprint(*args) != wc.message_fingerprint(
        *args[:7], "更换电池后")
    assert len(wc.message_fingerprint(*args)) == 20


def test_fingerprint_independent_of_year():
    """指纹刻意不含年份：年份是推断的，若入指纹会在跨年推断变化时失效"""
    import inspect
    sig = inspect.signature(wc.message_fingerprint)
    assert "year" not in sig.parameters
    text = "甲 1/3 10:00:00\n你好\n"
    a, _ = wc.parse_clip_text(text, collected_at=datetime(2026, 1, 5))
    b, _ = wc.parse_clip_text(text, collected_at=datetime(2027, 1, 5))
    assert a[0].msg_id == b[0].msg_id      # 同一条消息，年份不同但指纹相同
    assert a[0].msg_ts != b[0].msg_ts      # 时间戳不同（年份推断了）


def test_fingerprint_differs_by_room():
    args = ("沈喆", 9, 22, 18, 14, 31, "更换电池")
    assert wc.message_fingerprint("room_a", *args) != \
        wc.message_fingerprint("room_b", *args)


def test_dedup_by_msg_id():
    text = "甲 9/22 10:00:00\n你好\n"
    msgs, _ = wc.parse_clip_text(text, room_id="r", collected_at=COLLECTED)
    dup = msgs + msgs
    out = wc.dedup_messages(dup)
    assert len(out) == 1


def test_dedup_merges_truncated_leading_mention():
    """拖选边界会截掉正文开头的 @提及，同一条消息会以两种面貌出现"""
    long_text = "崔润平@微信@微信联系人 9/22 15:19:24\n@江苏~朱华国 朱老师，我们更新一下程序\n"
    short_text = "崔润平@微信@微信联系人 9/22 15:19:24\n苏~朱华国 朱老师，我们更新一下程序\n"
    a, _ = wc.parse_clip_text(long_text, room_id="r", collected_at=COLLECTED)
    b, _ = wc.parse_clip_text(short_text, room_id="r", collected_at=COLLECTED)
    assert a[0].msg_id != b[0].msg_id            # 指纹不同（@ 参与哈希）
    out = wc.dedup_messages(a + b)
    assert len(out) == 1                          # 但归一化后互为子串 → 合并
    assert out[0].content.startswith("@江苏~朱华国")   # 保留最完整的正文


def test_dedup_keeps_distinct_messages_in_same_second():
    """同一发送人同一秒发的两条不同消息不能被误并"""
    text = ("甲 9/22 10:00:00\n第一条\n"
            "\n"
            "甲 9/22 10:00:00\n完全不同的一条消息\n")
    msgs, _ = wc.parse_clip_text(text, room_id="r", collected_at=COLLECTED)
    assert len(wc.dedup_messages(msgs)) == 2


def test_dedup_sorted_by_time():
    text = ("甲 9/22 12:00:00\n晚\n\n"
            "乙 9/22 08:00:00\n早\n")
    msgs, _ = wc.parse_clip_text(text, room_id="r", collected_at=COLLECTED)
    # 正文顺序本身就是时间倒退，先确认消息按原始顺序解析出来
    assert [m.msg_ts[11:] for m in msgs] == ["12:00:00", "08:00:00"]
    # 去重后按时间升序，时间倒退的告警在 parse 阶段给出（见下一个测试）


# ==================== 告警 ====================

def test_warning_no_header():
    msgs, warns = wc.parse_clip_text("这不是聊天记录\n只是普通文本\n")
    assert msgs == []
    assert [w.code for w in warns] == ["no_header"]


def test_warning_leading_text_before_first_header():
    msgs, warns = wc.parse_clip_text("游荡的一行\n甲 8/12 1:00:00\n正文\n",
                                     collected_at=COLLECTED)
    assert len(msgs) == 1
    assert "leading_text" in [w.code for w in warns]


def test_warning_bad_datetime_drops_message():
    """2 月 30 日不存在 → 告警并丢弃该条，不影响其他消息"""
    text = ("甲 2/30 10:00:00\n不存在的日期\n"
            "\n"
            "乙 3/1 10:00:00\n正常\n")
    msgs, warns = wc.parse_clip_text(text, collected_at=COLLECTED)
    assert [m.sender_name for m in msgs] == ["乙"]
    assert "bad_datetime" in [w.code for w in warns]


def test_warning_time_regression_but_message_kept():
    """时间倒退只告警不过滤：宁可多留一条让人核对，也不能悄悄丢消息"""
    text = ("甲 9/22 18:00:00\n后\n\n"
            "乙 9/22 17:00:00\n前\n")
    msgs, warns = wc.parse_clip_text(text, collected_at=COLLECTED)
    assert len(msgs) == 2
    codes = [w.code for w in warns]
    assert "time_regression" in codes


# ==================== 批次与行号 ====================

def test_parse_batches_counts_and_overlap():
    b0 = "甲 9/22 10:00:00\n第一条\n\n乙 9/22 11:00:00\n第二条\n"
    b1 = "乙 9/22 11:00:00\n第二条\n\n丙 9/22 12:00:00\n第三条\n"
    msgs, warns, stats = wc.parse_batches([(0, b0), (1, b1)], room_id="r",
                                          collected_at=COLLECTED)
    assert stats["batches"] == 2
    assert stats["raw"] == 4          # 两批原始条数（含重叠）
    assert len(msgs) == 3             # 去重后
    assert [s["parsed"] for s in stats["per_batch"]] == [2, 2]
    assert warns == []


def test_line_no_points_at_header_line():
    text = "甲 8/12 1:00:00\n正文\n\n乙 8/12 2:00:00\n正文2\n"
    msgs, _ = wc.parse_clip_text(text, collected_at=COLLECTED)
    assert [m.line_no for m in msgs] == [1, 4]


def test_batch_index_recorded():
    msgs, _ = wc.parse_clip_text("甲 8/12 1:00:00\n正文\n", batch_index=7,
                                 collected_at=COLLECTED)
    assert msgs[0].batch_index == 7


def test_room_id_propagated_to_every_message():
    msgs, _ = _sample_messages()
    assert {m.room_id for m in msgs} == {"after_sales"}
    assert {m.source for m in msgs} == {wc.SOURCE}


# ==================== 入库行映射 ====================

def test_message_to_row_shape_and_media_count():
    msgs, _ = _sample_messages()
    row = wc.message_to_row(msgs[0], room_name="售后群", pull_batch="20261006")
    assert set(row) == set(["msg_id", "source", "room_id", "room_name",
                            "sender_id", "sender_name", "sender_raw",
                            "sender_kind", "msg_type", "content", "media_count",
                            "msg_ts", "msg_ts_inferred_year", "mentions",
                            "pull_batch"])
    assert row["room_name"] == "售后群"
    assert row["pull_batch"] == "20261006"
    assert row["msg_type"] == "video"
    assert row["media_count"] == 1
    assert isinstance(row["mentions"], list)

    text_msg = [m for m in msgs if m.msg_type == "text"][0]
    assert wc.message_to_row(text_msg)["media_count"] == 0


def _write(path, text):
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def test_load_batches_reads_sorted(tmpdir_path):
    _write(os.path.join(tmpdir_path, "batch_02.txt"), "丙 8/12 3:00:00\n三\n")
    _write(os.path.join(tmpdir_path, "batch_00.txt"), "甲 8/12 1:00:00\n一\n")
    _write(os.path.join(tmpdir_path, "batch_01.txt"), "乙 8/12 2:00:00\n二\n")
    _write(os.path.join(tmpdir_path, "meta.json"), "{}")
    items = wc.load_batches(tmpdir_path)
    assert [i for i, _ in items] == [0, 1, 2]
    assert "甲" in items[0][1]


def test_load_batches_ignores_non_batch_files(tmpdir_path):
    _write(os.path.join(tmpdir_path, "batch_00.txt"), "甲 8/12 1:00:00\n一\n")
    _write(os.path.join(tmpdir_path, "readme.txt"), "不是批次文件")
    assert [i for i, _ in wc.load_batches(tmpdir_path)] == [0]


def test_load_batches_handles_bom(tmpdir_path):
    _write(os.path.join(tmpdir_path, "batch_00.txt"),
           "\ufeff甲 8/12 1:00:00\n一\n")
    items = wc.load_batches(tmpdir_path)
    msgs, _ = wc.parse_clip_text(items[0][1], collected_at=COLLECTED)
    assert msgs[0].sender_name == "甲"
