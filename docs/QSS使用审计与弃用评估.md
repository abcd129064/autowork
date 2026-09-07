# AutoWork 项目 QSS 使用审计与弃用评估报告

> 审计人：高见远（架构师） ｜ 审计日期：2026-09-07
> 审计对象：`C:\Users\shen_zhe\Desktop\autowork`
> 环境基线：PySide6 **6.11.2** ＋ qfluentwidgets **1.11.3** ＋ Python 3.13
> 性质：**纯只读分析**，未修改任何源码

---

## 0. 审计方法与可信度说明

| 手段 | 说明 |
|---|---|
| 全量 grep | `setStyleSheet` / `setCustomStyleSheet` / `StyleSheetBase` / `\.qss` / `_QSS` 等关键字，排除 `dist/`、`build/` |
| 逐处读码 | 内联 QSS 多为跨行字符串，grep 只能定位起点，**每一处都读了上下 15~40 行**才下定论 |
| 官方文档 | WebFetch 抓取 https://qfluentwidgets.com/zh/pages/theme （抓取成功，内容见 §3.1） |
| 库内实证 | 直接读取本机 `site-packages/qfluentwidgets` 的内置 qrc 资源（`:/qfluentwidgets/qss/{light,dark}/*.qss`，共 **34 个**），确认框架到底覆盖了哪些控件 |
| 渲染实证 | offscreen 渲染 + 像素取样，验证「祖先容器 legacy QSS vs qfw 组件自带 QSS」的优先级（见 §3.4） |

**工作树瞬时抖动提示（重要）**：审计过程中（05:34–05:42）`main_window/` 目录出现过一次短暂回退（`ui_mixin.py` 曾被替换为窗口级 `self.setStyleSheet(stylesheet)` 的旧版本，`hub_pages.py`/`pivot_page.py`/`setting_cards.py` 一度消失），随后自动恢复。**本报告以 05:42 复核后的稳定态为准**（即 `ui_mixin._apply_business_qss()` 走 `stackedWidget + setCustomStyleSheet` 的合规版本）。若后续再变，请以 §4.3 的判定规则重新对照。

---

## 1. 结论速览（TL;DR）

| 问题 | 结论 |
|---|---|
| **QSS 有多少？** | 2 个独立 `.qss` 文件（243 / 256 行）＋ **31 个业务源文件**的 **88 处** `setStyleSheet` 真实调用 ＋ 5 个调试探针 6 处。全库匹配行 110 条 / 36 文件（含注释与探针）。 |
| **qfw 主题能覆盖多少？** | 按"样式功能项"计，**约 55% 已被 qfw 原生能力覆盖或可被等价替换**；**约 45% 无法被覆盖**。 |
| **能否直接弃用并移除？** | **不能一次性全删。** 准确结论是：**可以删除全局 `styles/*.qss` 双文件（其 90% 以上的规则已失效/被覆盖），但必须保留（或改造）约 30 处内联 QSS**。 |
| **红线碰没碰？** | **没碰。** 主窗口业务 QSS 已正确挂在 `stackedWidget` 上并走 `setCustomStyleSheet`；`apply_window_qss()` 的 7 个调用方**全部是原生 `QDialog`**（非 FluentWindow），不触发 Mica 杀手。 |
| **建议分几阶段？** | **3 个阶段（P0 / P1 / P2）**，P0 为"零风险纯删除"，P1 为"等价替换"，P2 为"必须保留 + 令牌化"。详见 §5。 |

---

## 2. QSS 全景清单

### 2.1 文件级总览

`setStyleSheet` 匹配共 **110 行 / 36 文件**（排除 `dist/`、`build/`）。其中 **16 行是注释/文档字符串中的提及**，真实调用为 **94 处**：

- **业务侧：31 个文件 / 88 处**
- **调试探针：5 个文件 / 6 处**（`tools/probe_mica_offscreen.py`、`probe_mica_qss_fix.py`、`probe_mica_steps_pink.py`、`probe_mica_wallpaper.py`、`smoke_fluent_mainwindow.py`、`verify_fluent_refactor.py`）

> 关于"调试探针"：`tools/probe_mica_*.py` 是 2026-09-07 定位 Mica 杀手时写的**一次性验证脚本**（洋红壁纸对照法），`smoke_fluent_mainwindow.py` / `verify_fluent_refactor.py` 是重构冒烟脚本。它们**不是业务样式**，本报告单列，不参与业务结论与占比计算。

| 目录 | 文件 | 匹配行数 | 真实调用 | 说明 |
|---|---|---:|---:|---|
| `core/` | `theme_qss.py` | 3 | **1** | 全局 QSS 加载/分发中枢（另有 2 行注释） |
| `core/` | `flow_widgets.py` | 3 | **2** | 流式工具栏滚动区透明化 |
| `main_window/` | `ui_mixin.py` | 5 | **0** | **5 行全是注释**（记录 Mica 血泪史）；真实挂载走 `setCustomStyleSheet` |
| `main_window/` | `hub_pages.py` | 3 | 3 | 设置 Hub 页图标块/色块/滚动区 |
| `main_window/` | `main_window.py` | 3 | 3 | 数据库状态灯、状态栏消息 |
| `main_window/` | `settings_dialog.py` | 3 | 3 | 日志高亮色预览、节标题 |
| `main_window/` | `setting_cards.py` | 2 | 2 | 卡片标题/组标题字重 |
| `main_window/` | `pivot_page.py` | 2 | 2 | Pivot 项 padding 微调、滚动区透明 |
| `main_window/` | `process_mixin.py` | 0 | 0 | 用 `setCustomStyleSheet`（DARK/LIGHT 双槽按钮样式） |
| `windows/aftersale/` | `common.py` | 8 | **7** | 候选列表/分组条/字段标签/错误提示/徽章/行内按钮（1 行注释） |
| `windows/aftersale/` | `records.py` | 8 | 8 | 指标卡数字、批量条、数据源色、重要行底色 |
| `windows/aftersale/` | `stats_dialog.py` | 8 | 8 | 统计弹窗各提示文字与 KPI 数字 |
| `windows/aftersale/` | `entry.py` | 7 | 7 | 周期芯片、滚动区、必填进度条语义色 |
| `windows/aftersale/` | `form.py` | 1 | 1 | 桌号关联确认条 |
| `windows/aftersale/` | `settings.py` | 1 | 1 | 设置页滚动区透明 |
| `windows/aftersale/` | `dialogs.py` | 0 | 0 | 只调用 `apply_window_qss(self)` |
| `windows/management/` | `widget_page.py` | 6 | 6 | 滚动区透明 ×5、视频画面黑底 |
| `windows/management/` | `health_page.py` | 6 | 6 | 预警条目红色系文字 |
| `windows/management/` | `device_page.py` | 4 | **2** | 文件列表面板背景（2 行注释） |
| `windows/management/` | `moyu_widgets.py` | 4 | 4 | 提示灰字、老板键伪装日志底 |
| `windows/management/` | `settings_page.py` | 2 | 2 | 滚动区 + 视口透明 |
| `windows/management/` | `moyu_page.py` | 1 | 1 | 滚动区透明 |
| `windows/management/` | `image_viewer.py` | 1 | 1 | 图片预览区浅灰底 |
| `windows/management/` | `common.py` | 1 | 1 | 表格只读编辑框 |
| `windows/remote_session/` | `sftp_window.py` | 2 | 2 | 连接健康指示圆点配色 |
| `windows/remote_session/` | `conn_diag_panel.py` | 2 | 2 | 底部提示灰字 |
| `windows/remote_session/` | `ssh_terminal.py` | 1 | 1 | 断线通知条橙底橙边 |
| `windows/remote_session/` | `ansi_terminal.py` | 1 | 1 | **ANSI 终端黑底**（终端观感核心） |
| `windows/remote_session/` | `rdp_window.py` | 1 | 1 | 状态栏内边距 |
| `windows/remote_session/` | `window.py` | 1 | 1 | 空态提示灰字 |
| `windows/run_video/` | `entry.py` | 6 | 6 | 滚动区、必填进度条语义色 |
| `windows/run_video/` | `records.py` | 2 | 2 | 指标卡数字、数据源色 |
| `windows/run_video/` | `settings.py` | 1 | 1 | 设置页滚动区透明 |
| `autowork_with_table.py` | — | 0 | 0 | 用 `setCustomStyleSheet`（工具栏 RadioButton 32px 行高） |
| `tools/` | 5 个探针 | 11 | **6** | 调试验证用，非业务样式 |

---

### 2.2 `styles/light.qss` 分块解读（243 行）

> 加载点：`main_window/ui_mixin.py:1805` → `_load_qss('light')` → `setCustomStyleSheet(stackedWidget, light, dark)`
> 另：`core/theme_qss.py:62` → `load_window_qss()` 给 7 个原生 QDialog 用

| # | 行号 | 选择器 / 模块块 | 实现的样式功能（人话） | 目标控件是否仍存在 |
|---|---|---|---|---|
| L1 | 9–11 | `QWidget#centralwidget` | 工作台整体底色浅灰 `#F5F6F8`（窗口自身底色段已注释删除，见 1–6 行注释） | ✅ `autowork_with_table.py:258` |
| L2 | 14–24 | `QScrollArea#toolbar_scroll` / `QWidget#toolbar_widget` / `QFrame#toolbar_separator` | 工具栏滚动区底色 + 底部 1px 分隔线；分隔条灰色 + 上下 4px 外边距 | ✅ 311 / 322 / 153 |
| L3 | 27–45 | `QPushButton` + `:hover/:pressed/:disabled` | **所有原生按钮**：青色 `#00BCD4` 实心底、白字、无边框、6px 圆角、5×14 内边距、最小高 22px、字重 500；hover `#00ACC1`、pressed `#00838F`、禁用灰底灰字 | ⚠️ **类型选择器，会命中所有 QPushButton 子类**（含 qfw PushButton，但实测被 qfw 覆盖，见 §3.4） |
| L4 | 48–59 | `QPlainTextEdit#show_log` + `:hover/:focus` | 日志输出区白底、`#374151` 字、无边框、选中区青底白字；**并强制覆盖 Fluent 内置 hover/focus 变色**，保持终端观感恒定 | ✅ `autowork_with_table.py:566`（实为 qfw `TextEdit`，`QPlainTextEdit` 选择器**不命中** ⚠️） |
| L5 | 62–70 | `QWidget#log_status_bar` / `QLabel#log_status_device` / `#log_status_count` | 日志顶部状态条白底 + 底部 1px 线；设备/条数文字 `#6B7280`、8pt、透明底 | ✅ 575 / 581 / 584 |
| L6 | 73–80 | `QLabel#left_panel_header` | 左侧三个分组标题：底色浅灰、青色加粗 9pt 字、下方 1px 线、左内边距 10px | ✅ 812 / 824 / 840 |
| L7 | 83–85 | `QWidget#left_panel`, `QWidget#center_panel` | 左栏/中栏容器底色浅灰 | ✅ 795 / 853 |
| L8 | 88–114 | `QListWidget`, `QTreeWidget` + `::item:hover/:selected`，`QDateEdit` + `::drop-down/:focus` | 列表/树：白底、`#20242B` 字、无边框无焦点框；悬停 4% 黑、选中 15% 青底 + `#00838F` 字；日期框白底 1px 灰边 6px 圆角，聚焦变青边 | 列表/树 ⚠️ 多为 qfw 组件（已被覆盖）；`QDateEdit` 项目中**已无实例** |
| L9 | 117–137 | `QFrame#p2p_panel` / `QLabel#p2p_panel_header` / `QLabel#section_label` | 远程面板白底 + 左侧 1px 线 + 6px 圆角；面板标题青色加粗 10pt + 底部线 + 上圆角；小分区标签青色加粗 8pt | ✅ 598 / 605 / 190 |
| L10 | 140–151 | `QSplitter::handle` (+`:horizontal/:vertical/:hover`) | 分割条 2px 灰线，悬停变青 | ✅ `autowork_with_table.py:790/800/804` |
| L11 | 154–157 | `QLabel` | **全局兜底**：所有原生 QLabel 文字 `#6B7280` + 透明底 | ⚠️ 实测**不影响 qfw BodyLabel/CaptionLabel**（§3.4） |
| L12 | 160–164 | `QLabel#list_empty_hint` | 列表空状态提示灰字 9pt | ✅ `autowork_with_table.py:170` |
| L13 | 167–170 | `QWidget#menubar_widget` | 菜单栏容器透明 + 底部 1px 线 | ✅ `ui_mixin.py:817`（注：菜单栏已移入设置页） |
| L14 | 173–214 | `QScrollBar` 全套（vertical/horizontal、handle、add-line/sub-line、add-page/sub-page） | 滚动条 8px 细条、滑块 `#D6D6D6` 4px 圆角最小 24px、悬停变青、隐藏箭头按钮与页槽 | ✅ 全局生效（作用在 stackedWidget 子树） |
| L15 | 217–223 | `QToolTip` | 工具提示白底 + 1px 灰边 + 6px 圆角 + 4×8 内边距 | ⚠️ **疑似失效**：QToolTip 是顶层窗口，不在 stackedWidget 子树内，Qt 不向其传播祖先样式表（待真机验证，见 §7） |
| L16 | 226–237 | `QProgressBar` + `::chunk` | 进度条 `#E8EAED` 槽 + 1px 边 + 6px 圆角 + 18px 高 + 文字居中；填充块青色 5px 圆角 | ⚠️ 项目用的是 **qfw `ProgressBar`**（自绘、无内置 QSS），另被 `entry.py:152` 等就地覆盖 |
| L17 | 240–243 | `QFormLayout QLabel` | 表单标签 `#6B7280`、8pt | 视具体表单而定 |

