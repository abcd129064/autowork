# -*- coding: utf-8 -*-
"""主窗口 FluentWindow 重构冒烟（offscreen）

验证点：
  1. MainWindow 能正常构造（换基类 + 布局重排后无异常）
  2. navigationInterface 存在且「工作台」已注册
  3. hBoxLayout 只有 [导航 | 页面区] 两槽位（原三槽位错乱已消除）
  4. 菜单栏 / centralwidget / 状态栏都落在 homeInterface 内
  5. 页面可用宽度足够容纳业务表
  6. 切页 API switchTo 可用

用法：
  QT_QPA_PLATFORM=offscreen <venv>/python.exe tools/smoke_fluent_mainwindow.py
注意：退出码 139 是 offscreen 下 Qt 清理的已知段错误，断言结果看 stdout。
"""
import os
import sys

# --- 剔除 conda 注入的 Qt DLL 路径，避免与 PySide6 自带 Qt 冲突 ---
_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

ok = True


def check(tag, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  [PASS] " if cond else "  [FAIL] ") + tag + (" :: " + detail if detail else ""))


print("\n[1] 构造 MainWindow（FluentWindow）")
try:
    from main_window import MainWindow
    w = MainWindow()
    w.show()
    for _ in range(8):
        app.processEvents()
    check("1.1 MainWindow 构造无异常", True)
except Exception as e:
    check("1.1 MainWindow 构造无异常", False, repr(e))
    import traceback
    traceback.print_exc()
    os._exit(1)

print("\n[2] 导航结构")
nav = w.navigationInterface
check("2.1 navigationInterface 已创建", nav is not None)
check("2.2 已注册「工作台」页", w.stackedWidget.count() >= 1,
      f"stackedWidget.count()={w.stackedWidget.count()}")
check("2.3 homeInterface 有非空 objectName",
      bool(w.homeInterface.objectName()), repr(w.homeInterface.objectName()))

print("\n[3] hBoxLayout 槽位（应仅 2 个：导航 + 页面区）")
n = w.hBoxLayout.count()
slots = []
for i in range(n):
    it = w.hBoxLayout.itemAt(i)
    if it.widget():
        slots.append("W:" + (it.widget().objectName() or type(it.widget()).__name__))
    else:
        slots.append("L(Layout)")
print("     槽位 =", slots)
check("3.1 无三槽位错乱（原写法会产生 3 个）", n == 2, f"count={n}")

print("\n[4] 内容归属（centralwidget/状态栏 在 homeInterface 内；菜单栏已移入设置页）")
mb_parent = w._menubar_widget.parentWidget()
cw_parent = w.ui.centralwidget.parentWidget()
sb_parent = w._statusbar_widget.parentWidget()
check("4.1 菜单栏已不在工作台顶部（移入统一设置页）", mb_parent is not w.homeInterface,
      f"parent={mb_parent.objectName() if mb_parent else None}")
check("4.2 centralwidget 在 homeInterface 内", cw_parent is w.homeInterface,
      f"parent={cw_parent.objectName() if cw_parent else None}")
check("4.3 状态栏在 homeInterface 内", sb_parent is w.homeInterface,
      f"parent={sb_parent.objectName() if sb_parent else None}")
check("4.4 homeInterface 已挂到 stackedWidget",
      w.stackedWidget.indexOf(w.homeInterface) >= 0)

print("\n[5] 页面可用宽度（业务表列宽 1252~1292px）")
page_w = w.stackedWidget.currentWidget().width()
nav_w = nav.width()
print(f"     窗口 {w.width()}px - 导航 {nav_w}px = 页面 {page_w}px")
for name, cols in (("球桌管理", 1260), ("售后记录", 1292), ("跑视频", 1252), ("设备状态", 870)):
    gap = cols - page_w
    print(f"     {name:<8} {cols}px -> {'OK' if gap <= 0 else '溢出 %dpx' % gap}")
check("5.1 最宽业务表(1292)可容纳", page_w >= 1292, f"页面 {page_w}px")

print("\n[6] 切页 API")
try:
    w.switchTo(w.homeInterface)
    app.processEvents()
    check("6.1 switchTo 可用", w.stackedWidget.currentWidget() is w.homeInterface)
except Exception as e:
    check("6.1 switchTo 可用", False, repr(e))

print("\n[7] 关键子控件仍可访问（防止布局重排后控件丢失）")
for attr in ("id_list", "log_list", "date"):
    node = getattr(w.ui, attr, None)
    check(f"7.x ui.{attr} 仍存在", node is not None)

print("\n[8] 导航形态（2026-09-06 修订：4 一级页 + 底部 2 页）")
expect_pages = [
    ("homeInterface", "工作台"), ("management_hub", "运维管理"),
    ("aftersale_hub", "售后"), ("ledger_hub", "跑视频"),
    ("settings_hub", "设置(底部)"), ("about_page", "关于(底部)"),
]
for attr, label in expect_pages:
    page = getattr(w, attr, None)
    if page is None:
        check(f"8.x {label} 已注册", False, f"MainWindow.{attr} 不存在")
        continue
    in_stack = w.stackedWidget.indexOf(page) >= 0
    check(f"8.x {label} 在 stackedWidget", in_stack,
          f"count={w.stackedWidget.count()}")
# 远程会话（二期）/ 统计图表（不单独建页）不应再注册
check("8.9 remote_hub 已移除", getattr(w, "remote_hub", None) is None)
check("8.10 stats_page_hub 已移除", getattr(w, "stats_page_hub", None) is None)

print("\n[9] Pivot 二级容器（各 Hub 不再含设置页）")
hubs = [("management_hub", [("table_page", "球桌管理"),
                            ("device_page", "设备状态"),
                            ("health_page", "设备健康度"),
                            ("test_page", "组件测试"),
                            ("game_page", "小游戏")]),
        ("aftersale_hub", [("entry_page", "填写录入"),
                           ("records_page", "记录与统计")]),
        ("ledger_hub", [("entry_page", "填写录入"),
                        ("records_page", "记录与统计")])]
for hub_attr, subpages in hubs:
    hub = getattr(w, hub_attr, None)
    if hub is None:
        check(f"9.x {hub_attr}", False, "不存在")
        continue
    check(f"9.x {hub_attr} 无 settings_page",
          getattr(hub, "settings_page", None) is None)
    for page_attr, label in subpages:
        page = getattr(hub, page_attr, None)
        if page is None:
            check(f"9.x {hub_attr}.{page_attr}", False, "不存在")
            continue
        try:
            hub.switchTo(page)
            for _ in range(3):
                app.processEvents()
            ok_cur = hub.stack.currentWidget() is page
            check(f"9.x {hub_attr} → {label}", ok_cur)
        except Exception as e:
            check(f"9.x {hub_attr} → {label}", False, repr(e))

print("\n[10] 宿主属性别名与路由")
check("10.1 主窗口.table_page 别名",
      w.table_page is w.management_hub.table_page)
check("10.2 主窗口.device_page 别名",
      w.device_page is w.management_hub.device_page)
check("10.3 主窗口.records_page 别名(售后)",
      w.records_page is w.aftersale_hub.records_page)
try:
    w.switch_to_page(w.management_hub.device_page)
    for _ in range(3):
        app.processEvents()
    ok_route = (w.stackedWidget.currentWidget() is w.management_hub
                and w.management_hub.stack.currentWidget()
                is w.management_hub.device_page)
    check("10.4 switch_to_page 路由（切 Hub + 容器内子页）", ok_route)
except Exception as e:
    check("10.4 switch_to_page 路由", False, repr(e))

print("\n[11] 关闭路径（各 Hub worker detach 不抛异常）")
try:
    for name in ("management_hub", "aftersale_hub", "ledger_hub"):
        getattr(w, name).detach_workers()
    check("11.1 各 Hub detach_workers 无异常", True)
except Exception as e:
    check("11.1 各 Hub detach_workers 无异常", False, repr(e))

print("\n[12] Pivot 点击切换修复（itemClicked bool 覆盖默认参数 bug）")
from qfluentwidgets.components.navigation.pivot import PivotItem  # noqa: E402
try:
    hub = w.aftersale_hub
    hub.switchTo(hub.entry_page)
    for _ in range(3):
        app.processEvents()
    items = [it for it in hub.pivot.findChildren(PivotItem)]
    check("12.1 找到 PivotItem", len(items) >= 2, f"count={len(items)}")
    # 点击「记录与统计」（当前不在该页时才有效）
    target = hub.records_page
    if hub.stack.currentWidget() is not target and len(items) >= 2:
        # PivotItem 按添加顺序 = addPage 顺序，items[1] 即「记录与统计」
        item = items[1]
        item.click()
        for _ in range(5):
            app.processEvents()
        check("12.2 点击 PivotItem 后 stack 真实切换",
              hub.stack.currentWidget() is target,
              f"current={type(hub.stack.currentWidget()).__name__}")
    else:
        check("12.2 点击 PivotItem 后 stack 真实切换", True, "已在该页，跳过")
except Exception as e:
    check("12.x Pivot 点击切换", False, repr(e))

print("\n[13] Pivot 固定宽度靠左 + 已缩小 30%（13px 字体 / 24px 行高）")
for hub_attr in ("management_hub", "aftersale_hub", "ledger_hub"):
    hub = getattr(w, hub_attr, None)
    try:
        # 先切到该一级页，让 Hub 完成一次布局（否则宽度还是默认 100px）
        w.switchTo(hub)
        for _ in range(5):
            app.processEvents()
        pw = hub.pivot.width()
        hw = hub.width()
        check(f"13.x {hub_attr} pivot 宽度 < 容器宽度", 0 < pw < hw,
              f"pivot={pw}px hub={hw}px")
        check(f"13.x {hub_attr} pivot 已锁固定宽",
              hub.pivot.minimumWidth() == hub.pivot.maximumWidth())
        h = hub.pivot.height()
        fh = hub.pivot.items[next(iter(hub.pivot.items))].height()
        check(f"13.x {hub_attr} item 行高已缩小(24px)", fh == 24,
              f"item h={fh}px pivot h={h}px")
    except Exception as e:
        check(f"13.x {hub_attr} pivot 宽度", False, repr(e))

print("\n[14] 统一设置页（左标题 + 右 SegmentedWidget 分页切换）")
from qfluentwidgets.components.navigation.segmented_widget import (  # noqa: E402
    SegmentedItem)
sh = w.settings_hub
check("14.1 分组存在（应用配置5组/远程连接/工具/性能/数据库/面板设置3组/外观）",
      all(hasattr(sh, m) for m in ("_group_appearance", "_group_perf",
          "_group_tools", "_group_paths", "_group_remote", "_group_ai",
          "_group_upload", "_group_log_rules", "_group_database",
          "_group_aftersale", "_group_ledger", "_group_management",
          "_group_files")))
check("14.2 分段控件 + 内容页栈存在",
      getattr(sh, "_seg", None) is not None
      and getattr(sh, "_stack", None) is not None)
check("14.3 分页共 7 页（2026-09-07 远程连接独立分页）",
      sh._stack.count() == 7,
      f"count={sh._stack.count()}")
check("14.4 管理设置已嵌入（AdminSettingsPage embedded）",
      getattr(getattr(sh, "admin_settings", None), "_embedded", False) is True)
check("14.5 售后周期页已嵌入",
      getattr(sh, "cycle_page", None) is not None)
check("14.6 跑视频署名卡已嵌入",
      getattr(sh, "signer_card", None) is not None)
try:
    w.switchTo(sh)
    for _ in range(5):
        app.processEvents()
    items = sh._seg.findChildren(SegmentedItem)
    check("14.7 找到 SegmentedItem", len(items) == 7, f"count={len(items)}")
    before = sh._stack.currentIndex()
    items[-1].click()  # 点最后一项「外观」
    for _ in range(5):
        app.processEvents()
    check("14.8 点击分段项后内容页真实切换",
          sh._stack.currentIndex() == 6 and before != 6,
          f"{before} -> {sh._stack.currentIndex()}")
    sh.table_smooth_changed.emit("all")
    for _ in range(3):
        app.processEvents()
    check("14.9 table_smooth_changed 信号转发无异常", True)
except Exception as e:
    check("14.x SegmentedWidget 切换", False, repr(e))

print("\n[15] 云母（Mica）链路：窗口透明、业务 qss 挂 stackedWidget（不碰窗口表面）")
try:
    # FluentWidget.__init__ 默认 setMicaEffectEnabled(True)（Win11 build>=22000 生效）
    check("15.1 窗口 Mica 已启用", w.isMicaEffectEnabled() is True)
    # 2026-09-07 洋红壁纸对照实证：窗口级 setStyleSheet 会永久改变原生表面
    # 合成格式，云母被盖死且不可恢复（DWM 重设/hide+show 均无效）。
    # 业务 qss 必须挂在 stackedWidget；窗口自身不得含业务 qss。
    # 注：qfw 框架自身在 init 早期设置的 37 字符 'AcrylicWindow{background:transparent}'
    # 常驻窗口 stylesheet（时序在表面创建前，实测无害），故只断言业务 qss 不在窗口。
    check("15.2 窗口自身无业务 stylesheet（表面格式不被污染）",
          "QWidget#centralwidget" not in w.styleSheet(),
          f"len={len(w.styleSheet())}")
    check("15.3 业务 qss 挂在 stackedWidget（合并 qfw FLUENT_WINDOW qss）",
          len(w.stackedWidget.styleSheet()) > 1000
          and "StackedWidget {" in w.stackedWidget.styleSheet()
          and "QWidget#centralwidget" in w.stackedWidget.styleSheet(),
          f"len={len(w.stackedWidget.styleSheet())}")
    # Mica 开启时 qfw 窗口背景色应为全透明（_normalBackgroundColor）
    from PySide6.QtGui import QColor as _QC
    check("15.4 窗口背景色为透明（DWM 云母可见）",
          w.backgroundColor.alpha() == 0,
          f"alpha={w.backgroundColor.alpha()}")
except Exception as e:
    check("15.x 云母链路", False, repr(e))

print("\n" + "=" * 56)
print("冒烟结论：" + ("全部通过" if ok else "存在失败项"))
sys.stdout.flush()
os._exit(0 if ok else 2)
