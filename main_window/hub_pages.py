# -*- coding: utf-8 -*-
"""业务域容器页（Hub）—— 把原独立 FluentWindow 面板降层嵌入主窗口

导航形态（2026-09-06 需求修订）：
    ⌂ 工作台 │ ▤ 运维管理 │ ☺ 售后 │ ▶ 跑视频        底部：⚙ 设置 │ ⓘ 关于

  - ManagementHub / AftersaleHub / LedgerHub：Pivot 二级容器，
    **不再包含各自设置页**（设置统一迁入底部 SettingsHubPage）
  - 远程会话页面：二期实现（设计稿 design/remote_session_v2.html），
    本期不注册导航；SSH/SFTP/RDP 仍从工作台远程面板/会话中心进入
  - 统计图表：不单独建页，记录页工具栏按钮直开（与重构前一致）
  - SettingsHubPage：Watt Toolkit 式分组卡片设置页，收编原菜单栏全部
    Action + 三个面板的设置项
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                               QScrollArea, QSizePolicy, QLabel,
                               QStackedWidget)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, CardWidget,
                            FluentIcon, SegmentedWidget)

from main_window.pivot_page import PivotPage
from main_window.setting_cards import (SettingGroup, SettingRow, make_switch,
                                       make_combo, make_button)


# ==================== 运维管理 ====================

class ManagementHub(PivotPage):
    """运维管理：五页 Pivot（球桌/设备/健康度/组件测试/小游戏）

    「管理设置」页已迁入统一设置界面（2026-09-06）；本类保留表格平滑
    滚动的刷新方法，由主窗口把统一设置页的开关信号转发到这里。
    「健康趋势」页原面板即注释隐藏（window.py L94），保持一致不迁。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("managementHub")

        from windows.management.table_page import TablePage
        from windows.management.device_page import DevicePage
        from windows.management.health_page import HealthPage
        from windows.management.moyu_page import GamePage
        from windows.management.widget_page import TestPage

        self.table_page = TablePage(self)
        self.table_page.setObjectName("tablePage")
        self.device_page = DevicePage(self)
        self.device_page.setObjectName("devicePage")
        self.health_page = HealthPage(self)
        self.health_page.setObjectName("healthPage")
        self.test_page = TestPage(self)
        self.test_page.setObjectName("testPage")
        self.game_page = GamePage(self)
        self.game_page.setObjectName("gamePage")

        self.addPage(self.table_page, "球桌管理", FluentIcon.LIBRARY)
        self.addPage(self.device_page, "设备状态", FluentIcon.IOT)
        self.addPage(self.health_page, "设备健康度", FluentIcon.PIE_SINGLE)
        self.addPage(self.test_page, "组件测试", FluentIcon.VIEW)
        self.addPage(self.game_page, "小游戏", FluentIcon.GAME)
        self.lock_pivot_width()

    def _apply_table_smooth_all(self):
        """刷新各子页表格滚动模式（按 覆盖→全局 生效）"""
        for page in (self.table_page, self.device_page, self.health_page):
            fn = getattr(page, "_apply_smooth_mode", None)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass

    def _apply_remote_table_smooth(self):
        """刷新已打开的远程会话窗口表格（隧道列表/连接诊断）"""
        from PySide6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if type(w).__name__ in ("TunnelPanelWindow", "ConnDiagPanel"):
                fn = getattr(w, "_apply_smooth_mode", None)
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        pass


# ==================== 售后 ====================

