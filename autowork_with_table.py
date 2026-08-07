# -*- coding: utf-8 -*-

################################################################################
## AutoWork - SCADA 工业监控 Dashboard UI
## 三区域布局: 左侧设备树 + 中间日志控制台 + 右侧远程控制面板
## 原 PySide6 Designer 自动生成，现已手工维护（亚克力补丁、双布局切换等）
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QEvent, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QTimer, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout,
    QMainWindow, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout,
    QWidget, QStatusBar)
from qfluentwidgets import (
    PushButton as FluentPushButton,
    PrimaryPushButton,
    ToggleButton,
    CalendarPicker,
    RadioButton,
    ListWidget,
    PlainTextEdit as FluentPlainTextEdit,
    LineEdit as FluentLineEdit,
    PasswordLineEdit,
    SpinBox as FluentSpinBox,
    FlowLayout,
    CardWidget,
    CaptionLabel,
    BodyLabel,
    ToolButton,
    FluentIcon,
    setFont,
    setCustomStyleSheet,
    isDarkTheme,
)
from qfluentwidgets.components.material.acrylic_combo_box import (
    AcrylicComboBox, AcrylicComboBoxMenu, AcrylicComboMenuActionListWidget)
from qfluentwidgets.components.material.acrylic_menu import AcrylicMenuBase
from qfluentwidgets.components.material import AcrylicSearchLineEdit
from qfluentwidgets.components.widgets.menu import MenuActionListWidget, MenuAnimationManager

from core.perf import is_acrylic_enabled, is_animation_enabled


class _VisibleAcrylicComboView(AcrylicComboMenuActionListWidget):
    """增强亚克力下拉列表视图：优化模糊半径/噪点/着色层参数，使磨砂玻璃效果清晰可见。
    亚克力开关关闭时跳过 brush 绘制，直接使用普通列表背景（即时生效）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 默认模糊半径 35，背景内容均匀色；15 保留更多背景细节
        self.acrylicBrush.setBlurRadius(15)
        # 库默认噪点不透明度 0.03；实际值 0.03 保持默认，几乎不可见
        self.acrylicBrush.noiseOpacity = 0.03

    def paintEvent(self, e):
        if not is_acrylic_enabled():
            # 亚克力关闭：绘制纯色背景替代截屏模糊（transparent 属性使
            # QSS 背景透明，不手动填充会导致下拉列表整体不可见）
            painter = QPainter(self.viewport())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(43, 43, 43) if isDarkTheme()
                             else QColor(243, 243, 243))
            painter.drawRect(self.viewport().rect())
            MenuActionListWidget.paintEvent(self, e)
            return
        super().paintEvent(e)

    def _updateAcrylicColor(self):
        """
        更新亚克力（Acrylic）材质的背景颜色。
        根据当前应用的主题（暗黑或明亮）动态调整色调（tintColor）和亮度颜色（luminosityColor），
        以确保下拉菜单的磨砂玻璃效果在不同主题下都能保持良好的可见度与视觉一致性。
        """
        if isDarkTheme():
            # 暗黑主题：使用深灰色调（RGB: 32, 32, 32，透明度 90），亮度颜色设为完全透明
            self.acrylicBrush.tintColor = QColor(32, 32, 32, 90)
            self.acrylicBrush.luminosityColor = QColor(0, 0, 0, 0)
        else:
            # 明亮主题：使用白色调（RGB: 255, 255, 255，透明度 70），亮度颜色设为完全透明
            self.acrylicBrush.tintColor = QColor(255, 255, 255, 90)
            self.acrylicBrush.luminosityColor = QColor(255, 255, 255, 0)


class _VisibleAcrylicComboMenu(AcrylicComboBoxMenu):
    """亚克力下拉菜单（增强可见度）。
    弹出时动态判断亚克力/动画开关，切换即时生效无需重启。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUpMenu(_VisibleAcrylicComboView(self))
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # PySide6/Shiboken 会将实例级 menu.exec 解析到 C++ QMenu.exec()，
        # 导致 AcrylicMenuBase.exec()（截屏→模糊）永不执行；
        # 绑定 Python 级 exec 实例属性可绕过此劫持
        _menu = self

        def _exec(pos, ani=True, aniType=None):
            from qfluentwidgets import MenuAnimationType, RoundMenu
            if aniType is None:
                aniType = (MenuAnimationType.DROP_DOWN if is_animation_enabled()
                           else MenuAnimationType.NONE)
            if not is_animation_enabled():
                # 无动画路径：绕过动画管理器，清除残留 mask，
                # 避免幽灵矩形窗口
                mgr = MenuAnimationManager.make(_menu, aniType)
                p = mgr._endPosition(pos)
                if is_acrylic_enabled():
                    _menu.view.acrylicBrush.grabImage(
                        QRect(p, _menu.layout().sizeHint()))
                _menu.clearMask()
                _menu.move(p)
                _menu.show()
                _menu.view.viewport().update()
                if _menu.isSubMenu:
                    _menu.menuItem.setSelected(True)
                return
            if is_acrylic_enabled():
                AcrylicMenuBase.exec(_menu, pos, ani=ani, aniType=aniType)
            else:
                RoundMenu.exec(_menu, pos, ani=ani, aniType=aniType)
        self.exec = _exec


