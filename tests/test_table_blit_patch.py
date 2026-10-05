# -*- coding: utf-8 -*-
"""表格滚动「位块搬移」修复回归（2026-10-06，core/perf.py patch_table_scroll_blit）

背景：qfluentwidgets 给每个表格挂 QSS（QStyleSheetStyle 因此把 viewport 的
WA_OpaquePaintEvent 置 False），叠加库自绘的悬浮滚动条压住 viewport 右缘，
两条各自都足以让 Qt 放弃 QWidget::scroll() 的位块搬移 —— 滚动条每动一格都
整视口重绘（1600x900 实测 11.5ms/步，其中光栅 11.2ms）。

第二轮实现（2026-10-06，"viewport 真正不透明"形态）——第一版只设
WA_OpaquePaintEvent 而不画底，真机上表格滚动整片叠影（Qt 不再代擦背景，
而本项目表格底色本是透明：QSS transparent + 单元格 2px 缝隙）。本版把
"画满 viewport" 显式承担：先实测取「表格背后的实底颜色」，再在每次绘制前
用该颜色填满 e.rect()，取不到颜色则不开启（失败即功能不生效）。

覆盖：
1. 开关口径：perf 域 perf_table_scroll_blit_v2（默认开）、显式关闭解析、
   旧键 perf_table_scroll_blit / _experimental 一律不再读取；
2. **像素等价**（本文件的核心判据）：同一状态下开关前后逐像素比对，
   差异必须只落在「预留的滚动条条带 + 被重排的最后一列」范围内，
   左侧主体逐字节一致 —— 第一版那种"整片叠影"会在这里全线飘红；
3. 失败安全：取不到实底颜色时**不得**设置 WA_OpaquePaintEvent；
4. 机制判据（不依赖机器速度）：滚动一步的绘制面积远小于视口面积；
5. 关闭开关后两个条件都回退（属性清除 + 右侧条带归零），重布局后保持。
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtCore import QEvent, QObject, Qt              # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidgetItem  # noqa: E402

import core.perf as perf                                     # noqa: E402

_qapp = None


def _ensure_qapp():
    global _qapp
    _qapp = QApplication.instance() or QApplication(sys.argv[:1])
    return _qapp


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


class _PaintArea(QObject):
    """记录 viewport 每个 QPaintEvent 的矩形（面积口径区分整视口/条带）"""

    def __init__(self):
        super().__init__()
        self.rects = []

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Paint:
            r = ev.rect()
            self.rects.append((r.width(), r.height()))
        return False


def _make_table(rows=60, cols=13):
    from qfluentwidgets import TableWidget
    t = TableWidget()
    t.setRowCount(rows)
    t.setColumnCount(cols)
    for i in range(rows):
        for j in range(cols):
            t.setItem(i, j, QTableWidgetItem(f"cell {i}-{j}"))
    t.resize(1000, 600)
    t.show()
    _ensure_qapp().processEvents()
    return t


def _make_host_table(rows=60, cols=13, w=1000, h=600):
    """表格套在一个有内边距的宿主里（标定底色要能在表格外取到像素）"""
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from qfluentwidgets import TableWidget
    host = QWidget()
    host.setAutoFillBackground(True)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(10, 10, 10, 10)
    t = TableWidget()
    t.setRowCount(rows)
    t.setColumnCount(cols)
    for i in range(rows):
        for j in range(cols):
            t.setItem(i, j, QTableWidgetItem(f"cell {i}-{j}"))
    lay.addWidget(t)
    host.resize(w, h)
    host.show()
    _ensure_qapp().processEvents()
    return host, t


def _diff_stats(a, b):
    """逐像素比对两张 QImage（不抽样）：返回差异数 / 最大通道差 / 包围盒"""
    if a.size() != b.size():
        return {"diff_px": 10 ** 9, "max_delta": 255, "bbox": None,
                "samples": []}
    n = 0
    mx = 0
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    samples = []
    for y in range(a.height()):
        for x in range(a.width()):
            ca, cb = a.pixel(x, y), b.pixel(x, y)
            if ca == cb:
                continue
            n += 1
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)
            for sh in (0, 8, 16):
                d = abs(((ca >> sh) & 255) - ((cb >> sh) & 255))
                mx = max(mx, d)
            if len(samples) < 5:
                samples.append((x, y, hex(ca), hex(cb)))
    return {"diff_px": n, "max_delta": mx, "bbox": (x0, y0, x1, y1),
            "samples": samples}


def _region_equal(a, b, x0, y0, x1, y1):
    for y in range(y0, y1):
        for x in range(x0, x1):
            if a.pixel(x, y) != b.pixel(x, y):
                return False
    return True


def _core_equal(a, b):
    """左上 80%×80% 主体区域是否逐字节一致

    预留条带在右缘/下缘，最后一列因视口变窄会重排 —— 这两处的像素差异是
    设计内的；主体区域必须一致，第一版「整片叠影」正是在这里全线飘红。
    """
    return _region_equal(a, b, 0, 0, int(a.width() * 0.8),
                         int(a.height() * 0.8))


def _step_paint_rects(table, steps=3):
    """滚动 steps 步，返回 (viewport, 每步首个 paint 矩形列表)"""
    app = _ensure_qapp()
    vp = table.viewport()
    d = _PaintArea()
    vp.installEventFilter(d)
    sb = table.verticalScrollBar()
    sb.setValue(0)
    app.processEvents()
    out = []
    for i in range(1, steps + 1):
        d.rects.clear()
        sb.setValue(i)
        for _ in range(5):
            app.processEvents()
        out.append(d.rects[:1])
    vp.removeEventFilter(d)
    return vp, out


def _visible_bars(table):
    from qfluentwidgets.components.widgets.scroll_bar import ScrollBar
    return [b for b in table.findChildren(ScrollBar) if b.isVisible()]


# ==================== 开关默认值与解析 ====================

def test_default_enabled(monkeypatch):
    """默认开（第二轮实现已过像素等价验收）"""
    monkeypatch.setattr(perf, "_table_blit_enabled", None)
    monkeypatch.setattr(perf.app_settings, "get_domain", lambda _d: {})
    assert perf.is_table_blit_enabled() is True


def test_legacy_keys_ignored(monkeypatch):
    """第一版的键一律不再读取：旧值（含当年开过的 true）不影响新实现"""
    monkeypatch.setattr(perf, "_table_blit_enabled", None)
    monkeypatch.setattr(
        perf.app_settings, "get_domain",
        lambda _d: {"perf_table_scroll_blit": False,
                    "perf_table_scroll_blit_experimental": False})
    assert perf.is_table_blit_enabled() is True


def test_explicit_disable_parsed(monkeypatch):
    monkeypatch.setattr(perf, "_table_blit_enabled", None)
    monkeypatch.setattr(perf.app_settings, "get_domain",
                        lambda _d: {"perf_table_scroll_blit_v2": False})
    assert perf.is_table_blit_enabled() is False


# ==================== 像素等价（核心判据） ====================

def test_pixel_equivalence_vs_baseline(qapp, monkeypatch):
    """开关前后同一状态逐像素比对：差异只能落在预留条带/最后一列

    这一条就是为第一版「整片叠影」补的验收：第一版把旧像素留在下面，
    差异会铺满整个表格区（实测 115 万像素，包围盒横跨全宽）。
    """
    perf.patch_table_scroll_blit()

    monkeypatch.setattr(perf, "_table_blit_enabled", False)
    host, table = _make_host_table()
    img_off = host.grab().toImage()

    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    table._perf_blit_bg = None
    table._perf_blit_bg_pending = False
    perf.apply_table_blit(table)
    for _ in range(6):
        qapp.processEvents()

    bg = getattr(table, "_perf_blit_bg", None)
    assert bg is not None and bg.alpha() == 255, "应标定出全不透明底色"
    img_on = host.grab().toImage()

    stats = _diff_stats(img_off, img_on)
    w, h = img_off.width(), img_off.height()
    assert stats["diff_px"] / float(w * h) < 0.05, f"差异像素过多：{stats}"
    assert _core_equal(img_off, img_on), (
        f"左上主体必须逐字节一致，实测差异 {stats['diff_px']} px，"
        f"包围盒 {stats['bbox']}，样本 {stats['samples'][:3]}；"
        "主体飘红 = 背景没画满（第一版故障）")
    host.close()


def test_guard_catches_unfilled_opaque(qapp, monkeypatch):
    """反证：只设不透明、不画底（第一版做法）必须被同一条判据拦下

    结构必须忠实：**根控件自身背景透明**（qfw 页面/Mica 窗口就是这样，
    表格底色本来由「谁都画一下」凑出来）。若根控件自带不透明底色，
    第一版故障在 grab() 里也看不出来，用例会假绿。
    """
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QVBoxLayout, QWidget, QTableWidgetItem as _Item
    from qfluentwidgets import TableWidget

    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", False)

    host = QWidget()
    pal = host.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
    host.setPalette(pal)
    host.setAutoFillBackground(True)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(10, 10, 10, 10)
    table = TableWidget()
    table.setRowCount(30)
    table.setColumnCount(8)
    for i in range(30):
        for j in range(8):
            table.setItem(i, j, _Item(f"c{i}-{j}"))
    lay.addWidget(table)
    host.resize(600, 420)
    host.show()
    for _ in range(5):
        qapp.processEvents()

    img_off = host.grab().toImage()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    table._perf_blit_bg = None          # 故意没有底色
    perf.apply_table_blit(table)
    table.viewport().setAttribute(
        Qt.WidgetAttribute.WA_OpaquePaintEvent, True)   # 强行开不透明
    for _ in range(4):
        qapp.processEvents()
    img_bad = host.grab().toImage()
    assert not _core_equal(img_off, img_bad), \
        "没画底却开不透明必须被判据判为不一致（否则用例是假绿）"
    host.close()


def test_screen_sampling_requires_agreement(qapp, monkeypatch):
    """真机取色口径：必须「多个点一致」才采信（防采样到别的控件/被遮挡）

    2026-10-06 真机事故：原先用「渲染父级 1×1」取色，父级背景本身透明时
    拿到的是 palette 色 → 暗色主题把表格填成纯黑、浅色填成灰白。现改为抓
    屏幕真实像素，并用一致性做可信度门槛。
    """
    from PySide6.QtGui import QColor
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    monkeypatch.setattr(perf, "_platform_name", lambda: "windows")
    host, t = _make_host_table()

    monkeypatch.setattr(perf, "_screen_pixel", lambda pt: QColor(18, 18, 18))
    assert perf._sample_table_bg(t) == QColor(18, 18, 18)

    seq = iter([QColor(18, 18, 18), QColor(240, 240, 240),
                QColor(18, 18, 18), QColor(18, 18, 18)])
    monkeypatch.setattr(perf, "_screen_pixel", lambda pt: next(seq, None))
    assert perf._sample_table_bg(t) is None, "颜色不一致必须不采信"

    monkeypatch.setattr(perf, "_screen_pixel", lambda pt: None)
    assert perf._sample_table_bg(t) is None, "一个点都抓不到必须不采信"
    host.close()


def test_no_bg_means_feature_off(qapp, monkeypatch):
    """失败安全：取不到实底颜色时不得开启不透明（否则必叠影）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    from qfluentwidgets import TableWidget
    t = TableWidget()          # 无父级 → 取不到「背后」的像素
    t.setRowCount(3)
    t.setColumnCount(2)
    t._perf_blit_bg = None
    perf.apply_table_blit(t)
    assert t.viewport().testAttribute(
        Qt.WidgetAttribute.WA_OpaquePaintEvent) is False
    assert t.viewportMargins().right() == 0


