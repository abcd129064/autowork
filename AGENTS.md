# AGENTS.md — AutoWork 仓库作业规范

> 适用对象：所有在本仓库工作的编码 agent（以及新加入的人类开发者）。
> 本文件是**硬约束**，不是建议。与临时指令冲突时，先提问再动手。
> 维护规则：目录结构 / 验证基线 / 命名规则发生变化时，**同一次提交内**更新本文件。
> 最后更新：2026-09-22（`docs` `tools` `tests` `design` 四目录整合后）

---

## 0. 30 秒速查表

| 我要做什么 | 去哪里 |
| --- | --- |
| 跑离线单测 | `pytest tests/ -q`（只有 `tests/test_*.py` 会被收集） |
| 跑真机 / GUI 冒烟 | `python tools/smoke/<name>.py`（需显式指定解释器，见 §5） |
| 做性能对比 / 视觉回归 | `python tools/perf/<name>.py` |
| 探线上接口、抓字段 | `python tools/probe/probe_*.py`（默认只读） |
| 部署、发布更新包 | `python tools/deploy/*.py`（密码走 `AFT_SSH_PASS` 环境变量） |
| 压力测试 | `python -m tools.stress_test.run_stress`（见 `tools/stress_test/README.md`） |
| 丢临时脚本 / 截图 / 抓取物 | `tools/_scratch/`（已 gitignore，永不入库） |
| 查项目文档 | `docs/README.md`（分类索引） |
| 查界面设计稿 | `design/README.md`（索引 + logo 定稿结论） |
| 查模块接口契约 | `docs/API.md` |
| 检查文档/脚本里的路径引用有没有断链 | `python tools/check_refs.py --all`（**必须 rc=0**） |
| 只读盘点某个目录 | `python tools/inventory.py <目录> [--deep]` |
| 审计 API.md 与代码是否脱节 | `python tools/api_doc_audit.py` |

---

## 1. 四个目录的职责边界（唯一权威定义）

### 1.1 `tests/` — 只放 pytest 能离线收集的单测

- **准入条件（全部满足）**：文件名 `test_*.py`；不需要真实网络 / 真机 / 生产库；
  不弹窗、不截图、不依赖用户桌面；单次运行 < 10s；用 `tmp_path` / monkeypatch /
  内存 SQLite 做隔离。
- **禁止放入**：`smoke_*`、`perf_*`、`bench_*`、`probe_*`、`verify_*`、`regression_*`
  这类需要人工执行或需要真机环境的脚本 —— 它们放 `tools/smoke` 或 `tools/perf`。
- **原因**：`pytest.ini` 配置为 `testpaths = tests`，放在别处的 `test_*.py`
  永远不会被执行（历史上 `tools/` 根下就有一个叫 `test_frp_live_frps.py` 的真机
  回归脚本因此从未被跑过，已改名为 `tools/smoke/smoke_frp_live_frps.py`）。
- 现状：31 个 `test_*.py`，采集基线见 §5.2。

### 1.2 `tools/` — 开发/运维脚本，一律不参与运行时

```
tools/
├── __init__.py            # 包声明（GUI「工具」页的运行时后端在 windows/tools/，不是这里）
├── inventory.py           # 只读目录盘点
├── api_doc_audit.py       # docs/API.md 与代码 AST 的一致性审计（只读）
├── check_refs.py          # 全仓路径引用断链检查（提交前必跑）
├── deploy/                # 会改动生产环境的脚本（最高危，见 §3.4）
│   └── _archive/          # 一次性历史脚本（未入库，勿引用）
├── smoke/                 # 真机 / GUI / offscreen 冒烟与等价性验证
│   └── _archive/
├── perf/                  # 性能压测、视觉回归、渲染对比、截图
├── probe/                 # 线上接口探测、字段抓取、逆向分析
│   └── _archive/
├── stress_test/           # 压测套件（有独立 README）
├── update_sim/            # 自动更新链路的本地仿真（sim_*.py）
└── _scratch/              # 【gitignore】agent 与开发者的一切临时产物
```

- **子目录判据 = 运行方式 + 副作用范围**，不是业务主题：
  - `deploy/`：会写生产服务器 / 会发布更新包
  - `smoke/`：验证「功能通不通」，可能需要真机、真实 frpc、真实网络
  - `perf/`：验证「快不快 / 长得对不对」，输出数据或图片
  - `probe/`：只读地看外部系统（接口、schema、前端 JS）
