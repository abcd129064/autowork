# -*- coding: utf-8 -*-
"""MainWindow 远程连接 Mixin：P2P 面板、XTCP/TCP 连接、frpc 管理、SFTP/SSH/RDP 窗口启动"""
from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Qt, QStringListModel, QEvent
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QFormLayout, QCompleter
from qfluentwidgets import (EditableComboBox, FluentIcon,
                            PushButton as FluentPushButton)
# CompleterMenu / MenuAnimationManager 未在 qfluentwidgets 顶层导出
# （同 core/perf.py 引用 menu 内部类的方式）
from qfluentwidgets.components.widgets.line_edit import CompleterMenu
from qfluentwidgets.components.widgets.menu import (
    MenuAnimationManager, MenuAnimationType)

if TYPE_CHECKING:
    from autowork_with_table import Ui_MainWindow

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from workers.network_workers import TCPWorker
from windows.remote_session.sftp_window import SFTPPanel
from windows.remote_session.ssh_terminal import SSHTerminalPanel
from windows.remote_session.rdp_window import RDPPanel
from windows.remote_session.remote_session_window import RemoteSessionWindow
from p2p import generate_random_port
from core.frp_remote import get_session_manager, SOURCE_MANUAL, SOURCE_TABLE
from database import table_db


class TablePickerMenu(CompleterMenu):
    """球桌候选弹层：已展示时原位刷新，不重跑淡入动画

    逐键过滤会反复 popup()；库内 FADE_IN_* 动画每次都把 windowOpacity 从 0
    动画到 1 并位移 8px，表现为弹层随输入闪烁跳动（「弹层错位」观感）。
    已展示且锚点未变时直接落位到同一终点，只换内容。
    """

    def exec(self, pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        if not self.isVisible():
            return super().exec(pos, ani, aniType)
        try:
            manager = MenuAnimationManager.make(self, aniType)
            old = getattr(self, "aniManager", None)
            for a in (getattr(old, "aniGroup", None), getattr(old, "ani", None)):
                if a is not None:
                    a.stop()          # 停掉上一次未跑完的动画，避免抢位置
            self.aniManager = manager
            self.clearMask()
            self.setWindowOpacity(1)
            self.move(manager._endPosition(pos))
            self.show()
        except Exception:             # 库内实现变动时退回原生弹出
            return super().exec(pos, ani, aniType)


class TablePickerComboBox(EditableComboBox):
    """球桌库选择下拉：EditableComboBox + 补全弹层承载大候选集

    交互与售后面板 form.py 的「问题类型」type_combo 一致：点箭头展开选择，
    输入即搜索。差别只在候选规模——球桌候选有数百条，库内置下拉（ComboBoxMenu）
    逐条 addAction，且每条都要 _hasItemIcon/adjustSize 全量重算尺寸（O(n²)，
    550 条实测约 350ms 卡顿，菜单宽度还会被最长候选撑到 550px 以上）。
    故下拉箭头改走 QCompleter 的补全弹层（CompleterMenu）：bulk setItems
    （550 条约 5ms）、宽度可控、且不取焦点（键盘始终留在输入框，不会抢焦点）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.popupMinWidth = 0     # 弹层最小宽度(px)，0 = 跟随输入框宽度
        self.aboutToPopup = None   # callable：弹层展示前的候选刷新钩子
        # 是否允许 textEdited 排队的延时补全弹层展示（见 _showCompleterMenu）
        self._popupAllowed = False
        self.textEdited.connect(self._onPickerTextEdited)

    def _onPickerTextEdited(self, _text: str):
        """用户真实编辑：重新允许随后的延时补全展示"""
        self._popupAllowed = True

    # ---------------------------------------------------------- 弹层控制

    def showPopup(self):
        """展示补全弹层：内容为当前过滤结果，空输入即全部候选"""
        if callable(self.aboutToPopup):
            self.aboutToPopup()
        completer = self.completer()
        if completer is None:
            return
        if self._completerMenu is None:
            self.setCompleterMenu(TablePickerMenu(self))
            if self.popupMinWidth > 0:
                view = self._completerMenu.view
                # 弹层项 sizeHint 宽度固定为 1，实际宽度完全由 minimumWidth 决定：
                # 一次设死就不会随候选长短在逐键过滤时抢宽度
                view.setMinimumWidth(self.popupMinWidth)
                # 库默认 ElideNone：弹层宽度受限时超长候选被硬切，改右侧省略号
                view.setTextElideMode(Qt.TextElideMode.ElideRight)
        menu = self._completerMenu
        completer.setCompletionPrefix(self.text().strip())
        changed = menu.setCompletion(
            completer.completionModel(), completer.completionColumn())
        # setMaxVisibleItems 会全量重算尺寸，值未变时不重复调用
        if menu.view.maxVisibleItems() != completer.maxVisibleItems():
            menu.setMaxVisibleItems(completer.maxVisibleItems())
        # items 未变且弹层已展示时不重复 popup（避免每次按键重跑弹出动画）
        if changed or not menu.isVisible():
            menu.popup()

    def hidePopup(self) -> bool:
        """收起补全弹层（返回是否确实收起了一个可见弹层）"""
        # 收起即失效：此前 textEdited 排队的延时展示不再执行，
        # 否则 Esc / 无命中收起后弹层会「自己弹回来」（候选残留）
        self._popupAllowed = False
        menu = self._completerMenu
        if menu is None:
            return False
        try:
            if not menu.isVisible():
                return False
            menu.close()
        except (RuntimeError, OSError):   # C++ 对象可能已销毁
            return False
        return True

    def isPopupVisible(self) -> bool:
        """补全弹层是否正在展示"""
        menu = self._completerMenu
        if menu is None:
            return False
        try:
            return menu.isVisible()
        except (RuntimeError, OSError):
            return False

    def popupCurrentText(self):
        """弹层高亮行文本（↑↓ 导航 / 悬停后的明确选择）；未展示或无高亮返回 None

        弹层默认无高亮行（库内 setItems 只 bulk addItems，不选首行），
        这正是「回车不误选第一条模糊命中」的前提；高亮行只能由用户主动
        导航产生（弹层持有 Qt.Popup 键盘抓取，↑↓ 由库内 CompleterMenu
        直接转给列表，本类不插手以免一键跳两行）。
        """
        menu = self._completerMenu
        if menu is None:
            return None
        try:
            if not menu.isVisible():
                return None
            row = menu.view.currentRow()
            item = menu.view.item(row) if row >= 0 else None
        except (RuntimeError, OSError):
            return None
        return item.text() if item is not None else None

    # -------------------------------------------------- 覆盖库内下拉行为

    def _showComboMenu(self):
        """下拉箭头：切换补全弹层（不走库内 ComboBoxMenu，原因见类注释）"""
        if not self.hidePopup():
            self.showPopup()

    def _showCompleterMenu(self):
        """输入触发的补全弹层（库内 textEdited 钩子延时 50ms 调用）

        弹层可见时按键由库内 CompleterMenu 转发回本输入框，焦点判定不可靠，
        故只按「空输入」与「是否被显式收起」两个条件忽略延时调用。
        """
        if not self.text().strip() or not self._popupAllowed:
            return
        self.showPopup()


class RemoteMixin:
    """远程连接相关方法（Mixin，需与 FluentWindowBase 组合使用）"""

    # 类型声明：由其他 Mixin / 主类提供，仅供 IDE 静态分析
    if TYPE_CHECKING:
        ui: Ui_MainWindow
        _p2p_visitors: list
        _p2p_current_index: int
        _tcp_worker: TCPWorker | None
        _remote_session_window: RemoteSessionWindow | None

        def _load_settings(self) -> dict: ...
        def _save_settings(self, data: dict) -> None: ...
        def _get_app_dir(self) -> str: ...
        def _append_log(self, msg: str) -> None: ...
        def _show_info_bar(self, message, message_type="info", title=None,
                           duration=2500) -> None: ...
        def _on_p2p_search_changed(self, text: str) -> None: ...
        def _resolve_remote_target(self, tag: str, feature_name: str) -> tuple[str, int, str, str, str] | None: ...

    # 球桌库选择：弹层可见条数 / 候选与查询上限 / 宽度(px) / 候选保鲜间隔(s)
    _TABLE_PICKER_VISIBLE_ROWS = 10
    _TABLE_PICKER_MAX_ITEMS = 1200      # 候选上限（现库约 550 台带 snk 的球桌）
    _TABLE_PICKER_QUERY_LIMIT = 3000    # 单次查库行数上限
    _TABLE_PICKER_WIDTH = 270           # 控件宽度上限 ≈ p2p_panel 内容宽度
    _TABLE_PICKER_POPUP_WIDTH = 320     # 弹层宽度：完整显示「球桌名 / 球房名 (snk)」
    _TABLE_PICKER_REFRESH_SEC = 600     # 超时后展开弹层时重载一次候选

    def _init_p2p_panel(self):
        """初始化远程面板状态，从统一会话中心恢复手工 visitor 列表"""
        settings = self._load_settings()
        ssh_user = settings.get("ssh_user", "")
        ssh_pass = settings.get("ssh_pass", "")
        if ssh_user:
            self.ui.p2p_ssh_user.setText(ssh_user)
        if ssh_pass:
            self.ui.p2p_ssh_pass.setText(ssh_pass)
        # 接入统一远程会话中心：frpc 日志转发到日志区，状态变化刷新按钮；
        # 「删除 snk」彻底移除 visitor 时同步清理本地列表，避免残留
        self._session_mgr = get_session_manager()
        self._session_mgr.log_message.connect(self._append_log)
        self._session_mgr.frpc_state_changed.connect(
            lambda _running: self._update_p2p_buttons())
        self._session_mgr.visitor_removed.connect(self._on_visitor_removed_external)
        self._load_visitors_from_manager()
        self._refresh_p2p_list()
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        self._init_table_picker()
        # 布局调整（Task #46）：按钮归位 + 服务器搜索框默认隐藏(Ctrl+F 唤起)
        self._rearrange_p2p_layout()
        self._init_p2p_search_shortcuts()
        # 「当前隧道」入口：挂在远程面板标题下方，展示全局活跃隧道
        self._tunnel_panel_window = None
        self._p2p_tunnels_btn = FluentPushButton(
            FluentIcon.LINK, "当前隧道", self.ui.p2p_panel)
        self._p2p_tunnels_btn.setObjectName(u"p2p_tunnels_btn")
        self._p2p_tunnels_btn.clicked.connect(self._open_tunnel_panel)
        self.ui.p2p_panel.layout().insertWidget(1, self._p2p_tunnels_btn)
        self._update_p2p_visibility()
        self._update_p2p_buttons()

    def _rearrange_p2p_layout(self):
        """布局调整：添加/删除按钮移到 secretKey 下方，连接/断开按钮移到密码框下方

        控件均在 Ui 构建阶段创建且信号已绑定，此处仅做运行时布局重排，
        不改变功能与信号连接。添加/删除按钮保留常显（TCP 模式复用为
        保存/删除服务器），故不加入 p2p_xtcp_widgets 显隐列表。
        """
        main_lay = self.ui.p2p_panel.layout()
        # 1) 从面板主布局取出「添加/删除」所在行布局，改挂到 XTCP 表单 secretKey 下方
        for i in range(main_lay.count()):
            sub = main_lay.itemAt(i).layout()
            if sub is not None and sub.indexOf(self.ui.p2p_add_btn) >= 0:
                main_lay.removeItem(sub)
                self.ui.p2p_xtcp_form.addRow("", sub)
                break
        # 2) 连接/断开按钮容器从主布局移到「权限与配置」密码框下方（跨列行）
        main_lay.removeWidget(self.ui.p2p_conn_widget)
        self.ui.p2p_ssh_form.addRow(self.ui.p2p_conn_widget)

    def _init_p2p_search_shortcuts(self):
        """服务器搜索框默认隐藏：Ctrl+F 显示并聚焦，Esc/清空后隐藏

        快捷键以 WidgetWithChildrenShortcut 上下文挂在远程面板上，仅焦点在
        面板区域内生效，不与主窗口全局 Ctrl+F/Esc 冲突。
        """
        self.ui.p2p_search.setVisible(False)
        self._p2p_search_fresh = False  # 刚被快捷键唤起（尚未输入）时不因空文本隐藏
        self.ui.p2p_search.textChanged.connect(self._on_p2p_search_edited)
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        self._p2p_search_sc = QShortcut(QKeySequence('Ctrl+F'), self.ui.p2p_panel)
        self._p2p_search_sc.setContext(ctx)
        self._p2p_search_sc.activated.connect(self._toggle_p2p_search)
        self._p2p_search_esc = QShortcut(QKeySequence('Escape'), self.ui.p2p_panel)
        self._p2p_search_esc.setContext(ctx)
        self._p2p_search_esc.setEnabled(False)  # 仅搜索框可见时拦截 Esc
        self._p2p_search_esc.activated.connect(self._hide_p2p_search)

    def _toggle_p2p_search(self):
        """Ctrl+F：切换服务器搜索框显示/隐藏（参考主窗口搜索快捷键模式）"""
        if self.ui.p2p_search.isVisible():
            self._hide_p2p_search()
        else:
            self._p2p_search_fresh = True
            self.ui.p2p_search.show()
            self.ui.p2p_search.setFocus()
            self.ui.p2p_search.selectAll()
            self._p2p_search_esc.setEnabled(True)

    def _hide_p2p_search(self):
        """隐藏服务器搜索框：清空内容并恢复完整列表（过滤逻辑不变）"""
        self._p2p_search_fresh = False
        self.ui.p2p_search.blockSignals(True)
        self.ui.p2p_search.clear()
        self.ui.p2p_search.blockSignals(False)
        self.ui.p2p_search.hide()
        self._p2p_search_esc.setEnabled(False)
        self._on_p2p_search_changed("")

    def _on_p2p_search_edited(self, text):
        """搜索框清空后自动隐藏（刚唤起尚未输入时除外）"""
        if text:
            self._p2p_search_fresh = False
        elif not self._p2p_search_fresh and self.ui.p2p_search.isVisible():
            self.ui.p2p_search.hide()
            self._p2p_search_esc.setEnabled(False)
            self._on_p2p_search_changed("")

    def _load_visitors_from_manager(self):
        """从统一会话中心恢复 visitor 列表（manager 启动时已解析
        frpc_xtcp_panel.toml，含手工/球桌库来源与关联球桌元数据）"""
        restored = self._session_mgr.manual_visitors()
        self._p2p_visitors.extend(restored)
        if restored:
            self._append_log(f"[远程] 从会话中心恢复了 {len(restored)} 个 visitor")

    def _on_visitor_removed_external(self, server_name: str):
        """隧道面板「删除 snk」彻底移除 visitor 后，立即同步清理本地列表并刷新，
        避免已删除的 snk 残留在远程面板 visitor 列表中。

        仅 delete_visitor 发此信号；主面板自己的断开/删除流程不经此路径，
        断开（非删除）后列表保留可重连的语义不受影响。
        """
        name = str(server_name or "").strip()
        if not name:
            return
        hit = [v for v in self._p2p_visitors if v.get("serverName", "") == name]
        if not hit:
            return
        for v in hit:
            self._p2p_visitors.remove(v)
        if self._p2p_current_index >= len(self._p2p_visitors):
            self._p2p_current_index = len(self._p2p_visitors) - 1
        self._append_log(f"[远程] 列表已同步移除: {name}")
        # TCP 模式下列表展示的是保存的服务器，无需刷新显示
        if self.ui.p2p_mode_combo.currentText() != "XTCP":
            return
        self._refresh_p2p_list()

    def _open_tunnel_panel(self):
        """打开全局「当前隧道」面板（单例复用，展示所有入口的活跃隧道）"""
        from windows.remote_session.window import TunnelPanelWindow
        win = self._tunnel_panel_window
        if win is not None:
            try:
                win.isVisible()  # 探测 C++ 对象是否已销毁
            except RuntimeError:
                win = None
        if win is None:
            win = TunnelPanelWindow()
            win.destroyed.connect(lambda: setattr(self, "_tunnel_panel_window", None))
            self._tunnel_panel_window = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _get_new_random_port(self):
        """生成不冲突的随机端口（排除会话中心与本地 visitor 列表已用端口）"""
        # 两处已用端口都要排除：注册表与本地列表可能短暂不同步，
        # 只查一边会把另一边的已用端口分配出去造成冲突
        taken = set(self._session_mgr.used_ports())
        taken |= {int(v.get("bindPort") or 0) for v in self._p2p_visitors}
        return generate_random_port(exclude_ports=taken)

    def _on_p2p_toggled(self, checked):
        """切换远程面板显示/隐藏"""
        self.ui.p2p_panel.setVisible(checked)

    def _on_p2p_add(self):
        """添加按钮：XTCP 模式添加 visitor，TCP 模式保存当前服务器(ip:port)"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_add()
            return
        server_name = self.ui.p2p_form_server.text().strip()
        if not server_name:
            self._append_log("[远程] 请填写 serverName")
            return
        port = self.ui.p2p_form_port.value()
        taken = set(self._session_mgr.used_ports())
        taken |= {v["bindPort"] for v in self._p2p_visitors}
        if 0 <= self._p2p_current_index < len(self._p2p_visitors):
            # 编辑已有项时先把它自己的端口从占用集合里剔除，
            # 否则校验会自己和自己冲突，原端口永远保存不了
            taken.discard(self._p2p_visitors[self._p2p_current_index].get("bindPort"))
        if port in taken:
            self._append_log(f"[远程] 端口 {port} 已被其他隧道使用，请更换端口")
            return
        visitor = {
            "serverName": server_name,
            "bindPort": port,
            "secretKey": self.ui.p2p_form_key.text().strip() or "abc123",
            "source": SOURCE_MANUAL,
        }
        self._p2p_visitors.append(visitor)
        self.ui.p2p_visitor_list.blockSignals(True)
        self._refresh_p2p_list()
        self.ui.p2p_visitor_list.blockSignals(False)
        self._p2p_current_index = len(self._p2p_visitors) - 1
        self.ui.p2p_visitor_list.setCurrentRow(self._p2p_current_index)
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        # 添加即注册进会话中心（不启动 frpc）：「当前隧道」面板立即可见该 snk，
        # 注册表与持久化文件同步对齐；隧道生效仍需断开重连（apply 重启 frpc）
        self._register_visitor_to_manager(visitor)
        if self._session_mgr.is_running():
            self._show_info_bar(
                f"隧道「{server_name}」已添加，请断开后重新连接以生效",
                "warning", title="提示", duration=4000)

    def _on_p2p_delete(self):
        """删除按钮：XTCP 模式删除 visitor，TCP 模式删除选中的服务器"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_delete()
            return
        row = self.ui.p2p_visitor_list.currentRow()
        if 0 <= row < len(self._p2p_visitors):
            removed = self._p2p_visitors.pop(row)
            self._p2p_current_index = -1
            self._refresh_p2p_list()
            # 同步会话中心（双向）：运行中先关该端口会话再 apply 释放端口；
            # 未运行仅移除并落盘——添加即注册后，不同步会残留在
            # 「当前隧道」面板与 frpc_xtcp_panel.toml 恢复文件中
            name = removed.get("serverName", "")
            if name and self._session_mgr.delete_visitor(name) == "error":
                self._append_log(f"[远程] 应用变更失败: 删除 {name}")

    def _on_p2p_visitor_selected(self, row):
        """列表选择：XTCP 模式加载 visitor 到表单，TCP 模式填充 host/port"""
        if self.ui.p2p_mode_combo.currentText() == "TCP":
            self._on_tcp_server_selected(row)
            return
        self._save_current_form()
        if 0 <= row < len(self._p2p_visitors):
            self._p2p_current_index = row
            v = self._p2p_visitors[row]
            self.ui.p2p_form_server.setText(v.get("serverName", ""))
            self.ui.p2p_form_port.setValue(v.get("bindPort", 10000))
            self.ui.p2p_form_key.setText(v.get("secretKey", "abc123"))
        else:
            self._p2p_current_index = -1

    def _save_current_form(self):
        """将当前表单内容保存回 visitor 数据"""
        if 0 <= self._p2p_current_index < len(self._p2p_visitors):
            v = self._p2p_visitors[self._p2p_current_index]
            v["serverName"] = self.ui.p2p_form_server.text()
            v["bindPort"] = self.ui.p2p_form_port.value()
            v["secretKey"] = self.ui.p2p_form_key.text()
            item = self.ui.p2p_visitor_list.item(self._p2p_current_index)
            if item:
                item.setText(v["serverName"])

    def _refresh_p2p_list(self):
        """刷新 visitor 列表显示"""
        self.ui.p2p_visitor_list.clear()
        for v in self._p2p_visitors:
            self.ui.p2p_visitor_list.addItem(v.get("serverName", ""))
        # 重新应用搜索过滤
        self._on_p2p_search_changed(self.ui.p2p_search.text())

    # 从球桌库选择

    def _init_table_picker(self):
        """在 XTCP visitor 表单顶部插入「从球桌库选择」可编辑下拉

        与售后面板 form.py 的「问题类型」type_combo 同款交互：点箭头即展开
        全部球桌候选，输入即按球桌名 / 球房名 / snk_code 模糊过滤；选中后
        自动回填 serverName(snk)/secretKey 并走「添加」流程注册为 visitor。
        直连入口由面板下方「功能」区的文件管理/SSH 终端/远程桌面承担，
        此处不再重复提供快捷按钮。
        """
        self._p2p_selected_table = None   # 当前选中的球桌行 dict
        self._p2p_picking = False         # 选中回填期间抑制过滤与弹层
        self._p2p_table_entries = []      # [(候选显示文本, 球桌行)]，已排序去重
        self._p2p_table_map = {}          # 候选显示文本 -> 球桌行 dict
        self._p2p_table_matches = []      # 当前过滤命中的球桌行（回车判定用）
        self._p2p_table_loaded_at = 0.0   # 候选载入时间戳（保鲜判定）

        combo = TablePickerComboBox(self.ui.p2p_panel)
        self._p2p_table_search = combo
        combo.setObjectName(u"p2p_table_search")
        combo.setPlaceholderText("从球桌库选择")
        combo.setClearButtonEnabled(True)
        combo.setToolTip("选择球桌后自动回填 snk 并加入下方列表；\n"
                         "可输入球桌名 / 球房名 / snk 模糊搜索")
        # 宽度固定：ComboBoxBase.setText 会 adjustSize()，长球桌名/球房名/snk
        # 回填时会把控件拉宽进而撑破 290px 面板；setFixedWidth 下只省略号截断，
        # 且宽度恒定——弹层锚定控件位置，控件宽度不再因文本变化而抖动
        panel_max = self.ui.p2p_panel.maximumWidth()
        inner = panel_max - 20 if 0 < panel_max < 4096 \
            else self._TABLE_PICKER_WIDTH
        combo.setFixedWidth(max(150, min(inner, self._TABLE_PICKER_WIDTH)))
        combo.popupMinWidth = self._TABLE_PICKER_POPUP_WIDTH
        combo.aboutToPopup = self._ensure_table_candidates

        # 弹层内容 = _p2p_table_model，过滤与排序全由 _apply_table_filter 掌控：
        # 球桌标签前缀是球桌名而非关键字，交给 QCompleter 自己过滤会把 snk /
        # 球房命中全筛没，故用 UnfilteredPopupCompletion 全量展示 model
        self._p2p_table_model = QStringListModel([], combo)
        completer = QCompleter(self._p2p_table_model, combo)
        completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(self._TABLE_PICKER_VISIBLE_ROWS)
        completer.activated[str].connect(self._on_table_match_activated)
        combo.setCompleter(completer)
        self._p2p_table_completer = completer

        # 插入 XTCP 表单首行（跨整行）：随模式切换的显隐复用既有
        # p2p_xtcp_widgets 遍历，无需改动 _update_p2p_visibility
        # 「球桌库」标题暂时隐藏腾出列表空间（Task #51）
        self.ui.p2p_xtcp_form.insertRow(0, combo)
        self.ui.p2p_xtcp_widgets.append(combo)

        combo.textChanged.connect(self._on_table_filter_changed)
        # 回车/Esc 统一由本类 eventFilter 截获（须在库内行为之前）
        combo.installEventFilter(self)
        # 候选异步载入：不阻塞主窗口构建（现库 550+ 台带 snk 的球桌）
        QTimer.singleShot(0, self._load_table_candidates)

    def _load_table_candidates(self):
        """载入全部可用球桌候选（默认展示，不需先输入关键字）"""
        rows = self._fetch_snk_table_rows()
        entries, table_map = [], {}
        for r in rows:
            label = self._format_table_label(r)
            if label in table_map:      # 同名同球房同 snk 的重复行只留一条
                continue
            table_map[label] = r
            entries.append((label, r))
        self._p2p_table_entries = entries
        self._p2p_table_map = table_map
        self._p2p_table_loaded_at = time.time()

        combo = self._p2p_table_search
        typed = combo.text()
        # items 承载全量候选，EditableComboBox 的 findText/currentIndex 语义
        # 与「问题类型」下拉一致；重建期间屏蔽信号：addItem 首项会写入文本、
        # setCurrentIndex(-1) 又清一次，不屏蔽会白跑两轮过滤
        combo.blockSignals(True)
        try:
            combo.clear()                # ComboBoxBase.clear：清 items 与文本
            combo.addItems([lb for lb, _ in entries])
            combo.setCurrentIndex(-1)    # 复位 addItem 首项写入的文本
            if typed:
                combo.setText(typed)
        finally:
            combo.blockSignals(False)
        self._apply_table_filter(typed)
        self._append_log(f"[球桌库] 已加载 {len(entries)} 台可选球桌候选")

    def _ensure_table_candidates(self):
        """展开弹层前的候选保鲜：首次未载入完成或超时才重查库"""
        if self._p2p_table_entries and \
                time.time() - self._p2p_table_loaded_at \
                < self._TABLE_PICKER_REFRESH_SEC:
            return
        self._load_table_candidates()

    def _fetch_snk_table_rows(self):
        """查全部 snk_code 非空的球桌（排除公司测试与手动版本设备）"""
        try:
            _, rows = table_db.query_page(
                1, self._TABLE_PICKER_QUERY_LIMIT, "",
                include_test=False, include_manual=False)
        except Exception as e:
            # 记录异常类型与数据库实际路径：打包版曾因种子库未随包分发
            # 连到自建空库导致无候选，日志带 DB 路径可秒定位此类问题
            self._append_log(
                f"[球桌库] 查询失败: {type(e).__name__}: {e} "
                f"(db={table_db.DB_PATH})")
            return []
        picked = []
        for r in rows:
            snk = str(r.get("snk_code") or "").strip()
            if not snk:
                continue
            r["snk_code"] = snk
            picked.append(r)
        if len(picked) > self._TABLE_PICKER_MAX_ITEMS:
            self._append_log(
                f"[球桌库] 候选 {len(picked)} 台超出上限，仅保留最近 "
                f"{self._TABLE_PICKER_MAX_ITEMS} 台（可输入关键字缩小范围）")
            picked = picked[:self._TABLE_PICKER_MAX_ITEMS]
        # 球房 + 球桌名排序：同球房的球桌聚在一起，长列表里更好找
        # （query_page 本身按 id DESC，即新增优先）
        picked.sort(key=lambda r: (str(r.get("roomName") or ""),
                                   str(r.get("name") or "")))
        return picked

    @staticmethod
    def _format_table_label(row: dict) -> str:
        """候选显示文本：球桌名 / 球房名 (snk_code)，便于区分同名球桌"""
        name = str(row.get("name") or "").strip() or "未命名"
        room = str(row.get("roomName") or "").strip()
        snk = str(row.get("snk_code") or "").strip()
        return f"{name} / {room} ({snk})" if room else f"{name} ({snk})"

    def _on_table_filter_changed(self, text: str):
        """输入变化：实时过滤候选（选中回填写入的文本不参与过滤）"""
        if self._p2p_picking:
            return
        self._apply_table_filter(text)

    def _apply_table_filter(self, keyword: str):
        """按关键字过滤候选并刷新弹层内容

        空格分隔的多关键字需全部命中（如「星牌 3」）；snk_code 命中的排前面
        （球桌库以 snk 隧道为选中目标，名称/球房含同数字的行往后排）。
        空关键字恢复展示全量候选。
        """
        kw = (keyword or "").strip().lower()
        entries = self._p2p_table_entries
        if kw:
            words = kw.split()
            hits = [(lb, r) for lb, r in entries
                    if all(w in lb.lower() for w in words)]
            # sort 稳定，同组保持球房 + 球桌名顺序
            hits.sort(key=lambda e: 0 if any(
                w in e[1]["snk_code"].lower() for w in words) else 1)
        else:
            hits = entries
        self._p2p_table_matches = [r for _, r in hits]
        self._p2p_table_model.setStringList([lb for lb, _ in hits])
        combo = self._p2p_table_search
        if not hits:
            combo.hidePopup()        # 无命中：立即收起，不留旧候选
        elif combo.isPopupVisible():
            combo.showPopup()        # 已展示：原位刷新为过滤结果

    def eventFilter(self, obj, event):
        """截获球桌库下拉的回车 / Esc，统一走安全选中语义

        库内 EditableComboBox 的 returnPressed 会把输入文本 addItem 进候选
        （污染全量候选），弹层又只在有高亮行时响应回车——两者都不可靠，
        故在它们之前截获：回车交 _on_table_search_return 判定，Esc 仅收起弹层
        并丢弃排队中的延时展示（避免候选关了又自己弹回来）。
        ↑↓ 不在此处理：弹层可见时按键由库内 CompleterMenu 直接转发给列表，
        重复驱动会一次跳两行。
        """
        # 搜索框在 super().__init__() 之后才创建；构造期间基类会向窗口
        # 派发 WindowStateChange 等事件并进入本过滤器，须容忍属性尚未
        # 初始化（直接访问会在 C++ 构造栈中升级为 SystemError）
        if obj is getattr(self, "_p2p_table_search", None) \
                and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._on_table_search_return()
                return True  # 消费回车：库内默认行为与弹层均不再处理
            if key == Qt.Key.Key_Escape:
                # 确实收起了弹层才消费，否则 Esc 照旧上传（关面板搜索框等）
                return self._p2p_table_search.hidePopup()
        return super().eventFilter(obj, event)

    def _on_table_search_return(self):
        """回车选中（安全语义）：弹层有高亮行（↑↓ 导航/悬停的明确选择）
        时选该行；无高亮时仅当关键词能唯一定位一台球桌——snk 精确命中，
        或当前命中仅一条——才自动选中。
    
        多条模糊命中绠不猜第一条：短关键词（如两位数字）会子串命中大量
        球桌，自动选首条极易连错球房（严重 bug），此时仅提示并展开候选
        列表，由用户明确选择。
        """
        if self._p2p_picking:
            return  # 选中回填流程进行中，忽略后续重复回车
        combo = self._p2p_table_search
        # 弹层高亮行优先（弹层默认无高亮，高亮只能由用户主动导航产生）
        row = self._p2p_table_map.get(combo.popupCurrentText() or "")
        if row is not None:
            self._on_table_picked(row)
            return
        kw = combo.text().strip()
        if not kw:
            combo.showPopup()   # 空输入回车：直接展开全部候选供浏览
            return
        self._ensure_table_candidates()
        self._apply_table_filter(kw)
        matches = self._p2p_table_matches
        exact = [r for r in matches if r["snk_code"].lower() == kw.lower()]
        if len(exact) == 1:
            self._on_table_picked(exact[0])
        elif len(matches) == 1:
            self._on_table_picked(matches[0])
        elif not matches:
            self._append_log(f"[球桌库] 关键词 {kw!r} 未匹配到球桌")
        else:
            self._append_log(
                f"[球桌库] 关键词 {kw!r} 命中 {len(matches)} 台球桌，"
                "请从候选列表选择（↑↓ 或点击），不做自动匹配")
            combo.showPopup()
    
    def _on_table_match_activated(self, text: str):
        """候选选中（鼠标点击 / 弹层高亮行回车）：按显示文本反查球桌行

        弹层与候选表短暂不同步（展开期间候选被保鲜重载）时，兜底从显示
        文本尾部解析 snk 在已载入的候选里回查，保证连续选择不静默失败。
        """
        if self._p2p_picking:
            return  # 同一次选择的其它触发路径（弹层与回车）不重复添加
        label = str(text or "")
        row = self._p2p_table_map.get(label)
        if row is None:
            snk = label.rpartition("(")[2].rstrip(")").strip()
            row = next((r for _lb, r in self._p2p_table_entries
                        if r["snk_code"] == snk), None) if snk else None
            self._append_log(
                f"[球桌库] 候选文本未命中候选表({label!r})，按 snk={snk!r} "
                f"兜底回查{'命中' if row else '无结果'}")
            if row is None:
                return
        self._on_table_picked(row)

    def _on_table_picked(self, row: dict):
        """选中球桌：回填表单后自动走「添加」流程，注册为 visitor 加入列表"""
        self._p2p_picking = True
        try:
            snk = row["snk_code"]
            name = str(row.get("name") or "").strip() or "未命名"
            room = str(row.get("roomName") or "").strip()
            self.ui.p2p_form_server.setText(snk)
            # secretKey 与会话中心生成逻辑一致（settings 优先，缺省 abc123）
            secret = str(self._load_settings().get("xtcp_secret_key") or "abc123")
            self.ui.p2p_form_key.setText(secret)
            self._p2p_selected_table = row
            shown = f"{name} / {room} ({snk})" if room else f"{name} ({snk})"
            self._append_log(
                f"[球桌库] 已选择 {shown}，自动添加到 visitor 列表")
            self._add_picked_table(snk, name)
        finally:
            self._p2p_table_search.hidePopup()
            # 选中结果由 InfoBar + visitor 列表选中行反馈，输入框延时清空：
            # 库内 CompleterMenu 先 setText(候选文本) 再发 activated，
            # EditableComboBox.__onActivated 随后又按 findText 写回一次，
            # 必须等本次调用栈结束再清，否则清完立刻被写回（选中后
            # 输入框残留长文本）；清空后直接可输下一个关键词
            QTimer.singleShot(0, self._finish_table_pick)

    def _finish_table_pick(self):
        """选中收尾：清空输入框并恢复全量候选（候选文本回写已在此前发生）"""
        combo = self._p2p_table_search
        try:
            # setCurrentIndex(-1) 清文本并恢复 placeholder；回车路径下库内
            # 未写回候选文本、currentIndex 本就是 -1，此时显式 setText 兜底
            combo.setCurrentIndex(-1)
            if combo.text():
                combo.setText("")
        finally:
            self._p2p_picking = False
        self._apply_table_filter("")

    def _add_picked_table(self, snk: str, table_name: str = ""):
        """把选中的球桌按表单注册为 XTCP visitor（复用「添加」按钮完整路径）

        重复添加保护：serverName 已在列表中时仅 InfoBar 提示并选中该行。
        球桌库仅在 XTCP 模式下可见，故此处按 XTCP visitor 语义添加。
        """
        existing = next(
            (i for i, v in enumerate(self._p2p_visitors)
             if v.get("serverName", "").strip() == snk), -1)
        if existing >= 0:
            self._show_info_bar(f"{snk} 已在列表中", "info")
            self._p2p_current_index = existing
            self.ui.p2p_visitor_list.setCurrentRow(existing)
            self._append_log(f"[球桌库] {snk} 已在 visitor 列表中，跳过重复添加")
            return
        # 复用添加按钮槽函数（表单已回填，内部完成端口校验/列表刷新/选中新行）
        # 先重新分配空闲端口：连接状态下表单残留的旧端口可能已被现有隧道
        # 占用，直接走 _on_p2p_add 会被端口校验拒绝导致添加静默失败
        self.ui.p2p_form_port.setValue(self._get_new_random_port())
        self._on_p2p_add()
        if self._p2p_visitors \
                and self._p2p_visitors[-1].get("serverName") == snk:
            # 打标来源与关联球桌：隧道面板展示「球桌库」而非「手工添加」
            added = self._p2p_visitors[-1]
            added["source"] = SOURCE_TABLE
            added["tableId"] = str(
                self._p2p_selected_table.get("name") or "").strip() \
                if self._p2p_selected_table else ""
            # 添加即注册进会话中心（不启动 frpc）：「当前隧道」面板立即可见
            self._register_visitor_to_manager(added)
            # 带球桌名提示：输入框选中后会被清空，反馈全靠这条 InfoBar
            self._show_info_bar(
                f"已添加 {table_name}（{snk}）" if table_name
                else f"已添加 {snk}", "success")
        else:
            # 添加未生效（如端口校验拒绝）：明确记日志，避免静默失败无迹可查
            self._append_log(f"[球桌库] 添加 {snk} 失败，请查看上方日志中的拒绝原因")

    def _register_visitor_to_manager(self, visitor: dict):
        """把面板 visitor 同步注册进会话中心（不启动 frpc）

        添加即注册：「当前隧道」面板读注册表，注册后球桌库/手工添加的
        snk 无需连接即可见；persist() 落盘即时对齐（重启后不丢不复活）。
        连接时 _on_xtcp_connect 全量重建注册表，口径一致。
        """
        name = str(visitor.get("serverName") or "").strip()
        if not name:
            return
        try:
            self._session_mgr.register_visitor(
                name,
                bind_port=visitor.get("bindPort"),
                secret_key=visitor.get("secretKey") or "abc123",
                source=visitor.get("source") or SOURCE_MANUAL,
                table_id=str(visitor.get("tableId") or ""))
            self._session_mgr.persist()
        except (OSError, RuntimeError, ValueError) as e:
            self._append_log(f"[远程] 注册 visitor {name} 失败: {e}")

    # ------------------------------------------------------------------ TCP 保存的服务器
    def _load_tcp_servers(self):
        """从 settings.json 读取保存的服务器列表（ip:port 字符串）"""
        settings = self._load_settings()
        servers = settings.get("tcp_servers", [])
        if not isinstance(servers, list):
            return []
        return [s for s in servers if isinstance(s, str) and s.strip()]

    def _save_tcp_servers(self, servers):
        """持久化保存的服务器列表到 settings.json"""
        self._save_settings({"tcp_servers": servers})

    def _refresh_tcp_server_list(self):
        """刷新保存的服务器列表显示（TCP 模式复用 p2p_visitor_list）"""
        self.ui.p2p_visitor_list.clear()
        for s in self._load_tcp_servers():
            self.ui.p2p_visitor_list.addItem(s)
        # 重新应用搜索过滤
        self._on_p2p_search_changed(self.ui.p2p_search.text())

    def _reload_p2p_list_for_mode(self):
        """按当前模式重载列表内容（XTCP=visitors，TCP=保存的服务器）"""
        mode = self.ui.p2p_mode_combo.currentText()
        self.ui.p2p_visitor_list.blockSignals(True)
        if mode == "TCP":
            self._p2p_current_index = -1
            self._refresh_tcp_server_list()
        else:
            saved = self._p2p_current_index
            self._refresh_p2p_list()
            if 0 <= saved < self.ui.p2p_visitor_list.count():
                self.ui.p2p_visitor_list.setCurrentRow(saved)
        self.ui.p2p_visitor_list.blockSignals(False)

    def _on_tcp_server_add(self):
        """TCP 模式：把当前 host:port 保存到服务器列表"""
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self._append_log("[远程] 请先填写主机地址 host")
            return
        entry = f"{host}:{self.ui.p2p_ssh_port.value()}"
        servers = self._load_tcp_servers()
        if entry in servers:
            self._append_log(f"[远程] 服务器 {entry} 已在列表中")
            return
        servers.append(entry)
        self._save_tcp_servers(servers)
        self._refresh_tcp_server_list()
        self.ui.p2p_visitor_list.setCurrentRow(len(servers) - 1)
        self._append_log(f"[远程] 已保存服务器: {entry}")

    def _on_tcp_server_delete(self):
        """TCP 模式：删除选中的服务器"""
        row = self.ui.p2p_visitor_list.currentRow()
        servers = self._load_tcp_servers()
        if 0 <= row < len(servers):
            removed = servers.pop(row)
            self._save_tcp_servers(servers)
            self._refresh_tcp_server_list()
            self._append_log(f"[远程] 已删除服务器: {removed}")

    def _on_tcp_server_selected(self, row):
        """TCP 模式：选中服务器时填充 host/port"""
        servers = self._load_tcp_servers()
        if not (0 <= row < len(servers)):
            return
        entry = servers[row]
        host, _, port_str = entry.rpartition(':')
        if not host:
            host, port = entry, 22
        else:
            try:
                port = int(port_str)
            except ValueError:
                host, port = entry, 22
        self.ui.p2p_ssh_host.setText(host)
        self.ui.p2p_ssh_port.setValue(port)

    def _on_p2p_connect(self):
        """连接按钮 - 根据当前模式分发连接"""
        mode = self.ui.p2p_mode_combo.currentText()
        self._append_log(f"[远程] 连接按钮点击，模式: {mode}")
        if mode == "XTCP":
            self._on_xtcp_connect()
        elif mode == "TCP":
            self._on_tcp_connect()

    def _on_p2p_disconnect(self):
        """断开按钮 - 根据当前模式分发断开"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            self._on_xtcp_disconnect()
        elif mode == "TCP":
            self._on_tcp_disconnect()

    def _on_p2p_mode_changed(self, _index):
        """连接方式切换时更新 UI 显隐"""
        self._save_current_form()
        self._update_p2p_visibility()

    def _update_p2p_visibility(self):
        """根据当前模式显示/隐藏对应表单"""
        mode = self.ui.p2p_mode_combo.currentText()
        is_xtcp = (mode == "XTCP")
        is_tcp = not is_xtcp
        self.ui.p2p_server_section_label.setText(
            "◎ 服务器 / visitors" if is_xtcp else "◎ 保存的服务器")
        for w in self.ui.p2p_xtcp_widgets:
            w.setVisible(is_xtcp)
        for i in range(self.ui.p2p_xtcp_form.rowCount()):
            lbl = self.ui.p2p_xtcp_form.itemAt(i * 2, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_xtcp)
        self.ui.p2p_conn_widget.setVisible(is_xtcp)
        for w in self.ui.p2p_ssh_widgets:
            w.setVisible(is_tcp)
        for row_idx in range(2):
            lbl = self.ui.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl and lbl.widget():
                lbl.widget().setVisible(is_tcp)
        self._reload_p2p_list_for_mode()
        self._update_p2p_buttons()

    def _on_xtcp_connect(self):
        """将手工 visitor 注册到统一会话中心并启动 frpc（共享单一进程/TOML）"""
        # 连接前先把正在编辑的表单写回列表，否则最后改的那条 visitor 用的还是旧值
        self._save_current_form()
        if not self._p2p_visitors:
            self._append_log("[远程] 请先添加 visitor 配置")
            return
        mgr = self._session_mgr
        try:
            # 先清除旧的面板注册（手工 + 球桌库），再按表单逐项注册（同名 serverName 复用隧道）
            # 不清干净会残留：表单里删掉/改过端口的旧 visitor 会继续占着注册表和端口
            for src in (SOURCE_MANUAL, SOURCE_TABLE):
                mgr.remove_visitors_by_source(src)
            for v in self._p2p_visitors:
                server_name = v.get("serverName", "")
                # 关联球桌：球桌库选择时已带 tableId，手工添加的按 snk 反查补全
                table_id = str(v.get("tableId") or "").strip() \
                    or self._lookup_table_name_by_snk(server_name)
                if table_id:
                    v["tableId"] = table_id
                mgr.register_visitor(
                    server_name,
                    bind_port=v.get("bindPort"),
                    secret_key=v.get("secretKey") or "abc123",
                    source=v.get("source") or SOURCE_MANUAL,
                    table_id=table_id)
            mgr.apply()
            # 连接即使用：刷新最近使用时间，隧道面板立即显示数据
            for v in self._p2p_visitors:
                mgr.mark_used(v.get("serverName", ""), str(v.get("tableId") or ""))
        except (OSError, RuntimeError, ValueError) as e:
            self._append_log(f"[远程] 启动失败: {e}")
            return
        total = len(mgr.records())
        self._append_log(f"[远程] frpc 已启动，共 {total} 条隧道（含 snk 快捷连接）")
        self._update_p2p_buttons()

    @staticmethod
    def _lookup_table_name_by_snk(snk: str) -> str:
        """按 snk 反查球桌库中的球桌号（手工添加 visitor 的关联球桌补全），
        查不到或库异常时返回空串不影响连接"""
        try:
            return table_db.get_table_name_by_snk(snk)
        except Exception:
            return ""

    def _on_xtcp_disconnect(self):
        """断开：注销手工 visitor；仍有其他隧道时 frpc 保持运行，否则停止"""
        mgr = self._session_mgr
        # 先优雅关闭这些隧道端口上的会话（含统一会话中心窗口里的 snk 会话），
        # 避免隧道移除后 SSH/SFTP 窗口假死
        for v in self._p2p_visitors:
            port = v.get("bindPort")
            if port:
                mgr.close_sessions_on_port(port, reason=v.get("serverName", ""))
        removed = 0
        for v in self._p2p_visitors:
            name = v.get("serverName", "")
            if name and mgr.remove_visitor(name):
                removed += 1
        if not removed and not mgr.is_running():
            self._append_log("[远程] frpc 未在运行")
            return
        self._append_log("[远程] 正在断开手工 visitor...")
        try:
            mgr.apply()
        except (OSError, RuntimeError) as e:
            self._append_log(f"[远程] 应用变更失败: {e}")
        remaining = len(mgr.records())
        if remaining:
            self._append_log(
                f"[远程] 已断开手工 visitor，frpc 保持运行（剩余 {remaining} 条隧道）")
        else:
            self._append_log("[远程] frpc 已停止")
        self._close_p2p_windows()
        self._update_p2p_buttons()

    def _on_tcp_connect(self):
        """启动 TCP 连接"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[TCP] paramiko 未安装，请执行: pip install paramiko")
            return
        if self._tcp_worker is not None and self._tcp_worker.isRunning():
            self._append_log("[TCP] 已有连接正在运行")
            return
        if self._tcp_worker is not None:
            self._tcp_worker.deleteLater()
            self._tcp_worker = None
        host = self.ui.p2p_ssh_host.text().strip()
        if not host:
            self._append_log("[TCP] 请输入主机地址")
            return
        port = self.ui.p2p_ssh_port.value()
        self._save_ssh_credentials()
        self._tcp_worker = TCPWorker(
            host, port,
            self.ui.p2p_ssh_user.text(), self.ui.p2p_ssh_pass.text()
        )
        self._tcp_worker.result_ready.connect(self._on_tcp_finished)
        self._tcp_worker.error.connect(self._on_tcp_error)
        self._tcp_worker.start()
        self._append_log(f"[TCP] 正在连接 {host}:{port}...")
        self._update_p2p_buttons()

    def _on_tcp_disconnect(self):
        """断开 TCP 连接"""
        if self._tcp_worker is None:
            self._append_log("[TCP] 未连接")
            return
        worker = self._tcp_worker
        self._tcp_worker = None
        if worker.isRunning():
            # 线程还在跑时不能直接销毁：挂 finished 回调等它自然退出再 deleteLater，
            # 否则 QThread 运行中被析构会直接崩
            # lambda 包装必须：PySide6 C++ 直连不持有 Python 引用，worker 会被 GC
            worker.finished.connect(lambda w=worker: w.deleteLater())
        else:
            worker.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._close_p2p_windows()
        self._update_p2p_buttons()
        self._append_log("[TCP] 已断开")

    def _on_tcp_finished(self, result):
        """TCP 连接成功回调"""
        self._append_log(f"[TCP] 连接成功: {result}")
        self._show_info_bar(f"TCP 连接成功: {result}", "success")
        self.ui.p2p_sftp_btn.setEnabled(True)
        self.ui.p2p_ssh_terminal_btn.setEnabled(True)
        self.ui.p2p_rdp_btn.setEnabled(True)
        if self._tcp_worker:
            w = self._tcp_worker
            self._tcp_worker = None
            if w.isRunning():
                w.finished.connect(lambda w=w: w.deleteLater())
            else:
                w.deleteLater()

    def _on_tcp_error(self, error):
        """TCP 连接失败回调"""
        self._append_log(f"[TCP] 连接失败: {error}")
        self._show_info_bar(f"网络连接失败: {error}", "error", duration=4000)
        if self._tcp_worker:
            w = self._tcp_worker
            self._tcp_worker = None
            if w.isRunning():
                w.finished.connect(lambda w=w: w.deleteLater())
            else:
                w.deleteLater()
        self.ui.p2p_sftp_btn.setEnabled(False)
        self.ui.p2p_ssh_terminal_btn.setEnabled(False)
        self.ui.p2p_rdp_btn.setEnabled(False)
        self._update_p2p_buttons()

    def _update_p2p_buttons(self):
        """更新连接/断开按钮状态，以及 SFTP/SSH 终端/远程桌面按钮状态"""
        mode = self.ui.p2p_mode_combo.currentText()
        if mode == "XTCP":
            running = self._session_mgr.is_running()
            self.ui.p2p_sftp_btn.setEnabled(running)
            self.ui.p2p_ssh_terminal_btn.setEnabled(running)
            self.ui.p2p_rdp_btn.setEnabled(running)
            self.ui.p2p_connect_btn.setEnabled(not running)
            self.ui.p2p_disconnect_btn.setEnabled(running)
        elif mode == "TCP":
            self.ui.p2p_sftp_btn.setEnabled(True)
            self.ui.p2p_ssh_terminal_btn.setEnabled(True)
            self.ui.p2p_rdp_btn.setEnabled(True)
        else:
            self.ui.p2p_sftp_btn.setEnabled(False)
            self.ui.p2p_ssh_terminal_btn.setEnabled(False)
            self.ui.p2p_rdp_btn.setEnabled(False)

    def _close_p2p_windows(self):
        """关闭远程会话标签容器窗口（释放所有会话面板）"""
        win = getattr(self, '_remote_session_window', None)
        if win is not None:
            self._remote_session_window = None
            try:
                win.close()
            except (RuntimeError, OSError):
                pass

    def _save_ssh_credentials(self):
        """将当前 SSH 账号/密码保存到 settings.json"""
        username = self.ui.p2p_ssh_user.text().strip()
        password = self.ui.p2p_ssh_pass.text()
        data = {}
        if username:
            data["ssh_user"] = username
        if password:
            data["ssh_pass"] = password
        if data:
            self._save_settings(data)

    # ------------------------------------------------------------------ 远程窗口通用逻辑

    def _resolve_remote_target(self, tag: str, feature_name: str):
        """解析当前远程连接模式的目标参数（通用逻辑，供三个远程窗口按钮复用）

        根据 p2p_mode_combo 当前模式获取 host/port/server_name，校验必填项。

        Returns:
            (host, port, server_name, username, password) 元组；校验失败时记录日志并返回 None。
        """
        mode = self.ui.p2p_mode_combo.currentText()
        server_name = ''
        if mode == "XTCP":
            if not self._p2p_visitors:
                self._append_log(f"[{tag}] 请先添加 visitor 配置")
                return None
            idx = self._p2p_current_index
            if not (0 <= idx < len(self._p2p_visitors)):
                self._append_log(f"[{tag}] 请先在列表中选择一个 visitor")
                return None
            host = "127.0.0.1"
            port = self._p2p_visitors[idx]["bindPort"]
            server_name = self._p2p_visitors[idx].get("serverName", "")
        elif mode == "TCP":
            host = self.ui.p2p_ssh_host.text().strip()
            port = self.ui.p2p_ssh_port.value()
        else:
            self._append_log(f"[{tag}] {feature_name}仅支持 XTCP/TCP 模式")
            return None
        username = self.ui.p2p_ssh_user.text()
        password = self.ui.p2p_ssh_pass.text()
        if not host:
            self._append_log(f"[{tag}] 主机地址不能为空")
            return None
        return host, port, server_name, username, password

    def _ensure_session_window(self) -> RemoteSessionWindow:
        """获取或创建远程会话标签容器窗口（单例复用）"""
        win = getattr(self, '_remote_session_window', None)
        if win is not None:
            try:
                # 检查 C++ 对象是否已被销毁
                win.isVisible()
                # 已有窗口：重新显示并置顶。新建会话后用户应立即看到会话窗口，
                # 否则窗口留在主窗口下层，用户会误以为点击新建无效
                win.show()
                win.raise_()
                win.activateWindow()
                return win
            except RuntimeError:
                self._remote_session_window = None
        # 不传 parent：避免成为主窗口的 owned window 而始终盖在主窗口之上（始终置顶）
        win = RemoteSessionWindow()
        win.destroyed.connect(lambda: setattr(self, '_remote_session_window', None))
        self._remote_session_window = win
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    # ------------------------------------------------------------------ 远程窗口按钮

    def _on_sftp_btn_clicked(self):
        """打开 SFTP 文件管理标签页"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SFTP] paramiko 未安装")
            return
        target = self._resolve_remote_target("SFTP", "SFTP")
        if target is None:
            return
        host, port, server_name, username, password = target
        self._save_ssh_credentials()
        # 从配置读取默认远程路径（如 /home/newbv/snooker）
        default_path = self._load_settings().get("sftp_default_remote_path", "")
        self._append_log(f"[SFTP] 打开文件管理: {server_name or host}:{port}")
        panel = SFTPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self._append_log(msg),
            default_remote_path=default_path or None,
        )
        self._ensure_session_window().add_session(panel)

    def _on_ssh_terminal_btn_clicked(self):
        """打开 SSH 终端标签页"""
        if not PARAMIKO_AVAILABLE:
            self._append_log("[SSH] paramiko 未安装")
            return
        target = self._resolve_remote_target("SSH", "SSH 终端")
        if target is None:
            return
        host, port, server_name, username, password = target
        self._save_ssh_credentials()
        self._append_log(f"[SSH] 打开终端: {server_name or host}:{port}")
        panel = SSHTerminalPanel(
            host, port, username, password,
            log_callback=lambda msg: self._append_log(msg),
            server_name=server_name,
        )
        self._ensure_session_window().add_session(panel)

    def _on_rdp_btn_clicked(self):
        """打开远程桌面标签页（嵌入 mstsc.exe）"""
        if sys.platform != 'win32':
            self._append_log("[RDP] 远程桌面仅支持 Windows")
            return
        target = self._resolve_remote_target("RDP", "远程桌面")
        if target is None:
            return
        host, port, server_name, username, password = target
        self._save_ssh_credentials()
        self._append_log(f"[RDP] 打开远程桌面: {server_name or host}:{port}")
        panel = RDPPanel(
            host, port, username, password,
            server_name=server_name,
            log_callback=lambda msg: self._append_log(msg),
        )
        self._ensure_session_window().add_session(panel)

    # ------------------------------------------------------------------ 会话恢复

    def _save_remote_sessions(self):
        """关闭前保存当前远程会话信息到 settings.json，下次启动可自动恢复"""
        # 会话恢复开关关闭时写入空列表：避免旧数据残留导致意外恢复
        if not bool(self._load_settings().get("restore_remote_sessions", True)):
            self._save_settings({"remote_sessions": []})
            return
        win = getattr(self, '_remote_session_window', None)
        sessions = []
        if win is not None:
            try:
                for panel in win._panels:
                    info = self._extract_session_info(panel)
                    if info:
                        sessions.append(info)
            except (RuntimeError, AttributeError):
                pass
        self._save_settings({"remote_sessions": sessions})

    def _extract_session_info(self, panel):
        """从面板提取会话信息（类型/主机/端口/用户名/服务器名/当前路径）"""
        panel_type = type(panel).__name__
        if panel_type == 'SFTPPanel':
            return {
                'type': 'sftp',
                'host': panel._host,
                'port': panel._port,
                'username': panel._username,
                'server_name': panel._server_name,
                'remote_path': panel._remote_path,
            }
        elif panel_type == 'SSHTerminalPanel':
            return {
                'type': 'ssh',
                'host': getattr(panel, '_host', ''),
                'port': getattr(panel, '_port', 0),
                'username': getattr(panel, '_username', ''),
                'server_name': getattr(panel, '_server_name', ''),
            }
        elif panel_type == 'RDPPanel':
            return {
                'type': 'rdp',
                'host': getattr(panel, '_host', ''),
                'port': getattr(panel, '_port', 0),
                'username': getattr(panel, '_username', ''),
                'server_name': getattr(panel, '_server_name', ''),
            }
        return None

    def _restore_remote_sessions(self):
        """启动时从 settings.json 恢复上次的远程会话（延迟 1s 执行，等待主窗口就绪）"""
        from PySide6.QtCore import QTimer
        # 会话恢复开关：默认开启，关闭时不自动恢复
        if not bool(self._load_settings().get("restore_remote_sessions", True)):
            return
        sessions = self._load_settings().get("remote_sessions", [])
        if not sessions or not isinstance(sessions, list):
            return
        if not PARAMIKO_AVAILABLE:
            return
        password = self.ui.p2p_ssh_pass.text()
        QTimer.singleShot(1000, lambda: self._do_restore_sessions(sessions, password))

    def _do_restore_sessions(self, sessions, password):
        """实际执行会话恢复"""
        restored = 0
        for s in sessions:
            try:
                stype = s.get('type', '')
                host = s.get('host', '')
                port = s.get('port', 0)
                username = s.get('username', '')
                server_name = s.get('server_name', '')
                if not host or not port:
                    continue
                if stype == 'sftp':
                    panel = SFTPPanel(
                        host, port, username, password,
                        server_name=server_name,
                        log_callback=lambda msg: self._append_log(msg),
                        default_remote_path=s.get('remote_path', ''),
                    )
                    self._ensure_session_window().add_session(panel)
                    restored += 1
                elif stype == 'ssh':
                    panel = SSHTerminalPanel(
                        host, port, username, password,
                        log_callback=lambda msg: self._append_log(msg),
                        server_name=server_name,
                    )
                    self._ensure_session_window().add_session(panel)
                    restored += 1
                elif stype == 'rdp' and sys.platform == 'win32':
                    panel = RDPPanel(
                        host, port, username, password,
                        server_name=server_name,
                        log_callback=lambda msg: self._append_log(msg),
                    )
                    self._ensure_session_window().add_session(panel)
                    restored += 1
            except Exception as e:
                self._append_log(f"[会话恢复] 失败: {e}")
        if restored:
            self._append_log(f"[会话恢复] 已恢复 {restored} 个远程会话")
