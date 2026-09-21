# docs/ 文档索引

> 新增文档请在**同一次提交内**补进本索引（分类 + 一句话摘要 + 状态）。
> 文档内引用仓库文件时，一律写从仓库根开始的正斜杠相对路径，
> 这样 `python tools/check_refs.py --all` 才能校验断链。
> 作业规范见根目录 [AGENTS.md](../AGENTS.md)。

状态图例：**规范** = 长期有效、必须遵守 ｜ **设计·已落地** = 方案已进代码，文档为权威说明
｜ **调研快照** = 某时点的调查结论，可能已过期，只作背景参考

---

## 一、规范与契约（改动代码前必读）

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [API.md](API.md) | 全项目公开接口契约：桌面端各层类/函数/信号 + 售后面板 Web API + 配置键路由 | 规范（用 `tools/api_doc_audit.py` 审计一致性） |
| [Qt内联引导说明.md](Qt内联引导说明.md) | conda base 激活态下为什么必须在 `import PySide6` 前执行内联引导、标准写法、哪些入口已内置 | 规范 |

## 二、架构与设计方案

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [MySQL兜底降级设计.md](MySQL兜底降级设计.md) | MySQL 为唯一主库，不可用时自动透明回退本地 SQLite，恢复后按 LWW 自动合并增量 | 设计·已落地（`database/backend.py`、`fallback_backup.py`、`merge_back.py`） |
| [auto_update_research.md](auto_update_research.md) | 程序内「检查更新 → 下载 → 替换重启」全链路调研，选定方案 A（整包 zip + 独立 updater 脚本 + nginx 静态分发） | 设计·已落地（`core/updater.py`、`tools/deploy/publish_update.py`、`tools/update_sim/`） |
| [架构评审与SQL代码优化方案.md](架构评审与SQL代码优化方案.md) | 代码拆分/合并需求评审 + SQL 代码专项优化（双后端方言、镜像推送下线等） | 设计·部分落地 |
| [售后面板UI改进方案.md](售后面板UI改进方案.md) | 售后面板 UI 分层改进方案，前置依赖 `core/design_tokens.py`、`core/theme_qss.py` | 设计·部分落地 |
| [frp-source-integration.md](frp-source-integration.md) | 由内置 `frpc.exe` 改为接入 frp Go 源码（`frp-dev`，0.70.1）的可行性调研 | 调研快照（2026-08-13） |
| [settings_panel_redesign/统一设置面板重构设计.md](settings_panel_redesign/统一设置面板重构设计.md) | 统一设置面板的 7 分页形态重构设计，配图在 `settings_panel_redesign/assets/` | 设计·**待拍板**（三个合并决策见文档第 7 节） |

## 三、性能调查

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [PERF_REVIEW.md](PERF_REVIEW.md) | `database/` `workers/` `windows/` 热点路径审查；问题集中在 MySQL 模式「每次调用都付出固定开销」 | 调研快照 |
| [perf_scroll_investigation.md](perf_scroll_investigation.md) | 全项目表格与滚动列表的系统性调研，结论全部基于实测，可用 `tools/perf/scroll_profile.py` 复跑 | 调研快照（2026-09-07） |
| [售后面板性能调查报告2026-09-06.md](售后面板性能调查报告2026-09-06.md) | 售后面板（记录页/统计弹窗/pygwalker）性能专项，只调研不改业务代码 | 调研快照（S1/S3/S4 已落地，见 `tests/test_aftersale_perf_opt.py`） |
| [QSS使用审计与弃用评估.md](QSS使用审计与弃用评估.md) | QSS 使用面审计与弃用评估（环境基线 PySide6 6.11.2 + qfluentwidgets 1.11.3） | 调研快照（2026-09-07） |

## 四、外部系统接口与字段

| 文档 | 摘要 | 状态 |
| --- | --- | --- |
| [xqzg接口清单.md](xqzg接口清单.md) | `xqzg.newbv.cn` API 清单，来源为 OpenAPI schema + 前端 JS 提取 + 逐端点验证；探测脚本在 `tools/probe/` | 调研快照（2026-09-20） |
| [newbv_inventory_fields.md](newbv_inventory_fields.md) | newbv 运营后台「仓库管理 → 库存查询」字段抓取记录（Struts2 + ExtJS 3 老架构） | 调研快照 |

## 五、图表

| 文件 | 摘要 |
| --- | --- |
| [class-diagram.mermaid](class-diagram.mermaid) | 类关系图（Mermaid 源码） |
| [sequence-diagram.mermaid](sequence-diagram.mermaid) | 关键流程时序图（Mermaid 源码） |

> 两张图均为历史快照，模块拆分后未同步刷新；以 `README.md`「项目结构」段与
> `docs/API.md` 为准。

---

## 已归档 / 已删除

- `项目文件整理清单.md`（2026-09-21 只读盘点稿，已从本目录删除）— 2026-09-22
  四目录整合落地后失效，结论已并入根目录 `AGENTS.md` 与 `README.md`「项目结构」。
- 历史上被 `.gitignore` 误命中的文档（`MySQL双后端边界分析.md`、`测试规划.md`、
  `代码审查标准与流程.md`、`售后面板Web化部署.md` 等）已从工作区移除，
  对应的僵尸 ignore 规则也已清理。
