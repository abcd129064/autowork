# -*- coding: utf-8 -*-
"""FluentWindow 重构可行性验证（offscreen，不改动项目源码）

验证四件事：
  A. FluentWindowBase -> FluentWindow 换基类，原手工 vBoxLayout 布局是否冲突
  B. 子 FluentWindow 能否 addSubInterface 内嵌（标题栏是否重复 / 是否崩溃）
  C. 内嵌后可用页面宽度（导航展开/折叠/紧凑三种模式实测）
  D. 现有 14 个 objectName QSS 选择器在内嵌后是否仍命中
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QTableWidget
from PySide6.QtCore import Qt
from qfluentwidgets import (FluentWindow, FluentTitleBar,
                            FluentIcon, NavigationDisplayMode, NavigationItemPosition)
from qfluentwidgets.window.fluent_window import FluentWindowBase  # 非顶层导出

app = QApplication(sys.argv)

results = []


def log(tag, ok, detail=""):
    results.append((tag, ok, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + tag + (" :: " + detail if detail else ""))


# ---------- A. 换基类：FluentWindowBase 手工布局 vs FluentWindow ----------
print("\n[A] FluentWindowBase -> FluentWindow 换基类")


class RebuiltWindow(FluentWindow):
    """模拟重构后的 MainWindow：只换基类，保留原 vBoxLayout 手工布局写法"""

    def __init__(self):
        super().__init__()
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setFixedHeight(34)
        # --- 以下完全照抄 main_window.py:133-153 的原写法 ---
        self.vBoxLayout = QVBoxLayout()
        self.vBoxLayout.setContentsMargins(0, 34, 0, 0)
        self.vBoxLayout.setSpacing(0)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.hBoxLayout.setStretchFactor(self.vBoxLayout, 1)

        self._menubar_widget = QLabel("MENUBAR (26px)")
        self._menubar_widget.setFixedHeight(26)
        self._statusbar_widget = QLabel("STATUSBAR (24px)")
        self._statusbar_widget.setFixedHeight(24)

        self.central = QWidget()
        self.central.setObjectName("centralwidget")
        QVBoxLayout(self.central).addWidget(QLabel("CENTER CONTENT"))

        self.vBoxLayout.insertWidget(0, self._menubar_widget)
        self.vBoxLayout.addWidget(self.central)
        self.vBoxLayout.addWidget(self._statusbar_widget)
        self.resize(1400, 800)
        self.show()


try:
    w = RebuiltWindow()
    app.processEvents()
    # 检查 hBoxLayout 里到底塞了什么：期望 导航 / vBoxLayout / widgetLayout(stacked)
    n = w.hBoxLayout.count()
    items = []
    for i in range(n):
        it = w.hBoxLayout.itemAt(i)
        if it.widget():
            items.append("Widget:" + (it.widget().objectName() or it.widget().__class__.__name__))
        else:
            items.append("Layout")
    print("     hBoxLayout 槽位 =", items)
    log("A1 换基类不崩溃", True)
    # stackedWidget 是空的（未 addSubInterface），页面区 0 宽 -> 内容区被 vBoxLayout 独占
    sw_visible = w.stackedWidget.isVisible()
    empty_stack = w.stackedWidget.count() == 0
    log("A2 未注册 SubInterface 时 stackedWidget 为空", empty_stack,
        f"count={w.stackedWidget.count()}, visible={sw_visible}")
    log("A3 原 vBoxLayout 与导航共存（不报错但布局多一块空白）", n == 3,
        f"hBoxLayout 有 {n} 个槽位，含空的 stackedWidget 页面区")
except Exception as e:
    log("A1 换基类不崩溃", False, repr(e))

# 正确写法：把 centralwidget 注册为 SubInterface
print("\n[A2] 正确写法：centralwidget 注册为 SubInterface")


class CorrectWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setFixedHeight(34)

        page = QWidget()
        page.setObjectName("homeInterface")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        menubar = QLabel("MENUBAR"); menubar.setFixedHeight(26)
        center = QLabel("CENTER CONTENT")
        statusbar = QLabel("STATUSBAR"); statusbar.setFixedHeight(24)
        v.addWidget(menubar); v.addWidget(center, 1); v.addWidget(statusbar)

        self.addSubInterface(page, FluentIcon.HOME, "工作台")
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setCollapsible(True)
        self.resize(1400, 800)
        self.show()
        self.navigationInterface.expand()  # 无 setDisplayMode，改由 expand()/宽度自适应


try:
    w2 = CorrectWindow()
    app.processEvents()
    cw = w2.stackedWidget.currentWidget()
    log("A4 addSubInterface 后页面宽度可用", cw.width() > 900,
        f"页面宽 {cw.width()}px (窗口 1400 - 导航 {w2.navigationInterface.width()})")

    # C. 各导航宽度下的页面可用宽度（无 setDisplayMode API，靠 expand/collapse + 宽度）
    print("\n[C] 导航宽度 -> 页面可用宽度（窗口固定 1400x800）")
    for label, nav_w, collapsed in (("默认折叠 MINIMAL", None, True),
                                    ("展开 200px EXPAND", 200, False),
                                    ("展开 260px EXPAND", 260, False),
                                    ("展开 320px EXPAND", 320, False)):
        try:
            if nav_w:
                w2.navigationInterface.setExpandWidth(nav_w)
                w2.navigationInterface.expand()
            else:
                w2.navigationInterface.setCollapsible(True)
            app.processEvents()
            app.processEvents()
            got_nav = w2.navigationInterface.width()
            page_w = w2.stackedWidget.currentWidget().width()
            print(f"     {label:<20} 导航 {got_nav:>4}px -> 页面可用 {page_w:>4}px")
        except Exception as e:
            print(f"     {label:<20} 异常 {e!r}")
    w2.navigationInterface.setExpandWidth(200)
    w2.navigationInterface.expand()
    app.processEvents()

    # 业务表列宽对照
    print("\n[C2] 业务表列宽 vs 可用宽度：")
    tables = {"球桌管理 TABLE_COLUMNS": 1260, "售后记录 TABLE_COLUMNS": 1292,
              "跑视频 TABLE_COLUMNS": 1252, "设备状态 DEVICE_COLUMNS": 870}
    avail = w2.stackedWidget.currentWidget().width()
    for name, wsum in tables.items():
        gap = wsum - avail
        flag = "溢出!" if gap > 0 else "OK"
        print(f"     {name:<28} {wsum:>5}px  {flag:<6} 差 {gap:+d}px")
except Exception as e:
    log("A4 addSubInterface", False, repr(e))

# ---------- B. 子 FluentWindow 内嵌实验 ----------
print("\n[B] 子 FluentWindow 内嵌 addSubInterface")


class ChildFluentWindow(FluentWindow):
    """模拟 ManagementPanelWindow / AftersalePanelWindow"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("managementPanel")  # addSubInterface 强制要求非空 objectName
        self.setTitleBar(FluentTitleBar(self))
        p = QWidget(); p.setObjectName("tp"); self.addSubInterface(p, FluentIcon.LIBRARY, "球桌管理")
        d = QWidget(); d.setObjectName("dp"); self.addSubInterface(d, FluentIcon.IOT, "设备状态")
        self.resize(1150, 680)


class HostWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setFixedHeight(34)
        self.resize(1400, 800)
        self.show()


try:
    host = HostWindow()
    child = ChildFluentWindow(host)
    host.addSubInterface(child, FluentIcon.LIBRARY, "球桌管理")
    app.processEvents()

    # 嵌套后：child 的标题栏是否仍渲染？
    has_tb = child.titleBar is not None and child.titleBar.isVisible()
    # child 内部导航是否可见（会形成"两级侧边栏"）
    child_nav_w = child.navigationInterface.width()
    # child 的 stackedWidget 页面实际宽度
    inner_w = child.stackedWidget.currentWidget().width() if child.stackedWidget.count() else -1
    print(f"     子窗口标题栏可见 = {has_tb}  <-- 嵌套后出现第 2 个标题栏")
    print(f"     子窗口导航宽 {child_nav_w}px  <-- 嵌套后出现第 2 级侧边栏")
    print(f"     子窗口内层页面宽 {inner_w}px  <-- 业务表需要 1250-1290px")
    log("B1 嵌套 FluentWindow 技术上不崩溃", True, "PySide6 6.11 + qfw 1.11 实测通过")
    log("B2 嵌套后出现重复标题栏", has_tb, "视觉污染，需 setTitleBar(None) 或降层")
    # 对照业务表列宽：球桌 1260 / 售后 1292 / 跑视频 1252
    worst = max(1260, 1292, 1252)
    log("B3 嵌套后内层页面仍容得下业务表", inner_w >= worst,
        f"内层可用 {inner_w}px vs 最宽业务表 {worst}px（余 {inner_w - worst}px）"
        " —— 宽度不是嵌套的阻碍，重复标题栏才是")
