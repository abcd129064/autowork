# DEVELOPMENT — 开发、测试与发布流程

> 本文覆盖「从拉代码到发版」的完整流程：环境、日常开发、测试矩阵、
> 提交前检查、发版动作。硬约束（目录边界/命名/禁改清单）以
> [AGENTS.md](../AGENTS.md) 为准，本文只讲**怎么做**。
> 部署到生产服务器的操作细节见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 1. 环境准备

### 1.1 解释器（关键：依赖是分裂的，没有单一解释器能跑通全量）

| 解释器 | Python | PySide6 | 用途 |
| --- | --- | --- | --- |
| `python`（PATH 默认） | 3.8 32-bit | 无 | **不可用**，连 pytest 都没有 |
| `E:\ANACONDA\python.exe` | 3.13.9 | 6.9.2 ✅ | GUI 运行 / pytest 采集基线 |
| `C:\Users\shen_zhe\miniconda3\python.exe` | 3.13.9 | 6.11.0 ❌ QtCore DLL 损坏 | 业务依赖最全；仅纯逻辑单测可用 |
| WorkBuddy 托管 venv `~/.workbuddy/binaries/python/envs/default` | 3.13 | 6.11 ✅ | agent 跑 pytest / offscreen 冒烟的可用环境 |

- 跑任何命令**必须显式写解释器全路径**，不要裸用 `python`。
- conda base 激活态下 `import PySide6` 前需要「内联引导」，标准写法见
  [Qt内联引导说明.md](Qt内联引导说明.md)。
- GUI 脚本一律 `QT_QPA_PLATFORM=offscreen`（无显示器环境）。
- 缺依赖 → 报告给用户，**不擅自 pip/conda install**（AGENTS.md §3.2）。

### 1.2 依赖安装

```bash
pip install -r requirements.txt          # 桌面端
# Web 后端
cd web/aftersale_api && pip install fastapi uvicorn pymysql python-jose bcrypt
# Web 前端
cd web/aftersale_front && npm install    # v1
cd web/vue-pure-admin && pnpm install    # v2
```

## 2. 日常开发

```bash
<解释器全路径> main.py                    # 开发模式运行桌面端
<解释器全路径> -m core.local_web_server    # 独立跑本地 Web（默认 :8787）
cd web/aftersale_front && npm run dev      # v1 前端 dev
cd web/vue-pure-admin && pnpm dev          # v2 前端 dev
```

开发约定高频条目（完整见 AGENTS.md）：

- 改界面：主窗口经典布局改 `autowork_with_table.ui` 后重新编译；FluentWindow
  各页直接改 `main_window/` 与 `windows/<module>/` 的 Python 代码。
- 改表结构：只改 `database/schema.py`，两侧自动迁移；新增 SQLite 专有 SQL 先扩
  `database/backend.py::_convert_sql`。
- 新增脚本：按前缀语义落目录（`test_`→tests/，`smoke_`/`verify_`/`regression_`→tools/smoke/，
  `perf_`/`bench_`/`scroll_`/`shot_`→tools/perf/，`probe_`→tools/probe/，
  `deploy_`/`upload_`/`publish_`→tools/deploy/，`sim_`→tools/update_sim/）。
- 脚本定位仓库根一律 `__file__` 上溯（tools/ 根=2 层，子目录=3 层），禁止 `sys.path.insert(0,'.')`。
- 一切临时产物落 `tools/_scratch/`（gitignore）；需留档的图落 `design/shots/` 或 `docs/<topic>/assets/`。
- 新增文档同一次提交内挂进 [docs/README.md](README.md)。

## 3. 测试矩阵

| 层 | 命令 | 说明 |
| --- | --- | --- |
| 离线单测 | `<解释器> -m pytest tests/ -q` | 基线见 AGENTS.md §5.2（passed 不低于基线、无新增失败） |
| GUI 冒烟（offscreen） | `<解释器> windows/tools/smoke_fluent_mainwindow.py` | 主窗口 14+ 组断言：导航/Pivot 切换/设置分页/表格列宽等 |
| 真机冒烟 / 等价性验证 | `python tools/smoke/<name>.py` | 需真机/真实网络/真实 frpc，人工执行 |
| 真机端到端回归 | `python tools/smoke/regression_*.py` | 如 `regression_aftersale_ui.py` |
| 性能测量 | `python tools/perf/<name>.py` | 如 `scroll_profile.py`、`column_merge_probe.py`、`shot_fluent_mainwindow.py`（真机截图） |
| 压测 | `python -m tools.stress_test.run_stress` | 见 `tools/stress_test/README.md` |
| 更新链路仿真 | `python tools/update_sim/sim_*.py` | 不碰真机，本地仿真 zip+updater 全链路 |
| Web E2E | `node web/aftersale_front/tools/verify_*.mjs`、`web/vue-pure-admin/tools/verify_pure_admin.mjs` | Playwright，executablePath 指本地 Chrome；`serve_dist_v2.mjs` 本地仿真 v2 产物 |
| 仓库自检 | `python tools/check_refs.py --all`、`python tools/api_doc_audit.py` | 断链 rc=0；API 文档与代码 AST 一致性 |