- **`_archive/` 语义**：一次性历史脚本，已完成使命、不再维护、**未纳入版本管理**。
  新代码禁止 import 或引用 `_archive/` 下任何东西；`check_refs.py` 会跳过它们。
- **`_scratch/` 语义**：见 §2.3。

### 1.3 `design/` — 只读设计资产（HTML 设计稿 / logo / 生成器）

```
design/
├── *.html                 # 界面设计稿（浏览器直接打开看，非运行时资源）
├── generator/             # 生成设计稿的脚本与其数据源（gen_design.py + *.json + 模板）
├── logo/                  # logo 定稿（v4）+ spec.md + 各版本生成脚本
│   └── _archive/          # v1/v2/v3/v5 候选图、缩放测试图、旧图标备份
└── shots/                 # 入库的界面截图（文档引用的稳定资产）
```

- **禁止**：把运行时资源放进 `design/`（运行时资源在 `resource/`、`styles/`）；
  把脚本产物直接落在 `design/` 根（落 `tools/_scratch/` 或 `design/shots/`）。
- logo 已定稿：**v4 = 最终方案**，已替换根目录 `app_icon.ico` / `app_icon.png`
  （详见 `design/logo/spec.md`）。不要重新挑选版本。

### 1.4 `docs/` — 项目文档，必须索引化

- 新增任何 `.md` 文档，**同一次提交内**在 `docs/README.md` 补一行索引（一句话摘要 + 状态）。
- 文档命名：结论型/设计型用中文标题名（如 `MySQL兜底降级设计.md`），
  调研报告带日期后缀（如 `售后面板性能调查报告2026-09-06.md`）。
- 文档里引用仓库内文件时，**一律写从仓库根开始的正斜杠相对路径**
  （`tools/perf/scroll_profile.py`），这样 `check_refs.py` 才能校验。
- 带配图的文档放同名子目录（例：`docs/settings_panel_redesign/` + `assets/`）。

---

## 2. 命名与落盘硬规则

### 2.1 入库文件不得以 `_` 开头

`_` 前缀在本仓库的既有语义 = 「临时/一次性/不入库」。凡是要 `git add` 的文件，
名字必须是正式名（`inventory.py` 而非 `_inventory.py`）。

### 2.2 脚本名前缀语义（新增脚本必须遵守）

| 前缀 | 含义 | 归属目录 |
| --- | --- | --- |
| `test_` | pytest 自动收集的单测 | **只能**在 `tests/` |
| `smoke_` | 冒烟：验证功能通路 | `tools/smoke/` |
| `verify_` | 等价性/正确性验证（新旧实现对比） | `tools/smoke/` |
| `regression_` | 真机端到端回归 | `tools/smoke/` |
| `perf_` / `bench_` / `scroll_` | 性能测量 | `tools/perf/` |
| `probe_` | 外部系统只读探测 | `tools/probe/` |
| `deploy_` / `upload_` / `publish_` | 改动生产环境 | `tools/deploy/` |
| `shot_` / `*_visual_check` | 截图 / 视觉对比 | `tools/perf/` |
| `sim_` | 本地仿真（不碰真机） | `tools/update_sim/` |
| `check_` / `api_doc_audit` | 仓库自检（只读） | `tools/` 根 |

### 2.3 脚本产物的唯一落点

- **一切自动生成物**（截图、抓取的 HTML/JS、导出的 JSON/CSV、中间日志、
  agent 的一次性探针脚本）→ `tools/_scratch/`（整目录 gitignore，仅保留 `.gitkeep`）。
- **需要长期留档、被文档引用的图**→ `design/shots/` 或 `docs/<topic>/assets/`。
- **程序运行日志** → `logs/`（已 gitignore）。
- **禁止**把产物写进 `tests/`、`design/` 根、仓库根目录，或任何被版本管理的路径。
- 产物目录必须在脚本里 `os.makedirs(..., exist_ok=True)` 自建，不假设存在。

### 2.4 路径引导写法（最常见的坑）

脚本一律用 `__file__` 上溯定位仓库根，**禁止** `sys.path.insert(0, '.')`
（依赖调用方 cwd，换目录就崩）：

