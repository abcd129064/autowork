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
from PySide6.QtGui import QFontDatabase, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame,
                               QScrollArea, QSizePolicy,
                               QStackedWidget)
from qfluentwidgets import (TitleLabel, CaptionLabel, BodyLabel, CardWidget,
                            FluentIcon, SegmentedWidget, CheckableMenu,
                            ComboBox, LineEdit, SwitchButton, PushButton)

from main_window.pivot_page import PivotPage
from main_window.setting_cards import (SettingGroup, SettingRow, make_switch,
                                       make_combo, make_button, make_spinbox)


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

    def apply_auto_refresh(self):
        """自动刷新开关/间隔变更：记录页即时启停定时器（2026-09-16）"""
        try:
            self.records_page._sync_auto_timer()
        except Exception:
            pass

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

class _PersistentCheckableMenu(CheckableMenu):
    """点击勾选项后不关闭菜单的多选菜单

    qfw RoundMenu._onItemClicked 无差别先 _hideMenu(False) 再触发
    action（原版点一下就关，无法连续勾选）；checkable 场景重写为只
    trigger（QAction 自动翻转 checked 并发 toggled），菜单维持打开。
    """

    def _onItemClicked(self, item):
        from PySide6.QtCore import Qt as _Qt
        action = item.data(_Qt.UserRole)
        if action not in self._actions or not action.isEnabled():
            return
        action.trigger()  # 翻转勾选 + 发 toggled；不隐藏菜单