### 2.3 `styles/dark.qss` 分块解读（256 行）

结构与 light 几乎一一对应，差异如下（**同名的块功能完全一致，仅色值不同**，不再重复描述）：

| # | 行号 | 选择器 / 模块块 | 相对 light 的差异 / 特殊说明 |
|---|---|---|---|
| D0 | 1–5 | 窗口自身底色注释段 | 同 light，已移除（Mica 原因） |
| D1 | 8–10 | `QWidget#centralwidget` | 深灰 `#202020` |
| D2 | 13–23 | 工具栏三件套 | `#202020` / 分隔线 `#383838` |
| D3 | 26–44 | `QPushButton` 全套 | 与 light **完全相同**（青底白字），仅 `:disabled` 用 `#383838` 底 + `#555960` 字 |
| D4a | 46–48 | 输入框段 | **已删除**，注释：已换 qfw `LineEdit/PasswordLineEdit`，内置双主题 |
| D4b | 50–52 | 数字微调框段 | **已删除**，注释：已换 qfw `SpinBox` |
| D4 | 54–70 | `QPlainTextEdit#show_log` | 黑底 `#202020` + `#B0BEC5` 字；**并显式注释：此处不写 font-family/font-size，字体由 `_apply_global_font()` 用 `setFont()` 统一控制**，避免 QSS 覆盖用户自选字体 |
| D5 | 73–81 | 日志状态条 | 底 `#2C2C2C`、线/字 `#8892a2` |
| D6 | 84–91 | 左侧标题 | 底 `#202020`、青字加粗 |
| D7 | 94–96 | 左右面板 | `#202020` |
| D8 | 99–125 | 列表/树/日期 | 底 `#202020`、字 `#C8D0DC`；悬停 5% 白、选中 20% 青底 + `#00BCD4` 字；注释强调"统一基底色，禁止回退调色板产生杂色" |
| D9 | 128–148 | 远程面板三件套 | 底 `#2C2C2C`、线 `#383838` |
| D10 | 151–162 | `QSplitter::handle` | `#383838`，悬停青 |
| D11 | 165–168 | `QLabel` | `#8892a2` |
| D12 | 171–175 | `QLabel#list_empty_hint` | `#555f6b` |
| D13 | 178–183 | `QWidget#menubar_widget` + 注释 | 深灰底；**并注释：QMenu 样式已由 `AcrylicMenu` 接管，不再自定义 QMenu QSS，避免干扰亚克力渲染** |
| D14 | 186–227 | `QScrollBar` 全套 | 轨道 `#202020`、滑块 `#383838`、悬停青 |
| D15 | 230–236 | `QToolTip` | 底 `#2C2C2C`、**字色却是青色 `#00BCD4`**（与 light 的 `#20242B` 不一致，疑为历史遗留 bug） |
| D16 | 239–250 | `QProgressBar` | 槽 `#2C2C2C`、字 `#C8D0DC`、填充青 |
| D17 | 253–256 | `QFormLayout QLabel` | `#8892a2`、8pt |

**双文件合计 17 个功能块**，其中 `dark.qss` 已有 4 处明确的"已迁移到 qfw，故删除"注释（输入框、微调框、QMenu、窗口底色），说明**这条迁移路径项目里已经走过一轮**。

---

### 2.4 内联 QSS 逐处清单（业务侧 88 处）

#### ① `core/` — 主题 QSS 中枢

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `core/theme_qss.py:82` | `apply_window_qss._apply()` | **把整份 `styles/{dark\|light}.qss` setStyleSheet 到目标窗口**，并订阅 `qconfig.themeChanged` / `themeColorChanged` 自动重应用。7 个调用方：aftersale `dialogs.py:90`、`stats_dialog.py:300`，management `image_viewer.py:126`，remote_session `conn_diag_panel.py:238`、`rdp_window.py:515`、`sftp_window.py:1917`、`ssh_terminal.py:770`（**全部为原生 `QDialog`，非 FluentWindow**） |
| `core/flow_widgets.py:27` | `_FlowScrollArea.__init__` | 流式工具栏滚动区 `background: transparent; border: none`（注释说明：不依赖 objectName，因为调用方可能覆写；`setStyleSheet` 只作用于自身子树） |
| `core/flow_widgets.py:28` | 同上 | 滚动区 viewport 也单独设透明（否则视口仍有灰底） |

#### ② `main_window/`

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `hub_pages.py:201` | 设置页头部 | 56×56 设置图标圆角块：12px 圆角 + `rgba(128,128,128,0.14)` 中性半透明灰底（深浅主题通用） |
| `hub_pages.py:259` | 设置页滚动区 | `QScrollArea { background: transparent; }`，去掉滚动区灰底 |
| `hub_pages.py:291` | 主题色色块 | 22×22 当前主题色预览方块：4px 圆角 + 实心底 + `rgba(128,128,128,0.5)` 描边 |
| `main_window.py:502` | `_refresh_db_status_text` | 数据库状态标签"MySQL 在线" → 绿色 `#1D9E75`（硬编码，未走 SEMANTIC 令牌） |
| `main_window.py:505` | 同上 | "SQLite 兜底" → 琥珀色 `#BA7517`（硬编码） |
| `main_window.py:1495` | `_show_kd_status_message` | 状态栏 kd 反查结果临时着色（强调色 + 字重 600），6s 后由 `clearStyleSheet` 清除 |
| `settings_dialog.py:593` | 日志高亮规则编辑 | 颜色值文字本身染成该颜色（"所见即所得"预览）+ 加粗 |
| `settings_dialog.py:602` | 同上（选色回调） | 选色后刷新上面那行颜色预览 |
| `settings_dialog.py:677` | `_add_section_header` | 设置弹窗节标题：加粗 + 13px + 上外边距 4px |
| `setting_cards.py:88` | 卡片标题 | 标题字重 600（注释紧接其后说明：副标题改用 `setTextColor` 双槽而非内联 color，因为内联 color 会覆盖 custom 槽） |
| `setting_cards.py:181` | 组标题 | 字重 600 + 2×4 内边距（同样注释了"color 必须留在 setTextColor 双槽"） |
| `pivot_page.py:101` | Pivot 项高度压缩 | **功能性副作用**：把 qfw 内置 Pivot 项 QSS 里的 `padding: 10px 12px` 字符串替换成 `1px 12px`，配合 `setFixedHeight(24)` 把默认 42px 行高压到 24px（注释说明只改 padding 一条，其余选中/hover 规则保留） |
| `pivot_page.py:159` | 滚动区 | `QScrollArea { background: transparent; }` |

#### ③ `windows/aftersale/`

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `common.py:111` | `_style_cand_list` | **桌号候选列表整套皮肤**（`_CAND_LIST_QSS_TMPL`，68–94 行）：圆角边框 + 主题背景 + 文字色 + 条目 4×8 内边距/4px 圆角 + hover/选中（强调色底白字）/禁用态 + 6px 细滚动条。深浅两套色在 105–110 行按 `isDarkTheme()` 二选一 |
| `common.py:139` | `_SectionCard.__init__` | 分组卡片左侧 3×12 强调色竖条（圆角 1px） |
| `common.py:142` | 同上（回调） | 订阅 `qconfig.themeColorChanged`，主题色变化时重刷竖条颜色 |
| `common.py:208` | `_field_label._apply` | 表单字段标签：12px + 主题自适应字色（深 `#c5c8ce` / 浅 `#444b55`）+ 透明底；注释特别说明"init 即设色，因为 themeChanged 只在主题『改变』时触发，深色启动时初始样式会失效" |
| `common.py:223` | `_inline_error` | 字段级内联错误提示：11px + 危险红 `#cf4452` + 透明底，默认 `setVisible(False)` |
| `common.py:263` | `_badge_label` | **状态徽章（业务语义核心）**：20px 高胶囊、9px 圆角、`rgba(color,30)` 半透明底 + 同色文字；色值来自 `_YES_NO_COLORS`（已解决=绿/红，我们问题=橙/灰，主动发起=蓝/灰） |
| `common.py:317` | `_row_btn` | 表格行内操作按钮（primary 强调色实心 / danger 红描边 / ghost 中性描边），hover/pressed 由 `lighten/darken` 令牌派生；`_ROW_BTN_TMPL` 见 269–274 行 |
| `entry.py:54` | `_init_ui` | 周期指示芯片：胶囊（`border-radius:10px`）+ `rgba(info,18)` 底 + info 蓝字 + 3×10 内边距 |
| `entry.py:66` / `entry.py:69` | 表单滚动区 | 滚动区透明无边框 + 内部视口 `QWidget { background: transparent; }` |
| `entry.py:151` / `152` | `_update_required_progress` | 必填进度：进度文字变**成功绿**；进度条槽 `rgba(success,18)` + 填充块 success 绿 + 2px 圆角 + 无边框 |
| `entry.py:161` / `162` | 同上（else 分支） | 必填未齐：文字变**警告橙**；进度条整体转 warning 橙 |
| `form.py:176` | 桌号关联确认条 | `QLabel#aftersaleLinkBar`：`rgba(success,26)` 底 + success 绿字 + 6px 圆角 + 5×10 内边距 + 12px 字（选桌后展示"桌号 + SNK"的可视反馈） |
| `records.py:375` | `_make_stats_card` | 指标卡大数字：24px / 字重 500 / 透明底 |
| `records.py:387` | `_make_batch_bar` | **批量操作条**：`QWidget#batchBar` 蓝色（`info`）20% 底 + 90% 蓝边 + 6px 圆角（勾选行后浮现） |
| `records.py:456` | `_update_source_label` | 数据源指示文字着色：MySQL 在线=绿 / 本地 SQLite=中性灰 / MySQL 不可用降级=橙 |
| `records.py:709/717/724/731` | `_update_overview` | 四张指标卡数字：`base`（24px/500/透明底）＋ 有积压时"未解决"数字追加危险红 `color` |
| `records.py:889` | `_make_ops_cell` | 重要行（`is_important`）操作列容器底色（样式串在循环外预构建，避免每行重算） |
| `settings.py:224` | 设置页滚动区 | `QScrollArea { border: none; background: transparent; }` |
| `stats_dialog.py:318` | `_init_ui` | 统计范围胶囊：透明底 + 2×10 内边距 + `rgba(0,188,212,.35)` 青色描边 + 6px 圆角（**注意：此处青色是硬编码，未走 `substitute_accent`**） |
| `stats_dialog.py:353` | 趋势卡提示 | 次要灰字（`_muted_color()` 主题自适应）+ 透明底 |
| `stats_dialog.py:371` | 图表占位文字 | 同上（居中对齐的次要灰） |
| `stats_dialog.py:389` | 地区分布统计值 | 次要灰字 |
| `stats_dialog.py:414` | 类型分布提示 | 次要灰字 |
| `stats_dialog.py:450` | `_make_kpi` | KPI 大数字：26px / 字重 600 / 透明底 |
| `stats_dialog.py:453` | 同上 | KPI 副说明：次要灰字 |
| `stats_dialog.py:582` | `_update_summary` | 六个 KPI 数字按语义着色：已解决=绿、未解决=红、解决率=蓝、主动发起=蓝、我方问题=橙 |

#### ④ `windows/management/`

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `common.py:423` | `_ReadOnlySelectDelegate.createEditor` | 表格双击进入的只读编辑框：`palette(base)` 底 + `palette(highlight)` 1px 边 + 4px 圆角 + 2px 内边距 + 选中配色跟随调色板（**这是全项目唯一用 `palette()` 的 QSS**，注释解释：主题切换后 QSS 的 `palette()` 不会重新解析，这里因为编辑框是临时创建所以无妨） |
| `device_page.py:169/173` | `_apply_theme` | **文件列表面板背景**（`QWidget#fileListPanel`）：深 `#2b2b2b` + 左侧 1px `#3f3f3f` 线 / 浅 `#ffffff` + `#e0e0e0` 线。注释（91–97 行）是本项目最有价值的一条经验：**`setCustomStyleSheet` 在普通 QWidget 上永远不生效**（`CustomStyleSheetWatcher` 只在控件注册过 `styleSheetManager` 时才安装），所以这里必须"直接 `setStyleSheet` 显式色值 + 监听 `themeChanged` 重应用"；且必须 `WA_StyledBackground` 否则背景不绘制 |
| `health_page.py:222` | 预警条目 | ⚠ 警告图标：危险红 + 15px |
| `health_page.py:232` | 同上 | 预警正文：危险红 |
| `health_page.py:235` | 同上 | "查看设备 →"跳转链接：危险红 + 字重 600 |
| `health_page.py:304` | 预警区标题 | 加粗 + 危险红 |
| `health_page.py:313` | 空预警提示 | 灰 `#8a919b` |
| `health_page.py:392` | 排行榜提示 | 灰 `#8a919b`（硬编码，未走令牌） |
| `image_viewer.py:183` | 图片预览区 | `rgba(127,127,127,8%)` 中性半透明底 + 8px 圆角（深浅主题通用，图片浮于其上） |
| `moyu_page.py:66` | 摸鱼页滚动区 | `QScrollArea { border: none; background: transparent; }` |
| `moyu_widgets.py:300` | 贪吃蛇提示 | 灰 `#8a8f98` |
| `moyu_widgets.py:1151` | 小说阅读器数据源 | 灰 `#8a8f98` |
| `moyu_widgets.py:1190` | 老板键伪装视图说明 | 灰 `#8a8f98` |
| `moyu_widgets.py:1194` | 老板键伪装日志 | **`QTextBrowser { background:#0b1015; color:#9fb3c8; border:none; }`** —— 伪装成"系统日志"的深蓝黑终端观感（配合 `Consolas 10` 字体），**纯业务创意，qfw 无对应能力** |
| `settings_page.py:125/127` | 设置页滚动区 | 滚动区 + 视口双层透明 |
| `widget_page.py:160/162` | 组件测试页 | 滚动区 + 容器透明 |
| `widget_page.py:252` | 媒体播放页 | 滚动区透明 |
| `widget_page.py:316` | 视频画面 | **`background:#101418; border-radius:8px; color:#8a8a8a; font-size:13px`** —— 播放器屏幕黑底 + "未加载媒体"占位灰字（qfw 已移除 VideoWidget，此处自建） |
| `widget_page.py:525/527` | 组件列表页 | `SmoothScrollArea` + 容器透明 |