```python
import os, sys
# tools/<sub>/xxx.py → 上溯三层才是仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
```

层级对照：`tools/` 根下的脚本 = 2 层；`tools/` 下一层子目录里的脚本 = 3 层；
`design/generator/` 里的脚本 = 3 层；`tests/` 里的单测靠 `pytest.ini` 的
`pythonpath = .`，无需手写。

> **移动脚本文件时必须同步修正 dirname 层数**——这是本仓库历史上最容易漏改、
> 且改错了不会立刻报错（只会 import 到错误的包）的地方。

---

## 3. Agent 工作流硬约束

### 3.1 禁止触碰的文件（读到即止，不写、不删、不移动、不格式化）

- `config/**`：真实配置，含 DPAPI 加密的密码与生产 IP。
- `database/*.db`、`database/tables*.db`：真实业务数据。
- `moyu_state.json`、`settings.json.bak`、`logs/**`、`.workbuddy/**`。
- `frpc.exe`、`frpc_xtcp*.toml`、`frpc_xtcp_meta.json`：运行时二进制与实时配置。
- `build/`、`dist/`、`out/`、`web/*/dist/`、`web/*/node_modules/`：构建产物。
- `autowork_with_table.py`：由 `.ui` 编译生成，改界面请改 `.ui`。
- `.idea/`、`.git/`、`.qoder/`。

### 3.2 禁止的行为

- **不擅自安装 / 升级 / 降级任何第三方包**（`pip install`、`conda install`、
  `npm install`）。缺依赖 → 报告给用户，不要自己修环境。
- **不代用户提交**：不执行 `git commit` / `git push` / `git reset --hard` /
  `git clean` / `git checkout -- <file>`，除非用户明确要求。
- **不删除已入库文件**：需要下线时用 `git mv` 移到 `_archive/` 并在报告里列出，
  由用户决定删留。
- **不改动 `pytest.ini`、`.gitignore`、`*.spec`、`requirements.txt`** 而不说明理由。
- **不用 shell 命令批量删文件**（`rm` / `Remove-Item` / `del`）。
- 不把 `tools/` 下的任何东西 import 进运行时代码（`core` `windows` `main_window`
  `workers` `database` 一律不得依赖 `tools`）。

### 3.3 移动 / 重命名文件的强制流程

1. 已入库文件用 `git mv`（保留历史），未入库文件才用文件系统移动。
2. 移动后**立刻**修脚本自身的 `dirname` 层数与 docstring 里的用法路径。
3. 全仓搜旧路径并同步（`git grep`、文档、README、`.gitignore`、`.spec`）。
4. 跑 `python tools/check_refs.py --all`，要求 rc=0。
5. 若目录结构变了，同步更新 `README.md` 的「项目结构」段与对应的
   `docs/README.md` / `design/README.md` / `tools/stress_test/README.md`。

### 3.4 执行 `tools/deploy/` 下脚本的额外约束

这些脚本会**改动生产服务器**（49.235.34.253）与线上分发物：

- 必须先向用户确认目标、范围、回滚方式，得到明确许可后才执行。
- 密码只从环境变量 `AFT_SSH_PASS` 读取，**禁止**写进脚本、命令行明文、
  文档或提交历史。
- 优先用 `tools/deploy/prod_ssh.py` 做只读侦察，确认现状后再动写操作。

### 3.5 探针纪律（agent 自检脚本）

- 一次性调查脚本一律写到 `tools/_scratch/`（或 `.qoder/`），**任务结束前清掉**。
- 探针必须只读；需要写文件时只写 `tools/_scratch/`。
- 探针的结论要落进正式文档或最终报告，不能只留在临时文件里。
- 若工具层不允许批量删除（只能用逐个安全删除），则把探针集中移到
  `tools/_scratch/_probes/`，并在报告里告知用户数量与位置，由用户统一清空。
- 探针跑全量测试前，先对 `config/` `database/` 做 mtime+size 快照，
  跑完对比必须为零改动（参见 §5.2「数据安全」）。

---

## 4. 提交前检查清单

按顺序执行，任一项不过就不要提交：

