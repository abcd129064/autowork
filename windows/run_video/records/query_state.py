# -*- coding: utf-8 -*-
"""跑视频记录页的纯查询态逻辑（筛选归一 / 日期区间 / 分页文案）

这些计算原本散在 windows/run_video/records/page.py 的视图方法里，只能靠
起 QApplication 再灌数据才能验；剥成纯函数后，月末、自定义倒置、空结果
这类边界可以在离线单测里直接钉死，不必连 SQLite。
本模块只依赖标准库，**不得** import Qt / database / workers：page.py 把
QDate、QComboBox 的当前值翻成这里的入参，依赖方向只有视图 → 本模块这一条。
"""
import calendar
from datetime import timedelta

__all__ = ["short_dt", "date_range", "current_filters",
           "pages_of", "clamp_page", "page_text", "total_text"]

# 下拉框的「不筛选」占位文案。三层各写各的，归一成空串后下游只认空串，
# 否则会出现「分类=全部分类」这种把占位词当真实值的查询。
_UNSET_CATEGORY = "全部分类"
_UNSET_KIND = "全部类别"
_UNSET_SIGNER = "全部署名"


def _clean(val) -> str:
    """任意值 → 去首尾空白的字符串；None 归一为空串而不是 "None"。"""
    return str(val or "").strip()


def _as_int(val) -> int:
    """宽容取整：None / 空串 / 脏值一律当 0，避免分页计算抛异常。

    QSpinBox、SQLite COUNT 正常都给 int；这里只是给「表格还没加载时
    total 为 None」留个兜底，不做四舍五入。
    """
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def short_dt(val) -> str:
    """「2026-08-22 21:14:33」→「08-22 21:14」；长度不足 16 位则原样返回

    时间列只有 128px，完整时间戳放不下；只在长度够时才截，避免把
    「2026-08-22」这类只有日期的值切成空串或半个日期。
    """
    s = _clean(val)
    return s[5:16] if len(s) >= 16 else s


def date_range(mode: str, base, end=None) -> tuple:
    """按日期模式与基准日期算出 (date_from, date_to)，ISO 字符串；空串=不过滤

    base/end 均为 datetime.date（QDate → date 的转换由 page.py 负责）。
    快捷档一律以基准日为右端：今天=当天；近 7 天=前推 6 天（含基准日共 7 天）；
    近 30 天=前推 29 天；本月=基准日所在自然月的 1 号到月末。

    「自定义」允许起止倒置，这里直接交换，避免上层拼出 date_from > date_to
    的 SQL（结果恒为空，用户只会看到「没有记录」而查不出原因）。
    未知模式按「今天」兜底，与拆分前 page.py 的末行 return 保持一致。
    """
    mode = _clean(mode)
    if mode == "全部":
        return "", ""
    if mode == "自定义":
        first, last = base, (end or base)
        if last < first:
            first, last = last, first
        return first.isoformat(), last.isoformat()
    if mode == "近7天":
        return (base - timedelta(days=6)).isoformat(), base.isoformat()
    if mode == "近30天":
        return (base - timedelta(days=29)).isoformat(), base.isoformat()
    if mode == "本月":
        # 月末不能用 replace(day=31)：2 月/小月会越界，必须查当月天数
        last_day = calendar.monthrange(base.year, base.month)[1]
        return (base.replace(day=1).isoformat(),
                base.replace(day=last_day).isoformat())
    return base.isoformat(), base.isoformat()  # 今天 / 未知模式


def _label(val, unset: str) -> str:
    """先 strip 再比占位词：手输补全的下拉可能带尾随空格。"""
    text = _clean(val)
    return "" if text == unset else text


def current_filters(category, kind, signer, keyword,
                    date_from, date_to, repro) -> dict:
    """收拢界面控件的当前值 → 查询参数 dict

    三个下拉的「全部 X」占位词归一为空串；其余文本一律 strip（关键词手输，
    尾部空格会让 LIKE 匹配不到）。键名与 page.py 原有 dict 一致，直接透传给
    worker / 统计弹窗。
    """
    return {
        "category": _label(category, _UNSET_CATEGORY),
        "kind": _label(kind, _UNSET_KIND),
        "signer": _label(signer, _UNSET_SIGNER),
        "keyword": _clean(keyword),
        "date_from": _clean(date_from),
        "date_to": _clean(date_to),
        "repro": _clean(repro),  # 全部→"" / 否 / 是
    }


def pages_of(total, page_size) -> int:
    """总页数，至少 1（空结果也显示「第 1/1 页」而不是「0/0」）

    page_size 非正数时返回 1 而不是抛 ZeroDivisionError：分页控件初始化
    时序上有机会先拿到 0，宁可显示一页也不要整页崩掉。
    """
    size = _as_int(page_size)
    if size <= 0:
        return 1
    return max(1, (_as_int(total) + size - 1) // size)


def clamp_page(page_no, total, page_size) -> int:
    """把页码夹进 [1, 总页数]：删记录后停在末页不会变成空表。"""
    return min(max(_as_int(page_no), 1), pages_of(total, page_size))


def page_text(page_no, total, page_size) -> str:
    """页脚页码文案「1/2」；入参页码先夹取，避免显示 5/2。"""
    return "%d/%d" % (clamp_page(page_no, total, page_size),
                      pages_of(total, page_size))


def total_text(total) -> str:
    """页脚总数文案「共 65 条」（空结果为「共 0 条」）。"""
    return "共 %d 条" % _as_int(total)
