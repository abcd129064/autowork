# docs/ 文档索引

> 新增文档请在**同一次提交内**补进本索引（分类 + 一句话摘要 + 状态）。
> 文档内引用仓库文件时，一律写从仓库根开始的正斜杠相对路径，
> 这样 `python tools/check_refs.py --all` 才能校验断链。
> 作业规范见根目录 [AGENTS.md](../AGENTS.md)。

状态图例：**规范** = 长期有效、必须遵守 ｜ **设计·已落地** = 方案已进代码，文档为权威说明
｜ **调研快照** = 某时点的调查结论，可能已过期，只作背景参考

---

## 0. 文档树（先读哪篇？）

```
docs/
├── README.md          ← 你在这里：索引 + 导读
│
│  ── 主干（长期维护，★）──
├── ARCHITECTURE.md    ★ 架构/目录/数据组织 —— 想懂项目怎么搭的，先读这篇
├── DEVELOPMENT.md     ★ 开发/测试/提交流程 —— 动手改代码前读
├── DEPLOYMENT.md      ★ 构建/发布/生产部署 —— 发版与上线操作手册
├── DESIGN.md          ★ 设计指南/令牌/UI 陷阱 —— 写界面前读
├── API.md             ★ 接口契约（类/函数/信号签名，api_doc_audit.py 审计）
├── CHANGELOG.md       ★ 版本演进记录（按阶段，newest 在上）
├── TODO.md            ★ 待办与未来计划（P0/P1/P2）
│
│  ── 专题（历史沉淀，按需查阅）──
├── 规范类             Qt内联引导说明.md / frp-065-api-reference.md /
│                      GPLv3依赖合规说明.md
├── 设计方案类         MySQL兜底降级设计.md / auto_update_research.md /
│                      架构评审与SQL代码优化方案.md / 售后面板UI改进方案.md /
│                      frp-source-integration.md / settings_panel_redesign/
├── 性能调查类         PERF_REVIEW.md / perf_scroll_investigation.md /
│                      售后面板性能调查报告2026-09-06.md / QSS使用审计与弃用评估.md /
│                      远程面板性能调查报告2026-09-24.md /
│                      管理面板性能调查报告2026-09-25.md /
│                      表格滚动延迟调查报告2026-09-25.md /
│                      大屏与超高DPI渲染性能调查报告2026-09-25.md /
│                      硬件加速方案调研报告2026-09-28.md /
│                      表格滚动位块搬移修复2026-10-06.md /
│                      自适应降级阈值与弹窗快照淡入2026-10-07.md
├── 外部接口类         xqzg接口清单.md / newbv_inventory_fields.md
├── 技术栈调研         WinUI3重构可行性调研2026-09-30.md
└── 图表              class-diagram.mermaid / sequence-diagram.mermaid
```

新人路径：`ARCHITECTURE.md` → `DEVELOPMENT.md` → `API.md`；
改界面前加读 `DESIGN.md`；发版前加读 `DEPLOYMENT.md` + `CHANGELOG.md`；
找「接下来做什么」看 `TODO.md`，找「之前做了什么」看 `CHANGELOG.md`。

---

## 一、主干文档（★ 长期维护）

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层依赖、目录职责、12 张表数据组织、配置域路由、双后端机制、Web/frp/打包架构 | 规范 |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 解释器矩阵、日常开发、测试矩阵（单测/冒烟/真机/压测/E2E）、提交前检查、发版流程 | 规范 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 生产环境一览、桌面端分发、Web v1/v2 并行部署、自动更新发布、回滚 | 规范 |
| [DESIGN.md](DESIGN.md) | Fluent 设计语言、设计令牌、主题/QSS 规则、组件选型、布局模式、UI 陷阱清单 | 规范 |
| [API.md](API.md) | 全项目公开接口契约：桌面端各层类/函数/信号 + 售后面板 Web API + 配置键路由 | 规范（用 `tools/api_doc_audit.py` 审计一致性） |
| [CHANGELOG.md](CHANGELOG.md) | 按阶段记录项目演进（3.9 → 3.13），条目注明落点文件/文档 | 长期追加 |
| [TODO.md](TODO.md) | P0/P1/P2 待办：桌面端、Web 端、文档治理、跨项目备忘 | 长期更新 |