class AftersaleHub(PivotPage):
    """售后：两页 Pivot（填写录入/记录与统计）

    「设置」页已迁入统一设置界面；周期保存后的刷新由主窗口把
    settings_hub.aftersale_cycle_saved 转发到 :meth:`reload_cycles`。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aftersaleHub")

        from windows.aftersale.entry import EntryPage
        from windows.aftersale.records import RecordsPage

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("aftersaleEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("aftersaleRecordsPage")

        self.addPage(self.entry_page, "填写录入", FluentIcon.EDIT)
        self.addPage(self.records_page, "记录与统计", FluentIcon.LIBRARY)
        self.lock_pivot_width()

    def reload_cycles(self):
        """周期设置保存成功：记录页重建周期下拉并重查（原 window.py:72-75）"""
        self.records_page._cycles_loaded = False
        self.records_page._load_cycles_then_data()

    def refresh_smooth(self):
        """表格平滑滚动开关变更后刷新记录页表格"""
        try:
            self.records_page._apply_smooth_mode()
        except Exception:
            pass

    def open_records_for_table(self, table_no: str):
        """球桌管理右键入口：跳转记录页并按桌号预筛选（原 window.py:98-102）"""
        self.records_page.set_keyword(table_no)
        self.switchTo(self.records_page)
        self.records_page.refresh_async()


# ==================== 跑视频 ====================

class LedgerHub(PivotPage):
    """跑视频：两页 Pivot（填写录入/记录与统计）；设置已迁统一设置界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ledgerHub")

        from windows.run_video.entry import EntryPage
        from windows.run_video.records import RecordsPage

        self.entry_page = EntryPage(self)
        self.entry_page.setObjectName("ledgerEntryPage")
        self.records_page = RecordsPage(self)
        self.records_page.setObjectName("ledgerRecordsPage")

        self.addPage(self.entry_page, "填写录入", FluentIcon.EDIT)
        self.addPage(self.records_page, "记录与统计", FluentIcon.LIBRARY)
        self.lock_pivot_width()

    def _apply_table_smooth_all(self):
        try:
            self.records_page._apply_smooth_mode()
        except Exception:
            pass

    def open_entry_with_context(self, ctx: dict):
        """主界面「跑视频」入口：切到填写录入页并预填会话上下文（原 window.py:54-61）"""
        self.switchTo(self.entry_page)
        self.entry_page.prefill(ctx or {})


# ==================== 设置（底部，Watt Toolkit 式分组卡片） ====================

