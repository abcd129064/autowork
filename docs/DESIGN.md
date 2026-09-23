# DESIGN — 设计指南、组件与视觉规范

> 本文是**界面视觉与交互规范的权威说明**：设计令牌、主题体系、组件选型、
> 布局模式与已知 UI 陷阱。设计资产（HTML 设计稿 / logo / 截图）的清单与
> 使用方式见 [design/README.md](../design/README.md)；logo 定稿规范见
> [design/logo/spec.md](../design/logo/spec.md)。
> 最后核对：2026-09-23。

---

## 1. 设计语言

- **Fluent Design**（微软风格），基于 `qfluentwidgets`（PySide6-Fluent-Widgets）控件库。
- 主窗口 = FluentWindow 单窗口结构：左侧导航（工作台 / 运维管理 / 售后 / 跑视频 /
  远程 / 工具，底部 设置 / 关于），业务大页为「**Pivot 二级导航 + 工作区**」容器页。
- 三种主题模式：深色 / 浅色 / 跟随系统；强调色可设置（`qconfig.themeColor`）。
- 材质：Mica（窗口云母）+ 亚克力（导航/弹层）；低性能模式可一键关闭（`config/perf.json`）。

## 2. 设计令牌（单一来源 `core/design_tokens.py`）

**任何新界面代码禁止硬编码颜色/间距/字号**，一律引用令牌：

### 2.1 语义色 `SEMANTIC`

| 令牌 | HEX | 用途 |
| --- | --- | --- |
| `success` | `#1a9e6c`（翡翠绿） | 已解决 / 正常 / 在线 / 使用 |
| `warning` | `#c98a2d`（琥珀金） | 警告 / 校准 / 精度 |
| `danger` | `#cf4452`（玫瑰红） | 错误 / 问题 / 离线告警 |
| `info` | `#0078d4`（标准蓝） | 信息 / 空闲 / 链接（非主色） |
| `neutral` | `#5c6675`（石板灰） | 次要 / 禁用 / 废弃 |

选值标准：白字按钮对比度 ≥ 3:1，深浅底均可读。历史漂移值（`#52c41a` `#ff5252`
`#fa8c16` 等）的「旧值 → 令牌」对照表见 `LEGACY_MAP`。

> ⚠️ 语义色仅用于**界面状态指示**。金融/涨跌类展示遵循国内习惯（涨红跌绿），与语义色体系无关。

### 2.2 灰阶与主题基底

| 令牌组 | 关键值 |
| --- | --- |
| `GRAY_DARK` | bg `#202020` / surface `#2C2C2C` / border `#383838` / text `#C8D0DC` |
| `GRAY_LIGHT` | bg `#F5F6F8` / surface `#FFFFFF` / border `#E0E0E0` / text `#20242B` |
| `ACCENT_FALLBACK` | `#00BCD4`（仅 qconfig 未初始化时兜底，运行时用 `qconfig.themeColor`） |

### 2.3 间距 / 圆角 / 排版 / 尺寸

- **间距**：4px 基准刻度 `SPACE`；组件间距与容器内边距默认 12px（`DEFAULT_GAP` / `DEFAULT_PADDING`）。
- **圆角**：`RADIUS` = sm 4 / md 6 / lg 8 / pill 999。
- **排版**：`TYPE_SCALE_PX` = xs 11 → 4xl 30px（QSS 不写 font-family，保留用户自选字体）；pt→px 换算统一走 `pt_to_px()`（1pt ≈ 4/3px，最小 12px）。
- **控件**：按钮/输入框高 33px，图标 16px，触控目标 ≥ 44px（`CONTROL`）。
- **对话框档位** `DIALOG_SIZE`：S 480×380（紧凑表单）/ M 900×560（常规表格）/ L 1180×680（多栏预览），新增对话框从档位取值，不随手写死。

## 3. 主题与 QSS 规则

- QSS 文件：`styles/dark.qss` / `styles/light.qss`（随包资源）。
- **FluentWindow 禁止窗口级 `setStyleSheet`**（破坏 Mica）——窗口级 QSS 一律经
  `core/theme_qss.py::apply_window_qss()` 挂到 `stackedWidget` 并用
  `setCustomStyleSheet`；组件级样式用 qfw 的 `setCustomStyleSheet`。
- QSS 的整体弃用评估与使用面审计见
  [QSS使用审计与弃用评估.md](QSS使用审计与弃用评估.md)（结论：保留但收敛，新代码优先令牌 + qfw API）。
- 提示条统一 `core.utils.show_info_bar()`：右下角、标题按类型自动映射、
  贴底遮挡自动抬升（bottom_offset）；常驻提示 duration=-1（**0 = 立即关闭**）。

