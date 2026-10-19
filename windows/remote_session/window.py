# -*- coding: utf-8 -*-
"""当前隧道列表窗口：展示 frpc 隧道、断开单条、删除注册、一键全部断开。

窗口本身不持有隧道状态，全部读写都走 core.frp_remote 的 session manager
单例；因此界面显示永远可能比注册表滞后一拍，操作前一律重新查记录。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QTableWidgetItem,
                               QHeaderView, QAbstractItemView)
from qfluentwidgets import (TableWidget, FluentIcon, BodyLabel,
                            CaptionLabel, MessageBox,
                            PushButton as FluentPushButton)
from qfluentwidgets.window.fluent_window import FluentTitleBar
from qframelesswindow import FramelessWindow

from core.frp_remote import get_session_manager
from core.ops_link_delegate import LINKS_ROLE, install_ops_links
from core.perf import apply_table_smooth_mode
from core.utils import show_info_bar

# 列宽是初始值，用户拖动后由 QHeaderView 记住；最后一列操作列自适应拉伸
_HEADERS = ["serverName", "关联球桌", "本地端口", "来源", "最近使用", "操作"]
_COL_WIDTHS = [160, 110, 70, 90, 95, 150]


class TunnelPanelWindow(FramelessWindow):
    """当前隧道列表窗口（无边框 + Fluent 标题栏，独立窗口）。

    用独立窗口而不是内嵌页签：隧道列表需要和远程面板并排看，
    且主窗口不可见时状态变化也要能自己刷新出来。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当前隧道")
        self.resize(880, 420)
        self.setMinimumSize(760, 300)
        # WA_DeleteOnClose：每次打开都是新实例，避免旧实例的信号与定时器残留
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # 无边框窗口必须自己塞标题栏，否则拖动、最大化和关闭都没有入口
        self.setTitleBar(FluentTitleBar(self))
        self.titleBar.setTitle(self.windowTitle())

        layout = QVBoxLayout(self)
        # 上边距 40 给悬浮标题栏让位：无边框窗口的标题栏是覆盖在内容之上的
        layout.setContentsMargins(12, 40, 12, 12)
        layout.setSpacing(8)

        # --- 顶部状态行：frpc 状态 + 隧道计数 + 全停按钮 ---
        # 状态行同时承担三件事：frpc 是否活着、有几条隧道、以及全停入口
        head = QHBoxLayout()
        head.setSpacing(8)
        self._status_label = BodyLabel("frpc 未启动", self)
        head.addWidget(self._status_label)
        head.addStretch(1)
        self._stop_all_btn = FluentPushButton(FluentIcon.POWER_BUTTON, "全部断开", self)
        # 固定 28 高：与标题栏按钮同高，避免把窗口顶部撑高
        self._stop_all_btn.setFixedHeight(28)
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        head.addWidget(self._stop_all_btn)
        layout.addLayout(head)

        # --- 隧道表格 ---
        self._table = TableWidget(self)
        # 表格接入平滑滚动开关：多个窗口共用 core.perf 的全局设置，
        # 设置页一改，这里调 _apply_smooth_mode 即可生效
        apply_table_smooth_mode(self._table, panel="remote")
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        # 只读、整行选中、单选：所有操作都由「操作」列链接触发，
        # 双击单元格没有语义，误编辑反而会让显示和注册表脱节
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for i, w in enumerate(_COL_WIDTHS):
            self._table.setColumnWidth(i, w)
        layout.addWidget(self._table, 1)
        # 操作列：委托自绘文字链接（断开/删除，零子控件）——与远程页会话
        # 总览/frps 代理表同款（2026-09-24 P0-2 委托化）
        # 行号 → 记录映射：重建表格后行号会变，操作回调不能靠下标反查记录
        self._row_recs: dict = {}
        # 委托自绘文字链接而不是每行塞按钮：几十行时子控件数量会拖慢滚动
        install_ops_links(self._table, self._on_ops_link)

        # 空态提示常驻但默认隐藏：用可见性切换代替反复创建销毁控件
        self._hint = CaptionLabel("暂无活跃隧道 —— 通过远程面板连接或球桌右键远程后在此显示", self)
        self._hint.setStyleSheet("color: #8a919b;")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)

        # 状态变化自动刷新（全部使用 bound method，配合 closeEvent 显式断开，
        # 避免窗口销毁后 manager 单例信号回调已删除的 C++ 控件）
        mgr = get_session_manager()
        # 连接要在首次 refresh 之前：构造期的首刷也得落在已连好的状态上
        mgr.visitors_changed.connect(self.refresh)
        mgr.frpc_state_changed.connect(self._on_frpc_state_changed)

        self.refresh()

    def _apply_smooth_mode(self):
        """按当前生效的平滑滚动设置刷新本窗口表格（远程会话设置页联动）。"""
        # 设置页改完不必重开窗口，调这里即时生效
        apply_table_smooth_mode(self._table, panel="remote")

    def closeEvent(self, event):
        """关闭时停止 frpc 状态监听（QTimer 挂在本窗口上，随窗口销毁自动停止）。

        信号连接挂在单例 manager 上，不会随窗口销毁，必须在这里手动断开。
        """
        """关闭时显式断开 manager 信号连接，防止销毁后回调崩溃"""
        # manager 是单例，窗口销毁后仍会回调已删除的 C++ 对象
        mgr = get_session_manager()
        try:
            mgr.visitors_changed.disconnect(self.refresh)
            mgr.frpc_state_changed.disconnect(self._on_frpc_state_changed)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    def _on_frpc_state_changed(self, _running: bool):
        """frpc 状态变化回调（bound method，Qt 可在接收者销毁时自动断开）"""
        self.refresh()

    # ------------------------------------------------------------------ 刷新

    def refresh(self):
        """按 session manager 的当前注册表重建整张表。

        全量重建而不是逐行 diff：活跃隧道通常个位数，重建成本可以忽略，
        也不会出现「记录变了但某一行没更新」的漏行问题。
        """
        mgr = get_session_manager()
        records = mgr.records()
        running = mgr.is_running()
        self._status_label.setText(
            f"frpc {'运行中' if running else '未启动'} · {len(records)} 条隧道")
        self._stop_all_btn.setEnabled(bool(records))
        self._hint.setVisible(not records)

        # 重建前清空表格并同步清映射：旧行号对应的记录已经作废
        self._table.setRowCount(0)
        self._row_recs.clear()
        for rec in records:
            # 行号取当前行数，insertRow 追加在末尾，保证与 _row_recs 的键一致
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._row_recs[row] = rec
            # 展示值与记录字段一一对应；空值用「—」占位，避免出现空白单元格
            values = [
                rec.get("serverName", ""),
                rec.get("tableId", "") or "—",
                str(rec.get("bindPort", "")),
                rec.get("source", ""),
                rec.get("lastUsed", "") or "—",
            ]
            # disabled 表示「注册保留但端口已释放」，界面上要和「已删除」明确区分
            disabled = bool(rec.get("disabled"))
            for col, text in enumerate(values):
                # 只在首列加「已断开」后缀：每列都加会变成噪声，扫服务器名就够了
                item = QTableWidgetItem(
                    f"{text}（已断开）" if disabled and col == 0 else text)
                # 已断开行的悬停里解释「注册还在」：用户最大疑问是这是否等于删除
                item.setToolTip(text if not disabled or col != 0
                                else "已断开：注册保留，重新连接即恢复")
                # 本地端口是纯数字，居中显示便于与左右两侧的文本列区分
                if col == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)
            # 「操作」列：委托自绘文字链接（零子控件，旧 cellWidget 按钮组已
            # 移除）；已断开行幂等无意义 → 省略「断开」链接
            links = []
            if not disabled:
                links.append(("断开", "ghost", "disconnect"))
            links.append(("删除", "danger", "delete"))
            ops = QTableWidgetItem("")
            ops.setData(LINKS_ROLE, tuple(links))
            # 链接数据挂在 LINKS_ROLE 角色上，委托按角色取，不依赖显示文本
            self._table.setItem(row, 5, ops)

    def _on_ops_link(self, row, _col, action):
        """操作列文字链接回调（行号 → 记录映射见 refresh）。

        行号是刷新那一刻的快照，点击时该行可能已被重建，
        映射里查不到记录就丢弃这次点击，不去猜它原来指谁。
        """
        rec = self._row_recs.get(row)
        if rec is None:
            return  # 行重建竞态：映射里已无此行，忽略本次点击
        sn = str(rec.get("serverName", ""))
        if action == "disconnect":
            self._on_disconnect(sn)
        elif action == "delete":
            self._on_delete(sn)

    def showEvent(self, event):
        """每次显示都重刷一次：窗口可能刚从最小化恢复，期间隧道状态已经变了。"""
        super().showEvent(event)
        self.refresh()
        # 从最小化恢复时标题栏按钮可能卡在 PRESSED，这里统一复位
        self._reset_titlebar_button_state()

    def _reset_titlebar_button_state(self):
        """重置标题栏按钮 PRESSED 卡滞状态（与 RemoteSessionWindow 同款处理）"""
        try:
            # 延迟 import：只在需要复位时碰 qframelesswindow 的私有模块，
            # 版本变动不会拖垮整个窗口的加载
            from qframelesswindow.titlebar.title_bar_buttons import (
                TitleBarButton, TitleBarButtonState)
            for btn in self.titleBar.findChildren(TitleBarButton):
                if btn.isPressed():
                    btn.setState(TitleBarButtonState.NORMAL)
        # 私有 API 变动时静默降级：按钮状态卡滞总比抛异常让窗口打不开强
        except Exception:
            pass

    # ------------------------------------------------------------------ 操作

    def _find_record(self, server_name: str) -> dict:
        """按 serverName 取当前注册表记录（不存在返回空 dict，列表可能已滞后）"""
        return next((r for r in get_session_manager().records()
                     if r.get("serverName") == server_name), {})

    def _confirm_interrupt_transfer(self, server_name: str) -> bool:
        """会话任务进行中二次确认：取消保持当前连接与任务不变，确认才允许断开。

        口径覆盖「文件传输 / 终端会话」两类任务：is_busy_on_port 同时
        识别 SFTP 在途传输与 SSH 取证任务，只有确认后才真的断。
        """
        dlg = MessageBox(
            "会话任务进行中",
            f"隧道「{server_name}」上有文件传输 / 终端会话任务正在进行。\n"
            "继续操作将立即中断当前任务，并关闭该隧道上的\n"
            "SSH / SFTP 会话。\n\n确定要继续吗？",
            self)
        # 用 MessageBox 而不是 QMessageBox：与主界面同为 Fluent 风格，按钮文案可改
        dlg.yesButton.setText("仍然断开")
        dlg.cancelButton.setText("取消")
        return bool(dlg.exec())

    def _on_disconnect(self, server_name: str):
        """断开单条隧道：仅 frpc 运行中生效，绝不自动启动 frpc。

        流程：frpc 未启动只提示；有任务在跑先二次确认；确认后由 manager
        关闭相关会话、把 visitor 置为「已断开」并 apply 释放端口。注册与
        持久化保留，这一点和「删除」不同，重新连接 SSH/SFTP/RDP 会自动
        恢复启用。
        """
        if not server_name:
            return
        mgr = get_session_manager()
        rec = self._find_record(server_name)
        if not rec:
            # 注册表才是准绳：列表可能已滞后（别处刚断开），查不到就刷新对齐后退出
            self.refresh()
            return
        # 1. 门控先看 frpc 状态：没启动时隧道压根没建立，只提示不动注册表，
        #    否则顺手 apply 会把 frpc 带着剩余隧道拉起来，断开变连接
        if not mgr.is_running():
            show_info_bar("当前 frpc 未启动，隧道未建立，无需断开",
                          "warning", title="无法断开", parent=self, duration=3500)
            return
        # 2. 通过状态门控后再查任务：有文件传输/终端取证在跑就先警告会中断，
        #    用户确认才继续，取消则什么都不动
        if mgr.is_busy_on_port(rec.get("bindPort", 0)) \
                and not self._confirm_interrupt_transfer(server_name):
            return
        # 3. 关会话/移除/apply 全委托 manager，保证「先关会话再释放端口」；
        #    P0-2：热重载 HTTP 往返已移后台，结果回 GUI 线程后提示
        # 结果在 GUI 线程回调：manager 已把 apply 移到后台线程，这里只管提示
        def _done(result: str):
            if result == "ok":
                self.refresh()  # 显式即时刷新兜底（信号刷新外的双保险）
                show_info_bar(f"已断开隧道 {server_name}，相关会话已关闭、本地端口已释放；"
                              "注册保留，可重新连接",
                              "success", title="断开成功", parent=self, duration=3000)
            elif result == "not_running":
                show_info_bar("当前 frpc 未启动", "warning",
                              title="无法断开", parent=self, duration=3500)
            else:
                show_info_bar(f"隧道 {server_name} 断开失败", "error",
                              title="断开失败", parent=self, duration=4000)

        mgr.disconnect_visitor_async(server_name, _done)

    def _on_delete(self, server_name: str):
        """删除 snk：从注册表与持久化配置中彻底移除。

        frpc 未运行时也可执行，且不会把 frpc 拉起来；运行中会先关闭
        相关会话，有传输任务时同样二次确认。
        """
        if not server_name:
            return
        mgr = get_session_manager()
        rec = self._find_record(server_name)
        if not rec:
            self.refresh()
            return
        # frpc 未运行时不存在活动会话与传输，任务中警告只针对运行中的情况
        transferring = mgr.is_running() and \
            mgr.is_busy_on_port(rec.get("bindPort", 0))
        msg = (f"确定删除 snk 隧道「{server_name}」吗？\n"
               "删除后将从隧道列表与持久化配置中移除，下次启动不再恢复。")
        # 有任务在跑时把警告并进确认文案，而不是再叠一层对话框
        if transferring:
            msg += ("\n\n注意：该隧道上有文件传输 / 终端会话任务正在进行，\n"
                    "删除将立即中断当前任务并关闭相关会话！")
        dlg = MessageBox("删除 snk 隧道", msg, self)
        dlg.yesButton.setText("删除")
        dlg.cancelButton.setText("取消")
        if not dlg.exec():
            return
        # 删除不受 frpc 状态门控：未运行也执行（仅落盘），
        # manager 内部保证这条路径绝不拉起 frpc（P0-2：apply 部分后台化）
        def _done(result: str):
            if result == "ok":
                self.refresh()  # 显式即时刷新兜底，确保已删除的 snk 不残留
                show_info_bar(f"已删除 snk 隧道 {server_name}",
                              "success", title="删除成功", parent=self, duration=3000)
            else:
                show_info_bar(f"隧道 {server_name} 删除失败", "error",
                              title="删除失败", parent=self, duration=4000)

        mgr.delete_visitor_async(server_name, _done)

    def _on_stop_all(self):
        """全部断开：关闭全部会话、全部隧道置「已断开」并停止 frpc。

        与单条断开同口径：注册与持久化保留，下次启动仍恢复、重新连接即
        恢复启用；要彻底移除请用行内「删除」。仅在 frpc 运行中生效。
        """
        mgr = get_session_manager()
        # 只统计未断开的记录：已断开的没有会话可关，也不该挡掉「无活跃隧道」的提示
        records = [r for r in mgr.records() if not r.get("disabled")]
        if not records:
            return
        if not mgr.is_running():
            show_info_bar("当前 frpc 未启动，无活跃隧道可断开",
                          "warning", title="无法断开", parent=self, duration=3500)
            return
        if any(mgr.is_busy_on_port(r.get("bindPort", 0))
               for r in records) \
                and not self._confirm_interrupt_transfer("全部隧道"):
            return
        # 顺序固定：先趁端口还活着关掉全部会话，再整体置 disabled 并一次
        # apply（无启用隧道，apply 自动停掉 frpc 释放全部端口）；注册表
        # 保留，disabled 记录落 TOML 之外的侧车。apply 失败回滚标记，
        # 避免「列表全显示已断开但 frpc 仍在跑」裂脑。
        mgr.close_all_sessions("全部隧道")
        # 记下本次实际置位的记录，apply 失败时按这份名单回滚，而不是整表重扫
        marked = []
        for rec in records:
            info = mgr._visitors.get(str(rec.get("serverName", "")))
            if info is not None and not info.get("disabled"):
                info["disabled"] = True
                marked.append(info)
        try:
            mgr.apply()
        # 只回滚本次改过的记录：并发的其它改动不能被顺手抹掉
        except (OSError, RuntimeError):
            for info in marked:
                info["disabled"] = False
        self.refresh()  # 显式即时刷新兜底
        show_info_bar("已断开全部隧道（注册保留，可重新连接）", "success",
                      title="全部断开", parent=self, duration=3000)


# 直接运行本文件可单独起窗口调试，只依赖一个 QApplication 与亚克力补丁
if __name__ == "__main__":
    import sys
    # 先打亚克力补丁再建窗口：顺序反了窗口背景会是实心色
    import core.acrylic_patch  # noqa: F401
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TunnelPanelWindow()
    win.show()
    sys.exit(app.exec())