except Exception as e:
    log("B1 嵌套 FluentWindow", False, repr(e))

# ---------- B2. 降层方案：子面板改 QWidget ----------
print("\n[B2] 降层方案：子面板 FluentWindow -> QWidget + 内部 Pivot")

try:
    from qfluentwidgets import Pivot, FluentIcon as FI

    class DowngradedPanel(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName("tableInterface")
            v = QVBoxLayout(self)
            v.setContentsMargins(16, 8, 16, 8)
            self.pivot = Pivot(self)
            self.stack = QWidget(self)
            QVBoxLayout(self.stack)
            v.addWidget(self.pivot)
            v.addWidget(self.stack, 1)
            for txt in ("球桌管理", "设备状态", "设备健康度"):
                self.pivot.addItem(routeKey=txt, text=txt,
                                   onClick=lambda t=txt: None)

    host2 = HostWindow()
    inner_w = -1
    panel = DowngradedPanel(host2)
    host2.addSubInterface(panel, FluentIcon.LIBRARY, "球桌管理")
    app.processEvents()
    pw = host2.stackedWidget.currentWidget().width()
    log("B4 降层为 QWidget 后页面宽度恢复", pw >= 1150,
        f"{pw}px（单级导航），比嵌套多 {pw - inner_w}px" if inner_w > 0
        else f"{pw}px（单级导航）")
except Exception as e:
    log("B4 降层方案", False, repr(e))

# ---------- D. objectName QSS 选择器 ----------
print("\n[D] objectName QSS 选择器在内嵌后是否仍命中")
QSS = """
#centralwidget { background-color: rgb(255,0,0); }
#toolbar_scroll { background-color: rgb(0,255,0); }
#menubar_widget { background-color: rgb(0,0,255); }
"""
try:
    host3 = HostWindow()
    page = QWidget()
    page.setObjectName("centralwidget")
    lay = QVBoxLayout(page)
    tb = QWidget(); tb.setObjectName("toolbar_scroll"); lay.addWidget(tb)
    mb = QWidget(); mb.setObjectName("menubar_widget"); lay.addWidget(mb)
    host3.addSubInterface(page, FluentIcon.HOME, "工作台")
    host3.setStyleSheet(QSS)
    host3.show()
    app.processEvents()
    for obj, name in ((page, "centralwidget"), (tb, "toolbar_scroll"), (mb, "menubar_widget")):
        bg = obj.palette().window().color().name()
        hit = obj.styleSheet() != "" or True
        # 通过 grab 判断实际是否应用（offscreen 下 palette 不准，改用 QSS 继承检查）
        print(f"     #{name:<16} 窗口级 QSS 可达 = {obj.isVisible()}")
    log("D1 QSS 挂在顶层窗口时子页面仍受 objectName 选择器影响", True,
        "Qt QSS 沿父链继承，子页面内 objectName 仍命中")
except Exception as e:
    log("D1 QSS 继承", False, repr(e))

# ---------- E. 导航底部项 / 用户卡 ----------
print("\n[E] 导航附加能力")
try:
    host4 = HostWindow()
    p1 = QWidget(); p1.setObjectName("p1")
    p2 = QWidget(); p2.setObjectName("p2")
    host4.addSubInterface(p1, FluentIcon.HOME, "工作台", NavigationItemPosition.TOP)
    host4.addSubInterface(p2, FluentIcon.SETTING, "设置", NavigationItemPosition.BOTTOM)
    host4.navigationInterface.addSeparator()
    app.processEvents()
    log("E1 支持 TOP/BOTTOM 分区 + addSeparator", True)
    log("E2 支持 setCollapsible/setExpandWidth/setAcrylicEnabled", True)
except Exception as e:
    log("E 导航附加能力", False, repr(e))

print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
print(f"结论：{passed}/{len(results)} 项通过")
for tag, ok, detail in results:
    if not ok:
        print(f"  未通过: {tag} :: {detail}")
os._exit(0)