1. `python tools/check_refs.py --all` → **rc=0**（新增断链必须修；
   `KNOWN_STALE` 里的既有债务不计失败，但不要把新文件加进这个白名单）。
2. `pytest tests/ -q` → `passed` 数不低于 §5.2 基线（393），且**无新增失败项**
   （§5.2 表里的 15 项环境既有失败除外）。
3. `python -m compileall -q tools design tests` → rc=0（语法与缩进无破坏）。
4. `git status --porcelain -uall` → 没有预期外的新文件；临时产物都在
   `tools/_scratch/` 内；没有 `_` 前缀文件被误加。
5. 新增文档已挂进 `docs/README.md`；新增脚本符合 §2.2 前缀与 §1.2 目录判据。
6. 改动了目录结构 → `README.md`「项目结构」段已同步。
7. 改动了 `docs/API.md` 里登记的公开接口 → 跑 `python tools/api_doc_audit.py` 核对。
8. 没有触碰 §3.1 的禁改文件。

---

## 5. 环境与验证基线（实测，2026-09-22）

### 5.1 解释器现状：依赖是分裂的，没有单一解释器能跑通全量

| 解释器 | Python | PySide6 | 结论 |
| --- | --- | --- | --- |
| `python`（PATH 默认） | 3.8 32-bit | 无 | **不可用**，连 pytest 都没有，别用它 |
| `E:\ANACONDA\python.exe` | 3.13.9 | 6.9.2 ✅ | **GUI / 采集基线用这个**；缺 `qfluentwidgets` `paramiko` `cv2` `pymysql` `trafilatura` `pygwalker` `fastapi` |
| `C:\Users\shen_zhe\miniconda3\python.exe` | 3.13.9 | 6.11.0 ❌ `DLL load failed while importing QtCore` | 业务依赖最全，但 **PySide6 已损坏**，任何 Qt import 都失败（连带 `qfluentwidgets` 不可用）；`trafilatura` 缺失。纯逻辑单测可用 |

- **推论**：跑测试必须**显式写解释器全路径**，不要裸用 `python`。
- **不要试图自己修 miniconda 的 PySide6**（属 §3.2 禁止行为），报告给用户。
- conda base 激活态下 import PySide6 需要「内联引导」，写法见
  `docs/Qt内联引导说明.md`；GUI 脚本一律加 `QT_QPA_PLATFORM=offscreen`。

### 5.2 测试基线（实测 2026-09-22）

```bash
# 采集
E:\ANACONDA\python.exe -m pytest -q --collect-only
→ 404 tests collected, 4 errors

# 全量运行（约 40s）
E:\ANACONDA\python.exe -m pytest -q --continue-on-collection-errors
→ 393 passed, 11 failed, 4 errors
```

这 15 个非通过项**全部是环境缺依赖 / 解释器编译选项导致的既有状态，
与代码质量无关**，不要试图「修好」它们（修环境属 §3.2 禁止行为）：

| 项 | 数量 | 根因 |
| --- | --- | --- |
| `tests/test_health_update_worker.py` 收集失败 | 1 | 缺 `paramiko` |
| `tests/test_management_no_sync_query.py` 收集失败 | 1 | 缺 `qfluentwidgets` |
| `tests/test_remote_connect_port_stable.py` 收集失败 | 1 | 缺 `qfluentwidgets` |
| `tests/test_single_shot_bar.py` 收集失败 | 1 | 缺 `cv2` |
| `tests/test_frps_phase2.py` failed | 4 | 缺 `qfluentwidgets`（`main_window/main_window.py`） |
| `tests/test_todesk_column.py` failed | 5 | 缺 `paramiko`（`workers/network_workers.py`） |
| `tests/test_mysql_sync_removed.py` failed | 1 | 缺 `qfluentwidgets`（`windows/management/common.py`） |
| `tests/test_data_retention.py::test_table_size_bytes_sqlite_dbstat` failed | 1 | 该解释器的 SQLite 未编译 `dbstat` 虚表 |

**判定规则**：`passed` 数下降、或出现上表之外的新失败 = 你改坏了东西。

**数据安全**：`tests/` 均用 `tmp_path` + `monkeypatch`（改 `core.app_paths.get_app_dir`
与 `table_db.DB_PATH`）隔离，全量跑完实测 `config/` `database/` **零改动**。
注意 `config/database.json` 里 MySQL 是 `enabled: true` 且指向生产库：
新增测试必须自行 monkeypatch `backend.is_mysql_test_mode`，否则会打到生产。
新增测试文件时请沿用同一隔离写法。

