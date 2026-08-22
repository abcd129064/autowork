# AutoWork UI 设计评审与优化方案

## 评审结论
设计系统成熟度：低。应用是「半 Fluent / 半原生 / 半 QSS」的混合体，没有集中的设计令牌。
主题色、语义色、灰阶、间距、字号散落在 `windows/*.py` 与 `styles/*.qss`，同一语义在不同窗口呈现不同色相。

## P0 修复完成情况（2026-08-23 上午，全部完成）

### 已落地
1. **`core/design_tokens.py`**：单一事实来源。ACCENT_FALLBACK、SEMANTIC 五色（翡翠绿/琥珀金/玫瑰红/标准蓝/石板灰，均为白字按钮安全色）、深浅灰阶、4px 间距标尺、圆角、排版刻度、控件尺寸、LEGACY_MAP 迁移对照。新增 `lighten()/darken()` 纯标准库派生函数（hover +12% / pressed -18%）。
2. **aftersale_panel.py**：`_accent_hex()` 收口委托 `theme_qss.current_accent_hex()`；数据源标签（灰/绿/橙）与已解决/未解决着色接入 SEMANTIC。
3. **management_panel.py**：迁移四色按钮改为令牌驱动 + 明度派生；运行状态、链接色、设备状态、高频告警、趋势图三色、预警文案、健康度异常分级全部接入 SEMANTIC。grep 确认无残留硬编码语义色。
4. **main_window/main_window.py**：`_KD_STATUS_ACCENTS` 接入 SEMANTIC warning/danger。
5. **`core/theme_qss.py`**（新增）：`load_window_qss()` 按深浅主题加载 QSS 并做强调色锚点替换（`#00BCD4` → 用户主题色）；`apply_window_qss()` 一行接入子窗口，订阅 `themeChanged` + `themeColorChanged` 双信号，shiboken6.isValid 判活守卫。
6. **SFTPWindow / SSHTerminalWindow / TablePanelWindow** 三个独立 QDialog 接入 `apply_window_qss`，原生控件不再掉出主题。
7. **styles/light.qss** 全量重写：补齐 QPushButton、列表、输入框、滚动条、进度条等全部选择器（原文件只有 4 条规则），修复浅色模式按钮失样。
8. **ui_mixin.py**：`_load_qss` 复用 `substitute_accent`；`_apply_theme_color_set` 换强调色后重刷 QSS。

### 刻意不动
- `settings_dialog.py` 的 `#ff5252`/`#f0a020`：用户可配置日志高亮规则的默认值（注释明确"不可删除"），属用户数据而非主题语义
- `ansi_terminal.py` 的 16 色 ANSI 调色板：终端标准配色，不应跟随应用主题
- `make_icon.py`：图标生成脚本，非运行时

## P1 / P2 修复完成情况（2026-08-23 下午）

### 已完成
1. **P1-剩余对话框主题接入**：`conn_diag_panel.py` / `rdp_window.py` / `image_viewer.py` 三个独立 QDialog 接入 `apply_window_qss(self)`。图片查看器的迁移按钮经 `setCustomStyleSheet` 控件级注入，优先级高于窗口级 QSS，语义配色不受影响。至此全部独立窗口的原生控件均纳入主题。
2. **P2-字体系统统一**：移除 `ssh_terminal.py` 6 处 `setFont(btn, 11)` 硬写（按钮改为继承全局字体）；`main.py` 与 `ui_mixin.py` 的重复 `max(12, int(pt*4/3))` 换算收口为 `design_tokens.pt_to_px()` 单一函数。
3. **P2-对话框尺寸令牌**：`design_tokens.DIALOG_SIZE` 三档 S=(480,380) / M=(900,560) / L=(1180,680)。存量对话框尺寸不动（避免改变用户习惯），令牌约束新增对话框。

### 待办（见 `docs/售后面板UI改进方案.md`）
- P1-按钮语言统一（SFTP 原生文本按钮 → Fluent）
- P1-导航范式收敛（结构性改动，建议下个大版本）
- P2-排版刻度与间距标尺落地（随售后面板改版一并做）

## 售后面板设计稿落地（2026-08-23 晚，方案 A + B 核心项已实施）
按设计图完成「记录与统计」页改造，五处核心变化全部落码：
1. **页头与数据源指示**：标题 + 副标题 + 数据源状态右对齐
2. **加载反馈**：IndeterminateProgressBar，异步查询期间显示
3. **周期概览指标卡 ×4**：本周期记录 / 未解决（有积压转红）/ 已解决率 / 主动发起占比（设计稿原第 4 卡「平均响应」因 response_time 是自由文本档位无法计算，改为真实可算的主动发起占比）
4. **表格信息整合**：球房+地区+桌号合并为「位置」双行列，填写人并入填写时间，解决人并入响应时间，「是否…」判定字段进 tooltip，信息零丢失
5. **状态徽章 + 一键解决**：红=未解决 / 绿=已解决语义徽章；未解决行行内实心「标记已解决」按钮（走新增 `mark_resolved_batch` 最小化更新，不弹编辑窗，3 步 → 1 步）
6. **批量操作**：勾选列 + 批量操作条（全选本页 / 批量标记已解决 / 批量删除 / 取消选择），`delete_records` 走数据库批量删除
7. **设置页**：整体包 ScrollArea，小分辨率不再裁剪

数据层新增：`mark_resolved_batch` / `delete_records`；`query_with_stats` stats 扩 `initiative`、`rate`。
新增测试：`tests/test_aftersale_batch_ops.py` 8 用例。全套 145 passed。