#### ⑤ `windows/remote_session/`

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `ansi_terminal.py:77` | `ANSITerminalWidget.__init__` | **SSH 终端底色**：`QTextEdit { background-color:#1e1e1e; border:none; }`。这是终端观感的核心（配合 `Consolas 10` 与 ANSI 富文本着色），**深浅主题都是黑底**（终端不应随主题变白） |
| `ssh_terminal.py:166` | `_init_ui` | 断线通知条：`rgba(255,152,0,0.15)` 橙底 + `#ff9800` 橙边 + 4px 圆角 |
| `sftp_window.py:539` | 健康指示器初始态 | `●` 圆点：灰色 + 14px |
| `sftp_window.py:633` | `_set_health_indicator` | 健康指示圆点按延迟着色：绿 `#4CAF50` / 橙 `#FF9800` / 红 `#F44336` / 灰（延迟 <200ms 绿，<500ms 橙，否则红） |
| `conn_diag_panel.py:358` | 底部状态栏 | 提示灰字 `gray`（"失败记录置顶 · 右键行可复制完整内容"） |
| `conn_diag_panel.py:574` | 汇总视图提示 | 提示灰字 `gray` |
| `rdp_window.py:101` | 状态栏 | **纯布局副作用**：`padding: 2px 8px`（不设颜色，只为把状态文字推离边缘） |
| `window.py:71` | 空态提示 | 灰 `#8a919b` + 居中 |

#### ⑥ `windows/run_video/`

| 文件:行 | 所在函数 | 样式功能 |
|---|---|---|
| `entry.py:56/59` | 表单滚动区 | 滚动区 + 视口双层透明（与 aftersale/entry 同款） |
| `entry.py:114/115` | `_update_required_progress` | 必填进度 3/3：文字绿 + 进度条 success 绿槽/块 |
| `entry.py:124/125` | 同上（else） | 必填未齐：文字橙 + 进度条 warning 橙槽/块 |
| `records.py:354` | `_make_stats_card` | 指标卡大数字 24px/500/透明底 |
| `records.py:373` | `_update_source_label` | 数据源指示着色（绿/灰/橙，同 aftersale） |
| `settings.py:94` | 设置页滚动区 | 滚动区透明无边框 |

#### ⑦ 走 `setCustomStyleSheet`（**不是 setStyleSheet，但是 QSS**，必须一并评估）

| 文件:行 | 样式功能 | 是否可删 |
|---|---|---|
| `main_window/ui_mixin.py:1805` | **把 light/dark.qss 全文注册到 `stackedWidget` 的 light/dark 双槽** | P0 目标 |
| `main_window/ui_mixin.py:1816` | 工具栏 3 个 RadioButton 固定 32px 行高 | 保留（qfw 默认 24px） |
| `main_window/process_mixin.py:44/47` | "结束"状态红底按钮（DARK `#d13438` / LIGHT `#c42b1c` + hover/pressed），空闲时置空串还原 | 可用 qfw `setCustomStyleSheet` 或换 `PrimaryPushButton` 语义，但需人工确认观感 |
| `autowork_with_table.py:220` | 工具栏 RadioButton 32px 行高（与 ui_mixin 同款，两套并存） | 保留 |
| `windows/management/device_page.py:155` | 4 个迁移按钮语义底（使用=绿/精度=橙/问题=红/废弃=灰 + hover/pressed 派生） | 保留（业务语义色） |
| `windows/management/image_viewer.py:212` | 同上，图片查看器底部 4 个迁移按钮 | 保留（业务语义色） |

---

### 2.5 用途分类与占比

按 **88 处业务调用点**分类：

| 类别 | 说明 | 处数 | 占比 |
|---|---|---:|---:|
| ① 文字语义色 / 状态色 | 数据源灯、预警红、健康圆点、次要灰、强调色 | **31** | **35.2%** |
| ② 滚动区 / 容器透明化 | 消除 qfw 滚动区默认灰底（多处重复） | **18** | **20.5%** |
| ③ 业务语义徽章 / 提示条 / 按钮 | 状态徽章、批量条、进度条语义色、迁移按钮、断线条 | **16** | **18.2%** |
| ④ 排版（字号 / 字重 / 内边距） | 大数字 24/26px、标题 600 字重、节标题内边距 | **11** | **12.5%** |
| ⑤ 业务容器底色 / 边框 | 文件列表面板、图片预览区、视频黑屏、图标块、色块 | **7** | **8.0%** |
| ⑥ 终端 / 富文本着色 | ANSI 终端黑底、老板键伪装日志深蓝底 | **2** | **2.3%** |
| ⑦ 功能性副作用（非视觉） | Pivot padding 压缩行高、RDP 状态栏 padding | **2** | **2.3%** |
| ⑧ 表格单元格编辑框 | 只读编辑框配色 | **1** | **1.1%** |
| ⑨ 全局主题分发 | `core/theme_qss.py:82` | **1** | **1.1%** |
| | **合计** | **88** | **100%** |

**关键读数**：纯"主题色/深浅底"类的（②+④+⑨ 中的大部分 + ⑤ 的一半）约占 30%；而**业务语义色（①+③）占 53.4%** —— 这正是 qfw 覆盖不到的大头。

---

## 3. qfluentwidgets 主题能力对照

### 3.1 官方能力（WebFetch 实测，非记忆）

抓取地址：https://qfluentwidgets.com/zh/pages/theme （标题《主题 | QFluentWidgets》，作者 zhiyiYo，2023-08-17）

官方文档明确提供的能力：

1. **主题切换**：`setTheme()` 接受 `Theme.LIGHT` / `Theme.DARK` / `Theme.AUTO`（跟随系统；检测不到时回落浅色）。主题变化时 `qconfig` 发出 `themeChanged` 信号；另有 `toggleTheme()` 快捷切换。
2. **自定义样式表（自动跟随主题）**：继承 `StyleSheetBase` 并重写 `path(theme)`，返回 `qss/{light|dark}/xxx.qss`；实例上调用 `StyleSheet.XXX.apply(widget)`。
3. **QSS 占位符**（qfw 在渲染 QSS 时替换）：`--ThemeColorPrimary`、`--ThemeColorLight1/2/3`、`--ThemeColorDark1/2/3`、`--FontFamilies`。
4. **`setCustomStyleSheet(widget, lightQss, darkQss)`**：在**原有样式基础上追加**自定义样式（官方原话："添加新样式"）。QtDesigner 中等价于给控件加 `lightCustomQss` / `darkCustomQss` 字符串动态属性。
5. **主题色**：`themeColor()` 读取、`setThemeColor()` 修改（接受 `QColor` / `Qt.GlobalColor` / 十六进制字符串）；变化发 `themeColorChanged` 信号。可配合 `qframelesswindow.utils.getSystemAccentColor()` 跟随系统主题色。
6. **系统主题监听**：`SystemThemeListener` 线程 + `closeEvent` 中 `terminate()`；Mica 场景官方建议在 `_onThemeChangedFinished` 里 `QTimer.singleShot(100, ...)` 重试 `setMicaEffect`。
7. **字体**：v1.9.0+ `setFontFamilies()` / `fontFamilies()`，默认 `['Segoe UI', 'Microsoft YaHei', 'PingFang SC']`。

> **重要认知纠正**：qfw 的主题机制**本身就是 QSS**（34 个内置 `.qss` 资源 + `setCustomStyleSheet` 追加）。所以"弃用 QSS"这个提法在技术上不成立——准确的问题是：**"能否删掉我们自己写的那部分 QSS"**。下文一律按这个口径判定。

### 3.2 qfw 1.11.3 内置 QSS 覆盖边界（读库实证）

内置 qss 资源 **34 个**（`:/qfluentwidgets/qss/light|dark/`）：

`button` `calendar_picker` `card_widget` `check_box` `color_dialog` `combo_box` `dialog` `expand_setting_card` `flip_view` `fluent_window` `folder_list_dialog` `info_badge` `info_bar` `label` `line_edit` `list_view` `media_player` `menu` `message_dialog` `navigation_interface` `pips_pager` `pivot` `setting_card` `setting_card_group` `slider` `spin_box` `state_tool_tip` `switch_button` `tab_view` `table_view` `teaching_tip` `time_picker` `tool_tip` `tree_view`

**对原生 Qt 控件的覆盖检测（逐 token 全文检索 34 个 qss）**：

| 原生选择器 | qfw 是否覆盖 | 判定 |
|---|---|---|
| `QProgressBar` | ❌ **无** | qfw 的 `ProgressBar` 是 `QProgressBar` 子类但**无任何内置 QSS**，靠 `paintEvent` + `QPainter` 自绘（`components/widgets/progress_bar.py:13`，默认 `setFixedHeight(4)`） |
| `QSplitter` | ❌ **无** | 完全无对应能力 |
| `QScrollBar` | ❌ **无** | 完全无对应能力（qfw 只提供 `SmoothScrollDelegate` 改滚动行为，不改外观） |
| `QDateEdit` | ❌ **无** | 需改用 qfw `DatePicker` / `ZhDatePicker` / `CalendarPicker` |
| `QToolTip` | ❌ **无** | qfw 的 `ToolTip` 是**自绘控件**（`ToolTip` 类 + `ToolTip` 选择器），与原生 `QToolTip` 是两套东西，不会互相影响 |
| `QScrollArea`（通用） | ❌ **无通用规则** | 仅在 `color_dialog` / `folder_list_dialog` / `navigation_interface` 三个特定 qss 内部出现，无全局 `QScrollArea` 规则 → **这就是 18 处"滚动区透明化" QSS 存在的根本原因** |
| `QPlainTextEdit` / `QTextEdit` | ❌ **无**（非 LineEdit 分支） | qfw 的 `TextEdit`/`PlainTextEdit`（`line_edit.py:450/466`）走的是 `LINE_EDIT` qss，但**该 qss 内没有 `QTextEdit`/`QPlainTextEdit` 选择器**，只有 `TextEdit`/`PlainTextEdit`/`EditLayer` 等 qfw 类名选择器 → 见下方 ⚠️ |
| `QFormLayout QLabel` | ❌ **无** | 无对应能力 |
| `QListWidget` / `QTreeWidget`（原生） | ❌ **无原生规则** | 但 `list_view.qss` / `tree_view.qss` 有 `ListWidget` / `QTreeView` 等 qfw 类名选择器 → 换用 qfw `ListWidget`/`TreeWidget` 即可覆盖 |

> ⚠️ **一个必须点名的坑**：`autowork_with_table.py:566` 里 `self.show_log = FluentTextEdit()`，而 `FluentTextEdit` 是 `from qfluentwidgets import TextEdit as FluentTextEdit`（`autowork_with_table.py:29`）——它是 **`QTextEdit` 子类，不是 `QPlainTextEdit`**。因此 `styles/*.qss` 里的 `QPlainTextEdit#show_log { ... }` 选择器**根本不会命中它**！这段 12 行 QSS（light 48–59 / dark 58–70）**当前是死规则**，日志区的实际外观来自 qfw `LINE_EDIT` qss + `setFont(Consolas,10)`。这一条极大提升了"可删"的比例。

### 3.3 逐项对照表

判定图例：
- ✅ **A 类 — qfw 组件自带样式自动覆盖**：删掉后外观不变或变得"更 Fluent"（可接受）
- 🔶 **B 类 — 需改用 qfw 组件/属性才能覆盖**：必须先做组件替换，否则删了就掉回原生观感
- ❌ **C 类 — qfw 无对应能力，必须保留**（或改写成代码绘制）