## 二、规范与契约（改动代码前必读）

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [Qt内联引导说明.md](Qt内联引导说明.md) | conda base 激活态下为什么必须在 `import PySide6` 前执行内联引导、标准写法、哪些入口已内置 | 规范 |
| [frp-065-api-reference.md](frp-065-api-reference.md) | frp 0.65 管理 API 完整参考手册：frps 六端点 + frpc 六端点 + 鉴权 + 版本墙（0.67/0.68/0.70）+ autowork 端点使用矩阵 | 规范（对照 v0.65.0 tag 源码核验；autowork 远程线改 API 用法前必读） |
| [GPLv3依赖合规说明.md](GPLv3依赖合规说明.md) | qfluentwidgets 带来的 GPL 传染怎么处置：义务由 conveying 而非商用触发、三个分发面逐一判定、已落地的声明清单（`LICENSE`/`NOTICE`/`licenses/`）与后续改动的硬约束 | 规范（改 `requirements.txt` 或新增随包资产前必读） |

## 三、架构与设计方案

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [MySQL兜底降级设计.md](MySQL兜底降级设计.md) | MySQL 为唯一主库，不可用时自动透明回退本地 SQLite，恢复后按 LWW 自动合并增量 | 设计·已落地（`database/backend.py`、`fallback_backup.py`、`merge_back.py`） |
| [auto_update_research.md](auto_update_research.md) | 程序内「检查更新 → 下载 → 替换重启」全链路调研，选定方案 A（整包 zip + 独立 updater 脚本 + nginx 静态分发） | 设计·已落地（`core/updater.py`、`tools/deploy/publish_update.py`、`tools/update_sim/`） |
| [架构评审与SQL代码优化方案.md](架构评审与SQL代码优化方案.md) | 代码拆分/合并需求评审 + SQL 代码专项优化（双后端方言、镜像推送下线等） | 设计·已落地（T01-T05 全过 QA，见文档第 10 节） |
| [售后面板UI改进方案.md](售后面板UI改进方案.md) | 售后面板 UI 分层改进方案，前置依赖 `core/design_tokens.py`、`core/theme_qss.py` | 设计·大部落地（09 月拆包重构随附；剩余 P2 视觉微调见文档头部核对块） |
| [frp-source-integration.md](frp-source-integration.md) | frp Go 源码接入调研 + 落地记录（附录 E~G：二期 P0/P1、frps 概览卡、开机静默预连与预热打洞） | 调研快照 + 设计·已落地（附录） |
| [settings_panel_redesign/统一设置面板重构设计.md](settings_panel_redesign/统一设置面板重构设计.md) | 统一设置面板重构设计，配图在 `settings_panel_redesign/assets/` | 设计·已落地（实际形态为 SegmentedWidget 7 段，见文档头部核对块；字段映射仍权威） |
| [终端全屏应用渲染修复.md](终端全屏应用渲染修复.md) | SSH 终端里 nano/vim/less/top 全屏界面错乱的根因（CSI 行定位序列被丢弃、`\E(B` 漏字、无固定网格）与修复设计：`core/vt_screen.py` 屏幕模型分层、网格↔PTY 尺寸契约、宽字符、输入侧模式；含"序列→行为"对照表与已知边界 | 设计·已落地（2026-10-05，回归见 `tests/test_vt_screen.py`、`tests/test_ansi_terminal_render.py`、`tools/smoke/smoke_ansi_terminal_tui.py`） |
| [远程会话凭据与传输可靠性.md](远程会话凭据与传输可靠性.md) | 远程会话（SFTP/SSH）三项加固：凭据**来源**显式化与「仅本次运行」策略（`core/credentials.py`）、未完成传输队列落盘 + 两侧大小校验（`core/transfer_queue.py` / `core/transfer_verify.py`）、终端 OSC 7 远端工作目录与 SFTP↔终端互跳；含共享 Transport 的测量结论（30 任务 = 30 次认证） | 设计·已落地（2026-10-06，回归见 `tests/test_credentials.py`、`tests/test_transfer_queue.py`、`tests/test_transfer_verify.py`、`tools/smoke/smoke_remote_ssh_creds.py`） |
| [大华相机工具集成调研2026-10-06.md](大华相机工具集成调研2026-10-06.md) | Desktop/Bin 大华 Demo 工具包能力盘点 + autowork 集成方式调研：推荐官方 Python NetSDK wheel 内嵌 + 工具页第 5 子页；登录/取流通路已实测（隧道 4236/4238），P0=搜索/初始化/抓图 | 调研快照（待实施） |

