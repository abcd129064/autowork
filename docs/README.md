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
├── 规范类             Qt内联引导说明.md / frp-065-api-reference.md
├── 设计方案类         MySQL兜底降级设计.md / auto_update_research.md /
│                      架构评审与SQL代码优化方案.md / 售后面板UI改进方案.md /
│                      frp-source-integration.md / settings_panel_redesign/
├── 性能调查类         PERF_REVIEW.md / perf_scroll_investigation.md /
│                      售后面板性能调查报告2026-09-06.md / QSS使用审计与弃用评估.md
├── 外部接口类         xqzg接口清单.md / newbv_inventory_fields.md
└── 图表              class-diagram.mermaid / sequence-diagram.mermaid
```

新人路径：`ARCHITECTURE.md` → `DEVELOPMENT.md` → `API.md`；
改界面前加读 `DESIGN.md`；发版前加读 `DEPLOYMENT.md` + `CHANGELOG.md`；
找「接下来做什么」看 `TODO.md`，找「之前做了什么」看 `CHANGELOG.md`。

---

## 一、主干文档（★ 长期维护）

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层依赖、目录职责、9 张表数据组织、配置域路由、双后端机制、Web/frp/打包架构 | 规范 |
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

## 三、架构与设计方案

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [MySQL兜底降级设计.md](MySQL兜底降级设计.md) | MySQL 为唯一主库，不可用时自动透明回退本地 SQLite，恢复后按 LWW 自动合并增量 | 设计·已落地（`database/backend.py`、`fallback_backup.py`、`merge_back.py`） |
| [auto_update_research.md](auto_update_research.md) | 程序内「检查更新 → 下载 → 替换重启」全链路调研，选定方案 A（整包 zip + 独立 updater 脚本 + nginx 静态分发） | 设计·已落地（`core/updater.py`、`tools/deploy/publish_update.py`、`tools/update_sim/`） |
| [架构评审与SQL代码优化方案.md](架构评审与SQL代码优化方案.md) | 代码拆分/合并需求评审 + SQL 代码专项优化（双后端方言、镜像推送下线等） | 设计·已落地（T01-T05 全过 QA，见文档第 10 节） |
| [售后面板UI改进方案.md](售后面板UI改进方案.md) | 售后面板 UI 分层改进方案，前置依赖 `core/design_tokens.py`、`core/theme_qss.py` | 设计·大部落地（09 月拆包重构随附；剩余 P2 视觉微调见文档头部核对块） |
| [frp-source-integration.md](frp-source-integration.md) | frp Go 源码接入调研 + 落地记录（附录 E~G：二期 P0/P1、frps 概览卡、开机静默预连与预热打洞） | 调研快照 + 设计·已落地（附录） |
| [settings_panel_redesign/统一设置面板重构设计.md](settings_panel_redesign/统一设置面板重构设计.md) | 统一设置面板重构设计，配图在 `settings_panel_redesign/assets/` | 设计·已落地（实际形态为 SegmentedWidget 7 段，见文档头部核对块；字段映射仍权威） |

## 四、性能调查

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [PERF_REVIEW.md](PERF_REVIEW.md) | `database/` `workers/` `windows/` 热点路径审查；问题集中在 MySQL 模式「每次调用都付出固定开销」 | 调研快照 |
| [perf_scroll_investigation.md](perf_scroll_investigation.md) | 全项目表格与滚动列表的系统性调研，结论全部基于实测，可用 `tools/perf/scroll_profile.py` 复跑 | 调研快照（2026-09-07） |
| [售后面板性能调查报告2026-09-06.md](售后面板性能调查报告2026-09-06.md) | 售后面板（记录页/统计弹窗/pygwalker）性能专项，只调研不改业务代码 | 调研快照（S1/S3/S4 已落地，见 `tests/test_aftersale_perf_opt.py`） |
| [QSS使用审计与弃用评估.md](QSS使用审计与弃用评估.md) | QSS 使用面审计与弃用评估（环境基线 PySide6 6.11.2 + qfluentwidgets 1.11.3） | 调研快照（2026-09-07；结论已吸收进 [DESIGN.md](DESIGN.md) §3） |

## 五、外部系统接口与字段

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [xqzg接口清单.md](xqzg接口清单.md) | `xqzg.newbv.cn` API 清单，来源为 OpenAPI schema + 前端 JS 提取 + 逐端点验证；探测脚本在 `tools/probe/` | 调研快照（2026-09-20） |
| [newbv_inventory_fields.md](newbv_inventory_fields.md) | newbv 运营后台「仓库管理 → 库存查询」字段抓取记录（Struts2 + ExtJS 3 老架构） | 调研快照 |

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