| # | QSS 样式项 | 证据位置 | 判定 | 覆盖方式 / 说明 |
|---|---|---|---|---|
| 1 | 工作台整体底色 `#centralwidget` | light:9 / dark:8 | 🔶B | qfw `FluentWindow` 自带 `FluentWindowBase` 背景规则（`fluent_window.qss`），但只在**窗口自身**生效；`centralwidget` 是自定义容器。可删（回落到 FluentWindow 背景）或改挂 `setCustomStyleSheet` |
| 2 | 工具栏三件套底色/分隔线 | light:14–24 / dark:13–23 | 🔶B | 容器底色非 qfw 能力；但 `toolbar_scroll` 是 `QScrollArea` → 可改用 qfw `ScrollArea`（自带透明化逻辑） |
| 3 | 原生 `QPushButton` 全套 | light:27–45 / dark:26–44 | ✅A | qfw `PushButton` 自带 44 条规则（含 hover/pressed/disabled）。实测（§3.4）qfw 按钮完全不受这段影响；项目中 103 处 `PushButton` 全是 qfw 版本 → **这段只影响极少数原生 QPushButton** |
| 4 | 日志区 `QPlainTextEdit#show_log` | light:48–59 / dark:58–70 | ✅A | **死规则**：`show_log` 实为 qfw `TextEdit`（`QTextEdit` 子类），选择器不命中。直接删 |
| 5 | 日志状态条 + 设备/条数字色 | light:62–70 / dark:73–81 | ❌C | `#8892a2` / `#6B7280` 是**自定义次要文字色**，qfw 无对应。但可改用 `CaptionLabel` + `setTextColor` 双槽替代（B 类路径） |
| 6 | 左侧分组标题 `#left_panel_header` | light:73–80 / dark:84–91 | ✅A | 可换 qfw `StrongBodyLabel`/`CaptionLabel`；青色可用 `--ThemeColorPrimary` 占位符 |
| 7 | 左右面板容器底色 | light:83–85 / dark:94–96 | 🔶B | 容器底色非 qfw 能力，但可删（透明后露出 Fluent 背景） |
| 8 | 原生 `QListWidget`/`QTreeWidget` 皮肤 | light:88–100 / dark:99–111 | 🔶B | 项目里 `ListWidget`×5、`TreeWidget`×4 **已是 qfw 版本**（`list_view.qss`/`tree_view.qss` 覆盖）；剩余原生 `QListWidget` 4 处（`settings_dialog.py:505`、`aftersale/dialogs.py:105`、`aftersale/form.py:161`、`management/dialogs.py:152`、`port_fake.py:62`、`ssh_terminal.py:721`）需先换成 qfw |
| 9 | `QDateEdit` 皮肤 | light:101–114 / dark:112–125 | ✅A | **项目中已无 `QDateEdit` 实例**（grep 确认 0 处）→ 死规则，直接删 |
| 10 | 远程面板/分区标签 | light:117–137 / dark:128–148 | 🔶B | `#p2p_panel` 已是 qfw `CardWidget`（`autowork_with_table.py:597`）→ 底色可删；`#section_label`/`#p2p_panel_header` 需改 qfw Label |
| 11 | `QSplitter::handle` 2px 线 + 悬停青 | light:140–151 / dark:151–162 | ❌C | **qfw 完全没有 QSplitter 样式**。删了就回到系统默认粗把手。要么保留，要么改用 `qfluentwidgets` 的 `SplitWidget`/`CardWidget` 分隔 |
| 12 | 全局 `QLabel` 兜底色 | light:154–157 / dark:165–168 | ✅A | 实测（§3.4）不影响 qfw `BodyLabel`/`CaptionLabel`（qfw `FluentLabelBase{color:black/white}` 优先）；只影响原生 QLabel。可删 |
| 13 | `#list_empty_hint` 空态提示 | light:160–164 / dark:171–175 | 🔶B | 改 `CaptionLabel` + `setTextColor` 即可 |
| 14 | `#menubar_widget` 容器 | light:167–170 / dark:178–181 | ✅A | 菜单栏已移入设置页（`main_window.py:161` 注释），**疑似死规则** → 优先验证后删 |
| 15 | `QScrollBar` 全套（8px 细条 + 悬停青） | light:173–214 / dark:186–227 | ❌C | **qfw 无 QScrollBar 样式**。删了恢复系统默认粗滚动条。观感差异明显，建议保留或改用 qfw `SmoothScrollArea`/自绘滚动条 |
| 16 | `QToolTip` | light:217–223 / dark:230–236 | ⚠️待定 | 规则**大概率失效**（QToolTip 是顶层窗口，不继承祖先样式表）；qfw 另有一套 `ToolTip` 自绘控件。建议实测后删，或改挂 `QApplication.setStyleSheet` |
| 17 | `QProgressBar` + `::chunk` | light:226–237 / dark:239–250 | ❌C | qfw `ProgressBar` 无内置 QSS（自绘）。**但项目已在 `entry.py:152/162`、`run_video/entry.py:115/125` 就地覆盖为语义色** → 全局这段可删，保留就地那 4 处 |
| 18 | `QFormLayout QLabel` | light:240–243 / dark:253–256 | 🔶B | 改 qfw `CaptionLabel` 即可 |
| 19 | 18 处"滚动区/容器透明化" | 见 §2.4 | ❌C | **qfw 无通用 `QScrollArea` 规则**。这是刚需（否则每页都是灰底）。**必须保留** |
| 20 | 31 处文字语义色 | 见 §2.4 | ❌C | 语义色（数据源绿/橙、预警红、健康圆点绿橙红）**qfw 完全没有**。部分可用 `setTextColor` 双槽替代，但状态灯类必须保留 |
| 21 | 徽章 / 批量条 / 迁移按钮（业务语义） | `common.py:263`、`records.py:387`、`common.py:204-227` | ❌C | 业务语义色，qfw 无。可用 qfw `InfoBadge`/`PillPushButton` + `setCustomStyleSheet` 改造，但属重构而非删除 |
| 22 | 排版（24/26px 数字、600 字重） | 见 §2.4 | 🔶B | 可改用 qfw `TitleLabel`/`StrongBodyLabel`（内置字号层级）或 `setFont`；但 `font-size` 写在 QSS 里与 `_apply_global_font()` 的 `setFont()` 是两套机制，**混用有已知冲突**（`dark.qss:55-57` 注释明确规避过） |
| 23 | ANSI 终端黑底 / 老板键伪装日志 | `ansi_terminal.py:77`、`moyu_widgets.py:1194` | ❌C | **终端观感是功能需求**（SSH 终端不能随主题变白）。必须保留 |
| 24 | 视频黑屏 / 图片预览底 | `widget_page.py:316`、`image_viewer.py:183` | ❌C | 业务容器，qfw 无 |
| 25 | 文件列表面板背景 | `device_page.py:169/173` | ❌C | 注释已论证 `setCustomStyleSheet` 在普通 QWidget 上不生效 → **必须保留 `setStyleSheet`** |
| 26 | Pivot padding 压缩 / RDP padding | `pivot_page.py:101`、`rdp_window.py:101` | ❌C | **功能性副作用**（撑开/压缩布局），不是"样式"，qfw 无法替代。必须保留 |
| 27 | 工具栏 RadioButton 32px 行高 | `ui_mixin.py:1816`、`autowork_with_table.py:220` | ❌C | qfw `RadioButton` 默认 24px；`min-height` 只能靠 QSS 覆盖（`setFixedHeight` 会被 QSS 打回，见 `ui_mixin.py:1818-1828` 注释）。必须保留 |
| 28 | "结束"红底按钮 | `process_mixin.py:44/47` | 🔶B | 可换 `PrimaryPushButton` + `setCustomStyleSheet`，已是 qfw 机制 |
| 29 | 主题色联动（`substitute_accent`） | `core/theme_qss.py:50-56` | 🔶B | 官方提供 `--ThemeColorPrimary` 占位符可等价替代（但需注意：占位符只在 qfw 渲染的 QSS 中生效，自定义 `.qss` 需走 `StyleSheetBase`） |

**汇总**：

| 判定 | 项数（按功能项，非调用点） | 占比 |
|---|---:|---:|
| ✅ A — qfw 已覆盖/死规则，可直接删 | **9** | 31% |
| 🔶 B — 需先替换组件/属性，改造后可删 | **8** | 28% |
| ❌ C — qfw 无能力，必须保留 | **11**（+2 待定） | 41% |

> 按**调用点数量**换算（更能反映工作量）：88 处中约 **48 处（55%）属 A/B（可删或改造后删）**，约 **36 处（41%）必须保留**，4 处待定。

### 3.4 渲染实证：祖先级 legacy QSS vs qfw 自带样式

用 offscreen 渲染 + 像素取样验证优先级（qfw 1.11.3 / PySide6 6.11.2 / `Theme.LIGHT`），构造"宿主容器套 legacy QSS" vs "不套"两个场景对比：

```
原生 QPushButton       有legacy=#00bcd4   无legacy=#fcfcfc   ← legacy 生效
qfw PushButton        有legacy=#ffffff   无legacy=#ffffff   ← 无影响（qfw 覆盖）
qfw PrimaryPushButton 有legacy=#009faa   无legacy=#009faa   ← 无影响（qfw 覆盖）
qfw ProgressBar       有legacy=#b1b1b1   无legacy=#b1b1b1   ← 无影响（自绘盖过）
qfw BodyLabel  文字最暗像素  有legacy=#000000  无legacy=#000000  ← 无影响（qfw 覆盖）
qfw CaptionLabel 文字最暗像素 有legacy=#000000  无legacy=#000000  ← 无影响（qfw 覆盖）
原生 QLabel    文字最暗像素  有legacy=#6b7280  无legacy=#000000  ← legacy 生效
qfw ListWidget        差异像素 0  ← 完全被 qfw 覆盖
qfw TableWidget       差异像素 0  ← 完全被 qfw 覆盖
原生 QDateEdit        差异像素 517 ← legacy 生效
原生 QListWidget      差异像素 183 ← legacy 生效
```

**结论（可直接作为迁移决策依据）**：
1. **qfw 组件自带样式优先于祖先容器继承的 legacy 规则**（Qt 的级联规则：控件自身样式表 > 祖先传播规则）。因此删除 legacy QSS **不会**改变 qfw `PushButton`/`BodyLabel`/`ListWidget`/`TableWidget` 等组件的外观。
2. **原生 Qt 控件完全依赖 legacy QSS**，`QDateEdit`/`QListWidget`/`QPushButton` 一旦删除会回落到系统默认观感。
3. `styles/*.qss` 中大量类型选择器（`QPushButton` / `QLabel` / `QListWidget` / `QTreeWidget` / `QDateEdit`）**对项目里的 qfw 组件早已不产生实际作用**——它们只是历史包袱。

---

## 4. 明确结论

### 4.1 能否直接弃用并移除 QSS？

> **不能一次性全删。准确结论是"部分可删、部分必须保留"：**
>
> 1. **`styles/light.qss` 与 `styles/dark.qss` 两个文件：可以整体删除（P0 阶段），但需先做完 3 处组件替换。**
>    依据：两文件共 17 个功能块，其中 6 块是死规则或已被 qfw 全覆盖（`QDateEdit` 无实例、`QPlainTextEdit#show_log` 选择器不命中、`QListWidget/QTreeWidget` 已换 qfw、`QPushButton` 被 qfw 覆盖、`QLabel` 被 `FluentLabelBase` 覆盖、输入框/微调框/QMenu 段已自行删除）；`QProgressBar` 段被 4 处就地覆盖架空；`QToolTip` 段大概率失效。
>    真正"删了会掉观感"的只有 4 块：`QSplitter::handle`、`QScrollBar`、`#centralwidget`/面板容器底色、`#log_status_bar` 文字色 —— 这 4 块建议**下沉为 `setCustomStyleSheet` 片段**（约 40 行），而不是保留两个 250 行的文件。
>
> 2. **88 处内联 QSS：约 36 处必须保留（41%），52 处可删或改造后删。**
>    - **必须保留**：18 处滚动区透明化、ANSI 终端/伪装日志底、健康圆点/数据源灯/预警红等语义色、业务徽章/批量条/迁移按钮、文件列表面板背景、Pivot/RDP 的 padding 副作用、工具栏 RadioButton 行高、图片/视频容器底。
>    - **可删**：已死规则的残留、`#menubar_widget`、日志区相关（若证实为死规则）。
>
> 3. **红线未被触碰**：主窗口业务 QSS 已挂 `stackedWidget` + `setCustomStyleSheet`（`ui_mixin.py:1805`），符合规范；`apply_window_qss` 的 7 个调用方经逐一核对**全部是原生 `QDialog`**（`QuickPhraseDialog` / `AfterSaleStatsDialog` / `ImageViewerDialog` / `ConnDiagPanel` / `RDPWindow` / `SFTPWindow` / `SSHTerminalWindow`），**不是 FluentWindow**，不触发 Mica 杀手。

### 4.2 必须保留清单（逐项：位置 + 为什么 + 建议）