## 四、性能调查

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [PERF_REVIEW.md](PERF_REVIEW.md) | `database/` `workers/` `windows/` 热点路径审查；问题集中在 MySQL 模式「每次调用都付出固定开销」 | 调研快照 |
| [perf_scroll_investigation.md](perf_scroll_investigation.md) | 全项目表格与滚动列表的系统性调研，结论全部基于实测，可用 `tools/perf/scroll_profile.py` 复跑 | 调研快照（2026-09-07） |
| [售后面板性能调查报告2026-09-06.md](售后面板性能调查报告2026-09-06.md) | 售后面板（记录页/统计弹窗/pygwalker）性能专项，只调研不改业务代码 | 调研快照（S1/S3/S4 已落地，见 `tests/test_aftersale_perf_opt.py`） |
| [QSS使用审计与弃用评估.md](QSS使用审计与弃用评估.md) | QSS 使用面审计与弃用评估（环境基线 PySide6 6.11.2 + qfluentwidgets 1.11.3） | 调研快照（2026-09-07；结论已吸收进 [DESIGN.md](DESIGN.md) §3） |
| [远程面板性能调查报告2026-09-24.md](远程面板性能调查报告2026-09-24.md) | 远程面板（四视图表格滚动 / 按钮反馈链路 / open_session 全链路）性能专项；harness 可复跑（`tools/perf/perf_remote_*.py`，已同步优化后口径） | 调研快照 + P0/P1/P2 已落地（2026-09-24，见报告头部核对块） |
| [管理面板性能调查报告2026-09-25.md](管理面板性能调查报告2026-09-25.md) | 运维管理面板（构造/懒构建/填充/查询/定时刷新）专项 + 售后面板落地收益复测；harness 可复跑（`tools/perf/perf_management_panel.py`、`tools/perf/perf_aftersale_residual.py`）；增量方案 N1~N4 已实施（见报告 §七） | 调研快照 + N1~N4 已落地（2026-09-25） |
| [表格滚动延迟调查报告2026-09-25.md](表格滚动延迟调查报告2026-09-25.md) | 售后「记录与统计」与球桌管理表滚动 150~300ms 延迟根因（性能全关 = 回退 2× 慢的 qfw 默认委托 × CJK 字体 × DPI 光栅放大）；文本层绘制缓存实测地板 -84%；harness `tools/perf/perf_scroll_latency.py` 支持 `--scale` 模拟真机 DPI；二·补充节：可见列数扫描（`--cols`）复现「列多更卡」 | 调研快照 + S2 文本层缓存已落地（2026-09-25）；**S5 整格缓存已落地**（2026-09-26，`core/lean_table_delegate.py`，35 例像素等价回归 + 全列实测 -44~-59%，勾选格 ±1 预乘微差已证明并容差化，见报告 §三 S5） |
| [大屏与超高DPI渲染性能调查报告2026-09-25.md](大屏与超高DPI渲染性能调查报告2026-09-25.md) | 三台真机（27寸/14寸/55寸外接）卡顿与「未响应」根因：四条逐帧整窗渲染路径（弹窗 opacity/页面切换/菜单 setMask/亚克力模糊）成本 ≈ 线性于物理像素数，场景 C 为事件泵饥饿非死锁；harness `tools/perf/perf_popup_dpi.py`（新增）+ `perf_scroll_latency.py --win`；P0 三项（阈值自动降级）+ P1 四项（卡片级弹窗淡入 / dpi 叠加告警 / 菜单 FADE_IN 映射 / 大屏性能模式开关）已实施，P2-2 取消、P2-1 缓期（见 §五） | 调研快照 + P0/P1 已落地（2026-09-25，`tests/test_perf_dpi_degrade.py` 17 例 + `test_perf_dpi_p1.py` 10 例；阈值与弹窗降级路径已由 [自适应降级阈值与弹窗快照淡入2026-10-07.md](自适应降级阈值与弹窗快照淡入2026-10-07.md) 接管） |
| [硬件加速方案调研报告2026-09-28.md](硬件加速方案调研报告2026-09-28.md) | 是否引入 GPU 硬件加速的决策依据：现状为 Qt Widgets raster（全仓零 GL 设置）；Qt 6 已移除 ANGLE，QWidget 系只剩 OpenGL proper 一条路；Intel/NVIDIA/AMD 驱动矩阵 + RDP 与 Mica  backdrop 两大风险场景；结论是表格热点（delegate 逐格绘制 × CJK 字体光栅化）在 GL 视口下仍在 CPU，收益有限 | 调研快照 + 勘误补充（2026-09-29，修订 dist 实测/GL 机制/引用出处等五处，见报告 §七；未改代码；P0 维持 raster，P1 试点条件与 P2 兜底观测见 §四） |

