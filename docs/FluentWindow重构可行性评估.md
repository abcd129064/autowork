# AutoWork 全面重构为 FluentWindow · 可行性评估

> 评估日期：2026-09-06　|　环境：PySide6 6.11.2 + qfluentwidgets 1.11.3
> 方法：源码静态调研（全量 `main_window/` `windows/` `core/`）+ offscreen 实测验证（见 `tools/verify_fluent_refactor.py`）

---

## 一、结论先行

**可行度评级：★★★★☆（高，推荐采用「渐进式分层重构」而非一次性重写）**

三个决定性发现，把这件事从"大手术"变成了"中等规模改造"：

| 发现 | 影响 |
|---|---|
| `MainWindow` **已经继承 `FluentWindowBase`**，自带 `stackedWidget`，只是 `navigationInterface = None` | 换 `FluentWindow` 是**同层基类替换**，不是跨层级重构。改动点仅 `main_window.py:121` 一行 |
| `FluentWindowBase` 是 **QWidget 系**（MRO 末端 `QWidget`），不是 QMainWindow | 不存在"QMainWindow 塞进 QStackedWidget"的非法用法问题，子面板内嵌技术可行 |
| 三个业务面板（`ManagementPanelWindow` / `AftersalePanelWindow` / `LedgerPanelWindow`）**已经是 `FluentWindow`** | 项目早已接受这套范式，主窗口只是历史遗留没跟上 —— 重构是"对齐"，不是"换血" |

**但**：当前 `FluentWindowBase` 的用法是"手工往 `hBoxLayout` 塞 `vBoxLayout`"的野路子，直接换基类会导致布局三槽位错乱（已实测）。这一步必须配套改布局装配，是整个重构唯一的技术硬骨头。

---

## 二、现状盘点

### 2.1 主窗口架构

```
MainWindow(SettingsMixin, ProcessMixin, RemoteMixin, UIMixin, FluentWindowBase)
   ├─ FluentTitleBar（34px，手工 setContentsMargins(0,34,0,0) 避让）
   ├─ vBoxLayout（手工 addLayout 到 hBoxLayout）
   │    ├─ 菜单栏 widget（26px，5 个下拉菜单 / 22 个 Action）
   │    ├─ centralwidget（Ui_MainWindow 903 行）
   │    │    ├─ 工具栏 FlowToolbarScrollArea（21 控件 / 13 入口，最高 132px ≈ 3 行）
   │    │    └─ Splitter 多列（设备树 | 日志控制台 | P2P 远程面板）
   │    └─ 状态栏 widget
   └─ stackedWidget（存在但为空，addSubInterface 抛 NotImplementedError）
```

### 2.2 导航现状：4 套机制并存

| 机制 | 位置 | 规模 |
|---|---|---|
| 顶部下拉菜单栏 | `ui_mixin.py:814-968` | 5 菜单 / 22 Action |
| 两行 FlowLayout 工具栏 | `autowork_with_table.py:304-510` | 21 控件 / 13 入口 |
| Splitter 多列 | `autowork_with_table.py:788-903` | 面板/经典双布局运行时重建 |
| **独立顶层窗口** | `ui_mixin.py:1150-1182` 等 | 球桌 / 售后 / 跑视频 / 远程 |

功能入口分散在 4 处，用户要在"菜单 → 工具栏 → 弹窗"之间跳转。这是重构的**真实收益点**。

### 2.3 代码规模

| 模块 | 行数 | 说明 |
|---|---|---|
| `main_window/ui_mixin.py` | 1899 | 菜单栏/主题/布局/面板调度 |
| `main_window/main_window.py` | 1731 | 主类 + 信号连接 + closeEvent |
| `main_window/remote_mixin.py` | 1353 | P2P / frpc |
| `autowork_with_table.py` | 903 | Ui_MainWindow（Qt Designer 生成 + 手改） |
| `windows/management/*` | ~6000 | 6 个 QWidget 子页 |
| `windows/aftersale/*` | ~3300 | 3 页 + 统计弹窗 |
| `windows/run_video/*` | ~1600 | 3 页 |
| `windows/remote_session/*` | ~5900 | SSH/SFTP/RDP/诊断 |