| 优先级 | 位置 | 为什么不能被 qfw 替代 | 处理建议 |
|---|---|---|---|
| **P0** | `core/flow_widgets.py:27,28`；`aftersale/entry.py:66,69`；`aftersale/settings.py:224`；`management/settings_page.py:125,127`；`management/widget_page.py:160,162,252,525,527`；`management/moyu_page.py:66`；`run_video/entry.py:56,59`；`run_video/settings.py:94`；`main_window/hub_pages.py:259`；`main_window/pivot_page.py:159`（**共 18 处**） | qfw **无通用 `QScrollArea` 规则**（仅在 3 个特定对话框 qss 内部出现）。删掉后所有滚动区露出系统灰底，是**最直观的视觉回归** | ✅ 原样保留。可选优化：抽成 `core/theme_qss.py` 里的 `TRANSPARENT_SCROLL_QSS` 常量统一引用，消除 10 处重复字符串 |
| **P0** | `windows/remote_session/ansi_terminal.py:77` | SSH 终端必须保持 `#1e1e1e` 黑底（不随主题变白），这是**功能需求**不是装饰 | ✅ 原样保留，并在注释里写明"业务终端观感，非主题样式，勿删" |
| **P0** | `main_window/ui_mixin.py:1816`、`autowork_with_table.py:220` | qfw `RadioButton` 内置 `min-height:24px`；`setFixedHeight` 会被 QSS 打回（`ui_mixin.py:1818-1828` 注释有实证）。**只有 QSS 能压过 QSS** | ✅ 原样保留（已经是 `setCustomStyleSheet` 合规姿势）。另建议合并 ui_mixin 与 awt 两套重复实现 |
| **P0** | `windows/management/device_page.py:169,173` | 普通 `QWidget` 上 `setCustomStyleSheet` **永远不生效**（`CustomStyleSheetWatcher` 只在注册过 `styleSheetManager` 的控件上安装，`device_page.py:91-97` 有实证）。这是全项目唯一可行的姿势 | ✅ 原样保留，注释已很完整，建议把这条经验写进 `docs/` 作为团队常识 |
| **P0** | `main_window/pivot_page.py:101`、`windows/remote_session/rdp_window.py:101` | **功能性副作用**（用 padding 压缩/撑开布局），不是视觉样式；qfw 无等价能力 | ✅ 原样保留，注释标明"布局副作用，非样式" |
| **P1** | `windows/management/health_page.py:222,232,235,304`、`moyu_widgets.py:300,1151,1190`、`remote_session/window.py:71`、`conn_diag_panel.py:358,574`、`aftersale/stats_dialog.py:353,371,389,414,453`（**次要灰字**） | qfw 无"次要文字色"属性暴露（只有 `setTextColor` 双槽 API） | 🔶 改 `CaptionLabel.setTextColor(light, dark)` 双槽（qfw 官方 API，见 `setting_cards.py:95` 已有先例），可逐步去 QSS |
| **P1** | `aftersale/records.py:456`、`run_video/records.py:373`、`main_window/main_window.py:502,505`、`remote_session/sftp_window.py:539,633`（**状态灯**） | 数据源/健康度/延迟状态色是**业务语义色**（绿/橙/红/灰），qfw 主题色只有单一 accent，无法表达 | ✅ 保留。建议把硬编码 `#1D9E75`/`#BA7517`/`#4CAF50`/`#F44336` 全部换成 `core/design_tokens.SEMANTIC` 令牌（目前 `main_window.py:502,505` 就是漏网之鱼） |
| **P1** | `aftersale/entry.py:151,152,161,162`、`run_video/entry.py:114,115,124,125` | 必填进度条的成功绿/警告橙是业务语义；且 qfw `ProgressBar` 无内置 QSS，只能就地覆盖 | ✅ 保留。可封装成 `_apply_progress_semantic(prog, label, ok)` 消除 4 处重复 |
| **P1** | `aftersale/common.py:263`（徽章）、`records.py:387`（批量条）、`records.py:889`（重要行底）、`form.py:176`（关联确认条）、`entry.py:54`（周期芯片）、`ssh_terminal.py:166`（断线条） | 业务语义半透明底 + 同色文字，qfw 无对应组件 | ✅ 保留。可选：徽章改 qfw `InfoBadge`（`info_badge.qss` 存在）+ `setCustomStyleSheet` 染业务色 |
| **P1** | `windows/management/common.py:204-227` + `device_page.py:155` + `image_viewer.py:212`（迁移按钮语义底） | 4 个业务分类（使用/精度/问题/废弃）的专属配色 | ✅ 保留（已经是 `setCustomStyleSheet` 合规姿势，且已用 `lighten/darken` 令牌派生，是**本项目 QSS 写得最好的一处**，可作为范本） |
| **P1** | `aftersale/common.py:111,139,142,208,223,317` | 候选列表皮肤、分组竖条、字段标签、错误提示、行内按钮 —— 都是业务自定义控件皮肤 | 🔶 部分可改造：`_field_label`/`_inline_error` 可换 `CaptionLabel` + `setTextColor`；`_row_btn` 可换 qfw `PushButton` + `setCustomStyleSheet`（已是 primary/danger/ghost 三档，与 qfw 语义接近） |
| **P2** | `aftersale/records.py:375,709,717,724,731`、`stats_dialog.py:450,582`、`run_video/records.py:354`（大数字排版）、`settings_dialog.py:677`、`setting_cards.py:88,181`（标题字重） | 字号/字重是排版不是主题；但**与 `_apply_global_font()` 的 `setFont()` 有已知冲突**（`dark.qss:55-57` 注释明确规避过在 QSS 里写 font-family） | 🔶 字号改 `qfw TitleLabel`/`StrongBodyLabel`（内置层级）；字重改 `setFont(QFont)`。**注意别在 QSS 里写 font-family** |
| **P2** | `management/widget_page.py:316`、`image_viewer.py:183`、`hub_pages.py:201,291` | 业务容器/占位块底色，qfw 无 | ✅ 保留（深浅主题通用的 `rgba` 中性色写法已很稳，可原样留着） |
| **P2** | `styles/dark.qss:151-162` + `light.qss:140-151`（`QSplitter::handle`）、`light.qss:173-214`/`dark.qss:186-227`（`QScrollBar`） | **qfw 完全无 QSScrollBar / QSplitter 样式**，删了回到系统默认粗控件，观感回归明显 | ⚠️ 若 P0 阶段整体删 `styles/*.qss`，请**先把这两段下沉**为 `setCustomStyleSheet(stackedWidget, ...)` 片段（约 25 行） |

### 4.3 可直接删除清单（P0，零视觉风险）

| 位置 | 删除理由 |
|---|---|
| `styles/*.qss` 的 `QDateEdit` 段（light:101–114 / dark:112–125，共 25 行） | 全项目 **0 个 `QDateEdit` 实例**（grep 实证） |
| `styles/*.qss` 的 `QPlainTextEdit#show_log` 段（light:48–59 / dark:58–70，共 24 行） | `show_log` 实为 qfw `TextEdit`（`QTextEdit` 子类），`QPlainTextEdit` 选择器不命中（§3.2 ⚠️） |
| `styles/*.qss` 的原生 `QListWidget`/`QTreeWidget` item 态（light:94–100 / dark:105–111） | 项目 `ListWidget`×5 / `TreeWidget`×4 已是 qfw 版本，实测差异像素 0 |
| `styles/*.qss` 的 `QPushButton` 全套（light:27–45 / dark:26–44，共 38 行） | 实测 qfw PushButton/PrimaryPushButton 完全不受影响；原生 QPushButton 仅极少数且多在 QDialog（另有 `apply_window_qss` 兜底） |
| `styles/*.qss` 的全局 `QLabel` 兜底（light:154–157 / dark:165–168） | 实测 qfw `BodyLabel`/`CaptionLabel` 不受影响（`FluentLabelBase{color}` 优先） |
| `styles/*.qss` 的 `QToolTip` 段（light:217–223 / dark:230–236） | 大概率失效（顶层窗口不继承祖先 QSS）；qfw 另有自绘 `ToolTip`。**删除前建议真机确认一次** |
| `styles/*.qss` 的 `QProgressBar` 段（light:226–237 / dark:239–250） | 已被 `entry.py:152/162` 等 4 处就地覆盖架空 |
| `styles/*.qss` 的 `#menubar_widget` 段（light:167–170 / dark:178–181） | 菜单栏已整体移入设置页（`main_window.py:161` 注释），需确认后删 |
| `styles/dark.qss:182-183` 的 QMenu 注释段 | 已是纯注释 |
| `tools/probe_mica_*.py`、`smoke_fluent_mainwindow.py`、`verify_fluent_refactor.py` 中的 6 处 | 一次性调试探针，任务已完成。**建议整个归档到 `tools/_archive/`**（注意：`tools/__pycache__/` 里还有已删源文件的 `.pyc`，一并清理） |

---

## 5. 分阶段迁移方案

### P0 — 零风险清理（预计 0.5 天，**不动任何窗口级 setStyleSheet**）

| 项 | 内容 |
|---|---|
| **涉及文件** | `styles/light.qss`（删 130 行 / 243）、`styles/dark.qss`（删 130 行 / 256）、`main_window/ui_mixin.py`（`_load_qss` 保留，文件内容瘦身）、`tools/`（探针归档） |
| **动作** | ① 按 §4.3 删掉两个 qss 里的死规则段（保留 `centralwidget`、面板容器、日志状态条、`QSplitter`、`QScrollBar`、`#left_panel_header`、`#p2p_panel`、`#list_empty_hint`、`#toolbar_*`、`#section_label`、`QFormLayout QLabel`）<br>② 探针脚本移入 `tools/_archive/`，清 `__pycache__` 残留 `.pyc`<br>③ `dist/AutoWork/_internal/styles/*.qss` 是 2026-08-26 的**过期打包产物**（与源码 diff 不一致），下次打包自动覆盖，无需手改 |
| **风险** | 极低。全是已证实不生效的规则 |
| **验证** | ① `pytest` 保持 **260 passed** 基线<br>② 真机跑一遍：工作台 + 三个面板 + 七个 QDialog，深/浅/跟随系统三种模式各切一次<br>③ **洋红壁纸对照法**：确认 Mica 仍在（窗口背景未被纯色盖死） |
| **回滚** | `git checkout styles/ main_window/ui_mixin.py`（`styles/` 已入库） |

### P1 — 等价替换（预计 2~3 天，**仍不动窗口级**）

| 项 | 内容 |
|---|---|
| **涉及文件** | `aftersale/common.py`、`aftersale/records.py`、`aftersale/stats_dialog.py`、`aftersale/entry.py`、`aftersale/form.py`、`run_video/entry.py`、`run_video/records.py`、`management/health_page.py`、`management/moyu_widgets.py`、`management/common.py`、`remote_session/*.py`、`main_window/main_window.py` |
| **动作** | ① 次要灰字 → `CaptionLabel.setTextColor(light, dark)` 双槽（参考 `setting_cards.py:95` 已有实现）<br>② 硬编码色 → `core/design_tokens.SEMANTIC` 令牌（重点修 `main_window.py:502/505` 的 `#1D9E75`/`#BA7517`、`health_page.py:313/392` 的 `#8a919b`、`stats_dialog.py:318` 的硬编码青）<br>③ 原生 `QListWidget`（6 处）→ qfw `ListWidget`；原生 `QDateEdit`（0 处，无需动）<br>④ 18 处滚动区透明 QSS 抽成 `core/theme_qss.TRANSPARENT_SCROLL_QSS` 常量<br>⑤ 4 处必填进度条语义色封装成 `_apply_progress_semantic()` |
| **风险** | 中。`setTextColor` 与内联 QSS `color` **会冲突**（`setting_cards.py:186-188` 注释："color 必须留在 setTextColor 双槽，styleSheet 内联 color 会覆盖 custom 槽"）。**同一控件上二者只能取一** |
| **验证** | 同上 + 逐个弹窗截图对比（深浅各一套） |
| **回滚** | 按文件粒度 `git checkout`，每个文件独立提交 |

### P2 — 结构性收尾（预计 2 天，⚠️ **此阶段才允许触碰 QSS 挂载点**）

| 项 | 内容 |
|---|---|
| **⚠️ 红线动作** | 把 `styles/*.qss` 剩余内容（`QSplitter`、`QScrollBar`、容器底色、状态条文字色，约 60 行）**下沉为 `setCustomStyleSheet(stackedWidget, light_frag, dark_frag)` 片段**，然后**删除 `styles/` 目录** |
| **配套改动** | ① `main_window/ui_mixin.py:1805,1841-1852`：`_load_qss()` 改为返回内联常量<br>② `core/theme_qss.py:59-68`：`load_window_qss()` 改为拼接「下沉片段 + 原生控件补丁」<br>③ **三个 `.spec` 文件**（`AutoWork.spec:164-166`、`AfterSale.spec:144`、`Management.spec:103`）删除 `('styles', 'styles')` 数据项<br>④ `core/design_tokens.py:75,87` 两处注释里对 `dark.qss`/`light.qss` 的引用改为对 `GRAY_*` 令牌的说明 |
| **风险** | **高**。这是唯一触碰 QSS 挂载点的阶段。必须严格遵守：<br>• ❌ **绝对禁止**在 `FluentWindow`（`MainWindow`）实例上 `setStyleSheet`<br>• ✅ 只允许 `setCustomStyleSheet(self.stackedWidget, ...)` 双槽<br>• ✅ `apply_window_qss()` 的 7 个 `QDialog` 可继续用 `setStyleSheet`（非 FluentWindow，安全）<br>• ⚠️ 注意 `device_page.py:91-97` 的坑：普通 `QWidget` 上 `setCustomStyleSheet` 不生效 |
| **验证** | ① `pytest` 260 passed<br>② **洋红壁纸对照法必做**（Mica 一旦被杀不可逆！）<br>③ 主题切换 10 次循环（浅→深→跟随系统），确认无残留旧主题底色<br>④ 打包验证：`pyinstaller AutoWork.spec` 后确认 `_internal/styles/` 不再被引用且程序能启动（**这是隐性依赖最容易翻车的地方**） |
| **回滚** | 保留 `styles/` 目录不删（只改加载路径），出问题改回 `_load_qss()` 读文件即可，秒级回滚 |

---

## 6. 风险与残留依赖

### 6.1 隐性依赖（已 grep 查证）