| [表格滚动位块搬移修复2026-10-06.md](表格滚动位块搬移修复2026-10-06.md) | 表格滚动为什么每步都整视口重绘：qfw 给表格挂 QSS 使 `WA_OpaquePaintEvent` 被置 False（阻断 A）+ 库自绘悬浮滚动条作为表格子控件压住 viewport（阻断 B），两条各自都足以让 Qt 放弃 `QWidget::scroll()` 位块搬移；****第二轮已按「viewport 真正不透明」形态落地**：先取「表格背后的实底颜色」（**抓屏幕真实像素**：四边各 1×1，至少两点一致才采信；渲染父级只能拿到 palette 色，真机上暗色主题会填成纯黑、浅色填成灰白），每次绘制前填满暴露区域，取不到颜色则不开启；实测绘制面积 1,173,120 → 1,547 px²/步（1/758）、每步 10.0 → 1.03 ms；`tools/perf/perf_scroll_latency.py --truth [--blit on\|off]` 可 A/B | 设计·已落地（2026-10-06 第二轮；两轮真机反馈各自补齐一处：第一版只设 `WA_OpaquePaintEvent` 不画底 → 整片叠影；第二轮画底但取色源错（渲染父级）→ 底色变纯黑/灰白；现为「屏幕取色 + 画满 viewport + 失败安全 + 主题重标定」；验收改为**像素等价**，`tests/test_table_blit_patch.py` 10 例含反证与取色可信度；开关 perf 域 `perf_table_scroll_blit_v2` 默认开，UI 可即时回退） |

| [自适应降级阈值与弹窗快照淡入2026-10-07.md](自适应降级阈值与弹窗快照淡入2026-10-07.md) | 2026-09-25 DPI 报告的收尾四项：**降级阈值不再写死 600 万**（改按本机半透明合成速率标定，`limit = clamp(600万 × clamp(0.298/rate, 0.25, 4.0), 120万, 2400万)`，基准机仍 600 万零回归；显式配置 > 自适应 > 静态默认）、**超阈值弹窗由「直显」改为「快照淡入」**（构建期隐遮罩显卡片、只带 `DrawChildren`、中心 alpha<250 即拒绝；`_SnapshotOverlay` 用 painter opacity 自绘、**自身不挂任何 QGraphicsEffect**；实测 overlay 每帧 0.08/0.91/0.24ms@1.0/1.5/2.0，像素平均通道差 ≤0.35；失败一律回退直显）、`TableBase` 包装重复赋值清理、位块搬移**可观测性**（applied/no_bg/hidden/off 四态 + 只在转移时写日志 + `table_blit_status_text()`，两个 UI 入口）；含「对话框自身带 effect 时 render/grab 丢全部子控件」与「`QRegion` 属 QtGui 不属 QtCore（导错会被 except 吞掉 → 快照静默失效）」两条踩坑记录 | 设计·已落地（2026-10-07，`tests/test_perf_auto_degrade.py` 14 例 + `tests/test_perf_dialog_snapshot.py` 17 例 + `test_table_blit_patch.py` 扩到 15 例 + `test_perf_dpi_degrade.py` 改写超阈值用例；同时回改 2026-09-25 报告 §4.1/P0-1/P1-1/P2-1 口径） |