class SettingsHubPage(QWidget):
    """统一设置页：左标题 + 右 SegmentedWidget 分页切换（Watt Toolkit 式）

    页签（2026-09-07 三期衔接：远程连接 独立分页）：
        应用配置 │ 远程连接 │ 工具 │ 性能 │ 数据库 │ 面板设置 │ 外观
        - 应用配置 = 路径 / 启动（默认启动页面，2026-09-19）/ 日志高亮 /
          配置文件（自工具页迁入）
        - 远程连接 = SSH/SFTP/FRP（为二期远程会话页预留落点）
        - 工具 = 快捷键与工具
        - 性能 = 亚克力 / 动画 / 表格平滑滚动（范围下拉 + 开关）
        - 数据库 = 数据源/双接口/MySQL（收集上传与手动添加已迁出）
        - 面板设置 = 售后（周期）+ 跑视频（署名）+ 运维（手动添加球桌记录）

    信号：
        aftersale_cycle_saved —— 周期设置保存成功（主窗口转发售后记录页刷新）
        aftersale_auto_refresh_changed —— 自动刷新开关/间隔变更（主窗口
            转发售后记录页即时启停定时器，2026-09-16）
        table_smooth_changed —— 任意表格平滑开关变更（主窗口转发各 Hub 刷新）
    """

    aftersale_cycle_saved = Signal()
    aftersale_auto_refresh_changed = Signal()
    table_smooth_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsHub")
        self._win = parent

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(12)

        # --- 头部：「设置」标题 + 单行描述；右侧分段切换 ---
        # 2026-09-07 用户反馈：删除左侧图标块，「设置」即标题；副标题一行
        header = QHBoxLayout()
        header.setSpacing(16)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(TitleLabel("设置", self))
        d = CaptionLabel("修改后立即生效并自动保存", self)
        d.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        d.setWordWrap(False)  # 副标题固定一行，不随分段控件挤压换行
        title_col.addWidget(d)
        header.addLayout(title_col)
        header.addStretch(1)

        # --- 右侧 SegmentedWidget：切换下方内容页 ---
        self._seg = SegmentedWidget(self)
        self._stack = QStackedWidget(self)
        pages = (
            # 2026-09-07 二期反馈：顺序 应用配置→工具→性能→数据库→面板设置→外观；
            # 配置文件自工具页迁入应用配置；手动添加球桌记录自数据库页迁入面板设置；
            # 2026-09-07：收集与上传自应用配置迁入面板设置「运维」组内
            ("config", "应用配置", (self._group_paths, self._group_startup,
                                    self._group_log_rules, self._group_files)),
            # 2026-09-07 三期衔接：远程连接独立分页（SSH/SFTP/FRP），
            # 为二期远程会话页（design/remote_session_v2.html）预留落点
            ("remote", "远程连接", (self._group_remote,)),
            # 2026-09-07：AI 分析自应用配置迁入工具页
            ("tools", "工具", (self._group_tools, self._group_ai)),
            ("perf", "性能", (self._group_perf,)),
            ("database", "数据库", (self._group_database,)),
            ("panels", "面板设置", (self._group_aftersale, self._group_ledger,
                                    self._group_management)),
            ("appearance", "外观", (self._group_appearance,)),
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
        self._seg.setCurrentItem("config")
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
        settings = win._load_settings()
        g = SettingGroup("外观", parent)

        # 主题模式（跟随系统/浅色/深色 → 复用菜单 Action 对象回调）
        mode = win._get_theme_mode(settings)
        acts = {"auto": win._act_theme_auto, "light": win._act_theme_light,
                "dark": win._act_theme_dark}
        g.addRow(SettingRow(
            FluentIcon.SYNC, "主题模式", "切换浅色、深色或跟随系统",
            make_combo([("跟随系统", "auto"), ("浅色", "light"),
                        ("深色", "dark")],
                       list(acts).index(mode if mode in acts else "auto"),
                       lambda m: win._on_theme_selected(acts[m]))))

        # 主题颜色：色块 + 更改 + 还原默认（选色天然需要弹窗，保持按钮）
        self._color_chip = QFrame(parent)
        self._color_chip.setFixedSize(22, 22)
        self._color_chip.setStyleSheet(
            f"border-radius: 4px; background: {win._theme_color}; border: 1px"
            f" solid rgba(128,128,128,0.5);")
        g.addRow(SettingRow(
            FluentIcon.PALETTE, "主题颜色",
            "按钮、选中态和进度条的强调色，即时生效",
            [self._color_chip,
             make_button("更改…", win._on_theme_color, width=76),
             make_button("还原默认", win._on_theme_color_reset, width=88)]))

        # 字号大小：SpinBox 内联直接调节（2026-09-07 由「更改…」弹窗内联化）
        self._spin_font_size = make_spinbox(
            int(settings.get("font_size", 10)), 10, 20, " pt",
            win._set_font_size_inline)
        g.addRow(SettingRow(
            FluentIcon.FONT_SIZE, "字号大小", "界面文字大小",
            self._spin_font_size))

        # 字体：ComboBox 内联（默认 + 系统全部字体族，同上内联化）
        families = sorted(set(QFontDatabase.families()))
        current_family = settings.get("font_family", "")
        font_items = [("默认", "")]
        cur_idx = 0
        for i, fam in enumerate(families):
            font_items.append((fam, fam))
            if fam == current_family:
                cur_idx = i + 1
        self._combo_font_family = make_combo(
            font_items, cur_idx,
            lambda v: win._set_font_family_inline(v), width=230)
        g.addRow(SettingRow(
            FluentIcon.FONT, "字体", "界面文字使用的字体",
            self._combo_font_family))

        # 界面缩放：ComboBox 内联（保存后提示重启生效，同上内联化）
        dpi = int(settings.get("dpi_scale", 100))
        dpi_options = [100, 125, 150, 175, 200]
        self._combo_dpi = make_combo(
            [(f"{o}%", o) for o in dpi_options],
            dpi_options.index(dpi) if dpi in dpi_options else 0,
            lambda v: win._set_dpi_scale_inline(v), width=110)
        g.addRow(SettingRow(
            FluentIcon.ZOOM, "界面缩放", "整体缩放界面，重启后生效",
            self._combo_dpi))

        classic = bool(settings.get("classic_layout", True))
        g.addRow(SettingRow(
            FluentIcon.TILES, "布局模式", "面板布局或经典布局",
            make_combo([("面板布局", False), ("经典布局", True)],
                       1 if classic else 0,
                       lambda v: win._on_layout_selected(
                           win._act_layout_classic if v
                           else win._act_layout_panel))))
        return g

    def _group_perf(self, parent):
        from qfluentwidgets import (Action,
                                    TransparentDropDownPushButton)
        from core.perf import (is_acrylic_enabled, is_animation_enabled,
                               get_table_smooth, set_acrylic_enabled,
                               set_animation_enabled, set_table_smooth)
        # 2026-09-07 用户反馈定稿：三项 亚克力 / 动画 / 表格平滑滚动。
        # 平滑滚动方案演进：ExpandSettingCard 折叠卡（渲染挤压错乱）→
        # 范围下拉+开关（两段式操作割裂）→ 本版单一下拉控件：点开
        # CheckableMenu 列出各面板勾选子项，勾选即写所选范围（全部面板=
        # 全局，具体面板=单独覆盖），按钮文本为状态摘要
        g = SettingGroup("性能", parent)

        g.addRow(SettingRow(
            FluentIcon.TRANSPARENT, "亚克力效果",
            "导航栏背景模糊效果，低配电脑建议关闭",
            make_switch(is_acrylic_enabled(), set_acrylic_enabled)))
        g.addRow(SettingRow(
            FluentIcon.QUIET_HOURS, "动画效果",
            "菜单和弹窗的过渡动画，关闭后立即显示",
            make_switch(is_animation_enabled(), set_animation_enabled)))

        # 表格平滑滚动：下拉勾选各面板子选项，点击后菜单维持可连续勾选
        # （2026-09-07 用户反馈定稿）：「全部面板」为主控——勾选=全部开启
        # 并清空面板覆盖，取消=全部取消并清空覆盖（消除主控关而子项开的
        # 无意义组合）；后续可单独勾选某面板开启（写面板覆盖）
        scopes = (("全部面板", None), ("售后面板", "aftersale"),
                  ("跑视频面板", "video"), ("运维管理", "management"),
                  ("远程会话", "remote"))

        def _summary_text():
            n = sum(get_table_smooth(p) for _l, p in scopes[1:])
            if n == 0:
                return "全部关闭"
            if n == len(scopes) - 1:
                return "全部开启"
            return f"自定义 · {n} 项开启"

        def _apply(panel, on):
            if panel is None:
                # 主控联动：全局开/关 + 清空全部面板覆盖（回到纯全局态）
                set_table_smooth(None, on)
                for _label, p in scopes[1:]:
                    set_table_smooth(p, None)
            else:
                set_table_smooth(panel, on)
            # 单独勾选面板后同样刷新：主控按 all(全局+各面板生效值) 判定
            # 自动翻转（全开→回勾，任一关→取消），消除主控与子项状态脱节
            _refresh_checked()
            self.table_smooth_changed.emit("all")
            smooth_btn.setText(_summary_text())

        def _refresh_checked():
            """按当前配置回显：主控=全局开且全部面板生效开；blockSignals
            防程序性 setChecked 触发 toggled 造成递归写盘"""
            master = (get_table_smooth(None) and all(
                get_table_smooth(p) for _l, p in scopes[1:]))
            for a, (_label, panel) in zip(smooth_menu.actions(), scopes):
                v = master if panel is None else get_table_smooth(panel)
                if a.isChecked() != v:
                    a.blockSignals(True)
                    a.setChecked(v)
                    a.blockSignals(False)

        smooth_btn = TransparentDropDownPushButton(_summary_text())
        smooth_btn.setFixedWidth(170)

        smooth_menu = _PersistentCheckableMenu(parent=smooth_btn)
        smooth_menu.setItemHeight(33)
        for label, panel in scopes:
            act = Action(label, smooth_menu)
            act.setCheckable(True)
            act.setChecked(get_table_smooth(panel))  # 初值近似，弹出前必刷
            act.toggled.connect(lambda on, p=panel: _apply(p, on))
            smooth_menu.addAction(act)

        # PySide6/Shiboken 劫持：实例级 menu.exec 会解析到 C++ QMenu.exec()，
        # 子类 Python exec（CheckableMenu→RoundMenu.exec）永不执行——
        # _showMenu 内 menu.exec(pd, aniType=...) 直接报
        # "unsupported keyword 'aniType'"，菜单弹不出。同
        # ui_mixin._patch_acrylic_exec 结论：实例绑定 Python 级 exec
        # 属性即可绕过劫持（Python 属性优先于 C++ 方法解析）
        from qfluentwidgets import MenuAnimationType, RoundMenu

        def _menu_exec(pos, ani=True, aniType=None):
            RoundMenu.exec(smooth_menu, pos, ani=ani,
                           aniType=aniType or MenuAnimationType.DROP_DOWN)

        smooth_menu.exec = _menu_exec

        # 刷新挂菜单 Show 事件（QMenu.aboutToShow 只在 popup() 路径发射，
        # qfw RoundMenu.exec 走 show() 不触发——eventFilter 必然命中）。
        # PySide6 installEventFilter 只收 QObject，函数式过滤器不可用
        from PySide6.QtCore import QObject, QEvent

        class _MenuShowFilter(QObject):
            def __init__(self, target_menu, refresh, parent):
                super().__init__(parent)
                self._m, self._refresh = target_menu, refresh

            def eventFilter(self, obj, ev):
                if obj is self._m and ev.type() == QEvent.Show:
                    self._refresh()
                return False

        smooth_menu.installEventFilter(
            _MenuShowFilter(smooth_menu, _refresh_checked, smooth_menu))
        smooth_btn.setMenu(smooth_menu)  # mouseReleaseEvent 自动弹出

        g.addRow(SettingRow(
            FluentIcon.SPEED_HIGH, "表格平滑滚动",
            "下拉勾选各面板的平滑滚动，可多选；全部面板为总控",
            smooth_btn))
        return g

    def _group_tools(self, parent):
        win = self._win
        g = SettingGroup("快捷键与工具", parent)

        def row(icon, title, desc, btn_text, cb):
            return g.addRow(SettingRow(
                icon, title, desc, make_button(btn_text, cb, width=76)))

        # 2026-09-07 二期：单杆视频/端口占用/上传清单/批量整理已迁独立
        # 「工具」页（main_window.tool_hub），此处仅保留纯动作入口
        row(FluentIcon.EDIT, "修改快捷键", "自定义全局快捷键",
            "修改…", win._on_modify_shortcuts)
        row(FluentIcon.DEVELOPER_TOOLS, "连接诊断", "查看连接日志与失败记录",
            "打开", win._on_open_conn_diag)
        row(FluentIcon.CODE, "打开工具页",
            "单杆视频 / 端口占用 / 上传清单 / 批量整理",
            "前往", win._on_open_tool_hub)

        # 工具页行为开关（单杆视频工作区读取，2026-09-07 二期需求）
        settings = win._load_settings()

        def _persist(key, value):
            win._save_settings({key: bool(value)})

        g.addRow(SettingRow(
            FluentIcon.SYNC, "随机生成 session_code",
            "选择日志文件后自动随机生成 session_code（关闭则保留当前值）",
            make_switch(bool(settings.get("single_random_session_code", True)),
                        lambda on: _persist("single_random_session_code", on))))
        g.addRow(SettingRow(
            FluentIcon.FOLDER, "生成后自动打开所在目录",
            "单杆视频生成完成后自动用资源管理器打开输出目录",
            make_switch(bool(settings.get("single_auto_open_dir", False)),
                        lambda on: _persist("single_auto_open_dir", on))))
        return g

    def _group_database(self, parent):
        g = SettingGroup("数据库与接口", parent)
        # 原管理设置页迁入（数据源/接口1·2 账号/MySQL 配置）；2026-09-07
        # 收集上传先迁「应用配置」再并入「面板设置→运维」组、手动添加球桌迁
        # 「面板设置→运维」，embedded 模式不再构建这三张卡
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
        from database import aftersale_db
        from windows.aftersale.settings import CycleSettingsPage
        self.cycle_page = CycleSettingsPage(parent)
        self.cycle_page.saved.connect(self.aftersale_cycle_saved.emit)
        g.addWidget(self.cycle_page)
        # 记住上次发生日期（2026-09-16）：上一条填 9/14，下一条新增默认仍 9/14
        g.addRow(SettingRow(
            FluentIcon.CALENDAR, "记住上次发生日期",
            "新增售后记录时，「发生时间」默认沿用上一条填写的日期，"
            "而不是回到当天（关闭后恢复默认当日）",
            make_switch(aftersale_db.remember_occurred_enabled(),
                        aftersale_db.set_remember_occurred)))
        # 自动刷新（2026-09-16）：定时比对全表指纹，有变化即静默重查，
        # 免手动点同步即可看到他人新填记录；开关/间隔变更即时转发记录页生效
        def _apply():
            self.aftersale_auto_refresh_changed.emit()
        g.addRow(SettingRow(
            FluentIcon.SYNC, "自动刷新记录",
            "定时检查数据库变化（他人填写的记录自动出现，无需手动同步）；"
            "仅在数据真正变化时刷新，不打扰当前浏览",
            make_switch(aftersale_db.auto_refresh_enabled(),
                        lambda on: (aftersale_db.set_auto_refresh(on),
                                    _apply()))))
        _interval_items = [("15 秒", 15), ("30 秒", 30),
                           ("1 分钟", 60), ("5 分钟", 300)]
        _cur_iv = aftersale_db.auto_refresh_interval()
        _iv_idx = next((i for i, (_l, v) in enumerate(_interval_items)
                        if v == _cur_iv), 1)
        g.addRow(SettingRow(
            FluentIcon.DATE_TIME, "自动刷新间隔",
            "两次数据库检查之间的时间",
            make_combo(_interval_items, _iv_idx,
                       lambda v: (aftersale_db.set_auto_refresh_interval(v),
                                  _apply()))))
        return g

    def _group_ledger(self, parent):
        g = SettingGroup("跑视频", parent)
        from windows.run_video.settings import SignerSettingsCard
        self.signer_card = SignerSettingsCard(parent)
        g.addWidget(self.signer_card)
        return g

    def _group_management(self, parent):
        """运维（手动添加球桌记录 + 收集与上传，2026-09-07 上传自应用配置并入）"""
        from windows.management.settings_page import AddTableRecordCard
        g = SettingGroup("运维", parent)
        self.add_record_card = AddTableRecordCard(parent)
        g.addWidget(self.add_record_card)
        self._add_upload_rows(g)
        return g

    def _group_files(self, parent):
        win = self._win
        g = SettingGroup("配置文件", parent)

        def row(icon, title, desc, name):
            return g.addRow(SettingRow(
                icon, title, desc,
                make_button("打开", lambda: win._open_config_file(name),
                            width=76)))

        row(FluentIcon.FOLDER, "配置目录", "应用分域配置目录",
            "settings.json")
        row(FluentIcon.DOCUMENT, "cfg.json", "识别端程序的配置文件",
            "cfg.json")
        row(FluentIcon.DOCUMENT, "frpc_xtcp_panel.toml", "远程会话穿透配置",
            "frpc_xtcp_panel.toml")
        return g

    # ---------- 应用配置（2026-09-07 弹窗 7 配置域迁入） ----------

    @staticmethod
    def _safe_int(value, default):
        """安全整数转换，失败时返回默认值"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _group_paths(self, parent):
        """路径配置：程序/视频/工具路径与批量整理目录，编辑即存"""
        from main_window.setting_cards import make_path_row
        win = self._win
        settings = win._load_settings()
        defaults = getattr(win, "DEFAULT_PATHS", {})
        g = SettingGroup("路径配置", parent)
        items = [
            ("exe_dir", "程序目录", "dir"),
            ("videos_dir", "视频/日志目录", "dir"),
            ("cipher_tool", "加密工具", "file"),
            ("front_exe", "前端程序", "file"),
            ("backend_exe", "后端程序", "file"),
            ("newlog_excel_dir", "整理Excel目录", "dir"),
            ("newlog_out_dir", "整理输出目录", "dir"),
        ]
        for key, title, mode in items:
            value = str(settings.get(key, "") or defaults.get(key, "") or "")
            g.addRow(make_path_row(key, title, value, mode, win))
        return g

    def _group_remote(self, parent):
        """远程连接：会话恢复开关 + SSH/SFTP 凭据 + FRP 穿透服务器

        凭据写 credentials 域，落盘由门面自动 DPAPI 加密（UI 只接触明文）；
        FRP 为嵌套 dict，三个控件任一编辑结束都整副本写回。
        """
        from main_window.setting_cards import make_line_edit
        win = self._win
        settings = win._load_settings()
        g = SettingGroup("远程连接", parent)

        g.addRow(SettingRow(
            FluentIcon.SYNC, "启动时恢复远程会话",
            "重启后自动恢复上次未退出的 SSH 与远程桌面会话",
            make_switch(bool(settings.get("restore_remote_sessions", True)),
                        lambda v: win._save_settings(
                            {"restore_remote_sessions": v}))))

        g.addRow(SettingRow(
            FluentIcon.PEOPLE, "SSH 用户名", "远程主机的登录用户名",
            make_line_edit(str(settings.get("ssh_user", "") or ""),
                           lambda t: win._save_settings(
                               {"ssh_user": t.strip()}),
                           "SSH 用户名")))

        g.addRow(SettingRow(
            FluentIcon.VPN, "SSH 密码", "落盘自动加密",
            make_line_edit(str(settings.get("ssh_pass", "") or ""),
                           lambda t: win._save_settings(
                               {"ssh_pass": t.strip()}),
                           "SSH 密码", password=True)))

        g.addRow(SettingRow(
            FluentIcon.FOLDER, "SFTP 默认路径", "远程文件传输的起始目录",
            make_line_edit(str(settings.get("sftp_default_remote_path", "") or ""),
                           lambda t: win._save_settings(
                               {"sftp_default_remote_path": t.strip()}),
                           "如 /home/user/project")))

        # FRP 穿透服务器（嵌套 dict：任一控件编辑结束整副本写回）
        frpc = dict(settings.get("frpc_server", {}) or {})
        e_addr = make_line_edit(str(frpc.get("serverAddr", "") or ""),
                                placeholder="服务器 IP")
        e_port = make_line_edit(str(frpc.get("serverPort", 7000)),
                                placeholder="端口号")
        e_token = make_line_edit(str(frpc.get("auth_token", "") or ""),
                                 placeholder="认证 Token", password=True)

        def _save_frpc():
            merged = dict(frpc)
            merged.update({
                "serverAddr": e_addr.text().strip(),
                "serverPort": self._safe_int(e_port.text().strip(), 7000),
                "auth_method": "token",
                "auth_token": e_token.text().strip(),
            })
            win._save_settings({"frpc_server": merged})

        for _e in (e_addr, e_port, e_token):
            _e.editingFinished.connect(_save_frpc)

        g.addRow(SettingRow(
            FluentIcon.CONNECT, "FRP 服务器地址", "内网穿透服务器地址", e_addr))
        g.addRow(SettingRow(
            FluentIcon.SPEED_HIGH, "FRP 服务器端口", "默认 7000", e_port))
        g.addRow(SettingRow(
            FluentIcon.CERTIFICATE, "FRP 认证 Token", "落盘自动加密", e_token))
        return g

    def _group_ai(self, parent):
        """AI 分析：总开关 + 厂商/Key/模型联动

        联动规则沿用原弹窗 _apply_ai_vendor：切换厂商 → 刷新该厂商已存
        Key、重置官方默认模型并更新接口地址提示；首次回显保留已保存的
        自定义模型。Key 按厂商分别保存在 ai_api_keys 嵌套 dict（清空即
        删除该厂商条目），落盘门面自动 DPAPI。
        """
        from core.ai_providers import AI_PROVIDERS, get_provider
        from main_window.setting_cards import make_line_edit
        win = self._win
        settings = win._load_settings()
        g = SettingGroup("AI 分析", parent)

        g.addRow(SettingRow(
            FluentIcon.ROBOT, "启用 AI 日志分析",
            "对采集的 SSH 日志做智能分析",
            make_switch(bool(settings.get("forensic_ai_analysis", True)),
                        lambda v: win._save_settings(
                            {"forensic_ai_analysis": v}))))

        saved_keys = dict(settings.get("ai_api_keys", {}) or {})
        saved_model = str(settings.get("ai_model", "") or "").strip()
        cur_vendor = str(settings.get("ai_vendor", "deepseek") or "deepseek")
        provider = get_provider(cur_vendor)

        combo = ComboBox()
        for p in AI_PROVIDERS:
            # qfw addItem 第二参是 icon，userData 必须关键字传参
            combo.addItem(p["label"], userData=p["id"])
        idx = next((i for i in range(combo.count())
                    if combo.itemData(i) == provider["id"]), 0)
        combo.setCurrentIndex(idx)  # 先回显再连接，初始化不误触发
        combo.setFixedWidth(170)

        e_key = make_line_edit(
            str(saved_keys.get(provider["id"]) or ""),
            placeholder="各厂商开放平台创建的 API Key", password=True)
        e_model = make_line_edit(
            saved_model or provider["default_model"],
            placeholder="模型名")

        url_row = SettingRow(FluentIcon.GLOBE, "接口地址",
                             provider["base_url"])

        state = {"vendor": provider["id"], "initial": True}

        def _apply_vendor(vendor_id):
            p = get_provider(vendor_id)
            state["vendor"] = p["id"]
            e_key.setText(str(saved_keys.get(p["id"]) or ""))
            if state["initial"]:
                # 首次回显：保留已保存的自定义模型
                e_model.setText(saved_model or p["default_model"])
            else:
                # 手动切换厂商：重置为官方默认模型并保存选择
                e_model.setText(p["default_model"])
                win._save_settings({"ai_vendor": p["id"]})
            if getattr(url_row, "desc_label", None) is not None:
                url_row.desc_label.setText(p["base_url"])
            state["initial"] = False

        def _save_key():
            v = e_key.text().strip()
            if v:
                saved_keys[state["vendor"]] = v
            else:
                saved_keys.pop(state["vendor"], None)
            win._save_settings({"ai_api_keys": dict(saved_keys)})

        def _save_model():
            win._save_settings({"ai_model": e_model.text().strip()})

        combo.currentIndexChanged.connect(
            lambda _i: _apply_vendor(combo.currentData()))
        e_key.editingFinished.connect(_save_key)
        e_model.editingFinished.connect(_save_model)

        g.addRow(SettingRow(FluentIcon.PALETTE, "模型厂商",
                            "OpenAI 兼容接口的服务商", combo))
        g.addRow(SettingRow(FluentIcon.VPN, "API Key",
                            "按厂商分别保存，清空即删除当前厂商的 Key",
                            e_key))
        g.addRow(SettingRow(FluentIcon.FONT, "模型",
                            "留空使用所选厂商的默认模型", e_model))
        g.addRow(url_row)
        return g

    def _add_upload_rows(self, g):
        """收集与上传行（2026-09-07 自应用配置并入「运维」组）

        精度/问题文件收集打包上传的 SFTP 目标；键与旧卡同（upload_*，
        credentials 域落盘自动 DPAPI），独立窗口模式的管理设置页仍保留
        原集中保存卡，两处写同一批配置键。
        """
        from main_window.setting_cards import make_line_edit
        win = self._win
        settings = win._load_settings()

        def _int_or(text, default):
            try:
                return int(text)
            except (TypeError, ValueError):
                return default

        g.addRow(SettingRow(
            FluentIcon.CONNECT, "上传服务器",
            "精度/问题文件收集打包上传的目标主机",
            make_line_edit(str(settings.get("upload_host", "49.235.34.253") or ""),
                           lambda t: win._save_settings(
                               {"upload_host": t.strip()}),
                           "上传服务器 IP")))
        g.addRow(SettingRow(
            FluentIcon.SPEED_HIGH, "上传端口", "SFTP 端口，默认 22",
            make_line_edit(str(settings.get("upload_port", 22)),
                           lambda t: win._save_settings(
                               {"upload_port": _int_or(t.strip(), 22)}),
                           "端口号（默认 22）", width=120)))
        g.addRow(SettingRow(
            FluentIcon.FOLDER, "远程目录", "服务器上的存储路径",
            make_line_edit(
                str(settings.get("upload_remote_dir",
                                 "/lhcos-data/videos") or ""),
                lambda t: win._save_settings(
                    {"upload_remote_dir": t.strip()}),
                "如 /lhcos-data/videos")))
        g.addRow(SettingRow(
            FluentIcon.PEOPLE, "上传用户名", "留空使用 root",
            make_line_edit(str(settings.get("upload_user", "root") or ""),
                           lambda t: win._save_settings(
                               {"upload_user": t.strip() or "root"}),
                           "上传用户名（默认 root）")))
        g.addRow(SettingRow(
            FluentIcon.VPN, "上传密码", "落盘自动加密",
            make_line_edit(str(settings.get("upload_pass", "") or ""),
                           lambda t: win._save_settings({"upload_pass": t}),
                           "上传密码", password=True)))

    def _group_startup(self, parent):
        """启动：设定打开程序时默认展示的界面（2026-09-19 需求）

        下拉选项 = 六个一级导航页；存 objectName（与主窗口导航路由解耦，
        改名/换序不影响持久值）。非法/缺失值回退「工作台」。
        """
        win = self._win
        settings = win._load_settings()
        g = SettingGroup("启动", parent)
        items = [
            ("工作台", "homeInterface"),
            ("运维管理", "managementHub"),
            ("售后", "aftersaleHub"),
            ("跑视频", "ledgerHub"),
            ("远程", "remoteHub"),
            ("工具", "toolHub"),
        ]
        valid = {obj for _label, obj in items}
        cur = str(settings.get("startup_default_page", "homeInterface")
                  or "homeInterface")
        if cur not in valid:
            cur = "homeInterface"
        idx = [obj for _label, obj in items].index(cur)
        g.addRow(SettingRow(
            FluentIcon.HOME, "默认启动页面",
            "打开程序时自动切换到该界面（下次启动生效）",
            make_combo(items, idx,
                       lambda v: win._save_settings(
                           {"startup_default_page": v}),
                       width=180)))
        g.addRow(SettingRow(
            FluentIcon.FIT_PAGE, "面板入口默认弹出",
            "工具栏「跑视频 / 售后面板 / 球桌管理」点击后弹出独立面板窗口；"
            "关闭则改为在主窗口内跳转对应页面",
            make_switch(bool(settings.get("panel_entry_popout", True)),
                        lambda v: win._save_settings(
                            {"panel_entry_popout": bool(v)}))))
        return g

    def _group_log_rules(self, parent):
        """日志高亮规则：列表 + 添加/编辑/删除，变更即时落盘并重编译生效"""
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QDialog
        from PySide6.QtWidgets import QFormLayout as _QForm
        from core.log_rules import DEFAULT_LOG_RULES, compile_log_rules
        win = self._win
        settings = win._load_settings()
        g = SettingGroup("日志高亮", parent)

        tip = CaptionLabel(
            "规则按序匹配，命中行整行着色；开启「命中通知」后弹提示"
            "（每规则 10 秒去重）。正则写法与 Python re 一致，"
            "如 ERROR|Exception。", parent)
        tip.setWordWrap(True)
        tip.setTextColor(QColor(0, 0, 0, 170), QColor(255, 255, 255, 170))
        g.addWidget(tip)

        self._log_rules_state = list(
            settings.get("log_highlight_rules") or DEFAULT_LOG_RULES)
        rules_list = QListWidget(parent)
        rules_list.setFixedHeight(150)
        g.addWidget(rules_list)

        btn_host = QWidget(parent)
        btn_lay = QHBoxLayout(btn_host)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(10)
        btn_lay.addWidget(make_button("添加", lambda: _add_rule(), width=76))
        btn_lay.addWidget(make_button("编辑", lambda: _edit_rule(), width=76))
        btn_lay.addWidget(make_button("删除", lambda: _del_rule(), width=76))
        btn_lay.addStretch(1)
        g.addWidget(btn_host)

        def _persist():
            """规则变更落盘 + 重编译（渲染与通知共用，不刷新则旧规则仍生效）"""
            win._save_settings(
                {"log_highlight_rules": list(self._log_rules_state)})
            win._log_rules = compile_log_rules(
                self._log_rules_state or DEFAULT_LOG_RULES)

        def _refresh():
            rules_list.clear()
            for r in self._log_rules_state:
                name = r.get("name", "") or ""
                pat = r.get("pattern", "") or ""
                notify = "通知" if r.get("notify") else "静默"
                item = QListWidgetItem(f"{name}  ·  {pat}  ·  {notify}")
                try:
                    item.setForeground(QColor(r.get("color", "#ff5252")))
                except Exception:
                    pass
                item.setData(1, r)
                rules_list.addItem(item)

        def _current_rule():
            item = rules_list.currentItem()
            return item.data(1) if item is not None else None

        def _add_rule():
            rule = _edit_rule_dialog({"name": "", "pattern": "",
                                      "color": "#ff5252", "notify": True})
            if rule:
                self._log_rules_state.append(rule)
                _refresh()
                _persist()

        def _edit_rule():
            rule = _current_rule()
            if rule is None:
                return
            new_rule = _edit_rule_dialog(dict(rule))
            if new_rule:
                i = self._log_rules_state.index(rule)
                self._log_rules_state[i] = new_rule
                _refresh()
                _persist()

        def _del_rule():
            rule = _current_rule()
            if rule is None:
                return
            self._log_rules_state.remove(rule)
            _refresh()
            _persist()

        def _edit_rule_dialog(rule):
            """规则编辑弹窗：确定返回新规则 dict，取消或正则非法返回 None"""
            import re as _re
            from qfluentwidgets import ColorDialog
            dlg = QDialog(self)
            dlg.setWindowTitle("编辑规则" if rule.get("name") else "添加规则")
            dlg.resize(460, 250)
            v = QVBoxLayout(dlg)
            form = _QForm()
            form.setSpacing(8)

            name_edit = LineEdit(dlg)
            name_edit.setText(rule.get("name", "") or "")
            name_edit.setPlaceholderText("规则名，如：错误")
            form.addRow("规则名:", name_edit)

            pat_edit = LineEdit(dlg)
            pat_edit.setText(rule.get("pattern", "") or "")
            pat_edit.setPlaceholderText("正则，如 ERROR|Exception")
            form.addRow("匹配正则:", pat_edit)

            color = QColor(rule.get("color", "#ff5252") or "#ff5252")
            color_lbl = BodyLabel(color.name(), dlg)
            color_lbl.setStyleSheet(f"color:{color.name()}; font-weight:bold;")
            btn_color = PushButton("选择颜色…", dlg)

            def _pick():
                nonlocal color
                cd = ColorDialog(color, "选择高亮颜色", dlg)
                if cd.exec():
                    color = cd.color
                    color_lbl.setText(color.name())
                    color_lbl.setStyleSheet(
                        f"color:{color.name()}; font-weight:bold;")

            btn_color.clicked.connect(_pick)
            h = QHBoxLayout()
            h.addWidget(btn_color)
            h.addWidget(color_lbl)
            h.addStretch(1)
            form.addRow("颜色:", h)

            # 语义由 form 行标签「命中通知:」承载；SwitchButton 自身
            # 只作状态指示（patch 后构造会覆盖初始 text 为中文开/关，
            # 语义文本放这里会在首次翻转时丢失）
            notify_sw = SwitchButton(dlg)
            notify_sw.setChecked(bool(rule.get("notify")))
            form.addRow("命中通知:", notify_sw)
            v.addLayout(form)

            btns = QHBoxLayout()
            btns.addStretch(1)
            btn_ok = PushButton("确定", dlg)
            btn_ok.clicked.connect(dlg.accept)
            btn_cancel = PushButton("取消", dlg)
            btn_cancel.clicked.connect(dlg.reject)
            btns.addWidget(btn_ok)
            btns.addWidget(btn_cancel)
            v.addLayout(btns)

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            pat = pat_edit.text().strip()
            if not pat:
                return None
            try:
                _re.compile(pat)
            except _re.error:
                return None
            return {"name": name_edit.text().strip() or pat,
                    "pattern": pat,
                    "color": color.name(),
                    "notify": notify_sw.isChecked()}

        _refresh()
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