| # | 依赖 | 证据 | 风险等级 | 处理 |
|---|---|---|---|---|
| 1 | **打包脚本/spec 引用 `styles/`** | `AutoWork.spec:164-166`（注释"styles/ 为只读主题资源，打包后位于 _internal/styles/"）、`AfterSale.spec:144`、`Management.spec:103` 均有 `('styles', 'styles')` | 🔴 高 | P2 删目录时**必须同步改三个 .spec**，否则打包报 `missing data` |
| 2 | **`build_exe.py` 未直接引用 styles** | `grep -n "styles\|qss" build_exe.py` → **0 命中**（它调用 spec 打包） | 🟢 无 | 无需改动 |
| 3 | **测试未断言 QSS** | `grep -rn "qss\|QSS\|setStyleSheet\|styles/" tests/ pytest.ini` → **0 命中** | 🟢 无 | 删 QSS 不会挂测试；但也意味着**测试对样式零保护**，只能靠人工冒烟 |
| 4 | **QSS 变量 ↔ `core/design_tokens.py` 联动** | `design_tokens.py:5,75,87` 注释明确"对齐 dark.qss 现有基调"、"补充 light.qss 缺失的基底定义"；`GRAY_DARK` 的 6 个值与 `dark.qss` 的主色完全同源（`#202020`/`#2C2C2C`/`#383838`/`#C8D0DC`/`#8892a2`/`#555f6b`） | 🟡 中 | 删 qss 文件后 `GRAY_*` 仍需保留（作为 Python 端事实来源）；`design_tokens.py:5,75,87` 的注释要改写 |
| 5 | **主题色锚点替换 `substitute_accent`** | `core/theme_qss.py:33`（`_ACCENT_PLACEHOLDER = "#00BCD4"`）、`:50-56`（正则替换）；调用方 `ui_mixin.py:1851`、`aftersale/*.py`（`current_accent_hex`） | 🟡 中 | 如果删 qss 文件，`substitute_accent` 只剩 `aftersale` 系列在用；官方等价物是 `--ThemeColorPrimary` 占位符，但**该占位符只在 qfw 渲染的 QSS 中生效**，自定义片段仍需自建替换 |
| 6 | **`aftersale/window.py:24` 导入 `current_accent_hex` 但文件内无 `setStyleSheet`** | 疑似残留导入 | 🟢 低 | 顺手清理（不影响功能） |
| 7 | **`dist/AutoWork/_internal/styles/` 过期拷贝** | `2026-08-26`，与 `styles/`（2026-09-07）diff 不一致 | 🟡 中 | 重新打包即自动同步；**不要手改** |
| 8 | **字符串拼接引用** | `_CAND_LIST_QSS_TMPL`（`aftersale/common.py:68`）、`_MIGRATE_BTN_QSS_TMPL`（`management/common.py:204`）、`_ROW_BTN_TMPL`（`aftersale/common.py:269`）、`_END_BTN_QSS_*`（`process_mixin.py:28/33`）、`_HEIGHT_QSS`（`autowork_with_table.py:216`）—— 5 个模板常量，被多处 `.format()` 引用 | 🟡 中 | 删模板前务必 grep 全部引用点；其中 `_MIGRATE_BTN_QSS` 被 `device_page.py:155,283` 与 `image_viewer.py:133,210` 跨模块引用（`common.__all__` 导出） |
| 9 | **`_prebuild_btn_css` 性能依赖** | `aftersale/common.py:302-308`：表格每页 2-3 个按钮 × 数百行，现算 QSS 会重复 150 次；靠循环外预构建优化 | 🟡 中 | 若改造成 qfw 组件，需重新评估这处性能优化是否仍必要 |
| 10 | **`pivot_page.py:101` 依赖 qfw 内置 QSS 的确切字符串** | `item.styleSheet().replace("padding: 10px 12px;", "padding: 1px 12px;")` | 🔴 高 | **最脆弱的一处**：qfw 升级若改了 `pivot.qss` 的 padding 写法，这行会静默失效（replace 无匹配不报错）。建议改为 `setCustomStyleSheet(item, "padding:1px 12px;", ...)` 追加而非字符串替换 |

### 6.2 迁移过程风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **Mica 不可逆** | 任何窗口级 `setStyleSheet`（FluentWindow 上）会永久杀死云母 | P0/P1 **完全不碰挂载点**；P2 动挂载点时必做洋红壁纸对照 |
| **dirty-qss 打回** | `ui_mixin.py:1798-1801` 实证：直接 `setStyleSheet(合并串)` 会在第一个 Paint 事件被 dirty watcher 打回纯 FLUENT_WINDOW qss（7445 → 1969 字符），与信号连接顺序无关 | 只用 `setCustomStyleSheet` 双槽 |
| **普通 QWidget 上 setCustomStyleSheet 不生效** | `device_page.py:91-97` 实证 | 普通 QWidget 一律用 `setStyleSheet` + `themeChanged` 重应用 + `WA_StyledBackground` |
| **`setTextColor` 与内联 QSS color 冲突** | `setting_cards.py:186-188` 注释 | 同一控件二选一，不混用 |
| **QSS `font-family` 与 `setFont()` 冲突** | `dark.qss:55-57` 注释明确规避 | QSS 里只写 `font-size`/`font-weight`，**绝不写 `font-family`** |
| **qfw 升级破坏字符串替换** | 见 6.1 #10 | 改 `setCustomStyleSheet` 追加式 |

---

## 7. 待验证 / 不明确项

| # | 问题 | 建议验证方式 |
|---|---|---|
| 1 | `styles/*.qss` 的 `QToolTip` 段是否真的失效（QToolTip 顶层窗口不继承祖先 QSS） | 真机：在任意控件上 `setToolTip`，观察提示框是否为系统默认样式。若已是默认样式 → 该段确为死规则，可直接删 |
| 2 | `#menubar_widget` 段是否已成死规则（菜单栏已移入设置页） | 真机看设置页是否仍有该 objectName 的控件（`ui_mixin.py:817` 仍在创建，但可能未插入布局） |
| 3 | `show_log` 的实际外观来源 | 确认 `autowork_with_table.py:566` 的 `FluentTextEdit`（= qfw `TextEdit`）后，日志区当前外观完全由 qfw `LINE_EDIT` qss 决定；删除 `QPlainTextEdit#show_log` 段应无变化 —— 真机截图对比确认 |
| 4 | `process_mixin.py` 的"结束"红底按钮能否换成 `PrimaryPushButton` + `setCustomStyleSheet` | 需产品确认观感（红底"结束"是危险操作语义，qfw 无 danger 按钮） |
| 5 | `core/design_tokens.py` 中 `GRAY_LIGHT`/`GRAY_DARK` 与 qfw 主题色的视觉一致性 | 删 qss 后这些令牌只服务 Python 端，建议与 qfw 的 `ThemeColor` 体系做一次对齐评审 |
| 6 | 18 处滚动区透明 QSS 是否可改为 qfw `ScrollArea` 的通用规则 | 需要给 qfw 提 issue 或本地 `QApplication.setStyleSheet` 全局注入一条 `QScrollArea{background:transparent}`（**注意：全局注入属于窗口级红线的边界情况，必须先做 Mica 验证**） |

---

## 附录 A：核心判定规则（供后续复用）

> 遇到任何一段 QSS，按以下顺序问 4 个问题：
>
> 1. **它作用的对象还是不是原生 Qt 控件？**
>    是 qfw 组件 → 大概率已被覆盖（✅A），除非是 qfw 也没定义的属性（如 `min-height`、终端底色）。
> 2. **这个 objectName / 类型在项目里还有实例吗？**
>    没有 → 死规则，直接删（✅A）。
> 3. **它表达的是「主题」还是「业务语义」？**
>    主题（底色/主色/圆角）→ qfw 可覆盖（🔶B，需换组件）。
>    业务语义（红黄绿状态灯 / 分类配色 / 终端黑底）→ qfw 无能力，必须保留（❌C）。
> 4. **它有没有非视觉的功能性副作用？**
>    有用 padding/margin 撑开布局、用 `min-height` 压过 qfw 内置值 → 必须保留（❌C），且要写注释标明"非样式勿删"。

## 附录 B：本次审计用到的实证脚本（均在 `/tmp`，未写入项目）

- `dump_qfw_qss.py` / `dump2.py` / `dump3.py`：枚举 qfw 内置 34 个 qss 资源与选择器，检测原生控件覆盖情况
- `probe3.py`：offscreen 渲染 + 区域差异像素计数，判定 legacy QSS 对各类控件的实际作用
- `probe4.py` / `probe5.py`：offscreen 渲染 + 最暗像素/边缘取样，精确判定文字色与背景色归属

---

# 附：QA 独立复核（严过关）

> 复核人：严过关（QA 工程师） · 复核时间：2026-09-07
> 复核方式：**独立重新盘点，未复用架构师清单**；关键结论均要求实测证据，不采信代码注释。
> 临时脚本目录：`_qss_qa_tmp/`（可整目录删除，未改动任何业务源码）
> 环境：PySide6 + qfluentwidgets **1.11.3**（`C:/Users/shen_zhe/.workbuddy/binaries/python/envs/default`）

## 0. 复核结论速览

| 项 | 架构师结论 | QA 复核结论 |
|---|---|---|
| 全库匹配规模 | 110 行 / 36 文件 | **101 调用点 / 37 文件**（口径见 §1） |
| 业务侧 | 31 文件 / 88 处 | **33 文件 / 96 处** —— 少计 8 处（漏了 7 处 `setCustomStyleSheet` 注入点） |
| 调试探针 | 5 文件 / 6 处 | **4 文件 / 5 处** —— 多计 1 处 |
| ①18 处滚动区透明化 | 全部必须保留 | **部分成立**：仅 **4 处**必须保留，**14 处**可用 qfw `enableTransparentBackground()` 替代（§2.1） |
| ②ANSI 终端/老板键黑底 | 必须保留 | **成立**（§2.2） |
| ③工具栏 RadioButton 32px | 必须保留 | **成立**（§2.3） |
| ④device_page 普通 QWidget 背景 | setCustomStyleSheet 不生效 | **成立，且比原文更强**（§2.4） |
| 红线（FluentWindow 窗口级 QSS） | 未触碰 | **未触碰**，7 个调用方全部为原生 `QDialog`（§3.1） |
| `styles/*.qss` 引用方 | 仅 `build_exe.py` + 3 个 `.spec` | **不成立**：存在 **2 条运行时读取链路**；且 `build_exe.py` 实际 **0 处**引用（§3.2） |
| 工作区洁净度 | — | ⚠️ **发现 2 个源码文件被改动**（非本次审计产物），见 §4 |

---

## 1. A 项 · 完整性验证（独立重新盘点）

### 1.1 盘点口径

```bash
grep -rn --include=*.py -E "\.setStyleSheet\(|[^a-zA-Z]setCustomStyleSheet\(" .
# 排除 build/ dist/ .workbuddy/ __pycache__/ .git/ .idea/ .qoder/ web/ docs/ tools/
```

| 类别 | 文件数 | 调用点数 |
|---|---:|---:|
| 业务侧 | **33** | **96**（`setStyleSheet(` 88 + `setCustomStyleSheet(` 7，含 1 处循环内调用） |
| `tools/` 调试探针 | **4** | **5** |
| **合计** | **37** | **101** |

与架构师「110 匹配行 / 36 文件、实际调用 94 处（业务 31 文件 88 处 + 探针 5 文件 6 处）」对比：
**业务侧少计 8 处、文件少计 2 个；探针侧多计 1 处。**

### 1.2 架构师漏报清单（QA 独立发现）

| # | 文件:行号 | 形态 | 作用 / 为何重要 |
|---|---|---|---|
| 1 | `main_window/ui_mixin.py:1820` | 变量间接 + 文件读取 | `setCustomStyleSheet(self.stackedWidget, self._load_qss('light'), self._load_qss('dark'))` —— **运行时读取 `styles/*.qss`**，是 `styles/` 的隐藏运行时依赖（详见 §3.2） |
| 2 | `main_window/ui_mixin.py:1900` | 加载路径 | `_load_qss()` 从 `get_resource_dir()/styles/{theme}.qss` 读盘，同上 |
| 3 | `core/theme_qss.py:59-68` `load_window_qss()` | 加载路径 | 第二条运行时读取 `styles/*.qss` 的链路，`apply_window_qss()` 在 `:82` 消费 |
| 4 | `core/flow_widgets.py:28` | viewport 独立设样式 | `self.viewport().setStyleSheet("background: transparent;")` —— 视口透明，与主控件规则不可合并 |
| 5 | `autowork_with_table.py:220` | 常量注入 | `setCustomStyleSheet(self, self._HEIGHT_QSS, self._HEIGHT_QSS)`，常量 `_HEIGHT_QSS` 定义在 `:216` |
| 6 | `main_window/process_mixin.py:44,47` | 条件注入 | 按运行状态动态挂/摘「结束」按钮红底 QSS |
| 7 | `windows/management/device_page.py:155` | 注入 | `setCustomStyleSheet(btn, qss, qss)` —— 迁移按钮固定底色 |
| 8 | `windows/management/image_viewer.py:212` | 注入 | `setCustomStyleSheet(btn, qss, qss)` |
| 9 | `windows/aftersale/common.py:111` | **模板常量 + `.format()`** | `widget.setStyleSheet(_CAND_LIST_QSS_TMPL.format(**c))` —— 常量定义在 `:100` 附近，与调用处分离，属典型易漏形态 |
| 10 | `windows/management/common.py:29,202` | 注释/import 暴露 | 迁移按钮底色走 `setCustomStyleSheet` 机制 |
| 11 | `main_window/main_window.py:1495` | f-string 动态 | `f"color: {accent}; font-weight: 600;"` |

### 1.3 已排查、确认**不存在**的形态（排除误报风险）