## 4. 组件选型约定

| 场景 | 用组件 | 备注 |
| --- | --- | --- |
| 二级页面切换 | `Pivot`（大页）/ `SegmentedWidget`（设置页头部） | 均包在 `main_window/pivot_page.py::PivotPage` 基建里 |
| 表格 | QTableView + `core/lean_table_delegate.py` 轻量委托 | 渲染性能关键路径；平滑滚动开关走 `core/perf.py` |
| 是/否开关 | `windows/aftersale/common.py` 共享 UI 工厂（`_SectionCard` / `YesNoSegment` 等） | 售后与跑视频面板复用同一套表单组件 |
| 日期 | `ZhDatePicker`（表单内）/ `CalendarPicker`（工具栏，minWidth 150）+ 前后步进 ToolButton | |
| 复选框表头全选 | 三态复选框用 `clicked` 信号接管 | 不要依赖 `stateChanged` |
| 连续录入对话框 | `EditRecordDialog(continuous=True)` | `validate()` 恒 False 保持弹窗 + 异步落库 + clear_form |
| Hub 弹出独立窗口 | `main_window/hub_popout.py::HubPopoutWindow` | 传 nav_icons 覆盖 `_page_meta` 时视图摘出挂左侧子导航 |

## 5. 布局模式

- **工作台**：四列并列（会话列表 / 设备 / 日志文件 / 日志输出），列间可拖动；
  **硬约束：id_list/设备/log_list/日志输出四列同屏，禁 Tab/浮层收纳**。
- **工具栏**：`FlowLayout + FlowToolbarScrollArea`（`core/flow_widgets.py`），高度 32px。
- **大页容器**：Pivot 左对齐（margins 24,10,0,0）+ `lock_pivot_width()` 按 sizeHint 锁宽；
  PivotItem 缩小过（字体 13px / 行高 24px / padding `1px 12px`）——只压 setFixedHeight 不够，
  必须同步改 item 样式表 padding。
- **默认窗口** 1500×900（容纳 1250~1292px 业务表格列宽）；导航展开宽 200px，可折叠。

## 6. 设计稿工作流（`design/`）

1. 大改版先出 **HTML 设计稿**（浏览器直接打开评审），落 `design/*.html`；
   同一页面多版本全部保留，当前生效版本看 [design/README.md](../design/README.md) 索引表。
2. 设计稿 → qfluentwidgets 控件 1:1 还原进代码；控件还原细节在稿内标注。
3. 数据类设计稿由 `design/generator/gen_design.py` + JSON 数据源生成（内嵌真实生产数据快照）。
4. 实现后真机截图 `tools/perf/shot_fluent_mainwindow.py`，需留档的拷进 `design/shots/` 并登记。

## 7. 已知 UI 陷阱（新代码必读）

- `Qt.CheckState` 是普通 `enum.Enum`（**非 IntEnum**）：`Qt.CheckState.Checked == 2` 为
  False，必须 `.value` 或 `getattr` 比较。
- Python 侧 emit 时 `self.sender()` 恒 None —— 跨线程 worker 信号归属必须**连接时闭包捕获**
  （ToDesk 开关并发、DB worker 均踩过）。
- `NavigationInterface` 项字典在 `nav.panel.items`（qfw 1.11 无 setDisplayMode API）。
- `PivotItem.itemClicked` 以位置参数回传 bool，回调必须 `def _go(*_args, ...)` 吞参，
  否则默认值被覆盖 → `setCurrentWidget(True)` TypeError 静默失败。
- Shiboken `exec` 被 Python 关键字劫持 → 用 `RoundMenu.exec(menu, pos, ...)` 形式。
- offscreen 验证禁在事件回调里调 `style().subElementRect()`（污染致 QTableView 崩溃）。
- `WA_DontShowOnScreen` 预热：撤属性前必须先 `hide()` 复位，否则真 show() 空操作。
- InfoBarManager 是单例，禁改 margin；`show_info_bar(bottom_offset=N)` 已封装抬升逻辑。
- 单杆视频比分条：选手 1 = image_0 镜像 + 品牌贴回；禁 transpose image_1；禁 cv2 HighGUI。

## 8. 图标与品牌

- **logo 定稿 v4**（三层矩形共享全局色相场 + Fluent 接触阴影 + 缘光），已替换根目录
  `app_icon.ico` / `app_icon.png`；完整规范、色板、构图理由、各版本设计意图见
  [design/logo/spec.md](../design/logo/spec.md)。**不要重新挑选版本**。
- 界面图标库 = qfw `FluentIcon`（控件测试页可搜索复制枚举名）；无对应枚举时用近似替代
  （先例：远程页用 `LINK` 代替缺失的 REMOTE）。