---

| ✔️ 全部 | **11 / 11 项通过** | offscreen 实测（`tools/verify_fluent_refactor.py`，可重复执行） |
| 📏 | 导航 200px → 页面可用 **1199px** | 窗口 1500 时可用 1300px，三张业务表全放得下 |
| ⚠️ | **+53~93px** | 业务表列宽在导航展开 200px、窗口 1400 时的溢出量（窗口提到 1500 即归零） |
| 🎯 | **1 行** | 主窗口基类改动点（`main_window.py:121`） |

## 三、实测验证结果（offscreen，11/11 全通过）

验证脚本：`tools/verify_fluent_refactor.py`（可重复执行）

> 退出码 139 是 offscreen 环境下的 Qt 清理段错误（项目已知现象，用 `os._exit(0)` 规避），不影响断言结果。

| # | 验证项 | 结果 | 关键数据 |
|---|---|---|---|
| A1 | 换基类不崩溃 | ✅ | `hBoxLayout` 槽位 = `['NavigationInterface', 'Layout', 'Layout']` |
| A2 | 未注册 SubInterface 时 stackedWidget 为空 | ✅ | `count=0, visible=True` → **页面区留白** |
| A3 | 原 vBoxLayout 与导航共存 | ⚠️ | 3 槽位导致内容错位，**必须重构布局装配** |
| A4 | addSubInterface 后页面宽度可用 | ✅ | 1351px（窗口 1400 − 导航 48） |
| B1 | 嵌套 FluentWindow 可行性 | ✅ | PySide6 6.11 + qfw 1.11 实测不崩溃 |
| B2 | 嵌套后重复标题栏 | ⚠️ | 子窗口 `titleBar.isVisible() = True` → **视觉污染** |
| B3 | 嵌套后内层页面宽 | ✅ | 1302px（仅比单级少 49px） |
| B4 | 降层为 QWidget + Pivot | ✅ | 1351px，宽度完全恢复 |
| D1 | objectName QSS 选择器 | ✅ | Qt QSS 沿父链继承，子页面内**仍命中** |
| E1 | TOP/BOTTOM 分区 + addSeparator | ✅ | — |
| E2 | setCollapsible / setExpandWidth / setAcrylicEnabled | ✅ | — |

### 实测挖出的两个真实坑

**坑 1：`addSubInterface()` 强制要求非空 objectName**
```
ValueError: The object name of `interface` can't be empty string.
```
当前 `Ui_MainWindow` 的 `centralwidget` 有 objectName，但**动态创建的页面需要显式 `setObjectName()`**。三个子面板在 `__init__` 里已做（`tablePage` / `devicePage` …），新页面必须补。

**坑 2：导航没有 `setDisplayMode()` API**
`NavigationInterface` 上不存在 `setDisplayMode`（qfw 1.11），显示模式由 `setExpandWidth()` + `setCollapsible()` + 窗口宽度自适应决定。设计稿里"折叠/展开"按钮对应 `setCollapsible(True)` + 宽度阈值。

---

## 四、关键量化：列宽溢出问题

这是唯一需要动手改业务的点。业务表列宽（固定 Interactive 模式）：

| 业务表 | 列数和 | 导航 48px<br>可用 1351px | 导航 200px<br>可用 1199px | 导航 260px<br>可用 1139px |
|---|---|---|---|---|
| 球桌管理 `TABLE_COLUMNS` | 1260px | ✅ −91 | ⚠️ **溢出 61** | ⚠️ **溢出 121** |
| 售后记录 `TABLE_COLUMNS` | 1292px | ✅ −59 | ⚠️ **溢出 93** | ⚠️ **溢出 153** |
| 跑视频 `TABLE_COLUMNS` | 1252px | ✅ −99 | ⚠️ **溢出 53** | ⚠️ **溢出 113** |
| 设备状态 `DEVICE_COLUMNS` | 870px | ✅ −482 | ✅ −330 | ✅ −270 |