class VisibleAcrylicComboBox(AcrylicComboBox):
    """亚克力下拉框：弹出列表带明显磨砂玻璃效果"""

    def _createComboMenu(self):
        return _VisibleAcrylicComboMenu(self)


def _create_combo_box(parent=None):
    """工厂函数：统一创建亚克力 ComboBox。
    亚克力/动画开关在弹出时动态判断，切换即时生效无需重启。"""
    return VisibleAcrylicComboBox(parent)


def _create_search_line_edit(parent=None):
    """工厂函数：统一创建亚克力 SearchLineEdit（其弹出行为同样受运行时开关控制）"""
    return AcrylicSearchLineEdit(parent)


def _make_separator(vertical=False):
    """创建分割线"""
    sep = QFrame()
    sep.setObjectName(u"toolbar_separator")
    sep.setFrameShape(QFrame.Shape.VLine if vertical else QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Plain)
    if vertical:
        sep.setFixedWidth(1)
        sep.setFixedHeight(24)  # 与 32px 按钮中线对齐
    else:
        sep.setFixedHeight(1)
    return sep


class _ListEmptyHint(QLabel):
    """列表空状态提示：覆盖在列表中央的灰色文字，跟随列表尺寸自动居中。
    鼠标事件穿透，不影响列表正常交互。布局切换时随列表实例一起迁移。"""

    def __init__(self, list_widget, text):
        super().__init__(list_widget)
        self.setObjectName(u"list_empty_hint")
        self.setText(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._list = list_widget
        list_widget.installEventFilter(self)
        # 供 main_window 的 _update_empty_hint() 快速定位
        list_widget._empty_hint = self
        # 列表创建时为空，默认显示提示；加载数据后由 _update_empty_hint 隐藏
        self.show()

    def eventFilter(self, obj, event):
        if obj is self._list and event.type() == QEvent.Type.Resize:
            self.setGeometry(0, 0, self._list.width(), self._list.height())
        return super().eventFilter(obj, event)


def _make_section_label(text, parent=None):
    """创建卡片模块标题标签（Fluent CaptionLabel）"""
    lbl = CaptionLabel(text, parent)
    lbl.setObjectName(u"section_label")
    lbl.setFixedHeight(24)
    lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    return lbl


class _ToolbarRadioButton(RadioButton):
    """工具栏专用单选按钮：强制 32px 行高，与 Fluent 按钮中线对齐。
    通过 setCustomStyleSheet 将高度 QSS 注册到 qfluentwidgets 主题管理器，
    setTheme() 切换主题时会自动重新追加，不会被内部 QSS 覆盖。"""

    _HEIGHT_QSS = "QRadioButton { min-height: 32px; max-height: 32px; }"

    def __init__(self, parent=None):
        super().__init__(parent)
        setCustomStyleSheet(self, self._HEIGHT_QSS, self._HEIGHT_QSS)


class _FlowScrollArea(QScrollArea):
    """工具栏专用滚动区域：根据自身宽度主动计算内容高度并锁定，
    保证父布局精确按内容高度分配空间（单行=单行高，折行=多行高，超上限滚动）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 高度上限（约 3 行控件），setFixedHeight 会覆盖 maximumHeight，
        # 因此用独立变量保存上限，避免窗口反复缩放时上限被“棘轮”压低
        self._height_cap = self.maximumHeight()
        # 垂直策略设为 Preferred（配合 HFW 标志，作为首次布局的兜底）
        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def setMaximumHeight(self, h):
        """同步更新高度上限"""
        self._height_cap = h
        super().setMaximumHeight(h)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        w = self.widget()
        if w is None:
            return super().heightForWidth(width)
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(0, width - margins.left() - margins.right() - sb_w)
        h = w.heightForWidth(inner_w)
        return min(h, self._height_cap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def _adjust_height(self):
        """按当前宽度计算流式内容实际高度，锁定自身高度，下方内容紧贴无空白"""
        w = self.widget()
        if w is None or self.width() <= 0:
            return
        margins = self.contentsMargins()
        sb = self.verticalScrollBar()
        sb_w = sb.width() if sb.isVisible() else 0
        inner_w = max(1, self.width() - margins.left() - margins.right() - sb_w)
        h = min(w.heightForWidth(inner_w), self._height_cap)
        if h > 0 and h != self.height():
            self.setFixedHeight(h)


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        # 设置窗口大小以及窗口最小大小
        MainWindow.resize(1440, 900)
        MainWindow.setMinimumSize(QSize(640, 400))

        # ===== Central Widget =====
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.centralwidget.setEnabled(True)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.centralwidget.sizePolicy().hasHeightForWidth())
        self.centralwidget.setSizePolicy(sizePolicy)
        self.centralwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        # 主垂直布局: 工具栏 + 三区域内容
        self.verticalLayout_2 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setSpacing(0)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)

        # 顶部工具栏
        self._build_toolbar()

        # 三区域 Splitter: 左侧设备树 | 中间日志控制台 | 右侧远程面板
        self._build_lists_and_containers()

        # 右侧: 远程控制面板
        self._build_p2p_panel()

        self.verticalLayout_2.addLayout(self.horizontalLayout_main)

        # 将中心控件挂到容器布局（兼容 QMainWindow 和 FluentWindowBase 容器模式）
        if hasattr(MainWindow, 'setCentralWidget'):
            MainWindow.setCentralWidget(self.centralwidget)
        else:
            # FluentWindowBase 模式：必须挂到 vBoxLayout（带 48px 标题栏预留的垂直布局），
            # 而非 MainWindow.layout()（hBoxLayout）——后者会导致内容绕过标题栏预留区，
            # 与菜单栏水平并排，造成顶部三行（标题栏/菜单栏/工具栏）挤压重叠
            MainWindow.vBoxLayout.addWidget(self.centralwidget)

        self.retranslateUi(MainWindow)

        # 注意：不能调用 QMetaObject.connectSlotsByName(MainWindow)
        # 它会按 on_<objectName>_<signal>_<signal> 规则自动连接 on_end_clicked / on_flush_clicked 等槽，
        # 与 connect_signals() 中的手动连接重复，导致按钮点击触发两次。
    # setupUi

    def _build_toolbar(self):
        """构建顶部工具栏 — FlowLayout 流式布局（窗口缩窄时控件自动换行）"""
        # ============================================================
        # 顶部工具栏 — FlowLayout 流式布局（窗口缩窄时控件自动换行，严禁重叠）
        # 包裹在 QScrollArea 中：折行过多时可上下滚动，不挤压下方日志区域
        # ============================================================
        self.toolbar_scroll = _FlowScrollArea(self.centralwidget)
        self.toolbar_scroll.setObjectName(u"toolbar_scroll")
        self.toolbar_scroll.setWidgetResizable(True)
        self.toolbar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 最多展示约 3 行控件（单行约 44px），超出部分滚动查看
        self.toolbar_scroll.setMaximumHeight(132)

        self.toolbar_widget = QWidget()
        self.toolbar_widget.setObjectName(u"toolbar_widget")
        self.horizontalLayout = FlowLayout(self.toolbar_widget)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setHorizontalSpacing(6)
        self.horizontalLayout.setVerticalSpacing(6)
        self.horizontalLayout.setContentsMargins(10, 6, 10, 6)

        # --- 组1: 文件操作 ---
        self.flush = ToolButton(FluentIcon.SYNC, self.toolbar_widget)
        self.flush.setObjectName(u"flush")
        self.flush.setFixedSize(32, 32)
        self.horizontalLayout.addWidget(self.flush)

        self.date = CalendarPicker(self.toolbar_widget)
        self.date.setObjectName(u"date")
        self.date.setMinimumWidth(150)
        self.date.setDate(QDate(2000, 10, 7))
        self.horizontalLayout.addWidget(self.date)

        # 日期步进键（复用运维面板 DevicePage 模式：前一天/后一天）
        self.date_prev = ToolButton(FluentIcon.LEFT_ARROW, self.toolbar_widget)
        self.date_prev.setObjectName(u"date_prev")
        self.date_prev.setFixedSize(26, 32)
        self.horizontalLayout.addWidget(self.date_prev)
        self.date_next = ToolButton(FluentIcon.RIGHT_ARROW, self.toolbar_widget)
        self.date_next.setObjectName(u"date_next")
        self.date_next.setFixedSize(26, 32)
        self.horizontalLayout.addWidget(self.date_next)

        self.table_panel_btn = FluentPushButton(self.toolbar_widget)
        self.table_panel_btn.setObjectName(u"table_panel_btn")
        self.horizontalLayout.addWidget(self.table_panel_btn)

        self.write_table = FluentPushButton(self.toolbar_widget)
        self.write_table.setObjectName(u"write_table")
        self.horizontalLayout.addWidget(self.write_table)

        # 「配置」按钮已迁移至第二行菜单栏「配置」下拉菜单，此处不再创建

        self.horizontalLayout.addWidget(_make_separator(vertical=True))

        # --- 组2: 程序配置 ---
        self.label_2 = QLabel(self.toolbar_widget)
        self.label_2.setObjectName(u"label_2")
        self.label_2.setFixedHeight(32)
        self.label_2.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        setFont(self.label_2, 11)
        self.horizontalLayout.addWidget(self.label_2)

        self.choose_exe = _create_combo_box(self.toolbar_widget)
        self.choose_exe.setObjectName(u"choose_exe")
        self.choose_exe.setMinimumWidth(145)
        self.horizontalLayout.addWidget(self.choose_exe)

        # 帧控制（_ToolbarRadioButton 强制 32px 行高与按钮中线对齐）
        self.input_frame_before = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_before.setObjectName(u"input_frame_before")
        self.input_frame_before.setChecked(True)
        self.horizontalLayout.addWidget(self.input_frame_before)

        self.input_frame_set = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_set.setObjectName(u"input_frame_set")
        self.horizontalLayout.addWidget(self.input_frame_set)

        self.input_frame_custom = _ToolbarRadioButton(self.toolbar_widget)
        self.input_frame_custom.setObjectName(u"input_frame_custom")
        self.horizontalLayout.addWidget(self.input_frame_custom)

        self.input_frame = FluentLineEdit(self.toolbar_widget)
        self.input_frame.setObjectName(u"input_frame")
        self.input_frame.setFixedWidth(75)
        self.horizontalLayout.addWidget(self.input_frame)

        self.horizontalLayout.addWidget(_make_separator(vertical=True))

        # --- 组3: 播放控制 ---
        self.open_daily = FluentPushButton(self.toolbar_widget)
        self.open_daily.setObjectName(u"open_daily")
        self.horizontalLayout.addWidget(self.open_daily)

        self.start = PrimaryPushButton(self.toolbar_widget)
        self.start.setObjectName(u"start")
        self.horizontalLayout.addWidget(self.start)

        self.pause_btn = FluentPushButton(self.toolbar_widget)
        self.pause_btn.setObjectName(u"pause_btn")
        self.pause_btn.setText("暂停")
        self.horizontalLayout.addWidget(self.pause_btn)

        self.start_three_btn = FluentPushButton(self.toolbar_widget)
        self.start_three_btn.setObjectName(u"start_three_btn")
        self.horizontalLayout.addWidget(self.start_three_btn)

        self.p2p_btn = ToggleButton(self.toolbar_widget)
        self.p2p_btn.setObjectName(u"p2p_btn")
        self.p2p_btn.setMaximumWidth(72)
        self.horizontalLayout.addWidget(self.p2p_btn)

        self.toolbar_scroll.setWidget(self.toolbar_widget)
        self.verticalLayout_2.addWidget(self.toolbar_scroll)

    def _build_lists_and_containers(self):
        """构建三区域 Splitter: 左侧设备树 | 中间日志控制台 | 右侧远程面板"""
        self.horizontalLayout_main = QHBoxLayout()
        self.horizontalLayout_main.setObjectName(u"horizontalLayout_main")
        self.horizontalLayout_main.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_main.setSpacing(0)

        # 创建核心控件（两套布局共享，切换时复用实例）
        self.id_list = ListWidget()
        self.id_list.setObjectName(u"id_list")

        # 设备搜索框（位于 id_list 正下方，默认隐藏，Ctrl+F 显示）
        self.id_search = _create_search_line_edit()
        self.id_search.setObjectName(u"id_search")
        self.id_search.setPlaceholderText("搜索设备代码...")
        self.id_search.setClearButtonEnabled(True)
        self.id_search.setVisible(False)

        # 容器：id_list + 搜索框，布局切换时随 id_list 一起迁移
        self.id_list_container = QWidget()
        self.id_list_container.setObjectName(u"id_list_container")
        _id_container_layout = QVBoxLayout(self.id_list_container)
        _id_container_layout.setContentsMargins(0, 0, 0, 0)
        _id_container_layout.setSpacing(0)
        _id_container_layout.addWidget(self.id_list, 1)
        _id_container_layout.addWidget(self.id_search)

        self.local_video_list = ListWidget()
        self.local_video_list.setObjectName(u"local_video_list")

        # 空状态提示（列表为空时中央显示灰色文字）
        _ListEmptyHint(self.id_list, "暂无设备\n请检查 videos 目录")
        _ListEmptyHint(self.local_video_list, "请选择设备")

        # 日志文件搜索框（位于 local_video_list 正下方，默认隐藏，Ctrl+F 显示）
        self.video_search = _create_search_line_edit()
        self.video_search.setObjectName(u"video_search")
        self.video_search.setPlaceholderText("搜索日志文件...")
        self.video_search.setClearButtonEnabled(True)
        self.video_search.setVisible(False)

        # 容器：local_video_list + 搜索框，布局切换时随列表一起迁移
        self.video_list_container = QWidget()
        self.video_list_container.setObjectName(u"video_list_container")
        _video_container_layout = QVBoxLayout(self.video_list_container)
        _video_container_layout.setContentsMargins(0, 0, 0, 0)
        _video_container_layout.setSpacing(0)
        _video_container_layout.addWidget(self.local_video_list, 1)
        _video_container_layout.addWidget(self.video_search)

        self.log_list = ListWidget()
        self.log_list.setObjectName(u"log_list")
        _ListEmptyHint(self.log_list, "请选择日志文件")

        self.show_log = FluentPlainTextEdit()
        self.show_log.setObjectName(u"show_log")
        self.show_log.setReadOnly(True)
        self.show_log.setFont(QFont("Consolas", 10))
        # 隐藏 Fluent EditLayer 覆盖层，避免聚焦时绘制主题色底边
        self.show_log.layer.hide()

        # 日志顶部状态条（仅新版布局显示）
        self.log_status_bar = QWidget()
        self.log_status_bar.setObjectName(u"log_status_bar")
        self.log_status_bar.setFixedHeight(24)
        log_status_layout = QHBoxLayout(self.log_status_bar)
        log_status_layout.setContentsMargins(8, 2, 8, 2)
        log_status_layout.setSpacing(12)
        self.log_status_device = QLabel("设备: --")
        self.log_status_device.setObjectName(u"log_status_device")
        log_status_layout.addWidget(self.log_status_device)
        self.log_status_count = QLabel("日志: 0 条")
        self.log_status_count.setObjectName(u"log_status_count")
        log_status_layout.addWidget(self.log_status_count)
        log_status_layout.addStretch()

        # 构建默认布局（新版）
        self._is_classic_layout = False
        self._build_modern_content()

        self.horizontalLayout_main.addWidget(self.splitter)

    def _build_p2p_panel(self):
        """构建右侧远程控制面板 (Fluent 卡片模块化, 默认隐藏)"""
        # ===== 右侧: 远程控制面板 (Fluent 卡片模块化, 默认隐藏) =====
        self.p2p_panel = CardWidget(self.centralwidget)
        self.p2p_panel.setObjectName(u"p2p_panel")
        p2p_main_layout = QVBoxLayout(self.p2p_panel)
        p2p_main_layout.setContentsMargins(10, 10, 10, 10)
        p2p_main_layout.setSpacing(8)

        # 面板标题（Fluent BodyLabel）
        p2p_header = BodyLabel("远程控制面板", self.p2p_panel)
        p2p_header.setObjectName(u"p2p_panel_header")
        p2p_header.setFixedHeight(18)
        p2p_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p2p_main_layout.addWidget(p2p_header)

        # --- 模块1: 服务器/网络 ---
        # 分区标题随连接模式切换文本（XTCP=visitors，TCP=保存的服务器），见 main.py _update_p2p_visibility
        # 控件保留创建（_update_p2p_visibility 会 setText），仅暂时隐藏腾出列表空间（Task #51）
        self.p2p_server_section_label = _make_section_label("◎ 服务器 / visitors")
        # p2p_main_layout.addWidget(self.p2p_server_section_label)

        self.p2p_visitor_list = ListWidget(self.p2p_panel)
        self.p2p_visitor_list.setObjectName(u"p2p_visitor_list")
        # 只保底下限（约3行），不设上限：列表随窗口高度自由伸缩，
        # 并通过 setStretchFactor 优先吸收剩余垂直空间
        self.p2p_visitor_list.setMinimumHeight(90)
        p2p_main_layout.addWidget(self.p2p_visitor_list)
        p2p_main_layout.setStretchFactor(self.p2p_visitor_list, 1)

        # 远程面板搜索框（常驻显示，实时过滤 visitor/服务器列表）
        self.p2p_search = _create_search_line_edit(self.p2p_panel)
        self.p2p_search.setObjectName(u"p2p_search")
        self.p2p_search.setPlaceholderText("搜索服务器...")
        self.p2p_search.setClearButtonEnabled(True)
        p2p_main_layout.addWidget(self.p2p_search)

        p2p_list_btn_layout = QHBoxLayout()
        # 添加/删除按钮样式与连接/断开成组一致：添加=Primary 蓝色（仿连接）、删除=普通灰（仿断开）
        self.p2p_add_btn = PrimaryPushButton("添加")
        self.p2p_add_btn.setObjectName(u"p2p_add_btn")
        self.p2p_delete_btn = FluentPushButton("删除")
        self.p2p_delete_btn.setObjectName(u"p2p_delete_btn")
        p2p_list_btn_layout.addWidget(self.p2p_add_btn)
        p2p_list_btn_layout.addWidget(self.p2p_delete_btn)
        p2p_main_layout.addLayout(p2p_list_btn_layout)

        # XTCP 表单
        self.p2p_form_server = FluentLineEdit(self.p2p_panel)
        self.p2p_form_server.setObjectName(u"p2p_form_server")
        self.p2p_form_server.setPlaceholderText("snk_xxxx")
        self.p2p_form_port = FluentSpinBox(self.p2p_panel)
        self.p2p_form_port.setObjectName(u"p2p_form_port")
        self.p2p_form_port.setRange(1024, 65535)
        self.p2p_form_key = FluentLineEdit(self.p2p_panel)
        self.p2p_form_key.setObjectName(u"p2p_form_key")
        self.p2p_form_key.setText("abc123")

        self.p2p_xtcp_form = QFormLayout()
        self.p2p_xtcp_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_xtcp_form.addRow("serverName:", self.p2p_form_server)
        self.p2p_xtcp_form.addRow("bindPort:", self.p2p_form_port)
        self.p2p_xtcp_form.addRow("secretKey:", self.p2p_form_key)
        p2p_main_layout.addLayout(self.p2p_xtcp_form)

        # XTCP 专属控件列表（visitor 列表与添加/删除按钮为两种模式共用，不在此列：
        # TCP 模式下该列表复用为"保存的服务器"，见 main.py _update_p2p_visibility）
        self.p2p_xtcp_widgets = [
            self.p2p_form_server, self.p2p_form_port, self.p2p_form_key
        ]

        # 连接/断开按钮（XTCP 专属：TCP 模式由各功能按钮点击时直连，无需统一连接入口）
        # 用容器包裹以便 TCP 模式下整体隐藏，避免留下空白布局间隙
        self.p2p_conn_widget = QWidget(self.p2p_panel)
        self.p2p_conn_widget.setObjectName(u"p2p_conn_widget")
        p2p_conn_layout = QHBoxLayout(self.p2p_conn_widget)
        p2p_conn_layout.setContentsMargins(0, 0, 0, 0)
        self.p2p_connect_btn = PrimaryPushButton("连接")
        self.p2p_connect_btn.setObjectName(u"p2p_connect_btn")
        self.p2p_disconnect_btn = FluentPushButton("断开")
        self.p2p_disconnect_btn.setObjectName(u"p2p_disconnect_btn")
        p2p_conn_layout.addWidget(self.p2p_connect_btn)
        p2p_conn_layout.addWidget(self.p2p_disconnect_btn)
        p2p_main_layout.addWidget(self.p2p_conn_widget)

        # 分割线
        p2p_main_layout.addWidget(_make_separator())

        # --- 模块2: 权限与配置 ---
        # 分区标题暂时隐藏腾出列表空间（Task #51）
        # p2p_main_layout.addWidget(_make_section_label("◎ 权限与配置"))

        mode_layout = QHBoxLayout()
        mode_label = CaptionLabel("连接方式:", self.p2p_panel)
        mode_label.setObjectName(u"p2p_mode_label")
        mode_layout.addWidget(mode_label)
        self.p2p_mode_combo = _create_combo_box(self.p2p_panel)
        self.p2p_mode_combo.setObjectName(u"p2p_mode_combo")
        self.p2p_mode_combo.addItems(["XTCP", "TCP"])
        mode_layout.addWidget(self.p2p_mode_combo)
        p2p_main_layout.addLayout(mode_layout)

        # TCP 表单
        self.p2p_ssh_host = FluentLineEdit(self.p2p_panel)
        self.p2p_ssh_host.setObjectName(u"p2p_ssh_host")
        self.p2p_ssh_host.setPlaceholderText("127.0.0.1")
        self.p2p_ssh_port = FluentSpinBox(self.p2p_panel)
        self.p2p_ssh_port.setObjectName(u"p2p_ssh_port")
        self.p2p_ssh_port.setRange(1, 65535)
        self.p2p_ssh_port.setValue(22)
        self.p2p_ssh_user = FluentLineEdit(self.p2p_panel)
        self.p2p_ssh_user.setObjectName(u"p2p_ssh_user")
        self.p2p_ssh_user.setText("newbv")
        self.p2p_ssh_pass = PasswordLineEdit(self.p2p_panel)
        self.p2p_ssh_pass.setObjectName(u"p2p_ssh_pass")
        # 密码不在此硬编码，由 _init_p2p_panel 从 settings.json 的 ssh_pass 读取填充
        self.p2p_ssh_pass.setPlaceholderText("请输入密码")

        self.p2p_ssh_form = QFormLayout()
        self.p2p_ssh_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.p2p_ssh_form.addRow("host:", self.p2p_ssh_host)
        self.p2p_ssh_form.addRow("port:", self.p2p_ssh_port)
        self.p2p_ssh_form.addRow("账号:", self.p2p_ssh_user)
        self.p2p_ssh_form.addRow("密码:", self.p2p_ssh_pass)
        p2p_main_layout.addLayout(self.p2p_ssh_form)

        # host/port 随模式切换显隐（账号/密码始终可见）
        self.p2p_ssh_widgets = [
            self.p2p_ssh_host, self.p2p_ssh_port,
        ]
        for w in self.p2p_ssh_widgets:
            w.setVisible(False)
        for row_idx in range(2):
            lbl_item = self.p2p_ssh_form.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
            if lbl_item and lbl_item.widget():
                lbl_item.widget().setVisible(False)

        # 分割线
        p2p_main_layout.addWidget(_make_separator())

        # --- 模块3: 高级功能 ---
        # 分区标题暂时隐藏腾出列表空间（Task #51）
        # p2p_main_layout.addWidget(_make_section_label("◎ 功能"))

        self.p2p_sftp_btn = FluentPushButton("文件管理")
        self.p2p_sftp_btn.setObjectName(u"p2p_sftp_btn")
        self.p2p_sftp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_sftp_btn)

        self.p2p_ssh_terminal_btn = FluentPushButton("SSH 终端")
        self.p2p_ssh_terminal_btn.setObjectName(u"p2p_ssh_terminal_btn")
        self.p2p_ssh_terminal_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_ssh_terminal_btn)

        self.p2p_rdp_btn = FluentPushButton("远程桌面")
        self.p2p_rdp_btn.setObjectName(u"p2p_rdp_btn")
        self.p2p_rdp_btn.setEnabled(False)
        p2p_main_layout.addWidget(self.p2p_rdp_btn)

        # 注意：尾部不再 addStretch()——剩余垂直空间全部由 p2p_visitor_list 吸收
        # （见上方 setStretchFactor），表单/按钮固定贴底，列表随窗口高度自由伸缩
        # 面板宽度 290px：保证表单字段（serverName/密码等）有足够呼吸空间
        self.p2p_panel.setFixedWidth(290)
        self.p2p_panel.setVisible(False)

        self.horizontalLayout_main.addWidget(self.p2p_panel)

    def retranslateUi(self, MainWindow):
        if hasattr(MainWindow, 'setCentralWidget'):  # 仅对真正的窗口设置标题
            MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"AutoWork", None))
        self.flush.setToolTip(QCoreApplication.translate("MainWindow", u"刷新（重新扫描程序与设备列表）", None))
        self.date.setDateFormat(QCoreApplication.translate("MainWindow", u"yyyy-MM-dd", None))
        self.date_prev.setToolTip(QCoreApplication.translate("MainWindow", u"前一天", None))
        self.date_next.setToolTip(QCoreApplication.translate("MainWindow", u"后一天", None))
        self.table_panel_btn.setText(QCoreApplication.translate("MainWindow", u"球桌管理", None))
        self.write_table.setText(QCoreApplication.translate("MainWindow", u"打开目录", None))
        self.label_2.setText(QCoreApplication.translate("MainWindow", u"程序:", None))
        self.input_frame_before.setText(QCoreApplication.translate("MainWindow", u"帧前", None))
        self.input_frame_set.setText(QCoreApplication.translate("MainWindow", u"帧后", None))
        self.input_frame_custom.setText(QCoreApplication.translate("MainWindow", u"自定义", None))
        self.input_frame.setText(QCoreApplication.translate("MainWindow", u"400", None))
        self.open_daily.setText(QCoreApplication.translate("MainWindow", u"CPP日志", None))
        self.start.setText(QCoreApplication.translate("MainWindow", u"播放", None))
        self.pause_btn.setText(QCoreApplication.translate("MainWindow", u"暂停", None))
        self.start_three_btn.setText(QCoreApplication.translate("MainWindow", u"启动三端", None))
        self.p2p_btn.setText(QCoreApplication.translate("MainWindow", u"远程", None))
    # retranslateUi

    # ================================================================
    # 布局构建与切换
    # ================================================================

    def _build_modern_content(self):
        """新版布局: 左侧嵌套Splitter(设备|文件 + 日志) | 中间日志控制台"""
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self.splitter.setObjectName(u"splitter")

        # ===== 左侧面板 =====
        self.left_panel = QWidget()
        self.left_panel.setObjectName(u"left_panel")
        left_outer = QVBoxLayout(self.left_panel)
        left_outer.setContentsMargins(0, 0, 0, 0)
        left_outer.setSpacing(0)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setObjectName(u"left_splitter")

        # 上方: 水平 Splitter，设备列表 | 文件列表
        self.left_top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_top_splitter.setObjectName(u"left_top_splitter")

        id_widget = QWidget()
        id_layout = QVBoxLayout(id_widget)
        id_layout.setContentsMargins(0, 0, 0, 0)
        id_layout.setSpacing(0)
        id_header = CaptionLabel("  设备", id_widget)
        id_header.setObjectName(u"left_panel_header")
        id_header.setFixedHeight(26)
        id_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        id_layout.addWidget(id_header)
        id_layout.addWidget(self.id_list_container, 1)
        self.left_top_splitter.addWidget(id_widget)

        file_widget = QWidget()
        file_layout = QVBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)
        file_header = CaptionLabel("  文件 / 日志", file_widget)
        file_header.setObjectName(u"left_panel_header")
        file_header.setFixedHeight(26)
        file_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        file_layout.addWidget(file_header)
        file_layout.addWidget(self.video_list_container, 1)
        self.left_top_splitter.addWidget(file_widget)

        self.left_top_splitter.setSizes([100, 120])
        self.left_splitter.addWidget(self.left_top_splitter)

        # 下方: 日志内容列表
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        log_header = CaptionLabel("  日志内容", log_widget)
        log_header.setObjectName(u"left_panel_header")
        log_header.setFixedHeight(26)
        log_header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        log_layout.addWidget(log_header)
        log_layout.addWidget(self.log_list, 1)
        self.left_splitter.addWidget(log_widget)

        self.left_splitter.setSizes([350, 650])
        left_outer.addWidget(self.left_splitter, 1)
        self.splitter.addWidget(self.left_panel)

        # ===== 中间: 日志控制台 =====
        self.center_panel = QWidget()
        self.center_panel.setObjectName(u"center_panel")
        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.log_status_bar)
        center_layout.addWidget(self.show_log, 1)
        self.splitter.addWidget(self.center_panel)

        self.splitter.setSizes([400, 600])
        self._is_classic_layout = False

    def _build_classic_content(self):
        """经典布局: 四列水平Splitter（设备|文件|日志内容|日志控制台）"""
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.centralwidget)
        self.splitter.setObjectName(u"splitter")

        self.splitter.addWidget(self.id_list_container)
        self.splitter.addWidget(self.video_list_container)
        self.splitter.addWidget(self.log_list)
        self.splitter.addWidget(self.show_log)

        self.splitter.setSizes([80, 150, 300, 500])
        self._is_classic_layout = True

    def switch_layout(self, classic=False):
        """切换布局模式，复用核心控件实例（保留数据和信号连接）"""
        if classic == self._is_classic_layout:
            return

        # 1. 将核心控件从旧布局中脱离（设置 parent=None 防止被旧 splitter 删除）
        # id_list 与其搜索框同在 id_list_container 内，迁移容器即可一并带走
        self.id_list_container.setParent(None)
        self.video_list_container.setParent(None)
        self.log_list.setParent(None)
        self.show_log.setParent(None)
        self.log_status_bar.setParent(None)

        # 2. 从 horizontalLayout_main 中移除旧 splitter 并删除
        self.horizontalLayout_main.removeWidget(self.splitter)
        self.splitter.deleteLater()

        # 3. 构建新布局
        if classic:
            self._build_classic_content()
        else:
            self._build_modern_content()

        # 4. 将新 splitter 插入到 horizontalLayout_main 的最前面（p2p_panel 之前）
        self.horizontalLayout_main.insertWidget(0, self.splitter)