class SettingsHubPage(QWidget):
    """统一设置页：左侧标题 + 右侧 SegmentedWidget 分页切换（Watt Toolkit 式）

    页签（2026-09-06 一期反馈：由单页长滚动改为分段切换）：
        外观 │ 性能 │ 工具 │ 数据库 │ 面板设置
        - 工具 = 快捷键与工具 + 配置文件
        - 面板设置 = 售后（周期）+ 跑视频（署名）
        - 数据库 = 管理设置整体（数据源/接口/上传/MySQL）

    信号：
        aftersale_cycle_saved —— 周期设置保存成功（主窗口转发售后记录页刷新）
        table_smooth_changed —— 任意表格平滑开关变更（主窗口转发各 Hub 刷新）
    """

    aftersale_cycle_saved = Signal()
    table_smooth_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsHub")
        self._win = parent

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        # --- 头部：左侧图标块 +「设置」标题 + 描述；右侧分段切换 ---
        header = QHBoxLayout()
        header.setSpacing(16)

        icon_card = QFrame(self)
        icon_card.setFixedSize(56, 56)
        icon_card.setStyleSheet(
            "QFrame { border-radius: 12px;"
            " background: rgba(128, 128, 128, 0.14); }")
        icon_lbl = QLabel(icon_card)
        icon_lbl.setPixmap(FluentIcon.SETTING.icon().pixmap(28, 28))
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lay = QVBoxLayout(icon_card)
        icon_lay.setContentsMargins(0, 0, 0, 0)
        icon_lay.addWidget(icon_lbl)
        header.addWidget(icon_card, 0, Qt.AlignTop)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(TitleLabel("设置", self))
        d = CaptionLabel("外观 / 性能 / 工具 / 数据库 —— 修改即时生效并持久化到 "
                         "config/ 分域配置", self)
        d.setWordWrap(True)
        title_col.addWidget(d)
        header.addLayout(title_col)
        header.addStretch(1)

        # --- 右侧 SegmentedWidget：切换下方内容页 ---
        self._seg = SegmentedWidget(self)
        self._stack = QStackedWidget(self)
        pages = (
            ("appearance", "外观", (self._group_appearance,)),
            ("perf", "性能", (self._group_perf,)),
            ("tools", "工具", (self._group_tools, self._group_files)),
            ("database", "数据库", (self._group_database,)),
            ("panels", "面板设置", (self._group_aftersale, self._group_ledger)),
        )
        self._keys = []
        for index, (key, text, builders) in enumerate(pages):
            self._keys.append(key)

            def _go(*_args, i=index):
                # SegmentedItem 继承 PivotItem，itemClicked 以位置参数回传
                # bool（版本相关），必须 *args 吞掉，否则默认值 i 被覆盖
                # → setCurrentIndex(True) → TypeError（与 hub 二级切换同坑）
                self._stack.setCurrentIndex(i)

            self._seg.addItem(routeKey=key, text=text, onClick=_go)
            self._stack.addWidget(self._make_page(builders))
        self._seg.setCurrentItem("appearance")
        header.addWidget(self._seg, 0, Qt.AlignTop)
        outer.addLayout(header)

        outer.addWidget(self._stack, 1)

    def _make_page(self, builders):
        """一组构建函数 → 独立滚动页（各页高度不同，互不牵拉）"""
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 滚动区自身不参与焦点，避免点击后 ensureVisible 自动滚动
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        body = QWidget(scroll)
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 8, 8, 8)
        lay.setSpacing(14)
        for build in builders:
            lay.addWidget(build(body))
        lay.addStretch(1)
        return scroll

    # ---------- 组构建 ----------

    def _group_appearance(self, parent):
        win = self._win
        g = SettingGroup("外观", parent)

        # 主题模式（跟随系统/浅色/深色 → 复用菜单 Action 对象回调）
        mode = win._get_theme_mode(win._load_settings())
        acts = {"auto": win._act_theme_auto, "light": win._act_theme_light,
                "dark": win._act_theme_dark}
        g.addRow(SettingRow(
            FluentIcon.SYNC, "主题模式", "跟随系统 / 浅色 / 深色",
            make_combo([("跟随系统", "auto"), ("浅色", "light"),
                        ("深色", "dark")],
                       list(acts).index(mode if mode in acts else "auto"),
                       lambda m: win._on_theme_selected(acts[m]))))

        # 主题颜色：色块 + 更改 + 还原默认
        self._color_chip = QFrame(parent)
        self._color_chip.setFixedSize(22, 22)
        self._color_chip.setStyleSheet(
            f"border-radius: 4px; background: {win._theme_color}; border: 1px"
            f" solid rgba(128,128,128,0.5);")
        g.addRow(SettingRow(
            FluentIcon.PALETTE, "主题颜色",
            "全局强调色（按钮 / 选中态 / 进度条 / 链接，即时生效）",
            [self._color_chip,
             make_button("更改…", win._on_theme_color, width=76),
             make_button("还原默认", win._on_theme_color_reset, width=88)]))

        g.addRow(SettingRow(
            FluentIcon.FONT_SIZE, "字号大小", "全局界面字号（pt）",
            make_button("更改…", win._on_font_size, width=76)))
        g.addRow(SettingRow(
            FluentIcon.FONT, "字体", "全局界面字体族",
            make_button("更改…", win._on_font_family, width=76)))
        g.addRow(SettingRow(
            FluentIcon.ZOOM, "界面缩放", "DPI 缩放百分比（重启后生效）",
            make_button("更改…", win._on_dpi_scale, width=76)))

        classic = bool(win._load_settings().get("classic_layout", True))
        g.addRow(SettingRow(
            FluentIcon.TILES, "布局模式", "面板布局（三列 Splitter）/ 经典布局",
            make_combo([("面板布局", False), ("经典布局", True)],
                       1 if classic else 0,
                       lambda v: win._on_layout_selected(
                           win._act_layout_classic if v
                           else win._act_layout_panel))))
        return g

    def _group_perf(self, parent):
        from core.perf import (is_acrylic_enabled, is_animation_enabled,
                               is_table_smooth_scroll_enabled,
                               get_table_smooth, set_acrylic_enabled,
                               set_animation_enabled,
                               set_table_smooth_scroll_enabled,
                               set_table_smooth)
        win = self._win
        g = SettingGroup("性能", parent)

        def smooth_row(title, desc, panel):
            return SettingRow(
                FluentIcon.SPEED_HIGH, title, desc,
                make_switch(
                    get_table_smooth(panel) if panel
                    else is_table_smooth_scroll_enabled(),
                    lambda v, p=panel: (
                        set_table_smooth_scroll_enabled(v) if p is None
                        else set_table_smooth(p, v),
                        self.table_smooth_changed.emit("all"))))

        g.addRow(SettingRow(
            FluentIcon.TRANSPARENT, "亚克力效果",
            "导航栏亚克力背景（截屏→高斯模糊，核显开销大，低配机器建议关闭）",
            make_switch(is_acrylic_enabled(), set_acrylic_enabled)))
        g.addRow(SettingRow(
            FluentIcon.QUIET_HOURS, "动画效果",
            "菜单弹出 / 弹窗淡入淡出（关闭后弹窗秒开）",
            make_switch(is_animation_enabled(), set_animation_enabled)))
        g.addRow(smooth_row(
            "表格平滑滚动（全局）",
            "默认关闭：大表格逐帧重绘卡顿；关闭后走原生滚动", None))
        g.addRow(smooth_row(
            "售后面板表格平滑滚动", "面板级覆盖（未开启回退全局）", "aftersale"))
        g.addRow(smooth_row(
            "跑视频面板表格平滑滚动", "面板级覆盖（未开启回退全局）", "video"))
        g.addRow(smooth_row(
            "运维管理表格平滑滚动", "球桌/设备/健康度三页生效", "management"))
        g.addRow(smooth_row(
            "远程会话表格平滑滚动", "隧道列表 / 连接诊断窗口生效", "remote"))
        return g

    def _group_tools(self, parent):
        win = self._win
        g = SettingGroup("快捷键与工具", parent)

        def row(icon, title, desc, btn_text, cb):
            return g.addRow(SettingRow(
                icon, title, desc, make_button(btn_text, cb, width=76)))

        row(FluentIcon.EDIT, "修改快捷键", "12 个全局快捷键自定义",
            "修改…", win._on_modify_shortcuts)
        row(FluentIcon.LIBRARY, "上传清单",
            "查看已收集待上传的文件（视频/日志目录/upload）",
            "查看", win._on_show_upload_list)
        row(FluentIcon.DEVELOPER_TOOLS, "连接诊断",
            "SSH/SFTP 连接日志与失败记录（含归档）",
            "打开", win._on_open_conn_diag)
        row(FluentIcon.VIDEO, "单杆视频",
            "从日志解析单杆得分，生成带计分水印的单杆视频",
            "打开", win._on_open_single_video)
        row(FluentIcon.CONNECT, "端口占用",
            "真实监听指定端口模拟服务占用（netstat 可见 LISTENING）",
            "打开", win._on_open_port_fake)
        row(FluentIcon.LIBRARY, "视频/日志批量整理",
            "按 Excel 署名筛选，批量归类视频/日志/配置文件（NewLog）",
            "打开", win._on_newlog_organize)
        return g

    def _group_database(self, parent):
        g = SettingGroup("数据库与接口", parent)
        # 原管理设置页整体迁入（数据源 / 接口1·2 账号 / 收集上传 / MySQL 配置）；
        # embedded=True 去掉内部滚动与性能卡（性能组已有同款开关），统一页统一滚动
        from windows.management.settings_page import AdminSettingsPage
        self.admin_settings = AdminSettingsPage(parent, embedded=True)
        self.admin_settings.table_smooth_changed.connect(
            lambda _v: self.table_smooth_changed.emit("all"))
        self.admin_settings.remote_smooth_changed.connect(
            lambda _v: self.table_smooth_changed.emit("all"))
        g.addWidget(self.admin_settings)
        return g

    def _group_aftersale(self, parent):
        g = SettingGroup("售后", parent)
        from windows.aftersale.settings import CycleSettingsPage
        self.cycle_page = CycleSettingsPage(parent)
        self.cycle_page.saved.connect(self.aftersale_cycle_saved.emit)
        g.addWidget(self.cycle_page)
        return g

    def _group_ledger(self, parent):
        g = SettingGroup("跑视频", parent)
        from windows.run_video.settings import SignerSettingsCard
        self.signer_card = SignerSettingsCard(parent)
        g.addWidget(self.signer_card)
        return g

    def _group_files(self, parent):
        win = self._win
        g = SettingGroup("配置文件", parent)

        def row(icon, title, desc, name):
            return g.addRow(SettingRow(
                icon, title, desc,
                make_button("打开", lambda: win._open_config_file(name),
                            width=76)))

        row(FluentIcon.FOLDER, "配置目录",
            "config/ 分域配置（settings.json 已迁移为 settings.json.bak）",
            "settings.json")
        row(FluentIcon.DOCUMENT, "cfg.json", "识别端程序配置（exe 目录）",
            "cfg.json")
        row(FluentIcon.DOCUMENT, "frpc_xtcp_panel.toml",
            "frp 持久化配置（远程会话注册表）", "frpc_xtcp_panel.toml")
        return g


# ==================== 关于（底部） ====================

class AboutPage(QWidget):
    """关于：应用信息卡"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aboutPage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)

        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(6)

        title = TitleLabel("AutoWork", card)
        v.addWidget(title)

        try:
            from core.version import APP_VERSION
        except Exception:
            APP_VERSION = "2.9"
        ver = CaptionLabel(f"版本 {APP_VERSION} · PySide6 6.11 · qfluentwidgets 1.11", card)
        v.addWidget(ver)

        desc = BodyLabel(
            "台球追踪视频控制 · 日志采集 · 球桌设备数据\n"
            "运维管理 · 售后 · 跑视频 · P2P 远程 · MySQL 双后端", card)
        desc.setWordWrap(True)
        v.addWidget(desc)

        v.addSpacing(8)
        row = QHBoxLayout()
        repo = CaptionLabel("FluentWindow 单窗口导航版 · design/fluent_window_proposal.html", card)
        row.addWidget(repo)
        row.addStretch(1)
        v.addLayout(row)

        lay.addWidget(card)
        lay.addStretch(1)