**三种解法**（推荐组合使用）：

1. **窗口默认宽度提到 1500+**（现子面板已 `resize(1680, 900)`，主窗口 1500 不突兀）
   → 导航 200px 时可用 1300px，**三张表全部放得下**。**零代码改动，首选。**
2. **导航默认折叠**（48px），鼠标悬停展开（qfw 原生 `setCollapsible` 行为）
   → 常态可用 1351px，展开时临时挤压可接受
3. **列宽策略微调**：把"描述/备注"这类弹性列改为 `ResizeMode.Stretch`
   → 兜底方案，需改 `aftersale/common.py:234` 等处列定义

---

## 五、方案对比

| 方案 | 做法 | 工作量 | 风险 | 收益 |
|---|---|---|---|---|
| **A. 激进** | 全部 13 个功能页平铺为一级导航 | 大 | 高（导航过长、失去层次） | 导航项达 13 个，Fluent 规范建议 ≤8 |
| **B. 渐进（推荐）** | 一级 6 项（工作台/运维/售后/跑视频/远程/统计）+ 二级 Pivot | **中** | **低** | 层次清晰，可分批上线 |
| **C. 最小** | 主窗口只加导航壳，子功能仍走独立窗口 | 小 | 最低 | 收益有限，等于"快捷启动器" |

**推荐方案 B**，导航结构：

```
⌂ 工作台          ← 原 centralwidget 整体，21 控件工具栏 + 三列 Splitter 原样保留
▤ 运维管理        ← 6 个 QWidget 子页，零改造直接平铺
☺ 售后            ← FluentWindow 降层为 QWidget + Pivot（登记/记录/统计）
▶ 跑视频          ← 同上（登记/记录/周期设置）
⇄ 远程会话        ← 仅隧道/诊断内嵌；SSH/SFTP/RDP 保持独立窗口
◫ 统计图表        ← 建议用本地图表替代内嵌 WebView
──────────────
⚙ 设置            ← NavigationItemPosition.BOTTOM
ⓘ 关于            ← BOTTOM
```

---

## 六、阻碍点与对策

| 级别 | 阻碍点 | 证据 | 对策 |
|---|---|---|---|
| 🔴 高 | 子面板是 `FluentWindow`，内嵌出现第二个标题栏 | 实测 `titleBar.isVisible()=True` | **降层为 QWidget + 内部 Pivot**。需删 `setTitleBar` / `navigationInterface.setAcrylicEnabled` / `switchTo()` 调用，改 `closeEvent` 清理为页面级 detach |
| 🔴 高 | 直接换基类导致 `hBoxLayout` 三槽位错乱 | 实测 A1/A3 | **必须配套重排顶层布局**：`centralwidget` 注册为 SubInterface，菜单栏/状态栏下沉 |
| 🟡 中 | 窗口级 `setStyleSheet` 会污染导航侧栏 | `ui_mixin.py:1759` | QSS 改挂各子页面 widget；实测 objectName 选择器仍命中（D1），**无需重写 14 个选择器** |
| 🟡 中 | Mixin 双向耦合，`_append_log` / `_show_info_bar` 约 220 处跨 mixin 依赖 | 四个 mixin 互相调用 | **Mixin 保持不动**，只换基类。这两个符号作为全局服务保留在主类 |
| 🟡 中 | RDP 依赖 `WA_NativeWindow` + `winId()` 做 SetParent 嵌入 | `rdp_window.py:96,333` | **保持独立窗口**，不做内嵌 |
| 🟡 中 | 快捷键 `QShortcut(key, self)` 全挂主窗口，切页后抢焦点 | `settings_mixin.py:147-199` | 按 `stackedWidget.currentWidget()` 动态 `setEnabled` |
| 🟢 低 | `switch_layout` 被 monkey-patch、`_rearrange_p2p_layout` 运行时搬控件 | `main_window.py:875-888` | 页面化后布局只构建一次，这些补丁**可整体删除**（反而简化） |
| 🟢 低 | `widget_page.py` 内嵌 `_DemoFluentWindow` 做组件演示 | `widget_page.py:205,222` | 改为静态截图或独立弹窗，避免三层嵌套 |
| 🟢 低 | 三个 `*_panel.py` shim 有独立进程入口 | `management_panel.py:67-73` | 保留 shim 与 `__main__` 入口，不受影响 |

