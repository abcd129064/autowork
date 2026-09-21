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
  QT_QPA_PLATFORM=offscreen <venv>/python.exe windows/tools/smoke_fluent_mainwindow.py
注意：退出码 139 是 offscreen 下 Qt 清理的已知段错误，断言结果看 stdout。
"""
import os
import sys

# --- 剔除 conda 注入的 Qt DLL 路径，避免与 PySide6 自带 Qt 冲突 ---
_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep)
          if "conda" not in p.lower() and "Library\\bin" not in p]
os.environ["PATH"] = os.pathsep.join(_parts)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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

print("\n[8] 导航形态（2026-09-07 二期：6 一级页 + 底部 2 页）")
expect_pages = [
    ("homeInterface", "工作台"), ("management_hub", "运维管理"),
    ("aftersale_hub", "售后"), ("ledger_hub", "跑视频"),
    ("remote_hub", "远程"), ("tool_hub", "工具"),
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
# 统计图表（不单独建页）不应注册；remote_hub 二期已回归为会话中心页
check("8.10 stats_page_hub 已移除", getattr(w, "stats_page_hub", None) is None)

print("\n[8b] 工具页 ToolHub（二期 2026-09-07 横排 Pivot 版）")
th = getattr(w, "tool_hub", None)
check("8b.1 ToolHub 四工作区", th is not None and th.stack.count() == 4)
check("8b.2 Pivot 4 项无图标风格", th is not None
      and len(th.pivot.items) == 4 and not hasattr(th, "tool_list"))
if th is not None:
    th.switchTo(th.newlog_work)
    for _ in range(3):
        app.processEvents()
    check("8b.3 switchTo 联动", th.stack.currentWidget() is th.newlog_work)
    th.switchTo(th.single_video_work)
    # 端口占用表格版（五列）与上传清单复选表格版（四列）
    check("8b.6 端口占用五列表格", th.port_fake_work.table.columnCount() == 5)
    check("8b.7 上传清单四列表格+全选框",
          th.upload_list_work.table.columnCount() == 4
          and th.upload_list_work.chk_all is not None)
    # 日志预览面板（2026-09-19 反馈：参数卡右侧空白区 → 日志预览）
    sv = th.single_video_work
    check("8b.8 日志预览面板存在（只读）",
          hasattr(sv, "log_preview") and sv.log_preview.isReadOnly()
          and hasattr(sv, "log_preview_meta"))
    sv.log_path_edit.setText("")
    sv._refresh_log_preview()
    check("8b.9 未选日志→提示态、刷新禁用",
          "未选择" in sv.log_preview_meta.text()
          and not sv.btn_log_refresh.isEnabled()
          and not sv.log_preview.toPlainText())
    import tempfile as _tempfile
    _fd, _log = _tempfile.mkstemp(suffix=".log")
    try:
        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
            _f.write("frame_id:5173 选手1 进1球，目标球-1，红球14\n")
        sv.log_path_edit.setText(_log)
        sv._refresh_log_preview()
        check("8b.10 选日志→预览联动（内容/大小行数）",
              "选手1 进1球" in sv.log_preview.toPlainText()
              and "行" in sv.log_preview_meta.text()
              and os.path.basename(_log) in sv.log_preview_meta.toolTip()
              and sv.btn_log_refresh.isEnabled())
        # 超上限截断保护（避免大日志把界面卡住）
        with open(_log, "w", encoding="utf-8") as _f:
            _f.write("x" * (type(sv)._LOG_PREVIEW_MAX_BYTES + 64))
        sv._refresh_log_preview()
        check("8b.11 超大日志截断提示",
              "仅预览前" in sv.log_preview.toPlainText())
    finally:
        try:
            os.remove(_log)
        except OSError:
            pass
    sv.log_path_edit.setText("")
    sv._refresh_log_preview()
    # 参数区 / 运行输出 可拖动分隔（2026-09-19 反馈）
    from PySide6.QtCore import Qt as _Qt
    check("8b.12 参数区与运行输出之间为可拖动分隔条（细间隔，同主界面）",
          hasattr(sv, "splitter")
          and sv.splitter.orientation() == _Qt.Orientation.Vertical
          and sv.splitter.count() == 2
          and not sv.splitter.childrenCollapsible()
          and sv.splitter.handleWidth() == 2)
    sv.splitter.setSizes([1000, 100])
    for _ in range(3):
        app.processEvents()
    _short = sv.splitter.sizes()[1]
    w.switchTo(w.tool_hub)      # 确保工具页已完成布局，否则几何断言恒为 0
    for _ in range(4):
        app.processEvents()
    sv.splitter.setSizes([100, 1000])
    for _ in range(3):
        app.processEvents()
    _tall = sv.splitter.sizes()[1]
    check("8b.13 拖动后输出区高度随 sizes 变化", _tall > _short,
          f"{_short} -> {_tall}")
    check("8b.14 批量整理页同构分隔条",
          th.newlog_work.splitter.count() == 2
          and not th.newlog_work.splitter.childrenCollapsible())
# 设置-工具瘦身：4 条已迁功能行不再出现在 _group_tools 源码里
import inspect as _inspect
from main_window.hub_pages import SettingsHubPage as _SHP
_gt_src = _inspect.getsource(_SHP._group_tools)
for gone in ("_on_open_single_video", "_on_open_port_fake",
             "_on_newlog_organize"):
    check(f"8b.4 设置-工具已移除 {gone}", gone not in _gt_src)
check("8b.5 设置-工具保留快捷键/诊断/前往",
      "_on_modify_shortcuts" in _gt_src and "_on_open_conn_diag" in _gt_src
      and "_on_open_tool_hub" in _gt_src)

print("\n[8c] 远程页 RemoteHub（二期 2026-09-21 四视图 Pivot：+连接质量）")
rh = getattr(w, "remote_hub", None)
check("8c.1 RemoteHub 四视图工作区",
      rh is not None and rh.stack.count() == 4)
check("8c.2 Pivot 4 项", rh is not None
      and len(rh.pivot.items) == 4)
if rh is not None:
    check("8c.2b 连接诊断不内嵌（入口在设置-工具）",
          getattr(rh, "diag_work", None) is None)
    check("8c.3 默认视图=会话总览",
          rh.stack.currentWidget() is rh.session_work)
    rh.switchTo(rh.visitor_work)
    for _ in range(3):
        app.processEvents()
    check("8c.4 switchTo 联动 P2P 访客",
          rh.stack.currentWidget() is rh.visitor_work)
    check("8c.5 会话总览表 9 列", rh.session_work.table.columnCount() == 9)
    check("8c.6 访客表 6 列+定宽控件",
          rh.visitor_work.table.columnCount() == 6
          and rh.visitor_work.edit_name.width() == 260)
    # 回归（真机首崩）：非空表 _add_row 行渲染（tooltip f-string 曾用错变量 s）
    _mgr = rh.session_work._mgr
    _tmp_sn = "__smoke_tmp_tunnel__"
    _base_rows = len(_mgr.records())
    _mgr.register_visitor(_tmp_sn)
    try:
        rh.session_work.refresh()
        check("8c.7 会话总览非空表行渲染无异常",
              rh.session_work.table.rowCount() == _base_rows + 1
              and _base_rows + 1 >= 1)
        rh.visitor_work.refresh()
        check("8c.8 访客表非空表行渲染无异常",
              rh.visitor_work.table.rowCount() == _base_rows + 1)
    finally:
        _mgr.remove_visitor(_tmp_sn)
        rh.session_work.refresh()
        rh.visitor_work.refresh()
    # 连接诊断独立弹窗仍可用（ConnDiagWidget 抽取后兼容属性）
    from windows.remote_session.conn_diag_panel import ConnDiagPanel
    dlg_probe = ConnDiagPanel()
    check("8c.9 ConnDiagPanel 兼容属性 _table/_body",
          dlg_probe._table is dlg_probe._body._table)
    dlg_probe.deleteLater()
    # 隧道配置：frpc 服务器卡 + 日志终端 + 平滑联动可调
    check("8c.10 隧道配置控件齐备",
          rh.tunnel_conf_work.log_view is not None
          and rh.tunnel_conf_work.edit_addr.width() == 200)
    try:
        rh._apply_table_smooth_all()
        check("8c.11 平滑联动 _apply_table_smooth_all 无异常", True)
    except Exception as e:
        check("8c.11 平滑联动 _apply_table_smooth_all 无异常", False, repr(e))
    # 主窗口路由联动：设置页开关联动应包含远程 Hub
    import inspect as _ins2
    from main_window.main_window import MainWindow as _MW
    _ats_src = _ins2.getsource(_MW._apply_all_table_smooth)
    check("8c.12 _apply_all_table_smooth 已接 remote_hub",
          "remote_hub" in _ats_src)
    rh.switchTo(rh.session_work)

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
check("14.1 分组存在（应用配置4组含启动/远程连接/工具2组含AI/性能/数据库/面板设置3组含上传/外观）",
      all(hasattr(sh, m) for m in ("_group_appearance", "_group_perf",
          "_group_tools", "_group_paths", "_group_startup", "_group_remote",
          "_group_ai", "_add_upload_rows", "_group_log_rules",
          "_group_database", "_group_aftersale", "_group_ledger",
          "_group_management", "_group_files")))
# 2026-09-07：AI 分析组自应用配置迁入工具页（行为断言：切分段查组标题）
def _page_has_group(page_widget, title):
    from qfluentwidgets import CaptionLabel as _CL
    return any(l.text() == title for l in page_widget.findChildren(_CL))
def _page_has_text(page_widget, text):
    from qfluentwidgets import BodyLabel as _BL
    return any(l.text() == text for l in page_widget.findChildren(_BL))
_sh = sh._stack
_sh.setCurrentIndex(sh._keys.index("tools"))
_t = _sh.currentWidget()
check("14.1b AI 组在工具分段页", _page_has_group(_t, "AI 分析"))
_sh.setCurrentIndex(sh._keys.index("config"))
_c = _sh.currentWidget()
check("14.1c AI 组不在应用配置页", not _page_has_group(_c, "AI 分析"))
# 2026-09-19：应用配置新增「启动」组（默认启动页面），位于日志高亮上方
import core.app_settings as _fas  # noqa: E402
check("14.1c2 应用配置页含「启动」组", _page_has_group(_c, "启动"))
check("14.1c3 启动组含「默认启动页面」行", _page_has_text(_c, "默认启动页面"))
# 切换行为：设 toolHub → _apply_startup_default_page → 当前页=工具
# （走 win._save_settings 真实链路：门面落盘 + 内存缓存同步，
#   _apply_startup_default_page 读的是 _load_settings 缓存）
_snap_sdp = _fas.get("startup_default_page")
w._save_settings({"startup_default_page": "toolHub"})
w._apply_startup_default_page()
check("14.1c4 配置 toolHub 后切换到工具页",
      w.stackedWidget.currentWidget() is w.tool_hub,
      repr(w.stackedWidget.currentWidget().objectName()))
w._save_settings({"startup_default_page": "homeInterface"})
w._apply_startup_default_page()
check("14.1c5 配置 homeInterface 后切回工作台",
      w.stackedWidget.currentWidget() is w.homeInterface)
w._save_settings({"startup_default_page": "notExistPage"})
w._apply_startup_default_page()
check("14.1c6 非法值回退工作台",
      w.stackedWidget.currentWidget() is w.homeInterface)
if _snap_sdp is None:
    _fas.remove("startup_default_page")
    w._reload_settings_cache()
else:
    w._save_settings({"startup_default_page": _snap_sdp})
# 2026-09-07：收集与上传自应用配置并入面板设置「运维」组
_sh.setCurrentIndex(sh._keys.index("panels"))
_p = _sh.currentWidget()
check("14.1d 上传行在面板设置运维组内", _page_has_text(_p, "上传服务器")
      and _page_has_text(_p, "上传密码"))
check("14.1e 上传行不在应用配置页", not _page_has_text(_c, "上传服务器"))
# 2026-09-16：面板设置-售后组新增「记住上次发生日期」开关（文案 + 数据往返）
check("14.1f 售后组含「记住上次发生日期」开关行",
      _page_has_text(_p, "记住上次发生日期"))
from database import aftersale_db as _adb
import core.app_settings as _fas
# 快照现场（冒烟可能跑在真实配置目录，测后必须精确还原）
_snap_remember = _fas.get("aftersale_remember_occurred")
_snap_last = _fas.get("aftersale_last_occurred")
_adb.set_remember_occurred(False)
_adb.save_last_occurred("2020-01-01")  # 关闭态写入应无效
check("14.1g 开关关闭时不记住日期",
      _adb.load_last_occurred() == "")
_adb.set_remember_occurred(True)
_adb.save_last_occurred("2020-01-01")
check("14.1h 开关开启时可记住日期",
      _adb.load_last_occurred() == "2020-01-01")
_adb.set_remember_occurred(False)  # 关闭应清除已记住值
check("14.1i 关闭开关清除记住值", _adb.load_last_occurred() == "")
# 还原现场（None=原本无键→删除；否则写回原值）
for _k, _v in (("aftersale_remember_occurred", _snap_remember),
               ("aftersale_last_occurred", _snap_last)):
    if _v is None:
        _fas.remove(_k)
    else:
        _fas.set(_k, _v)
# 2026-09-16：面板设置-售后组「自动刷新记录」开关 + 间隔行 + 联动信号
check("14.1j 售后组含「自动刷新记录」开关行",
      _page_has_text(_p, "自动刷新记录"))
check("14.1k 售后组含「自动刷新间隔」行",
      _page_has_text(_p, "自动刷新间隔"))
check("14.1l aftersale_auto_refresh_changed 信号存在",
      hasattr(sh, "aftersale_auto_refresh_changed"))
check("14.1m 主窗口已连线→AftersaleHub.apply_auto_refresh",
      hasattr(w.aftersale_hub, "apply_auto_refresh"))
_rp = w.aftersale_hub.records_page
_snap_ar = _fas.get("aftersale_auto_refresh")
_snap_iv = _fas.get("aftersale_auto_refresh_interval")
_fas.set("aftersale_auto_refresh", True)
_fas.set("aftersale_auto_refresh_interval", 60)
sh.aftersale_auto_refresh_changed.emit()  # 信号→连线槽→记录页 _sync_auto_timer
for _ in range(3):
    app.processEvents()
check("14.1n 设置信号转发链路：记录页定时器间隔同步为 60s",
      _rp._auto_timer.interval() == 60000)
# 不变式：定时器运行必然要求页面可见（开关已置 True；不可见则必须停）
if not _rp.isVisible():
    check("14.1o 隐藏页面不轮询", not _rp._auto_timer.isActive())
else:
    check("14.1o 可见+开关开=定时器运行", _rp._auto_timer.isActive())
for _k, _v in (("aftersale_auto_refresh", _snap_ar),
               ("aftersale_auto_refresh_interval", _snap_iv)):
    if _v is None:
        _fas.remove(_k)
    else:
        _fas.set(_k, _v)
_rp._sync_auto_timer()  # 还原现场
_sh.setCurrentIndex(sh._keys.index("config"))
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

print("\n[16] 弹出面板（2026-09-07 需求：每个 Hub 可复刻为独立窗口=重构前形态）")
try:
    check("16.1 open_hub_popout 存在", hasattr(w, "open_hub_popout"))
    check("16.2 五个 Hub 均有 btn_popout",
          all(hasattr(getattr(w, h), "btn_popout")
              for h in ("management_hub", "aftersale_hub", "ledger_hub",
                        "tool_hub", "remote_hub")))
    _t = w.open_hub_popout(w.tool_hub)   # 二期页 → 通用 HubPopoutWindow
    for _ in range(6):
        app.processEvents()
    check("16.3 工具弹出=HubPopoutWindow+独立hub实例",
          _t is not None and type(_t).__name__ == "HubPopoutWindow"
          and _t.hub is not w.tool_hub)
    check("16.4 嵌入 hub 隐藏二次弹出按钮", not _t.hub.btn_popout.isVisible())
    check("16.4b 弹出窗内提示文本随按钮隐藏", _t.hub.lbl_popout_hint.isHidden())
    w.switchTo(w.management_hub)
    for _ in range(4):
        app.processEvents()
    _mh = w.management_hub
    check("16.4c 弹出提示文本可见且贴按钮左侧",
          _mh.lbl_popout_hint.isVisible()
          and "弹出面板" in _mh.lbl_popout_hint.text()
          and _mh.lbl_popout_hint.x() + _mh.lbl_popout_hint.width()
          <= _mh.btn_popout.x(),
          f"hint=({_mh.lbl_popout_hint.x()},{_mh.lbl_popout_hint.width()}) "
          f"btn.x={_mh.btn_popout.x()}")
    _t._single_video_worker = "X"
    check("16.5 busy 守卫属性写透主窗口",
          getattr(w, "_single_video_worker", None) == "X")
    w._single_video_worker = None
    check("16.6 _load_settings 代理返回 dict",
          isinstance(_t._load_settings(), dict))
    check("16.7 二次弹出复用同实例", w.open_hub_popout(w.tool_hub) is _t)
    # 16.7b 远程面板弹出 = 左侧子导航形态（2026-09-21 需求：管理面板式）
    _r = w.open_hub_popout(w.remote_hub)
    for _ in range(6):
        app.processEvents()
    _rk = list(_r.navigationInterface.panel.items.keys())
    check("16.7b 远程弹出为 HubPopoutWindow",
          _r is not None and type(_r).__name__ == "HubPopoutWindow")
    check("16.7c 左侧导航含四视图且无 Hub 壳",
          set(["remoteSessionWork", "remoteVisitorWork",
               "remoteQualityWork", "remoteTunnelConfWork"]) <= set(_rk)
          and "remoteHub" not in _rk, str(_rk))
    check("16.7d Hub 空壳已隐藏", _r.hub.isHidden())
    check("16.7e 视图已从 hub.stack 摘出",
          _r.hub.stack.count() == 0)
    _r.switchTo(_r.hub.quality_work)
    for _ in range(4):
        app.processEvents()
    check("16.7f 左侧切换生效（当前=连接质量）",
          _r.stackedWidget.currentWidget() is _r.hub.quality_work)
    # 16.7g 视图内 _hub.switchTo 重绑转发（P2P 访客跳转可用）
    _r.hub.switchTo(_r.hub.visitor_work)
    for _ in range(4):
        app.processEvents()
    check("16.7g hub.switchTo 重绑到弹出窗导航",
          _r.stackedWidget.currentWidget() is _r.hub.visitor_work)
    _r2 = w._hub_popouts.get("remoteHub")
    if _r2 is not None:
        _r2.close()
        # close 不销毁 → 手动出登记，保证 16.9 计数口径不变
        w._hub_popouts.pop("remoteHub", None)
    _t.close()
    _a = w.open_hub_popout(w.aftersale_hub)  # 旧面板类原样复活
    for _ in range(6):
        app.processEvents()
    check("16.8 售后弹出走旧 AftersalePanelWindow",
          _a is not None and type(_a).__name__ == "AftersalePanelWindow")
    check("16.9 登记表收录弹出口", len(getattr(w, "_hub_popouts", {})) == 2)
    _t.close()
    for _ in range(4):
        app.processEvents()
    # close 仅隐藏（未销毁），登记表保留 → 再弹复用同实例并置顶
    check("16.10 关闭后再弹复用同实例",
          w.open_hub_popout(w.tool_hub) is _t)
    _t.hide()
    _t2 = w._hub_popouts.get("toolHub")
    if _t2 is not None:
        _t2.close()
    _a2 = w._hub_popouts.get("aftersaleHub")
    if _a2 is not None:
        _a2.close()
    for _ in range(4):
        app.processEvents()
    check("16.11 收尾关闭弹出窗口无异常", True)
except Exception as e:
    check("16.x 弹出面板", False, repr(e))

print("\n[17] 面板入口默认弹出（2026-09-20：跑视频/售后/球桌管理默认弹面板，设置可关）")
try:
    from PySide6.QtWidgets import QLabel as _QL
    # 17.1 设置页「启动」组存在开关行
    _found = False
    for _lbl in w.settings_hub.findChildren(_QL):
        if _lbl.text() == "面板入口默认弹出":
            _found = True
            break
    check("17.1 设置-应用配置-启动 含「面板入口默认弹出」行", _found)
    # 17.2 键登记 ui 域
    from core import app_settings as _aps
    check("17.2 panel_entry_popout 登记 ui 域",
          _aps.domain_of("panel_entry_popout") == "ui")
    # 17.3 默认开 → 入口走弹出（先备份原值，收尾恢复，避免污染真实配置）
    _orig_pop = w._load_settings().get("panel_entry_popout", True)
    w._save_settings({"panel_entry_popout": True})
    check("17.3 默认判定为弹出", w._panel_entry_popout_enabled() is True)
    _p = w._hub_popouts
    w.ui.table_panel_btn.click()
    for _ in range(6):
        app.processEvents()
    check("17.4 球桌管理入口弹出旧 ManagementPanelWindow",
          type(_p.get("managementHub")).__name__ == "ManagementPanelWindow")
    w.ui.btn_aftersale.click()
    for _ in range(6):
        app.processEvents()
    check("17.5 售后入口弹出旧 AftersalePanelWindow",
          type(_p.get("aftersaleHub")).__name__ == "AftersalePanelWindow")
    w.ui.btn_write_table.click()
    for _ in range(6):
        app.processEvents()
    check("17.6 跑视频入口弹出旧 LedgerPanelWindow",
          type(_p.get("ledgerHub")).__name__ == "LedgerPanelWindow")
    # 17.7 关闭设置 → 回落页内跳转（当前页变为对应 Hub，登记表不新增）
    _n_before = len(_p)
    w._save_settings({"panel_entry_popout": False})
    check("17.7a 判定为页内跳转", w._panel_entry_popout_enabled() is False)
    w.ui.table_panel_btn.click()
    for _ in range(6):
        app.processEvents()
    check("17.7b 关闭后球桌管理走 switchTo",
          w.stackedWidget.currentWidget() is w.management_hub
          and len(_p) == _n_before)
    # 恢复用户原值并收尾
    w._save_settings({"panel_entry_popout": bool(_orig_pop)})
    for _k in ("managementHub", "aftersaleHub", "ledgerHub"):
        _v = _p.get(_k)
        if _v is not None:
            _v.close()
    for _ in range(4):
        app.processEvents()
    check("17.8 收尾关闭弹出窗口无异常", True)
except Exception as e:
    check("17.x 面板入口默认弹出", False, repr(e))

print("\n[18] 自动更新（2026-09-21：关于页检查更新 + 下载对话框状态机 + 回执消费 + dev 安装守卫）")
try:
    import json as _json
    import tempfile as _tf
    from core import updater as _upd
    from main_window import update_mixin as _um

    # 18.1 UpdateMixin 已混入主窗口（四个编排方法齐全）
    check("18.1 UpdateMixin 已混入 MainWindow",
          all(callable(getattr(w, m, None)) for m in (
              "check_for_update", "_install_update",
              "consume_update_receipt_on_startup", "auto_check_update_on_startup")))

    # 18.2 关于页按钮存在且已接线到主窗口
    _btn = w.about_page.btn_check_update
    check("18.2 关于页「检查更新」按钮存在",
          _btn is not None and _btn.objectName() == "aboutCheckUpdateButton")
    check("18.2b 按钮文案正确", _btn.text() == "检查更新", _btn.text())
    check("18.2c 按钮已接线到 check_for_update",
          w.about_page.on_check_update == w.check_for_update)
    check("18.2d 按钮初始可用", _btn.isEnabled())

    # 18.3 set_checking / show_check_result 状态回写
    w.about_page.set_checking(True)
    check("18.3a 检查中禁用按钮", not _btn.isEnabled())
    check("18.3b 检查中文案", "正在检查" in w.about_page.update_status.text(),
          w.about_page.update_status.text())
    w.about_page.set_checking(False)
    w.about_page.show_check_result("已是最新版本 3.11.280", "success")
    check("18.3c 恢复可用并回写结果",
          _btn.isEnabled() and "3.11.280" in w.about_page.update_status.text())

    # 18.4 环境信息：dev 环境 frozen=False、main_exe 空、更新源为生产地址
    _env = w._update_env()
    check("18.4a dev 环境 frozen=False", _env["frozen"] is False)
    check("18.4b dev 环境 main_exe 为空", _env["main_exe"] == "")
    check("18.4c 更新源默认生产地址",
          _env["base_url"] == _upd.DEFAULT_UPDATE_BASE_URL, _env["base_url"])
    check("18.4d 本地版本非空", bool(_env["local_version"]), _env["local_version"])

    # 18.5 配置键登记到 misc 域
    from core import app_settings as _aps2
    check("18.5 update_base_url/update_auto_check 登记 misc 域",
          _aps2.domain_of("update_base_url") == "misc"
          and _aps2.domain_of("update_auto_check") == "misc")

    # 18.6 安全边界：dev 环境必须拒绝安装（否则会把源码目录替换掉）
    _dev_staging = os.path.join(_tf.gettempdir(), "_aw_smoke_staging")
    os.makedirs(_dev_staging, exist_ok=True)
    check("18.6 dev 环境 _install_update 返回 False",
          w._install_update(_dev_staging, "full") is False)

    # 18.7 staging 缺失也必须拒绝（即便假装是打包版）
    _orig_frozen = getattr(sys, "frozen", False)
    try:
        sys.frozen = True
        check("18.7 frozen 下 staging 不存在仍拒绝",
              w._install_update(os.path.join(_tf.gettempdir(), "_no_such_dir"),
                                "full") is False)
    finally:
        if not _orig_frozen:
            del sys.frozen

    # 18.8 更新对话框状态机（offscreen 不 exec，直接驱动 phase 迁移）
    from windows.update_dialog import UpdateDialog
    _entry = {"version": "3.11.999", "_remote_version": "3.11.999",
              "notes": "修复启动闪屏\n新增检查更新",
              "min_version": "",
              "_resolved_url": "http://h/u/packages/a.zip",
              "package": {"mode": "full", "url": "packages/a.zip",
                          "sha256": "abc", "size": 567 * 1048576}}
    _dlg = UpdateDialog(w, _entry, "http://h/u", _env["app_dir"],
                        main_exe="AutoWork.exe",
                        local_version=_env["local_version"],
                        on_install=lambda s, m: True)
    check("18.8a 初始 phase=found", _dlg._phase == "found")
    check("18.8b 版本对比文案含箭头", "→" in _dlg.ver_label.text(),
          _dlg.ver_label.text())
    check("18.8c 包大小人类可读", "567.0 MB" in _dlg.meta_label.text(),
          _dlg.meta_label.text())
    check("18.8d 完整包标识", "完整包" in _dlg.meta_label.text())
    check("18.8e 更新说明已填入", "修复启动闪屏" in _dlg.notes_edit.toPlainText())
    check("18.8f 进度区初始隐藏", not _dlg.prog_bar.isVisibleTo(_dlg))
    check("18.8g yes=立即更新 / cancel=稍后",
          _dlg.yesButton.text() == "立即更新" and _dlg.cancelButton.text() == "稍后")

    # 下载中：进度区显示、yes 禁用、忙碌动画（total 未知）
    _dlg._enter_downloading()
    check("18.8h phase=downloading", _dlg._phase == "downloading")
    check("18.8i 进度区可见", _dlg.prog_bar.isVisibleTo(_dlg))
    check("18.8j yes 禁用", not _dlg.yesButton.isEnabled())
    check("18.8k cancel 变为取消", _dlg.cancelButton.text() == "取消")
    _dlg._on_progress(50 * 1048576, 100 * 1048576)
    check("18.8l 进度百分比正确", _dlg.prog_bar.value() == 50,
          str(_dlg.prog_bar.value()))
    check("18.8m 进度文案含 MB 与百分比",
          "50.0 MB" in _dlg.stage_label.text()
          and "50%" in _dlg.stage_label.text(), _dlg.stage_label.text())
    _dlg._on_progress(10, 0)     # total 未知 → 忙碌动画
    check("18.8n total=0 走忙碌动画", _dlg.prog_bar.maximum() == 0)

    # 下载中禁止关闭（防信号打到已销毁控件）
    _dlg._phase = "downloading"
    _dlg.reject()
    check("18.8o 下载中 reject 被忽略", _dlg._phase == "downloading")

    # ready：进度满、yes=安装并重启
    _dlg._enter_ready({"staging_dir": _dev_staging, "mode": "full",
                       "files_count": 3210})
    check("18.8p phase=ready", _dlg._phase == "ready")
    check("18.8q 进度 100%", _dlg.prog_bar.value() == 100)
    check("18.8r yes=安装并重启", _dlg.yesButton.text() == "安装并重启")
    check("18.8s 文件数写入文案", "3210" in _dlg.stage_label.text(),
          _dlg.stage_label.text())

    # error：原因可见、yes=重试
    _dlg._enter_error("sha256 校验失败")
    check("18.8t phase=error", _dlg._phase == "error")
    check("18.8u 失败原因可见", "sha256" in _dlg.notes_edit.toPlainText())
    check("18.8v yes=重试", _dlg.yesButton.text() == "重试")
    _dlg._back_to_found()
    check("18.8w 重试回到 found 且说明复原",
          _dlg._phase == "found" and "修复启动闪屏" in _dlg.notes_edit.toPlainText())
    _dlg.deleteLater()
    for _ in range(3):
        app.processEvents()

    # 18.9 增量模式标签（min_version 不满足时显示为完整包）
    _inc_entry = dict(_entry)
    _inc_entry["package"] = {"mode": "incremental", "url": "p.zip",
                             "sha256": "", "size": 1048576}
    _inc_entry["min_version"] = "3.11.280"
    _d2 = UpdateDialog(w, _inc_entry, "http://h/u", _env["app_dir"],
                       main_exe="AutoWork.exe", local_version="3.11.999",
                       on_install=None)
    check("18.9a 满足 min_version 显示增量更新",
          "增量更新" in _d2.meta_label.text(), _d2.meta_label.text())
    _d2.deleteLater()
    _d3 = UpdateDialog(w, _inc_entry, "http://h/u", _env["app_dir"],
                       main_exe="AutoWork.exe", local_version="3.11.100",
                       on_install=None)
    check("18.9b 低于 min_version 降级显示完整包",
          "完整包" in _d3.meta_label.text(), _d3.meta_label.text())
    # on_install 未注入时安装必须报错而非静默
    _d3._staging_dir = _dev_staging
    _d3._do_install()
    check("18.9c 未注入安装回调时进入 error",
          _d3._phase == "error" and "回调" in _d3.notes_edit.toPlainText(),
          _d3.notes_edit.toPlainText()[:40])
    _d3.deleteLater()
    for _ in range(3):
        app.processEvents()

    # 18.10 回执消费：成功路径（patch app_dir 到临时目录，不碰真实项目）
    _rc_dir = os.path.join(_tf.mkdtemp(prefix="aw_smoke_rc_"))
    _upd.mark_update_pending(_rc_dir, {"version": "3.11.999", "mode": "full"})
    with open(os.path.join(_rc_dir, _upd.RESULT_LOG), "w",
              encoding="utf-8") as _f:
        _f.write('{"ok": true}')
    _orig_getdir = _um.get_app_dir
    try:
        _um.get_app_dir = lambda: _rc_dir
        _r = w.consume_update_receipt_on_startup()
        check("18.10a 成功回执被消费", bool(_r) and _r["ok"] is True, str(_r))
        check("18.10b 版本号带回", _r.get("version") == "3.11.999")
        check("18.10c 消费后 pending 删除",
              not os.path.isfile(os.path.join(_rc_dir, _upd.PENDING_FLAG)))
        check("18.10d 消费后 result.log 删除",
              not os.path.isfile(os.path.join(_rc_dir, _upd.RESULT_LOG)))
        check("18.10e 二次消费返回 None（不重复提示）",
              w.consume_update_receipt_on_startup() is None)

        # 18.11 失败路径：有 pending 无回执 → 判失败且绝不重试
        _upd.mark_update_pending(_rc_dir, {"version": "9.9.9"})
        _r2 = w.consume_update_receipt_on_startup()
        check("18.11a 无回执判定失败", bool(_r2) and _r2["ok"] is False, str(_r2))
        check("18.11b 失败原因可读", bool(_r2.get("error")), _r2.get("error"))
        check("18.11c 失败后 pending 已清（防死循环）",
              not os.path.isfile(os.path.join(_rc_dir, _upd.PENDING_FLAG)))

        # 18.12 残留 staging 被清理
        _st = os.path.join(_rc_dir, _upd.STAGING_DIRNAME, "sub")
        os.makedirs(_st, exist_ok=True)
        with open(os.path.join(_st, "leftover.dll"), "w") as _f:
            _f.write("x")
        _upd.mark_update_pending(_rc_dir, {"version": "1.0.0"})
        w.consume_update_receipt_on_startup()
        check("18.12 残留 staging 已清理",
              not os.path.isdir(os.path.join(_rc_dir, _upd.STAGING_DIRNAME)))
    finally:
        _um.get_app_dir = _orig_getdir
        import shutil as _sh
        _sh.rmtree(_rc_dir, ignore_errors=True)
        _sh.rmtree(_dev_staging, ignore_errors=True)

    # 18.13 updater 脚本生成安全约束（bat 必须在安装目录之外 + 占位符全解析）
    _bat_txt = _upd.build_bat(r"C:\Fake Install\AutoWork", r"C:\stg dir",
                              "AutoWork.exe", 4321, "full")
    check("18.13a bat 占位符全解析", "__INSTALL_DIR__" not in _bat_txt
          and "__WAIT_PID__" not in _bat_txt)
    check("18.13b bat 用 System32 绝对路径（防 PATH 污染穿透等待循环）",
          "%SYS32%\\tasklist.exe" in _bat_txt and "%SYS32%\\Robocopy.exe" in _bat_txt)
    check("18.13c bat 未用 timeout（改 ping，不依赖 console stdin）",
          "timeout" not in "\n".join(
              ln for ln in _bat_txt.splitlines()
              if not ln.strip().lower().startswith(("rem", "::"))).lower())
    check("18.13d vbs 用 Chr(34) 拼引号（防 raw 三引号吞转义）",
          "Chr(34)" in _upd._VBS_TEMPLATE)
    check("18.13e 用户数据排除目录齐全",
          all(d in _bat_txt for d in _upd.EXCLUDE_DIRS))

    # 18.14 打包版真正安装路径（生产主路径）：frozen + staging 存在
    #      → 必须调 launch_updater 并传对 app_dir/staging/main_exe/mode/pending
    _calls = []
    _orig_launch = _upd.launch_updater
    _inst2 = os.path.join(_tf.mkdtemp(prefix="aw_smoke_inst_"), "AutoWork")
    os.makedirs(_inst2, exist_ok=True)
    _stg2 = os.path.join(_tf.gettempdir(), "_aw_smoke_stg2")
    os.makedirs(_stg2, exist_ok=True)
    _orig_quit = _um.QTimer.singleShot
    try:
        sys.frozen = True
        sys.executable = os.path.join(_inst2, "AutoWork.exe")
        _upd.launch_updater = lambda *a, **kw: (_calls.append((a, kw)), True)[1]
        _um.QTimer.singleShot = lambda *a, **kw: None   # 不真退出事件循环
        # 走对话框 → on_install 注入的完整链路（版本号由对话框显式传出）
        _d4 = UpdateDialog(w, dict(_entry, _remote_version="3.11.999"),
                           "http://h/u", _inst2, main_exe="AutoWork.exe",
                           local_version="3.11.273",
                           on_install=w._install_update)
        _d4._staging_dir = _stg2
        _d4._mode = "full"
        _d4._do_install()
        for _ in range(3):
            app.processEvents()
        check("18.14a frozen+staging 存在 → 调用 launch_updater",
              len(_calls) == 1, str(len(_calls)))
        if _calls:
            _a, _kw = _calls[0]
            check("18.14b app_dir 传对", _a[0] == _inst2, _a[0])
            check("18.14c staging_dir 传对", _a[1] == _stg2, _a[1])
            check("18.14d main_exe 传对", _a[2] == "AutoWork.exe", _a[2])
            check("18.14e mode 传对", _kw.get("mode") == "full", str(_kw.get("mode")))
            _pi = _kw.get("pending_info") or {}
            check("18.14f pending_info 含目标版本",
                  _pi.get("version") == "3.11.999", str(_pi))
            check("18.14g pending_info 含来源版本（失败时可提示回退到哪个版本）",
                  bool(_pi.get("from_version")), str(_pi))
        check("18.14h 安装发起后对话框 accept 关闭",
              not _d4.isVisible() or _d4._phase == "ready")
        _d4.deleteLater()
        # 18.15 launch_updater 抛异常 → 必须报错且对话框进入 error（不静默退出）
        _calls.clear()

        def _boom(*a, **kw):
            raise OSError("wscript 不可用")

        _upd.launch_updater = _boom
        _d5 = UpdateDialog(w, dict(_entry), "http://h/u", _inst2,
                           main_exe="AutoWork.exe", local_version="3.11.273",
                           on_install=w._install_update)
        _d5._staging_dir = _stg2
        _d5._do_install()
        for _ in range(3):
            app.processEvents()
        check("18.15 拉起异常 → 对话框进入 error 且不退出程序",
              _d5._phase == "error" and "更新程序" in _d5.notes_edit.toPlainText(),
              _d5.notes_edit.toPlainText()[:60])
        _d5.deleteLater()
    finally:
        _upd.launch_updater = _orig_launch
        _um.QTimer.singleShot = _orig_quit
        if not _orig_frozen:
            del sys.frozen
        import shutil as _sh2
        _sh2.rmtree(os.path.dirname(_inst2), ignore_errors=True)
        _sh2.rmtree(_stg2, ignore_errors=True)
        for _ in range(3):
            app.processEvents()

    check("18.13f 收尾关闭更新对话框无异常", True)
except Exception as e:
    import traceback as _tb
    check("18.x 自动更新", False, repr(e) + "\n" + _tb.format_exc()[-600:])

# ==================== 19. 导航更新按钮（设置上方，下载进度/就绪提示） ====================
try:
    print("\n--- [19] 导航更新按钮 ---")
    _btn = getattr(w, "_update_nav_btn", None)
    check("19.1 导航更新按钮已创建", _btn is not None)
    check("19.2 初始隐藏（无更新不打扰）",
          _btn is not None and not _btn.isVisibleTo(w))

    # 19.3 状态机：found → downloading → ready → hidden
    w._set_update_nav("found", "发现新版本 3.11.999，点击下载")
    check("19.3a found 态可见且 tooltip 正确",
          _btn.isVisibleTo(w) and _btn.state == "found"
          and "3.11.999" in _btn.toolTip())
    w._set_update_nav("downloading", "正在下载更新 42%")
    check("19.3b downloading 态图标切换（DOWNLOAD）", _btn.state == "downloading")
    w._set_update_nav("ready", "已就绪，点击安装")
    check("19.3c ready 态", _btn.state == "ready")
    w._set_update_nav("hidden")
    check("19.3d hidden 态重新隐藏",
          not _btn.isVisibleTo(w) and _btn.state == "hidden")

    # 19.4 静默检查发现新版 → 挂 found 态 + 弹更新对话框（需求：打开程序弹出提示）
    _entry19 = {"version": "3.11.999", "_remote_version": "3.11.999",
                "notes": "x", "package": {"mode": "full", "url": "u",
                                          "sha256": "s", "size": 1}}
    _env19 = w._update_env()
    _dlg_calls = []
    _orig_open_dlg = w._open_update_dialog
    w._open_update_dialog = lambda e, env: _dlg_calls.append(e)
    try:
        w._on_update_found(_entry19, True, _env19)
    finally:
        w._open_update_dialog = _orig_open_dlg
    check("19.4a silent 发现新版 → 导航挂 found", _btn.state == "found")
    check("19.4a2 silent 发现新版 → 弹出更新对话框",
          _dlg_calls and _dlg_calls[0] is _entry19)
    check("19.4b entry/env 已保存供图标点击使用",
          getattr(w, "_update_pending_entry", None) is _entry19
          and getattr(w, "_update_pending_env", None) is _env19)

    # 19.5 点击图标（found 态）→ 走后台下载（mock worker，不真起线程）
    _bg_calls = []
    _orig_bg = w._start_background_download

    def _fake_bg(entry, env):
        _bg_calls.append((entry, env))
        w._set_update_nav("downloading", "正在下载更新…")

    w._start_background_download = _fake_bg
    w._on_update_nav_clicked()
    check("19.5a found 态点击 → 触发后台下载",
          len(_bg_calls) == 1 and _bg_calls[0][0] is _entry19)
    check("19.5b 点击后进入 downloading 态", _btn.state == "downloading")

    # 19.6 下载中点击 → 提示进度（不重复起下载）
    w._update_progress_text = "正在下载更新 50%（100.0 MB/200.1 MB）"
    _before = len(_bg_calls)
    w._on_update_nav_clicked()
    check("19.6 downloading 态点击不再起下载", len(_bg_calls) == _before)

    # 19.7 进度回调 → 文案与 tooltip 同步
    w._on_bg_progress(100 * 1048576, 200 * 1048576)
    check("19.7a 进度文案含百分比",
          "50%" in getattr(w, "_update_progress_text", ""),
          getattr(w, "_update_progress_text", ""))
    check("19.7b tooltip 同步", "50%" in _btn.toolTip(), _btn.toolTip())

    # 19.8 ready → 图标切 ready + 保存 staging；点击弹安装确认（mock 确认框）
    w._on_bg_ready({"staging_dir": "/tmp/stg", "mode": "full",
                    "files_count": 10})
    check("19.8a ready 态且 staging 保存",
          _btn.state == "ready"
          and getattr(w, "_update_staging_dir", "") == "/tmp/stg"
          and getattr(w, "_update_ready_mode", "") == "full")
    _inst_calls = []
    _orig_confirm = w._open_install_confirm
    w._open_install_confirm = lambda: _inst_calls.append(1)
    w._on_update_nav_clicked()
    check("19.8b ready 态点击 → 安装确认被调用", len(_inst_calls) == 1)
    w._open_install_confirm = _orig_confirm

    # 19.9 error → 回 error 态可重试；点击重新下载
    w._on_bg_error("sha256 校验失败")
    check("19.9a error 态且 tooltip 带原因",
          _btn.state == "error" and "sha256" in _btn.toolTip())
    w._on_update_nav_clicked()
    check("19.9b error 态点击 → 重试下载", len(_bg_calls) == 2)

    # 19.10 对话框注入 on_download：found 态点「立即更新」→ 关对话框转后台
    from windows.update_dialog import UpdateDialog
    _dl_calls = []
    _d19 = UpdateDialog(w, _entry19, "http://h/u", _env19["app_dir"],
                        main_exe="AutoWork.exe", local_version="3.11.0",
                        on_install=lambda s, m, v="": True,
                        on_download=lambda e: _dl_calls.append(e))
    _d19._on_yes()
    check("19.10a 立即更新 → on_download 收到 entry",
          _dl_calls and _dl_calls[0] is _entry19)
    # MaskDialogBase.done() 有 100ms 淡出动画，result 在动画结束后才落值
    import time as _t19
    _t0 = _t19.time()
    while _d19.result() == 0 and _t19.time() - _t0 < 2.0:
        app.processEvents()
        _t19.sleep(0.01)
    check("19.10b 对话框已关闭（result=accepted）",
          _d19.result() == 1, str(_d19.result()))
    _d19.deleteLater()
    # 未注入 on_download 时保持原对话框内下载入口（phase 迁移到 downloading）
    _d19b = UpdateDialog(w, _entry19, "http://h/u", _env19["app_dir"],
                         main_exe="AutoWork.exe", local_version="3.11.0",
                         on_install=None)
    _orig_start_dl = _d19b._start_download
    _inner = []
    _d19b._start_download = lambda: _inner.append(1)
    _d19b._on_yes()
    check("19.10c 未注入回调时走对话框内下载（兼容旧行为）",
          _inner == [1] and _d19b._phase == "found")
    _d19b.deleteLater()

    w._start_background_download = _orig_bg
    w._set_update_nav("hidden")
    for _ in range(3):
        app.processEvents()
    check("19.11 收尾恢复并隐藏按钮无异常", True)
except Exception as e:
    import traceback as _tb
    check("19.x 导航更新按钮", False, repr(e) + "\n" + _tb.format_exc()[-600:])

print("\n" + "=" * 56)
print("冒烟结论：" + ("全部通过" if ok else "存在失败项"))
sys.stdout.flush()
os._exit(0 if ok else 2)