# ==================== 机制判据 ====================

def test_blit_restores_strip_repaint(qapp, monkeypatch):
    """修复生效：滚动一步只重绘新露出条带（面积 << 视口面积）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    for _ in range(6):
        qapp.processEvents()

    vp = t.viewport()
    assert vp.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent) is True
    bars = _visible_bars(t)
    assert bars, "qfw 悬浮滚动条应存在（否则本条判据前提不成立）"
    assert not vp.geometry().intersects(bars[0].geometry()), \
        "悬浮滚动条不得压住 viewport（压住即拿不到位块搬移）"

    vp, rects = _step_paint_rects(t)
    assert rects and rects[0], "滚动一步应当产生绘制"
    area = sum(w * h for w, h in rects[0])
    limit = vp.width() * 8      # 条带高度个位数像素，留足机器差异余量
    assert area <= limit, (
        f"应只重绘新露出条带，实测 {rects[0]}（面积 {area} > {limit}）；"
        "退回整视口重绘说明位块搬移再次失效")
    t.close()


def test_blit_off_falls_back(qapp, monkeypatch):
    """关闭开关：不透明标记清除、右侧条带归零（回退库行为）"""
    perf.patch_table_scroll_blit()
    host, t = _make_host_table()
    monkeypatch.setattr(perf, "_table_blit_enabled", False)
    perf.apply_table_blit(t)
    qapp.processEvents()
    assert t.viewport().testAttribute(
        Qt.WidgetAttribute.WA_OpaquePaintEvent) is False
    assert t.viewportMargins().right() == 0
    t.close()


def test_strip_survives_relayout(qapp, monkeypatch):
    """换页/resize 触发重布局后条带仍然预留（updateGeometries 包装）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    for _ in range(6):
        qapp.processEvents()
    t.resize(t.width() - 40, t.height())
    qapp.processEvents()
    t.resize(t.width() + 40, t.height())
    qapp.processEvents()
    bars = _visible_bars(t)
    assert bars
    assert not t.viewport().geometry().intersects(bars[0].geometry())
    assert t.viewport().testAttribute(
        Qt.WidgetAttribute.WA_OpaquePaintEvent) is True
    t.close()

