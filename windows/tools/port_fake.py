# -*- coding: utf-8 -*-
"""工具：虚假端口占用（模拟服务在监听）

真实 bind + listen 指定端口并保持，netstat -ano / 任务管理器等外部
检测视角均为 LISTENING，模拟「本机有服务在运行」；支持多端口同时
占用与一键随机端口，关闭弹窗时自动释放（进程退出 socket 亦由系统回收）。
本工具唯一要保证的是外部检测一致：只 bind 不 listen 时 netstat 里没有条目，模拟立刻穿帮。
"""
import logging
import random
import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, FluentIcon, PushButton,
                            ToolButton)

from core.spin_fit_patch import FittedSpinBox
from core.utils import show_info_bar

logger = logging.getLogger(__name__)


class PortFakeWidget(QWidget):
    """虚假端口占用工具：真实监听端口，随时占用/释放

    端口占用状态以 _sockets 为唯一真相源，列表项与按钮可用性都从它派生。"""

    # 随机段取 20000-60000：低位是系统服务与知名端口，再往上贴近 65535 容易踩边界。
    _RANDOM_MIN, _RANDOM_MAX = 20000, 60000

    def __init__(self, parent=None):
        """搭界面：预填一个可用端口，此刻还没有任何端口被占用。"""
        super().__init__(parent)
        # 字典同时是唯一数据源：列表项、按钮状态都从它派生，不再维护第二份计数。
        self._sockets = {}  # port -> socket，保持引用防止 GC 关闭

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        tip = CaptionLabel(
            "点击「占用」后端口将被真实监听，netstat -ano 显示 LISTENING，"
            "模拟本机有服务在监听；可同时占用多个端口，关闭弹窗时自动释放。", self)
        # 提示文案偏长，必须允许换行，否则窄窗口下会被截断。
        tip.setWordWrap(True)
        v.addWidget(tip)

        row = QHBoxLayout()
        self._spin = FittedSpinBox(self)
        # 下限写 1 而不是 0：填 0 会被内核当成「随机分配」，用户看到的端口与实际监听的对不上。
        self._spin.setRange(1, 65535)
        # 打开就预填一个可用端口：多数场景用户要的就是随机端口，省掉一次点击。
        self._spin.setValue(self._random_port())
        btn_random = ToolButton(FluentIcon.SYNC, self)
        btn_random.setToolTip("随机可用端口")
        btn_random.clicked.connect(self._pick_random_port)
        self._btn_occupy = PushButton("占用", self)
        self._btn_occupy.clicked.connect(self._occupy)
        self._btn_release = PushButton(FluentIcon.DELETE, "全部释放", self)
        # 没有占用时禁用「全部释放」：否则点了没反应，还弹一句「已释放」的成功提示。
        self._btn_release.setEnabled(False)
        self._btn_release.clicked.connect(self._release_all)
        row.addWidget(self._spin)
        row.addWidget(btn_random)
        row.addWidget(self._btn_occupy)
        row.addStretch(1)
        row.addWidget(self._btn_release)
        v.addLayout(row)

        # 列表占满剩余高度：端口多时靠滚动，不去挤上方控件。
        self._list = QListWidget(self)
        self._list.setAlternatingRowColors(True)
        v.addWidget(self._list, 1)

    # ---------- 端口操作 ----------

    def _random_port(self) -> int:
        """探测一个当前可绑定的随机端口

        最多试 20 次就退回纯随机：极端情况下也不能让 UI 卡在探测循环里。"""
        for _ in range(20):
            port = random.randint(self._RANDOM_MIN, self._RANDOM_MAX)
            # 每个候选各起一个探测 socket：bind 成功即证端口空闲，随即关闭交给真正的监听。
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", port))
                s.close()
                return port
            except OSError:
                # 失败也要关掉探测 socket：每轮漏一个句柄，20 轮就攒下 20 个。
                s.close()
        return random.randint(self._RANDOM_MIN, self._RANDOM_MAX)

    def _pick_random_port(self):
        """只改输入框、不自动占用：随机按钮的语义是「换个候选」，占用仍需用户确认。"""
        self._spin.setValue(self._random_port())

    def _occupy(self):
        """占用输入框端口：bind + listen 保持监听

        失败时日志与提示条各写一份：弹窗一关就查不到原因了。"""
        port = self._spin.value()
        # 先自己判重：Windows 下 SO_REUSEADDR 允许同一端口重复 bind，会出现两个 socket 抢同一端口。
        if port in self._sockets:
            show_info_bar(f"端口 {port} 已在占用中", "warning",
                          title="提示", parent=self, duration=2000)
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # SO_REUSEADDR 让上次关闭留下的 TIME_WAIT 不至于让端口在几分钟内不可再占。
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            # 必须 listen 才进入 LISTEN 状态：只 bind 时 netstat 看不到条目，模拟就失效了。
            s.listen(5)
            # 本工具不 accept，设非阻塞是防止将来误加 accept 调用把 UI 线程卡死。
            s.setblocking(False)
        except OSError as e:
            # 失败先关掉半开的 socket：留着会让下次占用同一端口报 10048。
            s.close()
            logger.warning("占用端口 %s 失败: %s", port, e)
            show_info_bar(f"端口 {port} 占用失败：{e.strerror or e}", "error",
                          title="失败", parent=self, duration=3000)
            return
        # 存进字典才有人持有引用：局部变量一回收 socket 就被 GC 关掉，监听凭空消失。
        self._sockets[port] = s
        self._append_item(port)
        # 有占用才允许整体释放，与 __init__ 里的初始禁用对称。
        self._btn_release.setEnabled(True)
        show_info_bar(f"端口 {port} 已占用（LISTENING）", "success",
                      title="占用成功", parent=self, duration=2500)

    def _release_all(self, notify=True):
        """释放全部占用端口

        notify=False 是 closeEvent 的用法：窗口已经在关，再弹提示条只会闪一下。"""
        # 先复制键列表：边遍历边删字典会 RuntimeError。
        for port in list(self._sockets):
            self._release_one(port)
        # 列表整块清空，不走逐条删除，避免几十行时触发几十次重绘。
        self._list.clear()
        # 列表已空，按钮回禁用态：再点没有意义。
        self._btn_release.setEnabled(False)
        if notify:
            show_info_bar("已释放全部端口", "success",
                          title="释放成功", parent=self, duration=2000)

    def _release_one(self, port: int):
        """释放单个端口：先摘字典再关句柄，任何一步失败都不该阻塞其余端口。"""
        # 先 pop 再 close：即使 close 抛错，条目也已从字典移除，不会留下僵尸行。
        s = self._sockets.pop(port, None)
        if s is not None:
            try:
                s.close()
            except OSError:
                # close 失败已无补救手段（句柄交给系统回收），吞掉即可，不该冒泡到 UI。
                pass

    def _append_item(self, port: int):
        """建一行列表项，并把端口号存进 UserRole 供反查。"""
        item = QListWidgetItem(f"端口 {port}    LISTENING（模拟服务监听中）")
        # 端口存进 UserRole：以后做单条释放时不必去解析显示文本。
        item.setData(Qt.ItemDataRole.UserRole, port)
        self._list.addItem(item)

    # ---------- 生命周期 ----------

    def closeEvent(self, e):
        """页面/窗口关闭时释放全部端口，避免占用残留

        进程被强杀的情况覆盖不到，那时 socket 由系统回收，属于可接受残留。"""
        self._release_all(notify=False)
        super().closeEvent(e)