## 五、外部系统接口与字段

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [xqzg接口清单.md](xqzg接口清单.md) | `xqzg.newbv.cn` API 清单，来源为 OpenAPI schema + 前端 JS 提取 + 逐端点验证；探测脚本在 `tools/probe/` | 调研快照（2026-09-20） |
| [newbv_inventory_fields.md](newbv_inventory_fields.md) | newbv 运营后台「仓库管理 → 库存查询」字段抓取记录（Struts2 + ExtJS 3 老架构） | 调研快照 |
| [售后群消息归档可行性评估2026-10-05.md](售后群消息归档可行性评估2026-10-05.md) | 微信/企业微信售后群消息自动归档需求评估：企微会话存档（5 天窗口、edition≥2、公钥前置）为唯一合规主干，个人微信自动化路线否决（2025-04 Hook 实测封号数据），A 主干 + C 人工导出兜底 + D 机器人通知出口；含分层设计、风险登记与待拍板决策点 | 调研快照（2026-10-05） |
| [企微RPA采集工具调研2026-10-06.md](企微RPA采集工具调研2026-10-06.md) | 上面归档需求里"没有超管权限"那条缺口的候选填法：GitHub 开源（实为闭源二进制）RPA 工具 `rpa_wxcom_chat` 的可行性判定 —— 仓库**零源码**、PyInstaller+pywinauto+OCR(PP-OCRv4) 客户端自动化、**确实不需要管理员权限**，但授权绑机器且需联网、采集数据 EC1 加密明文导出要 Pro（归档可读性锁在作者授权服务器）、被企微官方公告点名"机器人流程自动化"属外挂、独占桌面；结论：只可作一次性补历史适配器（`source="rpa"`），不可作长期归档主干 | 调研快照（2026-10-06；未运行该程序、未联网激活） |
| [企微售后群消息归档-剪贴板采集器设计与落地2026-10-06.md](企微售后群消息归档-剪贴板采集器设计与落地2026-10-06.md) | 上面那份可行性评估里「没有超管权限 ⇒ 自动化消息入口为 0」的实际填法：**纯输入事件 + 剪贴板**的客户端采集器（拖选 → 按住左键 + PageUp + 微移鼠标 → Ctrl+C，**不用 OCR**、不注入进程、不读企微数据库），真机采全售后群历史 **35 条**（2026-08-10 ~ 2026-10-05，8 批次无漏采）；含 `win_api/wecom_clip_driver.py`（输入驱动）+ `core/wecom_clip.py`（解析器与入库管线）+ `database/chat_archive_{messages,tags,cursor}` 三张归档表 + 真机冒烟 CLI `tools/smoke/smoke_wecom_clip_collect.py`；88 项测试通过；含局限（图片/文件只有占位符）、两条既有缺陷（MySQL 模式下 SQLite 兜底库永不建表 / merge_back 不含归档表）与合规边界（内部自用可行、对外分发即「提供外挂服务」） | 设计·已落地（2026-10-06；真实入库 35 条待拍板，见文档 §九） |

## 五之二、技术栈调研

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [WinUI3重构可行性调研2026-09-30.md](WinUI3重构可行性调研2026-09-30.md) | 「项目用 WinUI 3 重构的可能性与技术架构」专项：实测 115 文件/5.45 万行规模、44 个测试文件、已作废的渲染层性能资产清单；WinUI 3 生态查证（SDK 2.4 stable 2026-08、DataGrid 无第一方且 WCT 已归档、商业控件厂商撤退、unpackaged 限制）；逐层映射难度表、路线对比（维持/WPF/WinUI 3/Avalonia）、成本 3~6 个月与 P0~P2 风险登记、五阶段路径（阶段 0 UI 解耦为无悔投入）；含本机工具链阻塞实测（只有 .NET 8 运行时无 SDK） | 调研快照（2026-09-30；建议 6 个月后复核生态部分再据以决策） |

## 六、图表

| 文件 | 摘要 |
| --- | --- |
| [class-diagram.mermaid](class-diagram.mermaid) | 数据层 + Worker 层类关系图（Mermaid 源码；`<<module>>` 标注实为模块级函数集合） |
| [sequence-diagram.mermaid](sequence-diagram.mermaid) | 双后端数据流时序图：拉取保存 / 降级兜底 / LWW 合并 / 周备份 / 保留清理 / 直读旁路 |

> 两图已于 2026-09-23 对齐当前代码（镜像推送下线、`schema.py` 单一来源、
> `sqlite_io` 实际引用、周备份与清理触发时序）。改动数据层结构后请同步刷新，
> 接口级细节仍以 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [API.md](API.md) 为准。

---

## 已归档 / 已删除

- `overview.md`（**仓库根目录**，2026-09-06「界面修复五项」总览 + 当晚二轮反馈）——
  内容整体过期（指向已不存在的 smoke 脚本，AGENTS.md §7 债务表 + check_refs
  `KNOWN_STALE` 均已登记）。该文件**已入库**，按 AGENTS.md §3.2 不由 agent 删除；
  本次整理已把其历史性结论承接进 [CHANGELOG.md](CHANGELOG.md) 3.12 节，
  **建议 `git mv` 归档后从根目录移除**（待用户拍板，见 [TODO.md](TODO.md) §3）。
- `项目文件整理清单.md`（2026-09-21 只读盘点稿，已从本目录删除）— 2026-09-22
  四目录整合落地后失效，结论已并入根目录 `AGENTS.md` 与 `README.md`「项目结构」。
- 历史上被 `.gitignore` 误命中的文档（`MySQL双后端边界分析.md`、`测试规划.md`、
  `代码审查标准与流程.md`、`售后面板Web化部署.md` 等）已从工作区移除，
  对应的僵尸 ignore 规则也已清理；其中测试/部署主题已由
  [DEVELOPMENT.md](DEVELOPMENT.md) 与 [DEPLOYMENT.md](DEPLOYMENT.md) 重新覆盖。