# ==================== 可观测性（misc2，2026-10-07） ====================

def _ts():
    return perf.table_blit_status()


def test_status_counts_applied_table(qapp, monkeypatch):
    """状态汇总以运行期事实为准：开了不透明 + 标定到底色 → applied"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    for _ in range(6):
        qapp.processEvents()

    assert t.viewport().testAttribute(
        Qt.WidgetAttribute.WA_OpaquePaintEvent) is True
    assert t._perf_blit_state == "applied"
    s = _ts()
    assert s["enabled"] is True
    assert s["total"] >= 1 and s["applied"] >= 1
    assert perf.table_blit_status_text().startswith("表格滚动加速：已生效")
    host.close()


def test_status_disabled_marks_all_off(qapp, monkeypatch):
    """总开关关闭：汇总里没有任何 applied，全部记 off（文案直说已关闭）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", False)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    qapp.processEvents()

    s = _ts()
    assert s["enabled"] is False
    assert s["applied"] == 0
    assert s["off"] == s["total"]
    assert perf.table_blit_status_text() == \
        "表格滚动加速：已关闭（perf_table_scroll_blit_v2=false）"
    host.close()


def test_status_classifies_hidden_vs_no_bg(qapp, monkeypatch):
    """「未显示」与「未取到底色」必须分开计数（排查时结论完全不同）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    for _ in range(6):
        qapp.processEvents()
    before = _ts()
    assert before["applied"] >= 1

    t.hide()
    t._perf_blit_bg = None
    perf.apply_table_blit(t)
    for _ in range(4):
        qapp.processEvents()
    assert t._perf_blit_state == "no_bg"
    assert "未显示" in (t._perf_blit_reason or "")

    after = _ts()
    assert after["hidden"] == before["hidden"] + 1
    assert after["applied"] == before["applied"] - 1

    # 换成「色不一致」原因 → 记 no_bg，并附上排查提示
    perf._set_blit_state(
        t, "no_bg", "表格四周取不到一致的实底颜色（有效采样 1/4，"
                    "需 ≥6px 纯色间隙且无遮挡）")
    s2 = _ts()
    assert s2["hidden"] == after["hidden"] - 1
    assert s2["no_bg"] == after["no_bg"] + 1
    txt = perf.table_blit_status_text()
    assert "未取到底色" in txt and "≥6px 纯色间隙" in txt
    host.close()


def test_state_change_logs_once(qapp, monkeypatch):
    """状态只在**转移**时写日志（重布局会反复调用 apply_table_blit）"""
    lines = []
    monkeypatch.setattr(perf, "_log", lambda msg: lines.append(msg))

    class _T:
        pass

    t = _T()
    perf._set_blit_state(t, "applied")
    perf._set_blit_state(t, "applied")
    assert len(lines) == 1 and "已生效" in lines[0]
    perf._set_blit_state(t, "no_bg", "表格四周取不到一致的实底颜色")
    assert len(lines) == 2
    assert "未生效：未取到底色" in lines[1]
    assert "表格四周取不到一致的实底颜色" in lines[1]


def test_status_text_no_bg_hint(qapp, monkeypatch):
    """有 no_bg 时文案必须给出「怎么才算生效」的提示（否则用户无从下手）"""
    perf.patch_table_scroll_blit()
    monkeypatch.setattr(perf, "_table_blit_enabled", True)
    host, t = _make_host_table()
    perf.apply_table_blit(t)
    for _ in range(6):
        qapp.processEvents()
    t._perf_blit_bg = None          # 制造一张「未取到底色」
    perf._set_blit_state(t, "no_bg", "offscreen 退化路径取不到底色（仅回归用）")

    txt = perf.table_blit_status_text()
    assert "未取到底色" in txt
    assert "≥6px 纯色间隙" in txt
    host.close()
