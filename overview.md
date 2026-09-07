# 界面修复五项 · 完成总览（2026-09-06）

## 结果
冒烟（tools/smoke_fluent_mainwindow.py）14 组全 PASS，pytest 全量 **260 passed**（基线保持）。

## 逐项改动

### 1. 菜单栏全部移入统一设置页
- `main_window/main_window.py`：删除 `vBoxLayout.insertWidget(0, self._menubar_widget)`（工作台顶部不再渲染菜单栏）；`_init_menubar()` 保留——Action 对象（主题/布局/字号等）与状态复用。
- 设置页新增 Watt Toolkit 式组件 `main_window/setting_cards.py`（SettingGroup 组标题+单张大卡+行间分隔线 / SettingRow 图标+标题+描述+右侧控件 / 带开/关文字的 Switch / ComboBox / Button）。
- `main_window/hub_pages.py::SettingsHubPage` 全新重写为滚动页 + 7 分组：外观（主题模式/颜色/字号/字体/缩放/布局）、性能（亚克力/动画+5 个表格平滑开关）、快捷键与工具（6 行）、数据库与接口（嵌入 AdminSettingsPage）、售后（周期设置）、跑视频（署名卡）、配置文件（3 行打开入口）。设置项直接绑定主窗口方法，修改即时生效。

### 2. 二级页切换无响应修复 + Pivot 固定宽度靠左
- 根因：qfw `PivotItem.itemClicked` 以**位置参数**回传 bool，`lambda w=page:` 的默认值被覆盖 → `setCurrentWidget(True)` → TypeError 静默失败。
- 修复：`main_window/pivot_page.py` 改 `def _go(*_args, w=page)`（`*args` 吞掉回传值）。
- 宽度：Pivot 包左对齐 HBox（margins 24,10,0,0）+ 新增 `lock_pivot_width()`（按 sizeHint 锁固定宽）。实测运维 600px / 售后·跑视频 258px，均远小于容器 1451px。
- 冒烟 [12] 模拟真实点击 PivotItem 验证 stack 真实切换（current=RecordsPage）。

### 3. 面板设置统一迁移
- ManagementHub / AftersaleHub / LedgerHub 全部删除各自设置页。
- 管理设置（数据源/接口账号/收集上传/手动添加/MySQL 同步）以 `AdminSettingsPage(embedded=True)` 整体嵌入统一设置「数据库与接口」组：去内部滚动区、去性能卡（统一页性能组已有同款开关）。
- 信号转发：`settings_hub.aftersale_cycle_saved → aftersale_hub.reload_cycles()`；`settings_hub.table_smooth_changed → _apply_all_table_smooth()`（刷新三个 Hub + 已打开远程窗口）。

### 4. 统计图表不单独建页
- 删除 StatsHubPage 导航注册与 `_open_aftersale_stats_chart`/`_open_ledger_stats_chart` 包装方法。
- 售后/跑视频记录页工具栏「统计图表」按钮（`_on_open_stats_chart`）直开图表窗口，与重构前呈现一致；面板功能内容零改动。

### 5. 远程会话列入二期
- RemoteHub 导航注册与回调全部移除；工作台远程面板 / 会话中心入口不受影响。
- 二期设计稿：`design/remote_session_v2.html`（一级导航回归 + 会话总览/P2P 访客/连接诊断/隧道配置四页，含原生 HWND 约束处理与实施顺序）。

## 改动文件
- 改：main_window/main_window.py、main_window/hub_pages.py、main_window/pivot_page.py、windows/management/settings_page.py、tools/smoke_fluent_mainwindow.py
- 新：main_window/setting_cards.py、design/remote_session_v2.html

## 后续可选
- 真机回归（主题切换/设置保存/MySQL 卡保存路径）
- 按 design/remote_session_v2.html 启动二期实施

---

# 一期反馈第二轮（2026-09-06 晚）

## 1. 二级切换条缩小 30% + 删除页头副标题
- `main_window/pivot_page.py`：`_shrink_items()`——PivotItem 字体 18→13px、行高→24px、qss `padding: 10px 12px`→`1px 12px`。实测运维切换条 600→500px、售后/跑视频 258→213px，行高 42→24px。
  - 坑：只 `setFixedHeight(24)` 压不住——全局字体大时 qss padding 使 item minimumSizeHint 涨到 42px 并被布局写回，整行仍撑高；必须同步改写 item 样式表 padding（保留选中态/hover 规则）。
- `windows/aftersale/records.py` / `windows/run_video/records.py`：删除记录页页头副标题「售后问题上报」「跑视频记录：…」（与二级页签重复）。「记录与统计」大标题保留，需要一并去掉可再删。

## 2. 设置页改为左标题 + 右 SegmentedWidget 分页（Watt 式）
- `main_window/hub_pages.py::SettingsHubPage` 重排：头部 = 左侧 56px 图标块 +「设置」大标题 + 描述，右侧 SegmentedWidget；内容 = QStackedWidget 5 页（每页独立滚动）：
  - 外观（外观组）│ 性能（性能组）│ 工具（快捷键与工具 + 配置文件）│ 数据库（管理设置整体）│ 面板设置（售后周期 + 跑视频署名）
  - SegmentedItem 继承 PivotItem，onClick 同样需 `*_args` 吞参（14.8 冒烟已验证真实切换）。
- 信号与嵌入属性不变（aftersale_cycle_saved / table_smooth_changed / admin_settings / cycle_page / signer_card）。

## 验证
- 冒烟 smoke_fluent_mainwindow.py：[13] 新增行高 24px 断言、[14] 新增 SegmentedWidget 点击切换断言，14 组全 PASS。
- pytest 全量 **260 passed**（基线保持）。