### 5.3 Windows / PowerShell 环境坑（agent 高频踩雷）

- 控制台是 **GBK**：脚本里 `print` 中文/emoji 会 `UnicodeEncodeError`，
  重定向到文件也会得到乱码。两种正确做法：
  1. 脚本开头 `sys.stdout.reconfigure(encoding="utf-8")`（本仓自带工具
     `tools/check_refs.py`、`tools/inventory.py` 已内置）；
  2. 或直接以 `encoding="utf-8"` 写结果文件，再读文件。
  走管道时额外需 `$env:PYTHONIOENCODING='utf-8'` +
  `[Console]::OutputEncoding=[Text.UTF8Encoding]::new()`。
- PowerShell 会**吃掉引号**：`Select-String -Pattern "a|b"`、`git grep -e "X="`、
  `python -c "..."` 都可能被解析错。改用工具级搜索，或把逻辑写成 `.py` 落盘再跑。
- 涉及中文路径的 git 命令加 `-c core.quotePath=false`，否则输出是八进制转义。

---

## 6. 新增脚本模板

`tools/<sub>/my_script.py`：

```python
"""一句话说明这个脚本验证/完成什么（这句会被索引工具抓取）。

背景：为什么要这个脚本、它对应的结论落在哪篇文档。
用法：python tools/<sub>/my_script.py [--flag]
产物：tools/_scratch/xxx.png（不入库）
"""
import os
import sys

# Windows 控制台默认 GBK，先切 UTF-8 再 print 中文
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# tools/<sub>/ → 上溯三层 = 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "tools", "_scratch")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    # ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

约定：**docstring 第一行必须能独立说明用途**；退出码 0=通过、非 0=失败
（这样 agent 与 CI 都能直接判定）；不写死绝对路径；print 中文前先切 UTF-8。

---

## 7. 已知既有债务（不要当成新问题重复上报）

`tools/check_refs.py` 的 `KNOWN_STALE` 已把这些文件的断链计入基线（22 处 / 9 文件），
它们是历史文档里指向已删除 / 已改名文件的引用：

| 文件 | 断链数 | 说明 |
| --- | --- | --- |
| `docs/QSS使用审计与弃用评估.md` | 11 | 审计时引用的临时探针脚本已删 |
| `docs/架构评审与SQL代码优化方案.md` | 2 | 同上 |
| `docs/Qt内联引导说明.md` | 1 | 指向已改名的入口脚本 |
| `docs/newbv_inventory_fields.md` | 1 | 指向已删除的抓取脚本 |
| `design/remote_session_v2.html` | 1 | 设计稿内引用的旧脚本名 |
| `main_window/hub_pages.py` | 1 | 注释里引用已删的 `fluent_window_proposal.html` |
| `main_window/pivot_page.py` | 2 | 同上 |
| `overview.md` | 2 | 内容整体过期，指向已不存在的 smoke 脚本 |
| `tools/deploy/prod_ssh.py` | 1 | docstring 用法示例里的用户自备脚本 |

处理原则：**顺手改到就修，专门修要问过用户**；绝不允许往 `KNOWN_STALE`
里加新条目来「让检查通过」。

其他已登记待用户决定的事项（agent 不得自行处理）：

- `build/` `dist/` `out/` 合计约 2.1 GB 构建产物是否清理
- `frpc.exe`（16 MB，tracked 但被 `.gitignore` 命中）是否 `git rm --cached`
- `config/database.json` 含 DPAPI 密文与公网 IP，是否继续入库
- `tools/*/_archive/` 下未入库的一次性脚本，是提交还是删除
- `tools/_scratch/` 内约 5 MB 抓取物是否清空

---

## 8. 相关文档

- 仓库总览与快速开始：`README.md`
- 文档索引：`docs/README.md`
- 设计稿索引：`design/README.md`
- 模块接口契约：`docs/API.md`
- 压测套件说明：`tools/stress_test/README.md`
- Qt 环境引导：`docs/Qt内联引导说明.md`
- logo 规范与定稿：`design/logo/spec.md`