### 真机运行回归修复（当晚）
用户实际运行日志（`logs/autowork_conn.log`）暴露两处，均已修复：
1. `AttributeError: '_on_toggle_all'`——中间编辑版本残留，最终代码已含，复核确认；
2. `_badge_label` QSS 解析错误——非 f-string 字面量段 `}}` 未被转义消费，产出多余右花括号，改单 `}` 后 offscreen 断言 `'}}' not in styleSheet()` 通过。
最终验证链：3 文件 py_compile + 145 passed + offscreen 冒烟 `ALL VERIFIED`（徽章/批量条显隐/操作按钮数/已解决率卡）。

## 填写录入页设计稿落地（2026-08-23 凌晨，v2 确认稿已实施）
按用户确认的 v2 设计稿重构「填写录入」页，交互逻辑不变，仅改 `windows/aftersale_panel.py`：
1. **三段式分组卡**：基本信息 / 位置关联 / 问题描述（`_SectionCard`，强调色竖条跟随主题色），替代原扁平 QFormLayout，标签上置、三列按比例排布
2. **是/否分段开关**：`YesNoSegment` 基于库现成 SegmentedWidget，选「是」染成功绿；默认值不变（是/否/是），collect/set_values 口径对齐旧下拉
3. **带出可视反馈**：桌号/地区被球桌带出时显示「带出」绿色小字；新增关联确认条展示桌号+SNK+城市（隐藏字段 snk_code 显式化）
4. **发生原因与解决方案等宽等高**（64px 多行框并排），问题独占一行，解决人/响应时间平分两列
5. **字段级内联校验**：提交失败红框 + 内联提示 + 滚动聚焦首个错误（FluentCombo 新增 setError；LineEdit 用库自带 setError）；顶部横幅保留
6. **页头周期芯片**（current_cycle_start + cycle_span_days 实时算 mm/dd–mm/dd）+ **操作条**（ProgressBar 必填进度 n/5，满绿缺琥珀点名）
7. **布局修正（用户两次截图反馈）**：外边距 20/16 与记录页对齐（标题不贴边）；操作条不置底、随内容放在表单末栏下方；纵向不拉伸（stretch 吸收剩余高度）；横向下拉/输入框恢复固定像素宽（type/problem/room 320、region 160、table_no 140、creator 160、resolver/response 220）+ 右侧留白，仅发生原因/解决方案多行框等分自适应

编辑弹窗复用同一表单，自动继承新布局。
验证：COMPILE_OK + 145 passed + offscreen 冒烟 `SMOKE_V2_ALL_VERIFIED`（`tests/_smoke_entry_v2.py`）。

## 架构评审与 SQL 代码优化（2026-08-23，T01-T05 全部落地）

按架构评审方案（`docs/架构评审与SQL代码优化方案.md`）完成数据层重构：

1. **DDL 三份合一**：`database/schema.py` 成为 8 表列元数据单一来源（`to_sqlite_ddl/to_mysql_ddl` 双方言生成），`backend.MYSQL_DDL` 与 `table_db._CREATE_*` 改由 schema 生成，**修复 mysql_sync 第三份 DDL 缺 `file_path` 的漂移**。QA 以 git HEAD 旧常量为基准逐表比对 8/8+8/8 语义等价。
2. **镜像推送机制 B 整体下线**（用户确认 MySQL 主 + SQLite 兜底）：删除 `push_all/push_table/push_aftersale`、`MysqlSyncWorker`、自动同步 UI、`_trigger_auto_mysql_sync`，保留连接测试 `test_connection`。
3. **迁移注册表**：`schema.MIGRATIONS`（4 表 31 列）驱动 SQLite/MySQL 两侧补列，旧迁移块逐项对照无遗漏。
4. **直读旁路收敛**：`table_panel.py`/`forensic_report.py` 裸 `sqlite3.connect` 改走 table_db 双后端 API（新增 `get_latest_kd_status_by_code`/`query_latest_kd_full`/`get_table_info_by_snk_or_host`）。
5. **management_panel.py 拆包**：4483 行 → `windows/management/` 包（10 文件）+ 46 行 re-export shim。

验证：**145 passed / 0 failed**，QA 两轮独立验证均 NoOne。详见 `docs/架构评审与SQL代码优化方案.md` 第 10 节。

## 剩余工作
- 售后面板方案 C（信息层级与视觉统一收尾）：字段级校验、空状态引导
- SFTP 按钮语言统一、导航范式收敛
- 编辑弹窗宽度（DIALOG_SIZE S 档）待用户确认后对齐

## 交付物清单
| 文件 | 变更 |
|------|------|
| `core/design_tokens.py` | 新增（令牌模块，含 pt_to_px / DIALOG_SIZE） |
| `core/theme_qss.py` | 新增（窗口 QSS 应用工具） |
| `styles/light.qss` | 重写（补全全部选择器） |
| `windows/aftersale_panel.py` | 接入令牌 |
| `windows/management_panel.py` | 接入令牌 |
| `windows/sftp_window.py` | 接入主题 |
| `windows/ssh_terminal.py` | 接入主题 + 移除硬写字号 |
| `windows/table_panel.py` | 接入主题 |
| `windows/conn_diag_panel.py` | 接入主题 |
| `windows/rdp_window.py` | 接入主题 |
| `windows/image_viewer.py` | 接入主题 |
| `main_window/main_window.py` | 接入令牌 |
| `main_window/ui_mixin.py` | QSS 替换逻辑复用 + pt_to_px |
| `main.py` | pt_to_px 替换重复换算 |
| `docs/售后面板UI改进方案.md` | 新增（完整改进方案） |