---

## 七、分阶段实施路径

| 阶段 | 内容 | 改动文件 | 预估 |
|---|---|---|---|
| **P0 验证** | 建分支，换基类跑通空壳 + offscreen 冒烟 | `main_window.py:121` | 0.5 天 |
| **P1 主窗口** | 换 `FluentWindow`；`centralwidget` 注册为"工作台"；菜单栏/状态栏下沉；删 QMainWindow 兼容分支 | `main_window.py:121-153`、`autowork_with_table.py:288-295` | 2–3 天 |
| **P2 运维管理** | 6 个 QWidget 子页 `addSubInterface` 平铺（零改造） | `management/window.py` | 1 天 |
| **P3 售后/跑视频** | 两个 FluentWindow 降层为 QWidget + Pivot；改 Worker detach 生命周期 | `aftersale/window.py`、`run_video/window.py` | 3–4 天 |
| **P4 远程/统计** | 隧道 + 诊断内嵌；SSH/SFTP/RDP 保持独立；统计页本地图表化 | `remote_session/*`、`stat_charts.py` | 2 天 |
| **P5 收尾** | QSS 下沉、快捷键按页启用、列宽/窗口宽度微调、pytest 回归（基线 235）+ 真机验证 | 全局 | 2–3 天 |

**合计约 10–14 人天**（含自测；不含真机跨 DPI 回归）

**每阶段都可独立上线**——这是选方案 B 的核心价值：P1 完成即可交付一个"带侧边导航的工作台"，后续页面逐个迁入。

---

## 八、风险与不做的事

**明确不做**（避免过度重构）：
- ❌ 不动 4 个 Mixin 的内部结构 —— 它们是"按职责切文件"，强拆收益低风险高
- ❌ 不重写 `Ui_MainWindow` 的 903 行 —— 整体作为"工作台"页面注册
- ❌ 不强制内嵌 SSH/SFTP/RDP —— 原生句柄机制不允许
- ❌ 不顺手做 design_tokens 全量落地（`design_tokens.py:160-173` 自陈 P0 未完成）—— 独立立项

**回滚预案**：
- 每阶段一个分支，P1 只改基类 + 布局装配，可单 commit revert
- `autowork_with_table.py:288-295` 的 QMainWindow 兼容分支先**保留**（不删），作为回退保险，P5 确认稳定后再清理

**回归基线**：pytest 235 passed（`tests/`），offscreen GUI 冒烟

---

## 九、设计稿

见 `design/fluent_window_proposal.html`（浏览器打开，可交互）：

- 1500×880 主窗口，浅色主题，强调色 `#00BCD4`（取自 `core/design_tokens.py`）
- 点击左侧导航切换 6 个一级页面
- 二级 Pivot 切换子页（运维管理 4 页 / 售后 3 页 / 跑视频 3 页 / 远程 5 页）
- 左上角按钮可切换**导航折叠/展开**，实时显示页面可用宽度
- 右上按钮可开关 4 处**设计标注**（① 导航分区 ② 为何用 Pivot 而非嵌套 ③ 页面区来源 ④ 状态栏下沉）