| 形态 | 排查结果 |
|---|---|
| `QApplication.setStyleSheet` / `app.setStyleSheet` 全局注入 | **0 处**（全库 grep 无匹配）—— 红线边界情形未发生 |
| `.ui` 内嵌 stylesheet | **0 处**（`autowork_with_table.ui` grep `stylesheet` 计数 = 0） |
| `setProperty` + `polish/unpolish` 的 qproperty 技巧 | **0 处**。全库 `setProperty` 仅 `health_page.py:737`（`alert_name` 数据挂载，非样式）；`polish/unpolish` 仅 `ui_mixin.py:1782-1783`（主题重刷，非设样式） |
| `%` 格式化 / 字符串拼接动态 QSS | 已覆盖（`aftersale/entry.py:151,161`、`run_video/entry.py:114,124` 等使用 `%s` 拼接 `SEMANTIC[...]`） |

### 1.4 架构师多报 / 偏高的项

| 项 | 说明 |
|---|---|
| 调试探针 | 实测 **4 文件 5 处**（`probe_mica_offscreen.py:185`、`probe_mica_qss_fix.py:149,190`、`probe_mica_steps_pink.py:208`、`verify_fluent_refactor.py:257`），非 5 文件 6 处 |
| `build_exe.py` 引用 `styles/` | **0 处**（grep `styles|qss` 于 `build_exe.py` 无匹配）。3 个 `.spec` 的引用属实：`AutoWork.spec:166`、`AfterSale.spec:144`、`Management.spec:103` |
| qfw 内置 qss 资源数 | 原文称「34 个」，实测 dump 为 **68 份**（dark 34 + light 34），应为 34 个样式主题 × 2 套主题色 |

---

## 2. B 项 · 四条最硬保留项的实证验证

### 2.1 ① 18 处滚动区透明化 —— **部分成立（4 处必须保留，14 处可替代）**

**验证方法**：离线 `QApplication` + `QDir` 枚举 Qt 资源系统 `:/qfluentwidgets`，实际 dump 出 qfw 1.11.3 内置 **68 份** QSS 全文（落盘 `_qss_qa_tmp/qfw_qss_dump/`，合并文件 `ALL_QSS.txt`），再 grep 目标选择器。

**实测输出（真实 dump，非推断）**：

```
选择器在 qfw 内置 QSS（全 68 份）中出现次数：
  QScrollArea    : 8 行
  QScrollBar     : 0 行     ← qfw 完全不提供滚动条样式
  QSplitter      : 0 行     ← qfw 完全不提供分隔条样式
  QRadioButton   : 0 行
  QStackedWidget : 0 行
```

**`QScrollArea` 的 8 次出现按文件归因**（关键）：

```
dark/color_dialog.qss         : QScrollArea=2
dark/folder_list_dialog.qss   : QScrollArea=1
dark/navigation_interface.qss : QScrollArea=1
dark/fluent_window.qss        : QScrollArea=0   ← 注意：FluentWindow 主样式不含任何滚动区透明规则
```
（light 侧同分布）

> 结论 A：qfw **不会**对 FluentWindow 下的滚动区自动施加透明规则 —— 架构师的方向性判断正确。
> 结论 B：`QSplitter` / `QScrollBar` 在 qfw 内置 QSS 中 **0 次出现**，可 100% 确认这两段必须保留 —— 与报告结论一致。

**但 18 处并非同质。按控件实际类型拆分后：**

| 类型 | 处数 | 位置 | 可否用 qfw 替代 |
|---|---:|---|---|
| 原生 `QScrollArea`（+ 其 viewport） | **4** | `core/flow_widgets.py:27`、`:28`(viewport)、`main_window/hub_pages.py:259`、`main_window/pivot_page.py:159` | ❌ **必须保留**（原生类，无 qfw API） |
| qfw `ScrollArea` / `SmoothScrollArea` | **9** | `aftersale/entry.py:67`、`aftersale/settings.py:225`、`management/moyu_page.py:66`、`management/settings_page.py:125`、`management/widget_page.py:160`、`:252`、`:525`(`SmoothScrollArea`)、`run_video/entry.py:57`、`run_video/settings.py:95` | ✅ **可用 `enableTransparentBackground()` 替代** |
| 滚动区内部容器 `QWidget` | **5** | `aftersale/entry.py:69`、`management/settings_page.py:127`、`management/widget_page.py:162`、`:527`、`run_video/entry.py:59` | ✅ 同上（该 API 一并处理内部 widget） |

**关键证据 —— qfw 自带等效 API**：

```python
# qfluentwidgets/components/widgets/scroll_area.py:34-39
class ScrollArea(QScrollArea):
    def enableTransparentBackground(self):
        self.setStyleSheet("QScrollArea{border: none; background: transparent}")
        if self.widget():
            self.widget().setStyleSheet("QWidget{background: transparent}")
```

实测该类可用性（`_qss_qa_tmp` 脚本输出）：

```
ScrollArea                   exists=True  enableTransparentBackground=True
SmoothScrollArea             exists=True  enableTransparentBackground=True
SingleDirectionScrollArea    exists=True  enableTransparentBackground=True
```

> **对迁移方案的影响**：报告「遗留问题表」第 6 行把这条路判为「需要给 qfw 提 issue 或全局注入 `QApplication.setStyleSheet`（红线边界）」——**偏悲观**。
> 14 处（9 + 5）可直接改写为 `scroll.enableTransparentBackground()`（需在 `setWidget()` 之后调用），
> 无需提 issue、更无需触碰全局注入红线。真正必须保留手写 QSS 的只有 **4 处**。

### 2.2 ② ANSI 终端 / 老板键日志黑底 —— **成立**

```
windows/remote_session/ansi_terminal.py:77
    "QTextEdit { background-color: #1e1e1e; border: none; }"
    ↑ class ANSITerminalWidget(QTextEdit) —— ANSI 序列解析 + 键盘直传的终端仿真器（:66）

windows/management/moyu_widgets.py:1194
    "QTextBrowser { background: #0b1015; color: #9fb3c8; border: none; }"
    ↑ self._boss_log，老板键伪装页的「系统日志（实时）」（:1187 标题、:1188 副标题）
```

判定：两处均为**固定深色、与主题无关**的终端仿真底色（`#1e1e1e` 为终端经典深色；`#0b1015` 同族）。
`ansi_terminal.py` 内含 ANSI 颜色解析与 `_DEFAULT_FG` 前景色常量，前景色按深底设计 ——
删除后在浅色主题下会变成白底深字/浅字，**终端可读性直接退化**，属功能需求而非主题色。
✅ 架构师结论正确。

### 2.3 ③ 工具栏 RadioButton 32px 行高 —— **成立**

**实测 qfw 内置 QSS 原文**（`_qss_qa_tmp/qfw_qss_dump/dark/button.qss`，dump 第 173-179 行）：

```css
RadioButton {
    min-height: 24px;
    max-height: 24px;
    background-color: transparent;
    font: 14px --FontFamilies;
    color: white;
}
```
light 侧 `light/button.qss`（dump 第 2198 行）同为 `min-height: 24px`。

**确认作用对象确为 qfw 控件**：

```
autowork_with_table.py:211  class _ToolbarRadioButton(RadioButton)   ← qfw RadioButton 子类
autowork_with_table.py:216  _HEIGHT_QSS = "QRadioButton { min-height: 32px; max-height: 32px; }"
autowork_with_table.ui:108  <widget class="QRadioButton" name="input_frame_before">
```

qfw 用 `min-height + max-height: 24px` **双侧锁死**行高，仅靠 `setFixedHeight(32)` 无法突破
（`ui_mixin.py:1833` 注释亦记录了 min(35) > max(32) 的矛盾），必须用 QSS 覆盖。
✅ 架构师结论正确，且证据链完整。

### 2.4 ④ device_page 普通 QWidget 背景 —— **成立，且实际约束比原文更强**

**验证方法**：读 qfw `common/style_sheet.py` 源码 + 写脚本实测（`_qss_qa_tmp/test_custom_qss_widget.py`）。

**源码机制链**：

```python
# style_sheet.py:345
def setCustomStyleSheet(widget, lightQss, darkQss):
    CustomStyleSheet(widget).setCustomStyleSheet(lightQss, darkQss)   # 只写动态属性

# style_sheet.py:197 → :212/:221
self.widget.setProperty(self.LIGHT_QSS_KEY, qss)   # 'lightCustomQss'
self.widget.setProperty(self.DARK_QSS_KEY,  qss)   # 'darkCustomQss'

# style_sheet.py:238 CustomStyleSheetWatcher.eventFilter
#   ↓ 捕获 DynamicPropertyChange 后才调用 addStyleSheet → register（装过滤器）→ setStyleSheet
# style_sheet.py:21  StyleSheetManager.register() —— 事件过滤器只在注册时安装
```

**鸡生蛋问题**：事件过滤器只在 `register()` 内安装；未注册控件首次 `setProperty` 时无人监听 → 静默失效。

**实测输出（offscreen 真机运行）**：

```
--- 用例1 普通 QWidget（从未注册） ---
  调用前已注册: 否   调用后已注册: 否
  lightCustomQss : 'QWidget { background-color: rgb(255, 0, 0); }'   ← 属性写进去了
  styleSheet 长度: 0 -> 0                                            ← 但 QSS 没生效
  判定: 未生效

--- 用例3 qfw CardWidget（全新实例） ---
  调用前已注册: 否   调用后已注册: 否   styleSheet 长度: 0 -> 0
  判定: 未生效          ← 注意：不只是普通 QWidget，连 qfw 组件在「未被 apply 过」时同样失效

--- 用例4 qfw PushButton（构造时已 apply） ---
  调用前已注册: 是   调用后已注册: 是   styleSheet 长度: 7549 -> 7595
  判定: 红色/蓝色规则已注入 ✅

--- 用例5 FluentWindow.stackedWidget ---
  调用前已注册: 是   调用后已注册: 是   styleSheet 长度: 1845 -> 1891
  判定: 红色/蓝色规则已注入 ✅

--- 用例6 普通 QWidget（FluentWindow 的子对象，未自行注册） ---
  调用后已注册: 否   styleSheet 长度: 0 -> 0
  判定: 未生效          ← 「父控件已注册」不能让子控件免注册
```

**生效条件（结论）**：`setCustomStyleSheet` **只对已注册进 `styleSheetManager` 的控件生效**，
与「是不是 FluentStyleSheet 子类」无关，而与「是否被 `apply()` / `setStyleSheet(w, source)` 注册过」有关。

✅ 架构师方向正确。补充两点工程含义：

1. **项目内已有正确注释**：`windows/management/device_page.py:93-97` 明确记录了
   「不能用 `setCustomStyleSheet`：它只写动态属性，实际应用依赖…」—— 与实测一致，可作为迁移时的判据。
2. **可用但需代价的破解法**（若仍想下沉）：先 `setStyleSheet(w, FluentStyleSheet.FLUENT_WINDOW)`
   完成注册，再 `setCustomStyleSheet(...)` 即生效（用例 2 实测 1845 → 1891）。
   代价是会额外灌入 `FLUENT_WINDOW` 全套规则；对 `device_page` 这类只想设底色的场景，
   **直接保留 `setStyleSheet` + 监听 `qconfig.themeChanged` 重应用（现状做法）反而更干净**。
   → 支持架构师「这处保留」的结论。

---

## 3. C 项 · 安全边界验证

### 3.1 `apply_window_qss` 的 7 个调用方 —— **全部为原生 QDialog，红线未触碰**

| # | 调用点 | 所属类（定义行） | QDialog 来源 |
|---|---|---|---|
| 1 | `windows/aftersale/dialogs.py:90` | `QuickPhraseDialog`（:73） | `PySide6.QtWidgets`（:10） |
| 2 | `windows/aftersale/stats_dialog.py:300` | `AfterSaleStatsDialog`（:275） | `PySide6.QtWidgets`（:27） |
| 3 | `windows/management/image_viewer.py:126` | `ImageViewerDialog`（:108） | `PySide6.QtWidgets`（:18） |
| 4 | `windows/remote_session/conn_diag_panel.py:238` | `ConnDiagPanel`（:233） | `PySide6.QtWidgets`（:20） |
| 5 | `windows/remote_session/rdp_window.py:515` | `RDPWindow`（:510） | `PySide6.QtWidgets`（:21） |
| 6 | `windows/remote_session/sftp_window.py:1917` | `SFTPWindow`（:1912） | `PySide6.QtWidgets`（:9） |
| 7 | `windows/remote_session/ssh_terminal.py:770` | `SSHTerminalWindow`（:765） | `PySide6.QtWidgets`（:34） |

7 处逐一打开验证，**无一是 FluentWindow / FluentDialog 子类**，`QDialog` 全部 import 自 `PySide6.QtWidgets`。
✅ **红线（窗口级 setStyleSheet 杀死 DWM Mica）未被触碰。**

*补充观察*：这些 QDialog 均为独立顶层窗口。QSS 挂在顶层窗口理论上同样会改变其原生表面合成格式；
但项目既定的红线口径是「FluentWindow 窗口自身」，且这些对话框不走 Mica 云母，**按现行口径判定为安全**。
建议迁移后回归时对这些对话框做一次真机外观抽查（属「建议」非「缺陷」）。

### 3.2 `styles/*.qss` 引用方 —— **架构师结论不成立**

架构师称「引用方只有 `build_exe.py`（打包拷贝）和 3 个 `.spec`，没有别处运行时依赖」。实测：