交付标准（团队约定）：真实代码修复 + 视觉截图 + smoke 断言 + 全量 pytest 回归四件套；
性能类另附 cProfile 前/后证据。标准链路 = 本地 mock 自测 → Linux 真机回归 → 生产端到端。

## 4. 提交前检查清单

按顺序执行，任一项不过不提交（与 AGENTS.md §4 同步）：

1. `python tools/check_refs.py --all` → rc=0（新断链必须修，不许加白名单）。
2. `pytest tests/ -q` → passed 不低于基线且无新增失败。
3. `python -m compileall -q tools design tests` → rc=0。
4. `git status --porcelain -uall` → 无预期外新文件；临时产物都在 `tools/_scratch/`。
5. 新增文档已挂 `docs/README.md`；新增脚本符合前缀与目录判据。
6. 目录结构变了 → 同步根 `README.md`「项目结构」段。
7. 改了 `docs/API.md` 登记的公开接口 → 跑 `tools/api_doc_audit.py`。
8. 没碰 AGENTS.md §3.1 禁改文件（config/、*.db、frpc*、build/dist、.ui 生成物等）。

Git 纪律：AI 不代用户 commit/push/reset；移动已入库文件用 `git mv` 并同步修 dirname 层数与全仓引用。

## 5. 发版流程

1. **打构建**：`python build_exe.py`（双产物：`dist/AutoWork/` onedir + `dist/aftersale.exe` onefile）。
   首次发布更新包前必须重新 build（updater bat/vbs 随包）。
2. **本地仿真**：`tools/update_sim/sim_publish_and_client.py` 等跑通 zip → updater → 换位 → 重启 → 回执全链路。
3. **发布更新包**：`AFT_SSH_PASS=... python tools/deploy/publish_update.py`
   （latest.json `.tmp`+mv 原子切换，保留 3 版；更新源 `update_base_url`，默认 `http://49.235.34.253/update`）。
4. **Web 端发布**：见 [DEPLOYMENT.md](DEPLOYMENT.md) §3（v1/v2 并行整包 / 后端重启）。
5. 发版后在 [CHANGELOG.md](CHANGELOG.md) 补阶段条目；版本号 BASE 提升时改 `core/version.py`。

## 6. 故障与取证

- SSH 连接失败 → 远程页/会话窗口「故障取证」一键生成诊断包（球桌/设备/会话/连接日志 + 诊断命令输出），可选 AI 分析。
- 连接日志落 `logs/autowork_conn.log`（2MB 轮转）；frp 日志可在首页工作台排除显示。
- 生产服务器只读侦察用 `tools/deploy/prod_ssh.py`，确认现状后再动写操作。

## 7. API.md 审计基线（`tools/api_doc_audit.py`）

审计对比 `docs/API.md` 登记符号与各模块 AST，输出 JSON（`deleted` / `undocumented` /
`missing_modules` / `no_source`）。**退出码恒为 0**（它是报告工具，不是门禁），
价值在于人工甄别。当前基线快照 **2026-09-24**（API.md 补全完成后）：

| 类别 | 数量 | 解读 |
| --- | --- | --- |
| `deleted` | 35 符号 / 12 模块 | **全部为工具盲区**：`code_symbols()` 只提取函数/类，不提取模块级常量与 Signal。故 `BASE_VERSION` / `SEMANTIC` / `MYSQL_DDL` / `TABLE_NAMES` / `MIGRATIONS` / `AI_PROVIDERS` / `CATEGORY_DIRS` 等常量、`frp_remote` 的信号均被误报，实际仍存在（已逐条核对）。真实删除项需逐个 grep 复核。 |
| `undocumented` | **0** | 2026-09-24 补全清零（原 18 模块 34 条）。 |
| `missing_modules` | **0** | 2026-09-24 补全清零（原 26 个）。 |
| `no_source` | 5 | **工具解析局限**：`### REST API` 与配置域小节标题（`api_credentials` / `mysql_sync` / `aftersale_cycle` / `data_retention` 子结构）被当成模块名找源文件，非真实缺失。**故意保留不降级**——把这些 ### 标题改成 ####/中文名会导致其下方表格的配置键登记进上一个真实模块（p2p），制造大量假 `deleted`。 |

**判定规则**：`missing_modules` 与 `undocumented` 是真正要补的（当前已清零，改公开接口后
重跑本审计保持为 0）；`deleted` / `no_source` 先对照上表盲区清单再当真——**新增条目**
（不在上表名单内的）才需要人工复核。

