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

隔离：脚本会临时改写 settings.json 的少量键（启动页、面板入口、售后开关等），
每处都在 finally 或收尾处精确还原；中途被强杀时，下次运行前先检查这些键。

退出码：0=全部通过，2=有断言失败，1=主窗口构造即失败。[FAIL] 行以 stdout 为准，
因为 offscreen 下 Qt 清理可能段错误，进程退出码并不可靠。
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

# 全脚本只建一个 QApplication：offscreen 下没有真实屏幕，几何断言只反映布局结果，
# 因此这里不承担视觉验收，只保证结构没退化。
app = QApplication(sys.argv)

# 断言累加而不遇错即停：一次运行尽量多暴露回归点，最后统一由退出码表态。
ok = True


# 自建断言而不用 assert：assert 在 -O 解释器下会被整体剥离，且失败会中断后续用例。
def check(tag, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  [PASS] " if cond else "  [FAIL] ") + tag + (" :: " + detail if detail else ""))


print("\n[1] 构造 MainWindow（FluentWindow）")
# 先构造主窗口，后面每一节都直接解引用 w：这里失败就没有继续的意义，立即退出。
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
    # 用 os._exit 而不是正常返回：offscreen 下 Qt 析构会段错误，会盖住真正的构造异常。
    os._exit(1)

# navigationInterface 由 FluentWindow 构造时创建：为空说明基类没换成 FluentWindow，
# 后续页面路由与展开/收起都会一起失效。
print("\n[2] 导航结构")
nav = w.navigationInterface
check("2.1 navigationInterface 已创建", nav is not None)
# stackedWidget 是 qfw 的页面路由容器，至少要有「工作台」一页才说明 addSubInterface 真的走过了。
check("2.2 已注册「工作台」页", w.stackedWidget.count() >= 1,
      f"stackedWidget.count()={w.stackedWidget.count()}")
# objectName 为空会让 qss 选择器与 findChild 路由失配，这是 qfw 的隐式契约。
check("2.3 homeInterface 有非空 objectName",
      bool(w.homeInterface.objectName()), repr(w.homeInterface.objectName()))

# 重构前菜单栏与状态栏被当成布局项塞进 hBoxLayout，页面区因此被压窄；
# 槽位数固定为 2 就是这条退化的哨兵，多一个说明有人又把控件加回布局。
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

# 用 parentWidget 判断归属：菜单栏必须已在统一设置页，centralwidget 与状态栏必须仍在工作台内，
# 否则设置入口重复、页面高度预算也跟着错位。
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

# 列宽预算来自业务表设计稿：导航一宽、页面就窄，表格被迫出横向滚动条，
# 这一节专门拦这种「布局改动悄悄吃掉表宽」的退化。
print("\n[5] 页面可用宽度（业务表列宽 1252~1292px）")
page_w = w.stackedWidget.currentWidget().width()
nav_w = nav.width()
print(f"     窗口 {w.width()}px - 导航 {nav_w}px = 页面 {page_w}px")
# 逐表报出差值，失败时能直接看出是哪张表放不下，而不是只知道总宽不够。
for name, cols in (("球桌管理", 1260), ("售后记录", 1292), ("跑视频", 1252), ("设备状态", 870)):
    gap = cols - page_w
    print(f"     {name:<8} {cols}px -> {'OK' if gap <= 0 else '溢出 %dpx' % gap}")
check("5.1 最宽业务表(1292)可容纳", page_w >= 1292, f"页面 {page_w}px")

# switchTo 是 qfw 对外的切页契约，业务代码大量直接调用，不能用内部 index 替代。
print("\n[6] 切页 API")
try:
    w.switchTo(w.homeInterface)
    app.processEvents()
    check("6.1 switchTo 可用", w.stackedWidget.currentWidget() is w.homeInterface)
except Exception as e:
    check("6.1 switchTo 可用", False, repr(e))

# 布局重排最容易丢 ui 上的控件属性，逐项探测可在冒烟阶段暴露，而不是等用户点到时报 AttributeError。
print("\n[7] 关键子控件仍可访问（防止布局重排后控件丢失）")
for attr in ("id_list", "log_list", "date"):
    node = getattr(w.ui, attr, None)
    check(f"7.x ui.{attr} 仍存在", node is not None)

# 一级导航是 6 个业务页 + 底部设置/关于；页数与归属一起断言，
# 防止新增页面时忘了 addSubInterface，或把设置页又塞回某个业务 Hub。
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

# 工具页从「列表 + 详情」改成横排 Pivot 后，工作区数量与控件形态都变了，
# 这里锁住 4 个工作区，避免悄悄回退到旧结构。
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
    # 用临时日志文件验证预览链路：不依赖仓库内样例，也不往工作区丢产物。
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
        # 写入超上限内容来触发截断分支，比造一个真实大文件快，也不留垃圾。
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

# 远程页二期扩到六视图（会话、访客、连接质量、frps 代理、隧道配置、RDP），
# 视图与 Pivot 项要同时存在，否则左侧导航与 stack 各说各话。
print("\n[8c] 远程页 RemoteHub（六视图 Pivot：+连接质量 +frps 代理 +RDP 2026-10-04）")
# 用 getattr 而不是属性访问：缺页时能给出可读的失败信息，而不是整脚本 AttributeError 崩掉。
rh = getattr(w, "remote_hub", None)
check("8c.1 RemoteHub 六视图工作区",
      rh is not None and rh.stack.count() == 6)
check("8c.2 Pivot 6 项", rh is not None
      and len(rh.pivot.items) == 6
      and "remoteFrpsProxiesWork" in rh.pivot.items
      and "remoteRdpWork" in rh.pivot.items)
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
    # frp 总开关（2026-09-22 需求：立即感知左侧，随 frpc 态翻转）
    _sw = rh.session_work
    # 按钮文案与真实 frpc 状态绑定，避免出现「显示在跑、实际已停」的假状态。
    check("8c.5e frp 开关存在且文案随 frpc 态",
          _sw.btn_frp_toggle.text() ==
          ("停止 frp" if _sw._mgr.is_running() else "启动 frp"))
    _sw._update_frp_toggle()   # 幂等刷新不抛
    check("8c.5f 开关刷新无异常", True)
    # frps 概览卡（2026-09-22 需求：/api/serverinfo 喂会话总览）
    # 概览卡依赖远端数据，网络不通时也必须存在（显示占位），所以只断言控件在、不断言远端值。
    _ov = getattr(rh.session_work, "_overview_card", None)
    check("8c.5b 概览卡存在且在会话总览内",
          _ov is not None and _ov.parent() is not None)
    check("8c.5c 概览五字段齐备",
          set(rh.session_work.ov_fields.keys()) == {"version", "clientCounts",
                                                    "curConns", "traffic",
                                                    "xtcp"})
    rh.session_work._update_overview()   # 无数据/异常均不得抛
    check("8c.5d 概览刷新无异常且字段有文本",
          all(num.text() for num in rh.session_work.ov_fields.values()))
    check("8c.6 访客表 6 列+定宽控件",
          rh.visitor_work.table.columnCount() == 6
          and rh.visitor_work.edit_name.width() == 260)
    # 回归（真机首崩）：非空表 _add_row 行渲染（tooltip f-string 曾用错变量 s）
    # 注：refresh() 自 2026-09-24 起只把整表重建排到下一轮事件循环（五源归并），
    # 断言前必须跑事件循环，否则读到的是上一次的行集
    # 直接拿 manager 注入一条临时访客记录，覆盖「非空表渲染」这条真机上最容易首崩的路径。
    _mgr = rh.session_work._mgr
    _tmp_sn = "__smoke_tmp_tunnel__"
    _base_rows = len(_mgr.records())
    _mgr.register_visitor(_tmp_sn)
    # 临时记录必须在 finally 移除，否则会污染后续用例，也可能让真实 frp 状态对不上。
    try:
        rh.session_work.refresh()
        for _ in range(3):
            app.processEvents()
        check("8c.7 会话总览非空表行渲染无异常",
              rh.session_work.table.rowCount() == _base_rows + 1
              and _base_rows + 1 >= 1)
        rh.visitor_work.refresh()
        for _ in range(3):
            app.processEvents()
        check("8c.8 访客表非空表行渲染无异常",
              rh.visitor_work.table.rowCount() == _base_rows + 1)
        # 回归（2026-09-22 真机 bug「断开和删除操作一样」）：断开=disabled
        # 态渲染——状态列「已断开」，操作列保留重连入口（SSH/SFTP）与「删除」；
        # 2026-10-04 起操作列不再有 RDP（visitor 只映射 22，无 3389 隧道）
        _mgr._visitors[_tmp_sn]["disabled"] = True
        rh.session_work.refresh()
        for _ in range(3):
            app.processEvents()
        _row_sn = next(i for i in range(rh.session_work.table.rowCount())
                       if (rh.session_work.table.item(i, 2)
                           and rh.session_work.table.item(i, 2).text() == _tmp_sn))
        check("8c.7b disabled 行状态列显示已断开",
              rh.session_work.table.item(_row_sn, 0).text() == "已断开")
        # 操作列不用文本列承载，而是把链接列表写进自定义角色；断言标签与顺序，
        # 因为渲染顺序直接决定用户点到的是哪一个操作。
        from core.ops_link_delegate import LINKS_ROLE
        _labels = [l[0] for l in
                   rh.session_work.table.item(_row_sn, 8).data(LINKS_ROLE)]
        check("8c.7c disabled 行链接=SSH/SFTP/删除（无 RDP、无「断开」）",
              _labels == ["SSH", "SFTP", "删除"])
        _mgr._visitors[_tmp_sn]["disabled"] = False
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
    # 开机静默预连（2026-09-23）：配置页开关 + manager 能力齐备
    check("8c.10b 静默预连开关存在",
          hasattr(rh.tunnel_conf_work, "chk_autostart")
          and rh.tunnel_conf_work.chk_autostart.text() == "开启时静默预连")
    check("8c.10c manager 提供 autostart/prewarm_async/active_count",
          all(callable(getattr(_mgr, a, None))
              for a in ("autostart", "prewarm_async", "active_count")))
    # frps 代理视图（2026-09-23；09-24 联动扩 9 列：+本地+操作）：8 类型 tab + 9 列表 + 搜索
    # 代理页是独立工作区，取属性而不是建页面：不存在说明 Hub 的组装漏了一节。
    _fp = getattr(rh, "frps_proxies_work", None)
    check("8c.10d frps 代理视图 9 列表+tab 存在",
          _fp is not None and _fp.table.columnCount() == 9
          and hasattr(_fp, "tabs") and hasattr(_fp, "edit_kw"))
    try:
        # 平滑开关是跨模块入口，任何一个 Hub 漏接，用户一拨开关就会抛 AttributeError。
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

# 各 Hub 只保留业务子页，设置统一收进 settings_hub：Hub 里再出现 settings_page
# 就等于有第二个设置入口，改一处漏一处的老问题会回来。
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
# 子页逐个切过去：Hub 的 switchTo 是 Pivot 与 stack 的粘合层，重构时最先断。
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

# 主窗口保留旧属性名做别名，老调用点不必全量改；别名一旦被删，调用方拿到的是
# AttributeError 而不是可读的迁移提示，所以必须锁住。
print("\n[10] 宿主属性别名与路由")
check("10.1 主窗口.table_page 别名",
      w.table_page is w.management_hub.table_page)
check("10.2 主窗口.device_page 别名",
      w.device_page is w.management_hub.device_page)
check("10.3 主窗口.records_page 别名(售后)",
      w.records_page is w.aftersale_hub.records_page)
try:
    # 一次调用要同时切 Hub 与其子页，少切一层就是白屏，所以校验的是最终 currentWidget。
    w.switch_to_page(w.management_hub.device_page)
    for _ in range(3):
        app.processEvents()
    ok_route = (w.stackedWidget.currentWidget() is w.management_hub
                and w.management_hub.stack.currentWidget()
                is w.management_hub.device_page)
    check("10.4 switch_to_page 路由（切 Hub + 容器内子页）", ok_route)
except Exception as e:
    check("10.4 switch_to_page 路由", False, repr(e))

# 关闭路径单独验证：worker 若还持有已销毁控件，进程退出时会段错误，
# 真机表现为「关窗口后卡几秒再崩」，日志里什么都看不到。
print("\n[11] 关闭路径（各 Hub worker detach 不抛异常）")
try:
    # 依次对三个 Hub 调 detach_workers：只测一个的话，漏改的 Hub 要到退出时才暴露。
    for name in ("management_hub", "aftersale_hub", "ledger_hub"):
        getattr(w, name).detach_workers()
    check("11.1 各 Hub detach_workers 无异常", True)
except Exception as e:
    check("11.1 各 Hub detach_workers 无异常", False, repr(e))

# 必须走真实点击：qfw 的点击信号与 switchTo 之间还有一层选中态，直接调 API 覆盖不到。
# 曾经只测 switchTo 就漏掉了「点得动但页面不切」这类回归。
print("\n[12] Pivot 点击切换修复（itemClicked bool 覆盖默认参数 bug）")
from qfluentwidgets.components.navigation.pivot import PivotItem  # noqa: E402
try:
    hub = w.aftersale_hub
    hub.switchTo(hub.entry_page)
    for _ in range(3):
        app.processEvents()
    # 只在当前 Hub 内找 PivotItem，避免把设置页的分段项也算进来。
    items = [it for it in hub.pivot.findChildren(PivotItem)]
    check("12.1 找到 PivotItem", len(items) >= 2, f"count={len(items)}")
    # 点击「记录与统计」（当前不在该页时才有效）
    target = hub.records_page
    if hub.stack.currentWidget() is not target and len(items) >= 2:
        # PivotItem 按添加顺序 = addPage 顺序，items[1] 即「记录与统计」
        item = items[1]
        item.click()
        # 点击后的切页走事件队列与动画，先泵几帧再断言，否则读到的是上一页。
        for _ in range(5):
            app.processEvents()
        check("12.2 点击 PivotItem 后 stack 真实切换",
              hub.stack.currentWidget() is target,
              f"current={type(hub.stack.currentWidget()).__name__}")
    else:
        check("12.2 点击 PivotItem 后 stack 真实切换", True, "已在该页，跳过")
except Exception as e:
    check("12.x Pivot 点击切换", False, repr(e))

# Pivot 宽度与行高是视觉规范的硬约束：宽度锁死保证靠左不拉伸，24px 是缩小 30% 后的行高。
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
        # 同一 Pivot 内所有项共享行高样式，取任意一项即可。
        fh = hub.pivot.items[next(iter(hub.pivot.items))].height()
        check(f"13.x {hub_attr} item 行高已缩小(24px)", fh == 24,
              f"item h={fh}px pivot h={h}px")
    except Exception as e:
        check(f"13.x {hub_attr} pivot 宽度", False, repr(e))

print("\n[14] 统一设置页（左标题 + 右 SegmentedWidget 分页切换）")
from qfluentwidgets.components.navigation.segmented_widget import (  # noqa: E402
    SegmentedItem)
# 设置页是唯一入口，直接取属性比遍历导航更稳，也顺便验证该属性没被改名。
sh = w.settings_hub
check("14.1 分组存在（应用配置4组含启动/远程连接/工具2组含AI/性能/数据库/面板设置3组含上传/外观）",
      all(hasattr(sh, m) for m in ("_group_appearance", "_group_perf",
          "_group_tools", "_group_paths", "_group_startup", "_group_remote",
          "_group_ai", "_add_upload_rows", "_group_log_rules",
          "_group_database", "_group_aftersale", "_group_ledger",
          "_group_management", "_group_files")))
# 2026-09-07：AI 分析组自应用配置迁入工具页（行为断言：切分段查组标题）
# 探针按控件文本查分组标题，CaptionLabel 是标题用的标签类，比读内部字段稳。
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
# 走真实保存链路而不是直接改内存，才能覆盖「落盘 + 缓存同步」这一段。
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
# 分段按 key 定位而不是硬编码下标，设置页增删分组后仍能跑。
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
# 自动刷新涉及定时器：先快照再改，测完恢复，避免冒烟把常驻刷新打开。
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
# 分段控件与内容页栈是这套设置页的骨架，缺一个整页都不可用。
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
    # 从别的页切回设置页，确保下面的宽度断言不是「本来就在」的假通过。
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
    # 直接 emit 验证转发链路：从设置页到三个 Hub 的槽任一段断裂都会抛异常。
    sh.table_smooth_changed.emit("all")
    for _ in range(3):
        app.processEvents()
    check("14.9 table_smooth_changed 信号转发无异常", True)
except Exception as e:
    check("14.x SegmentedWidget 切换", False, repr(e))

# 云母的最大风险是窗口表面被业务 qss 污染，一旦发生不可恢复；
# 因此断言窗口自身没有业务 qss，业务 qss 只挂在 stackedWidget 上。
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

# 弹出面板是把 Hub 复刻成独立窗口的旧形态：复用同一实例、关闭只隐藏，
# 这两点决定用户反复开关时不会堆出多份定时器与 worker。
print("\n[16] 弹出面板（2026-09-07 需求：每个 Hub 可复刻为独立窗口=重构前形态）")
try:
    # 入口必须在主窗口上，Hub 自己 new 窗口会绕过登记表，关闭时无法统一回收。
    check("16.1 open_hub_popout 存在", hasattr(w, "open_hub_popout"))
    check("16.2 五个 Hub 均有 btn_popout",
          all(hasattr(getattr(w, h), "btn_popout")
              for h in ("management_hub", "aftersale_hub", "ledger_hub",
                        "tool_hub", "remote_hub")))
    _t = w.open_hub_popout(w.tool_hub)   # 二期页 → 通用 HubPopoutWindow
    for _ in range(6):
        app.processEvents()
    # 弹出的是独立 Hub 实例而不是复用页内那个，否则两边共享状态会互相打断。
    check("16.3 工具弹出=HubPopoutWindow+独立hub实例",
          _t is not None and type(_t).__name__ == "HubPopoutWindow"
          and _t.hub is not w.tool_hub)
    check("16.4 嵌入 hub 隐藏二次弹出按钮", not _t.hub.btn_popout.isVisible())
    # isHidden 依赖显式 hide 时序（offscreen 下父容器未 realize 时
    # 事件同步不到）；语义应为「随按钮不可见」，与 16.4 同口径
    check("16.4b 弹出窗内提示文本随按钮隐藏",
          not _t.hub.lbl_popout_hint.isVisible())
    # 提示文本贴按钮左侧是布局约束：先切页让布局落定再量坐标，否则读到 0。
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
    # 属性代理要写透回主窗口，写不回去会让主窗口以为空闲，同一条业务被并行触发两次。
    _t._single_video_worker = "X"
    check("16.5 busy 守卫属性写透主窗口",
          getattr(w, "_single_video_worker", None) == "X")
    w._single_video_worker = None
    check("16.6 _load_settings 代理返回 dict",
          isinstance(_t._load_settings(), dict))
    # 二次弹出必须命中同一实例，否则每点一次就多一个窗口和一组 worker。
    check("16.7 二次弹出复用同实例", w.open_hub_popout(w.tool_hub) is _t)
    # 16.7b 远程面板弹出 = 左侧子导航形态（2026-09-21 需求：管理面板式）
    _r = w.open_hub_popout(w.remote_hub)
    for _ in range(6):
        app.processEvents()
    # 左侧导航以视图 objectName 为 key，用它断言六视图都挂上了。
    _rk = list(_r.navigationInterface.panel.items.keys())
    check("16.7b 远程弹出为 HubPopoutWindow",
          _r is not None and type(_r).__name__ == "HubPopoutWindow")
    check("16.7c 左侧导航含六视图且无 Hub 壳",
          set(["remoteSessionWork", "remoteVisitorWork",
               "remoteQualityWork", "remoteFrpsProxiesWork",
               "remoteTunnelConfWork", "remoteRdpWork"]) <= set(_rk)
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
    # 售后仍走旧面板类，用来验证入口是按 Hub 分派，而不是一刀切新建。
    _a = w.open_hub_popout(w.aftersale_hub)  # 旧面板类原样复活
    for _ in range(6):
        app.processEvents()
    check("16.8 售后弹出走旧 AftersalePanelWindow",
          _a is not None and type(_a).__name__ == "AftersalePanelWindow")
    # 登记表计数用固定值，能同时抓到「忘了登记」和「重复登记」。
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

# 面板入口默认弹旧面板窗口，读不到设置时才退回页内跳转；两条路径都要验证，
# 因为设置只影响入口分发，业务页面本身必须完全一致。
print("\n[17] 面板入口默认弹出（2026-09-20：跑视频/售后/球桌管理默认弹面板，设置可关）")
try:
    from PySide6.QtWidgets import QLabel as _QL
    # 17.1 设置页「启动」组存在开关行
    # 遍历标签文本而不是读配置，验证「界面上真的出现了这一行」。
    # 只读配置的话，设置项写进 json 却没渲染出来这种情况会漏掉。
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
    # 备份是必须的：这条会改用户的真实配置文件，
    # 跑完不还原就把用户的偏好悄悄改掉了。
    _orig_pop = w._load_settings().get("panel_entry_popout", True)
    w._save_settings({"panel_entry_popout": True})
    check("17.3 默认判定为弹出", w._panel_entry_popout_enabled() is True)
    # 直接读登记表比数顶层窗口可靠：隐藏的弹窗已经不算可见窗口。
    # 而关闭只隐藏不销毁，数可见窗口会以为一个都没弹。
    _p = w._hub_popouts
    w.ui.table_panel_btn.click()
    for _ in range(6):
        app.processEvents()
    check("17.4 球桌管理入口弹出旧 ManagementPanelWindow",
          # 三个入口分别映射三个旧面板类，映射错会开出错误业务的窗口。
          type(_p.get("managementHub")).__name__ == "ManagementPanelWindow")
    w.ui.btn_aftersale.click()
    for _ in range(6):
        app.processEvents()
    check("17.5 售后入口弹出旧 AftersalePanelWindow",
          type(_p.get("aftersaleHub")).__name__ == "AftersalePanelWindow")
    # 售后入口故意写成和后两条同样的形状：三个入口走同一条分发路径，
    # 少测一个就说不清「是按 Hub 分派」还是「硬编码了第一个」。
    w.ui.btn_write_table.click()
    for _ in range(6):
        app.processEvents()
    check("17.6 跑视频入口弹出旧 LedgerPanelWindow",
          type(_p.get("ledgerHub")).__name__ == "LedgerPanelWindow")
    # 17.7 关闭设置 → 回落页内跳转（当前页变为对应 Hub，登记表不新增）
    # 用登记表长度差判断「没有新弹窗」，比看当前页更严格。
    # 只看当前页的话，即使弹了窗又立刻关掉也会误判成跳转成功。
    _n_before = len(_p)
    w._save_settings({"panel_entry_popout": False})
    check("17.7a 判定为页内跳转", w._panel_entry_popout_enabled() is False)
    w.ui.table_panel_btn.click()
    # 点击后要泵事件等窗口构造，帧数不够会读到还没登记的中间态。
    # 6 次是实测下限：再少会出现偶发失败，多等只是拖慢冒烟。
    for _ in range(6):
        app.processEvents()
    check("17.7b 关闭后球桌管理走 switchTo",
          w.stackedWidget.currentWidget() is w.management_hub
          and len(_p) == _n_before)
    # 恢复用户原值并收尾
    # 用 bool() 包一层：配置里可能存成字符串 "false"，
    # 直接写回去会让开关永远开着。
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

# 自动更新要动磁盘、拉进程，全部在临时目录里跑，并对 launch_updater / QTimer.singleShot 打桩，
# 避免真的替换安装目录或提前退出事件循环。
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
    # 按钮文案是用户唯一能看到的检查结果，状态机跑完必须回到可点状态。
    _btn = w.about_page.btn_check_update
    # 用 objectName 而不是 isa 判断：同一页面可能塞多个按钮，
    # 只有名字能证明拿到的是检查更新那一个。
    check("18.2 关于页「检查更新」按钮存在",
          _btn is not None and _btn.objectName() == "aboutCheckUpdateButton")
    check("18.2b 按钮文案正确", _btn.text() == "检查更新", _btn.text())
    # 接线要逐个按钮比函数对象：只比 callable 的话，
    # 哪怕接的是别的回调这条也会过。
    check("18.2c 按钮已接线到 check_for_update",
          w.about_page.on_check_update == w.check_for_update)
    check("18.2d 按钮初始可用", _btn.isEnabled())

    # 18.3 set_checking / show_check_result 状态回写
    # 这两条守的是「检查期间不能重复点」：不禁用的话，
    # 用户连点会同时发起多个检查请求，互相覆盖结果。
    w.about_page.set_checking(True)
    check("18.3a 检查中禁用按钮", not _btn.isEnabled())
    check("18.3b 检查中文案", "正在检查" in w.about_page.update_status.text(),
          w.about_page.update_status.text())
    w.about_page.set_checking(False)
    w.about_page.show_check_result("已是最新版本 3.11.280", "success")
    check("18.3c 恢复可用并回写结果",
          _btn.isEnabled() and "3.11.280" in w.about_page.update_status.text())

    # 18.4 环境信息：dev 环境 frozen=False、main_exe 空、更新源为生产地址
    # 先取一份真实环境再局部覆盖字段，顺带验证环境探测本身不抛异常。
    # 探测抛异常会直接把后面十几节全带走，所以这条其实是环境自检。
    _env = w._update_env()
    check("18.4a dev 环境 frozen=False", _env["frozen"] is False)
    check("18.4b dev 环境 main_exe 为空", _env["main_exe"] == "")
    check("18.4c 更新源默认生产地址",
          _env["base_url"] == _upd.DEFAULT_UPDATE_BASE_URL, _env["base_url"])
    check("18.4d 本地版本非空", bool(_env["local_version"]), _env["local_version"])

    # 18.5 配置键登记到 misc 域
    # 域登记决定这些键会被哪个设置页读写，登记错的话
    # 界面上的开关改了却写不进配置文件。
    from core import app_settings as _aps2
    check("18.5 update_base_url/update_auto_check 登记 misc 域",
          _aps2.domain_of("update_base_url") == "misc"
          and _aps2.domain_of("update_auto_check") == "misc")

    # 18.6 安全边界：dev 环境必须拒绝安装（否则会把源码目录替换掉）
    # 用临时目录模拟 staging，避免碰到真实安装目录。
    # staging 也必须真建出来：不建的话 _install_update 会因为路径不存在提前拒绝，
    # 这条测的就不是 dev 守卫而是路径检查了。
    _dev_staging = os.path.join(_tf.gettempdir(), "_aw_smoke_staging")
    os.makedirs(_dev_staging, exist_ok=True)
    check("18.6 dev 环境 _install_update 返回 False",
          w._install_update(_dev_staging, "full") is False)

    # 18.7 staging 缺失也必须拒绝（即便假装是打包版）
    # 单独测这一条是因为 dev 守卫通常是 frozen 判断的短路分支，
    # 打包版下 staging 检查容易被跳过。
    _orig_frozen = getattr(sys, "frozen", False)
    # 假装打包版，验证「staging 缺失也要拒绝」；finally 必须精确还原 sys.frozen，
    # 否则后面的用例会以打包版身份运行。
    # 一旦没还原，18.9 起的所有对话框都会走打包版分支，装到真实目录上去。
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
    # 构造一份最小更新元数据，字段覆盖对话框与状态机会读到的全部键。
    # 故意带一个 _remote_version：本地版本号相同时要显示「已是最新」，
    # 没有这个字段就测不到那条分支。
    _entry = {"version": "3.11.999", "_remote_version": "3.11.999",
              "notes": "修复启动闪屏\n新增检查更新",
              "min_version": "",
              "_resolved_url": "http://h/u/packages/a.zip",
              "package": {"mode": "full", "url": "packages/a.zip",
                          "sha256": "abc", "size": 567 * 1048576}}
    # 直接调 _enter_* 驱动状态机：offscreen 下不能 exec 模态循环，但 phase 与文案照样能断言。
    # 不 exec 的话对话框不会阻塞，用例可以一路把四个 phase 全推完。
    _dlg = UpdateDialog(w, _entry, "http://h/u", _env["app_dir"],
                        main_exe="AutoWork.exe",
                        local_version=_env["local_version"],
                        on_install=lambda s, m: True)
    check("18.8a 初始 phase=found", _dlg._phase == "found")
    # 箭头是给用户一眼看清方向的：写成 3.11.273 / 3.11.999 的话，
    # 用户分不清哪个是当前版本，容易误以为在降级。
    check("18.8b 版本对比文案含箭头", "→" in _dlg.ver_label.text(),
          _dlg.ver_label.text())
    check("18.8c 包大小人类可读", "567.0 MB" in _dlg.meta_label.text(),
          _dlg.meta_label.text())
    # 大小要按 1024 换算而不是 1000：安装包实际是 567 MiB，
    # 按 1000 算会显示 594 MB，用户对不上下载管理器里的数字。
    check("18.8d 完整包标识", "完整包" in _dlg.meta_label.text())
    check("18.8e 更新说明已填入", "修复启动闪屏" in _dlg.notes_edit.toPlainText())
    # 进度区必须一开始就藏着：没下载就显示一条 0% 的进度条，
    # 用户会以为更新已经在进行，直接关掉对话框。
    check("18.8f 进度区初始隐藏", not _dlg.prog_bar.isVisibleTo(_dlg))
    check("18.8g yes=立即更新 / cancel=稍后",
          _dlg.yesButton.text() == "立即更新" and _dlg.cancelButton.text() == "稍后")

    # 下载中：进度区显示、yes 禁用、忙碌动画（total 未知）
    _dlg._enter_downloading()
    check("18.8h phase=downloading", _dlg._phase == "downloading")
    check("18.8i 进度区可见", _dlg.prog_bar.isVisibleTo(_dlg))
    # yes 禁用是防重复点击发起第二次下载，cancel 变「取消」是告诉用户
    # 现在点它会中断下载而不是关掉窗口。
    check("18.8j yes 禁用", not _dlg.yesButton.isEnabled())
    check("18.8k cancel 变为取消", _dlg.cancelButton.text() == "取消")
    # 进度用整数字节喂进去，验证 MB 换算与百分比文案，不走真实下载。
    # 喂 50/100 而不是 1/2：百分比换算漏乘 100 时 1/2 会得到 0，
    # 刚好和真值撞车，测不出来。
    _dlg._on_progress(50 * 1048576, 100 * 1048576)
    check("18.8l 进度百分比正确", _dlg.prog_bar.value() == 50,
          str(_dlg.prog_bar.value()))
    check("18.8m 进度文案含 MB 与百分比",
          "50.0 MB" in _dlg.stage_label.text()
          and "50%" in _dlg.stage_label.text(), _dlg.stage_label.text())
    _dlg._on_progress(10, 0)     # total 未知 → 忙碌动画
    # total 为 0 时进度条切成忙碌动画：有些 CDN 不发 Content-Length，
    # 这时候显示 0% 会让用户以为卡住了。
    check("18.8n total=0 走忙碌动画", _dlg.prog_bar.maximum() == 0)

    # 下载中禁止关闭（防信号打到已销毁控件）
    # 直接调 reject 而不是发关闭事件：这条测的是对话框自己的守卫，
    # 走事件路径会被模态循环拦得住，测不到真实分支。
    _dlg._phase = "downloading"
    _dlg.reject()
    check("18.8o 下载中 reject 被忽略", _dlg._phase == "downloading")

    # ready：进度满、yes=安装并重启
    _dlg._enter_ready({"staging_dir": _dev_staging, "mode": "full",
                       "files_count": 3210})
    check("18.8p phase=ready", _dlg._phase == "ready")
    # 进度必须满：停在 99% 会让用户以为还没下完，迟迟不敢点安装。
    check("18.8q 进度 100%", _dlg.prog_bar.value() == 100)
    check("18.8r yes=安装并重启", _dlg.yesButton.text() == "安装并重启")
    # 文件数是给用户的一个体感：差一个数量级说明统计口径错了。
    check("18.8s 文件数写入文案", "3210" in _dlg.stage_label.text(),
          _dlg.stage_label.text())

    # error：原因可见、yes=重试
    _dlg._enter_error("sha256 校验失败")
    check("18.8t phase=error", _dlg._phase == "error")
    # 失败原因必须原样展示：只显示「更新失败」的话用户无法判断是网络问题
    # 还是包坏了，只能重试到天荒地老。
    check("18.8u 失败原因可见", "sha256" in _dlg.notes_edit.toPlainText())
    check("18.8v yes=重试", _dlg.yesButton.text() == "重试")
    _dlg._back_to_found()
    # 回到 found 时说明区要复原成原始更新说明，否则错误信息会一直粘在框里，
    # 用户下次打开同一个对话框会看到上次的失败原因。
    check("18.8w 重试回到 found 且说明复原",
          _dlg._phase == "found" and "修复启动闪屏" in _dlg.notes_edit.toPlainText())
    _dlg.deleteLater()
    for _ in range(3):
        app.processEvents()

    # 18.9 增量模式标签（min_version 不满足时显示为完整包）
    # 增量包复用完整包字段，只改 mode 与版本号，确保两条分支读的是同一套键。
    # 用 dict 浅拷贝再改两个字段：比另写一份元数据更能说明
    # 「两条分支读的是同一套键」这个前提。
    _inc_entry = dict(_entry)
    _inc_entry["package"] = {"mode": "incremental", "url": "p.zip",
                             "sha256": "", "size": 1048576}
    _inc_entry["min_version"] = "3.11.280"
    # 同一元数据换个 mode 就应切成增量话术，用来抓「分支写反」。
    _d2 = UpdateDialog(w, _inc_entry, "http://h/u", _env["app_dir"],
                       main_exe="AutoWork.exe", local_version="3.11.999",
                       on_install=None)
    check("18.9a 满足 min_version 显示增量更新",
          # 只改版本号就让文案切成增量：这是分支判据的最小复现，
          # 改别的字段都可能绕过真正在读的那个条件。
          "增量更新" in _d2.meta_label.text(), _d2.meta_label.text())
    _d2.deleteLater()
    _d3 = UpdateDialog(w, _inc_entry, "http://h/u", _env["app_dir"],
                       main_exe="AutoWork.exe", local_version="3.11.100",
                       on_install=None)
    # 本地版本低于 min_version 时必须降级成完整包：硬装增量包
    # 会漏掉中间版本的文件，装出来是个缺文件的程序。
    check("18.9b 低于 min_version 降级显示完整包",
          "完整包" in _d3.meta_label.text(), _d3.meta_label.text())
    # on_install 未注入时安装必须报错而非静默
    # 静默是最坏情况：对话框会停在 ready，用户再点一次还是没反应，
    # 且没有任何日志能说明为什么。
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
    # 回执文件真的写到临时目录，验证的是文件生命周期而不是桩的返回值。
    # 用桩的话「pending 删了但 result.log 留着」这类问题就测不出来。
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
        # 两个临时文件都必须消失：留着 result.log 的话，
        # 下次启动会把上一次的成功又提示一遍。
        check("18.10c 消费后 pending 删除",
              not os.path.isfile(os.path.join(_rc_dir, _upd.PENDING_FLAG)))
        check("18.10d 消费后 result.log 删除",
              not os.path.isfile(os.path.join(_rc_dir, _upd.RESULT_LOG)))
        # 幂等是这条链路的关键：安装成功提示只能出现一次，
        # 重复弹会让用户以为又装了一回。
        check("18.10e 二次消费返回 None（不重复提示）",
              w.consume_update_receipt_on_startup() is None)

        # 18.11 失败路径：有 pending 无回执 → 判失败且绝不重试
        _upd.mark_update_pending(_rc_dir, {"version": "9.9.9"})
        _r2 = w.consume_update_receipt_on_startup()
        # 有 pending 却没回执意味着更新进程没跑完，此时必须判失败：
        # 判成功的话用户会拿到一个根本没装上的新版本号。
        check("18.11a 无回执判定失败", bool(_r2) and _r2["ok"] is False, str(_r2))
        check("18.11b 失败原因可读", bool(_r2.get("error")), _r2.get("error"))
        # pending 也要清掉：不清的话每次启动都重新判一次失败，
        # 用户每开一次程序就被提示一次更新失败。
        check("18.11c 失败后 pending 已清（防死循环）",
              not os.path.isfile(os.path.join(_rc_dir, _upd.PENDING_FLAG)))

        # 18.12 残留 staging 被清理
        # 残留 staging 必须清掉，否则每次启动都白扫一遍大目录。
        _st = os.path.join(_rc_dir, _upd.STAGING_DIRNAME, "sub")
        os.makedirs(_st, exist_ok=True)
        with open(os.path.join(_st, "leftover.dll"), "w") as _f:
            _f.write("x")
        _upd.mark_update_pending(_rc_dir, {"version": "1.0.0"})
        w.consume_update_receipt_on_startup()
        check("18.12 残留 staging 已清理",
              # 残留 staging 是上次失败留下的，不清会越攒越大，
              # 而且用户手动删又怕删错。
              not os.path.isdir(os.path.join(_rc_dir, _upd.STAGING_DIRNAME)))
    finally:
        _um.get_app_dir = _orig_getdir
        import shutil as _sh
        # 两个临时目录都要删：忽略错误是因为 Windows 上文件可能还被占用，
        # 清理失败不该让整条用例报错。
        _sh.rmtree(_rc_dir, ignore_errors=True)
        _sh.rmtree(_dev_staging, ignore_errors=True)

    # 18.13 updater 脚本生成安全约束（bat 必须在安装目录之外 + 占位符全解析）
    # 用带空格的假路径生成脚本，只检查文本约束，不执行。
    # 带空格是刻意的：bat 里路径不加引号是最常见的事故来源，
    # 只有带空格的路径能让它现形。
    _bat_txt = _upd.build_bat(r"C:\Fake Install\AutoWork", r"C:\stg dir",
                              "AutoWork.exe", 4321, "full")
    check("18.13a bat 占位符全解析", "__INSTALL_DIR__" not in _bat_txt
          and "__WAIT_PID__" not in _bat_txt)
    check("18.13b bat 用 System32 绝对路径（防 PATH 污染穿透等待循环）",
          "%SYS32%\\tasklist.exe" in _bat_txt and "%SYS32%\\Robocopy.exe" in _bat_txt)
    # 先剥掉 rem 与 :: 注释行再搜：脚本里本来就有注释提到 timeout，
    # 不剥的话这条会恒失败。
    check("18.13c bat 未用 timeout（改 ping，不依赖 console stdin）",
          "timeout" not in "\n".join(
              ln for ln in _bat_txt.splitlines()
              if not ln.strip().lower().startswith(("rem", "::"))).lower())
    # vbs 里的引号必须用 Chr(34) 拼：直接写 " 会被 Python 三引号吞掉，
    # 生成的脚本语法错而且在生成时看不出来。
    check("18.13d vbs 用 Chr(34) 拼引号（防 raw 三引号吞转义）",
          "Chr(34)" in _upd._VBS_TEMPLATE)
    # 排除目录漏一个，用户的数据就被 robocopy 覆盖掉：这是不可逆的损失，
    # 所以宁可多断言几次。
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
        # sys.executable 也要改：安装链路会用它推断进程路径，
        # 不改的话断言读到的是 python.exe 而不是 AutoWork.exe。
        sys.executable = os.path.join(_inst2, "AutoWork.exe")
        # 用 lambda 记录参数而不是简单返回 True：后面几条要逐个校验传参，
        # 只验证「调用了」不够。
        _upd.launch_updater = lambda *a, **kw: (_calls.append((a, kw)), True)[1]
        # 把 singleShot 打成空操作：安装成功后它负责退出事件循环，
        # 真执行会让冒烟提前结束。
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
        # 必须是恰好一次：0 次说明链路断了，2 次说明重复发起，两种都是事故。
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
            # 来源版本是回滚提示的唯一依据：缺了它，更新失败时用户不知道该退回哪一版，
            # 只能手动翻安装包。
            check("18.14g pending_info 含来源版本（失败时可提示回退到哪个版本）",
                  bool(_pi.get("from_version")), str(_pi))
        check("18.14h 安装发起后对话框 accept 关闭",
              not _d4.isVisible() or _d4._phase == "ready")
        _d4.deleteLater()
        # 18.15 launch_updater 抛异常 → 必须报错且对话框进入 error（不静默退出）
        # 清空调用记录：上一条的记录不能影响这条的计数断言。
        _calls.clear()

        # 模拟 wscript 不可用，验证异常路径不会把程序留在半安装状态。
        # wscript 在某些精简系统上真的不存在，这条不是假想故障。
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
        # 删的是 inst2 的父目录：inst2 下面还有一层 AutoWork，
        # 直接删 inst2 会把这层空壳留在临时目录里。
        import shutil as _sh2
        _sh2.rmtree(os.path.dirname(_inst2), ignore_errors=True)
        _sh2.rmtree(_stg2, ignore_errors=True)
        for _ in range(3):
            app.processEvents()

    check("18.13f 收尾关闭更新对话框无异常", True)
except Exception as e:
    import traceback as _tb
    # 只带堆栈尾部 600 字符：更新这条链路的异常往往裹着一长串 Qt 内部帧，
    # 全打会把真正的原因冲出屏幕。
    check("18.x 自动更新", False, repr(e) + "\n" + _tb.format_exc()[-600:])

# ==================== 19. 导航更新按钮（设置上方，下载进度/就绪提示） ====================
# 导航更新按钮是「静默检查」的唯一可见出口：停在 found 却不弹窗，用户就永远不知道有新版本。
try:
    print("\n--- [19] 导航更新按钮 ---")
    _btn = getattr(w, "_update_nav_btn", None)
    check("19.1 导航更新按钮已创建", _btn is not None)
    # 默认必须隐藏：没有新版本时挂个更新图标在导航栏，用户会以为出问题了。
    check("19.2 初始隐藏（无更新不打扰）",
          _btn is not None and not _btn.isVisibleTo(w))

    # 19.3 状态机：found → downloading → ready → hidden
    # 按钮状态由主窗口统一驱动，这里只喂状态，不重复测状态机。
    # 四个状态连起来推一遍，是因为这条链路是「发现→下载→就绪→收起」的完整生命周期，
    # 中间任何一态卡住，用户就再也走不到安装那一步。
    w._set_update_nav("found", "发现新版本 3.11.999，点击下载")
    # 这里只喂状态不测状态机：状态机本身在 18.8 已经逐 phase 验过，
    # 重复一遍只会让失败原因变模糊。
    check("19.3a found 态可见且 tooltip 正确",
          _btn.isVisibleTo(w) and _btn.state == "found"
          # tooltip 里必须带版本号：只写「发现新版本」的话，
          # 用户不知道该不该为这一版专门重启一次。
          and "3.11.999" in _btn.toolTip())
    w._set_update_nav("downloading", "正在下载更新 42%")
    check("19.3b downloading 态图标切换（DOWNLOAD）", _btn.state == "downloading")
    w._set_update_nav("ready", "已就绪，点击安装")
    check("19.3c ready 态", _btn.state == "ready")
    w._set_update_nav("hidden")
    # 四个状态连起来推一遍：这条链路是「发现→下载→就绪→收起」的完整生命周期，
    # 中间任何一态卡住，用户就再也走不到安装那一步。
    check("19.3d hidden 态重新隐藏",
          not _btn.isVisibleTo(w) and _btn.state == "hidden")

    # 19.4 静默检查发现新版 → 挂 found 态 + 弹更新对话框（需求：打开程序弹出提示）
    _entry19 = {"version": "3.11.999", "_remote_version": "3.11.999",
                "notes": "x", "package": {"mode": "full", "url": "u",
                                          "sha256": "s", "size": 1}}
    _env19 = w._update_env()
    # 用一个只收参数的 lambda 当桩：真弹窗会启动模态循环，冒烟跑完这一节
    # 才会继续，而后面十几节全在等这个循环退出。
    _dlg_calls = []
    _orig_open_dlg = w._open_update_dialog
    w._open_update_dialog = lambda e, env: _dlg_calls.append(e)
    try:
        # 直接调回调而不是真跑检查，避免依赖网络与更新服务器。
        # 冒烟跑在内网和离线机器上都得过，所以不能有真实请求。
        w._on_update_found(_entry19, True, _env19)
    finally:
        #  finally 里还原：这条桩如果漏还原，后面的用例点按钮就再也不会真弹窗。
        w._open_update_dialog = _orig_open_dlg
    check("19.4a silent 发现新版 → 导航挂 found", _btn.state == "found")
    # 需求是打开程序就提示，所以 found 必须同时弹对话框：只挂图标的话，
    # 用户永远不知道有新版本，除非正好瞄到导航栏。
    check("19.4a2 silent 发现新版 → 弹出更新对话框",
          _dlg_calls and _dlg_calls[0] is _entry19)
    check("19.4b entry/env 已保存供图标点击使用",
          # 必须存的是同一个对象而不是副本：点图标时要用这份 env 里的 app_dir，
          # 存副本的话安装路径会指向别处。
          getattr(w, "_update_pending_entry", None) is _entry19
          and getattr(w, "_update_pending_env", None) is _env19)

    # 19.5 点击图标（found 态）→ 走后台下载（mock worker，不真起线程）
    _bg_calls = []
    _orig_bg = w._start_background_download

    # 桩掉后台下载，只验证点击后的分发与界面反应。
    def _fake_bg(entry, env):
        _bg_calls.append((entry, env))
        w._set_update_nav("downloading", "正在下载更新…")

    w._start_background_download = _fake_bg
    w._on_update_nav_clicked()
    # 桩只记录参数并改状态，不真起线程：后台下载在冒烟里跑完要好几分钟，
    # 而且会往磁盘写真实安装包。
    check("19.5a found 态点击 → 触发后台下载",
          len(_bg_calls) == 1 and _bg_calls[0][0] is _entry19)
    check("19.5b 点击后进入 downloading 态", _btn.state == "downloading")

    # 19.6 下载中点击 → 提示进度（不重复起下载）
    w._update_progress_text = "正在下载更新 50%（100.0 MB/200.1 MB）"
    _before = len(_bg_calls)
    w._on_update_nav_clicked()
    # 下载中再点必须什么都不做：重复起下载会写坏 staging 目录，
    # 而且两份下载互相覆盖会让校验永远失败。
    check("19.6 downloading 态点击不再起下载", len(_bg_calls) == _before)

    # 19.7 进度回调 → 文案与 tooltip 同步
    # 文案与 tooltip 都要更新：tooltip 是用户 hover 时唯一能看到进度的地方，
    # 不同步的话用户会以为下载停了。
    w._on_bg_progress(100 * 1048576, 200 * 1048576)
    # 文案与 tooltip 都要更新：tooltip 是用户hover 时唯一能看到进度的地方，
    # 不同步的话用户会以为下载停了。
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
    # 换掉安装确认，避免冒烟真的去起安装流程。
    # 安装一旦真跑起来会替换当前进程，冒烟后面十几节全部不会执行。
    _orig_confirm = w._open_install_confirm
    w._open_install_confirm = lambda: _inst_calls.append(1)
    w._on_update_nav_clicked()
    check("19.8b ready 态点击 → 安装确认被调用", len(_inst_calls) == 1)
    # 验证完立刻还原：这个桩如果留着，19.9 的点击就会走进假的确认框。
    w._open_install_confirm = _orig_confirm

    # 19.9 error → 回 error 态可重试；点击重新下载
    w._on_bg_error("sha256 校验失败")
    # tooltip 里必须带失败原因：用户点之前就知道是校验失败还是网络超时，
    # 决定是直接重试还是先看看网络。
    check("19.9a error 态且 tooltip 带原因",
          _btn.state == "error" and "sha256" in _btn.toolTip())
    w._on_update_nav_clicked()
    # 第二次计数变成 2：证明 error 态的点击真的重新走了下载分支，
    # 而不是和 downloading 一样被忽略。
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

# os._exit 跳过 Qt 析构，规避 offscreen 下已知的清理段错误；退出码 0/2 供 CI 判定，
# 但真正的结论看上面的 [FAIL] 计数，段错误可能发生在 stdout flush 之后。
print("\n" + "=" * 56)
print("冒烟结论：" + ("全部通过" if ok else "存在失败项"))
sys.stdout.flush()
os._exit(0 if ok else 2)