| 引用方 | 类型 | 证据 |
|---|---|---|
| `AutoWork.spec:166` | 打包 | `('styles', 'styles')` ✅ 属实 |
| `AfterSale.spec:144` | 打包 | `('styles', 'styles')` ✅ 属实 |
| `Management.spec:103` | 打包 | `('styles', 'styles')` ✅ 属实 |
| `build_exe.py` | — | ❌ **0 处引用**（grep `styles\|qss` 无匹配），架构师多报 |
| **`core/theme_qss.py:59-68` `load_window_qss()`** | ⚠️ **运行时** | `os.path.join(get_resource_dir(), 'styles', f'{name}.qss')` → `open().read()`；被 `apply_window_qss()` 于 `:82` 消费（7 处调用方，见 §3.1） |
| **`main_window/ui_mixin.py:1900` `_load_qss()`** | ⚠️ **运行时** | 同上读盘；被 `_apply_business_qss()` 于 `:1820` 消费，挂到 `stackedWidget` |

> **对迁移方案的实质影响（重要）**：
> `styles/dark.qss`（256 行）/ `light.qss`（243 行）不是「只被打包拷贝的死资源」，
> 而是**主窗口业务样式的唯一载体**。实测其内含：
> `QSplitter::handle`（:151-161）、`QScrollBar`（:186-233 等 10 个规则块）、
> `QScrollArea#toolbar_scroll`（:13）、`QWidget#centralwidget` 等。
>
> 因此「整体删除 `styles/*.qss`」**不等于**只下沉 4 段约 60 行 ——
> 必须同时改写 **2 条加载链路**：
> 1. `ui_mixin._load_qss()` / `_apply_business_qss()`：把文件内容换成内联常量片段；
> 2. `core.theme_qss.load_window_qss()` / `apply_window_qss()`：7 个 QDialog 会因此拿到空串，
>    其原生控件（QPushButton/QSplitter/QListWidget）将退回 Fusion 默认灰 —— 即该文件头部注释所记录的原始 bug 会复现。
>
> 这两条链路在架构师报告的正文中未被列为「删除 styles/ 的前置条件」，**建议补入迁移清单**。

---

## 4. D 项 · 环境完整性 —— ⚠️ 发现源码改动（非本次审计产物，未自行 revert）

审计开始时的 `git status --porcelain`：**无 ` M ` 条目**，工作区对已跟踪文件是干净的。
审计进行到后半段复检时，出现 **2 个已跟踪文件被修改**：

```
 M main_window/setting_cards.py
 M main_window/ui_mixin.py
```

`git diff --stat`：

```
 main_window/setting_cards.py | 23 +++++++++++++++-
 main_window/ui_mixin.py      | 65 ++++++++++++++++++++++++++++++++++++++++++--
 2 files changed, 84 insertions(+), 4 deletions(-)
```

**改动内容摘要（按团队要求：只报告，未 revert、未做任何修改）**：

1. `main_window/setting_cards.py`（+23/-1）
   - 新增 `qconfig` 导入；`SettingRow.__init__` 末尾新增
     `qconfig.themeChanged.connect(self._on_theme_changed_direct)`（:111）
   - 新增 `_on_theme_changed_direct()`（:113）：主题切换时停止 qfw 的 120ms 背景色动画，
     直接写入终点色，修复「切换后卡片停在旧主题色」。

2. `main_window/ui_mixin.py`（+65/-3）
   - 新增 `qconfig, FluentStyleSheet` 导入，并新增
     `from qfluentwidgets.common.style_sheet import styleSheetManager, StyleSheetCompose, CustomStyleSheet, getStyleSheet`
   - `_apply_theme()` 中在 `_apply_business_qss()` 之后新增
     `self._reset_qss_compose_trees()` + `QTimer.singleShot(0, ...)`（:1774 附近）
   - `_apply_business_qss()` 增加 `_business_qss_applied` 幂等守卫
   - 新增 `_reset_qss_compose_trees()`（:1824）：下钻并重建 qfw 样式合并树，
     消除主题切换导致的 `StyleSheetCompose` 嵌套膨胀（注释记录 17039 → 23906 → 32255 → 45846 字符）

**性质判断**：这两处改动是**主题切换相关的缺陷修复**，与本次 QSS 审计（只读盘点）无关，
应为并行作业的其他成员在审计窗口期内写入；时间戳（`tools/_tmp_assert_fix.py` 05:59、
`tools/_tmp_trace_register.py` 05:53）显示审计期间确实有并行写入活动。

**⚠️ 需要团队确认的两点**：

1. **本报告的正文行号基准**：`ui_mixin.py` 的引用行号以**改动后**的文件为准
   （`_apply_business_qss` :1794、`setCustomStyleSheet` :1820、`_reset_qss_compose_trees` :1824、`_load_qss` :1900）。
   若与架构师原报告行号不一致，是因这次改动导致位移（约 +9 行），非盘点差异。
2. **审计窗口期内源码在变**，意味着架构师报告的部分行号可能基于改动前的基线。
   建议定稿前统一以同一 commit 基准复核一次行号。

**未跟踪文件（untracked，非改动）**：`docs/QSS使用审计与弃用评估.md`、`overview.md`、
`design/*`（6 个截图/html）、`tools/_tmp_*.py`（**7 个**临时探针脚本：
`_tmp_assert_fix.py`、`_tmp_check_sources.py`、`_tmp_check_theme_render.py`、
`_tmp_check_theme_switch.py`、`_tmp_dump_inner.py`、`_tmp_dump_nest.py`、`_tmp_trace_register.py`）、
`_qss_qa_tmp/`（本次复核产物）。

> 建议：`tools/_tmp_*.py` 是一次性探针，**建议清理**；`_qss_qa_tmp/` 可直接删除。

**git 对象库损坏情况**：本次 `git status` / `git diff` / `git diff --stat` **均正常返回，未触发
"unable to read sha" 错误**。未做任何 git 修复操作。

---

## 5. E 项 · 回归基线

```bash
C:/Users/shen_zhe/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m pytest tests/ -q
```

| 时点 | 结果 |
|---|---|
| 审计开始前 | `260 passed`（与既定基线一致） |
| 源码改动后复跑 | `260 passed in 7.15s` |

✅ **基线未破坏**，无失败用例，无需路由给工程师。

---

## 6. 复核后的补充建议（对迁移方案的修正）

1. **§2.1 的 14 处滚动区改写为 `enableTransparentBackground()`**，不必走「给 qfw 提 issue / 全局注入」的悲观路线。
   迁移后必须保留的手写滚动区 QSS 从 18 处降到 **4 处**。
2. **把「改写 2 条 `styles/*.qss` 运行时加载链路」列入删除 `styles/` 的前置条件**（§3.2），
   否则 7 个 QDialog 会退回 Fusion 默认灰（重现 `core/theme_qss.py` 头部记录的原始缺陷）。
3. **`QSplitter::handle` 与 `QScrollBar` 的保留判断已由实测坐实**（qfw 内置 QSS 中 0 次出现），
   可放心标为「永久保留」，后续不必再论证。
4. **`setCustomStyleSheet` 的使用守则建议写入项目规范**：
   「仅适用于已被 qfw `apply()` 注册过的控件；对裸 `QWidget` 会静默失效」——
   实测连全新 `CardWidget` 也失效（§2.4 用例 3），这条坑很容易踩。
5. **建议清理 `tools/_tmp_*.py`（7 个）与 `_qss_qa_tmp/`**，保持工作区干净。

## 7. 本次复核产物清单（可删除）

| 路径 | 用途 |
|---|---|
| `_qss_qa_tmp/dump_qfw_qss.py` | 枚举 Qt 资源系统，dump qfw 1.11.3 内置 68 份 QSS |
| `_qss_qa_tmp/qfw_qss_dump/` | dump 结果（`dark/` 34 份、`light/` 34 份、`ALL_QSS.txt` 合并全文） |
| `_qss_qa_tmp/test_custom_qss_widget.py` | `setCustomStyleSheet` 生效条件实测（6 用例） |

**未修改任何业务源码。**

---

## 附：主理人裁决（齐活林 · 对两份结论的分歧裁定）

架构师与 QA 的结论在 3 处冲突。我逐条复核原始证据后裁定如下，**以本节的结论为最终口径**。

### 裁决 1：QA 的「重大漏报①」不成立 —— 架构师已覆盖两条运行时读取链路

QA 称「`styles/*.qss` 有 2 条运行时读取链路被漏报，删文件不止下沉 60 行」。**经复核，架构师已覆盖**：

- 报告 §2.2 第 89–90 行（原文）：`加载点：main_window/ui_mixin.py:1805 → _load_qss('light') → setCustomStyleSheet(stackedWidget, light, dark)`；`另：core/theme_qss.py:62 → load_window_qss() 给 7 个原生 QDialog 用`
- 报告 §5-P2 第 477 行（原文）：配套改动已列出「① `ui_mixin.py:1805,1841-1852` `_load_qss()` 改为返回内联常量 ② `core/theme_qss.py:59-68` `load_window_qss()` 改为拼接下沉片段」

**误判原因**：审计窗口期 `main_window/` 被并行编辑，行号整体漂移约 +9 行（架构师用的 `1805` 对应 QA 看到的 `1820/1900`），QA 以新行号检索旧报告未命中，误判为漏报。

> 结论：P2 阶段的配套改动范围按架构师 §5-P2 执行即可，**无需扩大**。但请注意：QA 指出的「7 个 QDialog 会退回 Fusion 灰」是**真实风险**，架构师 §5-P2 只写了「改为拼接」，两处表述需合并理解——删 `styles/` 时 `load_window_qss()` 必须返回等价内容，不能返回空串。

### 裁决 2：QA 的「误报 build_exe.py」不成立 —— 架构师原判正确

QA 称「`build_exe.py` 实际 0 处引用 styles（架构师多报）」。但架构师 §6.1 第 2 行原文即为「`build_exe.py` **未直接引用** styles，grep 0 命中，风险：无，无需改动」——**两者结论一致，不存在误报**。

真正需要改的是 **3 个 `.spec` 文件**（`AutoWork.spec:164-166`、`AfterSale.spec:144`、`Management.spec:103` 的 `('styles', 'styles')` 数据项），架构师已列，QA 未反驳。此项维持原判。

### 裁决 3：QA 的「18 处滚动区 → 仅 4 处必留」成立，采纳

这是 QA 最有价值的纠正，架构师原判偏保守：

- 架构师判「18 处全部必须保留（C 类）」，理由是 qfw 无通用 `QScrollArea` 规则（此事实成立，QA dump 68 份 QSS 后也确认 `QScrollArea` 在 `fluent_window.qss` 中出现 0 次）。
- 但 QA 实测发现：这 18 处中 **14 处用的是 qfw 自带 `ScrollArea` / `SmoothScrollArea`**，而 qfw 为其提供了 `enableTransparentBackground()`（`scroll_area.py:34-39`），可直接替代内联 QSS。
- **修正后口径：真正必须保留的仅 4 处**（`core/flow_widgets.py:27,28`、`main_window/hub_pages.py:259`、`main_window/pivot_page.py:159`），其余 14 处应改为调用 `enableTransparentBackground()`。

> 影响：架构师 §4.2 的 P0 保留清单第 1 行（18 处）应据此缩减为 4 处；§5-P1 追加一条动作「14 处滚动区 QSS 替换为 `enableTransparentBackground()`」。此项显著降低迁移成本。

### 裁决 4：QA 强化的「setCustomStyleSheet 生效判据」采纳，且比原判更严

架构师原判「普通 QWidget 上 `setCustomStyleSheet` 不生效」，QA 实测后给出更准确的判据：**是否生效取决于该控件是否注册进 `styleSheetManager`，与是否 `FluentStyleSheet` 子类无关**——连全新的 `CardWidget` 也不生效，只有被库 `apply` 过的（如 `PushButton`、`stackedWidget`）才生效。

采纳 QA 版本，并写入团队常识。

### 最终口径汇总（覆盖前文的冲突处）

| 项 | 最终结论 | 依据 |
|---|---|---|
| QSS 规模 | 业务 **33 文件 / 96 处**（QA 口径，比架构师多计 7 处 `setCustomStyleSheet` 注入点）＋ 探针 4 文件 / 5 处 | QA 复核 |
| 能否全删 | **不能**。可删 `styles/*.qss` 双文件，但必须先改 2 条加载链路；内联 QSS 中约 36 处必须保留 | 架构师 §4.1 + QA 修正 |
| 滚动区保留数 | **4 处**（非 18 处），另 14 处改用 `enableTransparentBackground()` | 裁决 3 |
| 删 `styles/` 的配套改动 | 2 条加载链路 + 3 个 `.spec` + `design_tokens.py` 注释 | 架构师 §5-P2 + 裁决 1 |
| 红线 | 未触碰。7 个 `apply_window_qss` 调用方均为原生 `QDialog` | 双方一致 |
| pytest 基线 | **260 passed**（改动前后各跑一次均绿） | QA 实测 |

### ⚠️ 遗留事项（需用户处置，AI 未擅自处理）

1. **工作区不干净**：`main_window/pivot_page.py`、`setting_cards.py`、`ui_mixin.py` 共 **+96/-10** 行改动，内容是「qfw 样式合并树嵌套膨胀修复」（注释日期 2026-09-07），与本次审计无关，疑为审计窗口期内并行编辑所致。**已按约定未 revert，请确认是否保留。**
2. `main_window/ui_mixin.py` 行号已漂移约 +9 行，本文引用的行号以**改动后**为准；若 revert 需重新校准。
3. 临时产物 `_qss_qa_tmp/`（dump 脚本 + qfw 68 份 QSS dump）可整目录删除；`tools/_tmp_*.py`（7 个）建议清理。
