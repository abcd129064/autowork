# AGENTS.md — AutoWork 仓库作业规范

> 适用对象：所有在本仓库工作的编码 agent（以及新加入的人类开发者）。
> 本文件是**硬约束**，不是建议。与临时指令冲突时，先提问再动手。
> 维护规则：目录结构 / 验证基线 / 命名规则发生变化时，**同一次提交内**更新本文件。
> 最后更新：2026-11-18（新增 §9 界面文案与视觉规范、§9.8 视觉对齐 token；§4 补第 9/10 条自检）

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
- 现状：59 个 `test_*.py`，采集基线见 §5.2。

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

### 2.5 注释约定（2026-10-18 起）

- 新增或改写代码的注释密度按 **约 50%** 把握：关键分支、边界条件、反直觉
  选择都要有注释，不写复述代码的废话。
- 注释优先写「为什么」和「不这样会怎样」，而不是「做了什么」；函数
  docstring 第一行给结论，再补背景。
- **不用括号式旁白堆叠说明**，如「（S2 返工，2026-09-06：……）」；把时间、
  原因、后果拆成正常句子。
- 连续三行以上的注释压到两行以内，信息密度要高；必须展开的复杂背景放文档，
  不塞在行内。
- 改代码导致注释失真时，同一次编辑内更新注释；历史演进留在文档或 commit，
  不留在过时注释里。

---

## 3. Agent 工作流硬约束

### 3.1 禁止触碰的文件（读到即止，不写、不删、不移动、不格式化）

- `config/**`：真实配置，含 DPAPI 加密的密码与生产 IP。
- `database/*.db`、`database/tables*.db`：真实业务数据。
- `moyu_state.json`、`settings.json.bak`、`logs/**`、`.workbuddy/**`。
- `frpc.exe`、`frpc_xtcp*.toml`、`frpc_xtcp_meta.json`：运行时二进制与实时配置。
- `build/`、`dist/`、`out/`、`web/*/dist/`、`web/*/node_modules/`：构建产物。
- `autowork_with_table.py`：历史生成物，生成链已断且已人手维护；仓库里
  没有对应 `.ui`，改前先读 `docs/售后期记录页分层改造与UI组织评审2026-10-18.md`。
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
2. `pytest tests/ -q` → `passed` 数不低于 §5.2 基线（744），且**无新增失败项**
   （§5.2 表里的 49 项环境既有失败除外）。
3. `python -m compileall -q tools design tests` → rc=0（语法与缩进无破坏）。
4. `git status --porcelain -uall` → 没有预期外的新文件；临时产物都在
   `tools/_scratch/` 内；没有 `_` 前缀文件被误加。
5. 新增文档已挂进 `docs/README.md`；新增脚本符合 §2.2 前缀与 §1.2 目录判据。
6. 改动了目录结构 → `README.md`「项目结构」段已同步。
7. 改动了 `docs/API.md` 里登记的公开接口 → 跑 `python tools/api_doc_audit.py` 核对。
8. 没有触碰 §3.1 的禁改文件。
9. 改动了用户可见文案 → 跑中点自检 + 截图对比 + `pytest tests/ -q`。
   `windows/aftersale/records/query_state.py` 的页脚与 tooltip 文案被单测锁定，
   改格式必须同步改测试断言。
10. 改动了样式值 → 跑 `core.design_tokens.ui_self_check()`（返回空列表）
    + 亮暗双主题截图对比 + `pytest tests/ -q`；
    确认没有引入新的硬编码十六进制或像素字面量。

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

### 5.2 测试基线（实测 2026-10-06）

```bash
# 采集
E:\ANACONDA\python.exe -m pytest -q --collect-only --continue-on-collection-errors
→ 784 tests collected, 10 errors

# 全量运行（约 35s）
E:\ANACONDA\python.exe -m pytest -q --continue-on-collection-errors
→ 744 passed, 36 failed, 7 skipped, 13 errors
```

这 49 个非通过项**全部是环境缺依赖 / 解释器编译选项导致的既有状态，
与代码质量无关**，不要试图「修好」它们（修环境属 §3.2 禁止行为）：

| 项 | 数量 | 根因 |
| --- | --- | --- |
| `test_health_update_worker` / `test_mica_policy` 等收集失败 | 13 | 缺 `qfluentwidgets`（`windows/remote_session/remote_hub.py`、`remote_session_window.py`、`management/common.py` 等）· 缺 `paramiko`（`workers/network_workers.py`）· 缺 `cv2` |
| `tests/test_perf_dpi_degrade.py` failed | 12 | 缺 `qfluentwidgets`（弹窗/亚克力相关实现） |
| `tests/test_perf_dpi_p1.py` failed | 6 | 同上 |
| `tests/test_frps_phase2.py` failed | 6 | 缺 `qfluentwidgets`（`main_window/main_window.py`） |
| `tests/test_state_sync_fixes.py` failed | 5 | 缺 `qfluentwidgets` |
| `tests/test_todesk_column.py` failed | 5 | 缺 `paramiko`（`workers/network_workers.py`） |
| `tests/test_mysql_sync_removed.py` failed | 1 | 缺 `qfluentwidgets`（`windows/management/common.py`） |
| `tests/test_data_retention.py::test_table_size_bytes_sqlite_dbstat` failed | 1 | 该解释器的 SQLite 未编译 `dbstat` 虚表 |

> 该数字会随他人未提交的工作漂移（本仓常有多会话并行改动）。**判定标准不变**：
> `passed` 数下降、或出现上表之外的新失败 = 你改坏了东西；只增减上表内项目不算。
> 需要严格自证时用 A/B：把改动涉及的文件换回 `HEAD` 版各跑一次全量，比较两次的
> `passed/failed/errors`（例见 [终端全屏应用渲染修复.md](docs/终端全屏应用渲染修复.md) §二 证据 4）。
>
> 可离线直接跑的远程会话相关回归（纯逻辑，`E:\ANACONDA` 即可）：
> `tests/test_vt_screen.py`（50 例，含 OSC 7）、`tests/test_ansi_terminal_render.py`（15 例控件层）、
> `tests/test_credentials.py`（21 例）、`tests/test_transfer_queue.py`（13 例）、
> `tests/test_transfer_verify.py`（21 例）；
> 同主题离屏冒烟在 `tools/smoke/`：`smoke_ansi_terminal_tui.py`、`smoke_ssh_cwd_chip.py`、
> `smoke_remote_ssh_creds.py`、`smoke_sftp_queue_persist.py`、`smoke_sftp_size_verify.py`
> （需 `QT_QPA_PLATFORM=offscreen` + 全依赖解释器，见 [远程会话凭据与传输可靠性.md](docs/远程会话凭据与传输可靠性.md) §六）。

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
---

## 9. 界面文案与视觉规范（2026-11-18 起）

适用：`windows/**`、`main_window/**` 下所有**用户可见文案**与控件布局，
以及 `styles/**`、`core/design_tokens.py` 下所有样式值。以下均为硬约束，
评审与提交前自检逐条过。

### 9.1 文案分隔符

- 禁止用中点 `·`（含 `・` `•` `∙`）连接两个语义单元。
- 替代顺序：拆成独立字段 / 用版式间距分组 / 括号补充 / 顿号连接同类枚举
  （同类项且不超过三项）。禁止连续斜杠串联、破折号串联、堆叠括号。
- 例外（不受此限）：注释与 docstring、日志与控制台输出、数值单位
  （`h/人·日`）、图形占位符（RTT sparkline 的 `·`、方向键标签）、
  以及测试里作为原文引用保留的旧文案。改旧文案时同步改对应断言。
- 自检：全仓搜索 `[·・•∙]`，命中项须落在上述例外或已评审。
  `windows/aftersale/records/query_state.py` 的页脚与 `row_tooltip`
  文案被单测锁定，改格式必须同步改测试。

### 9.2 冗余文本

- 禁止三类文案：教用户点哪里的操作指引、描述控件形态的说明、
  复述主数字的副标题。
- placeholder 不重复标签文字，不写「多行文本」「自动带出」等实现描述。
- tooltip 只写删不掉的口径信息；操作指引交给控件本身或首次引导。
- 调试态信息（本地端口、Mock 标记）不得出现在生产界面。
- 导入预览的缺列警告属于必要风险提示，不计入冗余。

### 9.3 用词与长度

- 同一概念全链路统一：列头 / 筛选 / 表单 / 统计 / 导出 / 提示用同一个词。
- 用词取自既有业务词表，不为求简洁造新词；新增业务词须在设计稿给出
  定义并同步 `docs/API.md`。
- 词表：`我方问题` / `已解决` / `主动发起` / `解决时间` / `新增记录` /
  `常用句` / `球房` / `桌号` / `地区` / `问题` / `发生原因` / `解决方案`。
  收敛掉的旧写法：`我们问题`、`是我们的问题`、`是否解决`、
  `我们主动发起`、`响应时间`（仅界面文案；Excel 表头与 DB 列名不动）。
- 标签名词化，不用「是否…」疑问句，不用「我们」作主语。
- 长度：按钮 2–6 字，字段标签 2–5 字，placeholder ≤8 字，tooltip ≤20 字。

### 9.4 光学对齐

- 标签与控件共用同一条左基线；栅格 gap 12px、控件高 32px（文本域可倍增）。
- 数值列右对齐并使用等宽数字；图标按钮按视觉重心下移 1px 补偿。
- 分区标题竖线与标题首字按光学边界对齐，不用几何边界。
- 同组卡片保持等宽（`windows/aftersale/rank.py` 已有决策），
  不因内容长度改变宽度。

### 9.5 同心圆角

- 公式：`内圆角 = 外圆角 − 4px`。
- 唯一档位：弹窗 R12 / 分区卡 R8 / 输入控件与按钮 R4 / 状态标签胶囊 /
  表格单元格 0。V1 稿的控件 R6 作废，R6 不在 4px 网格上。
- 嵌套层级不超过 3 层，相邻层级背景亮度递进 ≤4%。

### 9.6 动效

- 默认无过渡。仅允许：展开收起、选中态、下拉与浮层出现。
- 统一 `ease-out` `250ms`；禁止弹窗 opacity 淡入、循环动画、弹性回弹、
  >250ms 过渡、表格滚动动画。
- 新动效必须接入 `windows/aftersale/settings.py` 的动画开关，
  禁用时返回 `NONE` 跳过动画管线（`windows/aftersale/common.py` 现有机制）。
- 尊重系统「减少动画」设置。

### 9.7 交互与信息优先级

- 判断类控件不常驻；默认收起，在用户准备做该判断的位置展开。
- 已激活的筛选条件回显为可删除标签，放在筛选栏原位。
- 条件字段在触发条件成立时原位出现，不用禁用态加文字解释。
- 优先级：主动作 > 结论区 > 查询区 > 数据区 > 页脚 > 次要动作。
- 每屏只保留一个主按钮；次要动作文字化或收进溢出菜单。
- 不新增确认弹层；破坏性操作沿用既有二次确认。

### 9.8 视觉对齐 token（2026-11-18 起）

**单一事实源是 `core/design_tokens.py` 的 `UI_V2` / `NEUTRAL`**，
配套 `ui_color()` / `ui_hover_colors()` / `ui_qss()` / `ui_self_check()`。
所有值从 token 出，禁止在 QSS 或 Python 里就地写十六进制与像素字面量。
旧的 `SPACE` / `RADIUS` / `CONTROL` 保持不动，`worklog` 与 `tools` 仍在用。

- **间距**：最小单位 4px，阶梯 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64，
  全为整数倍；禁止 5 / 10 / 14 / 22px 等非倍数做视觉微调。
  分组优先靠间距，不靠分隔线。
- **字排**：标题 16/24/500、正文 14/20/400、辅助 12/16/400、
  数值 28/32/600；标题行高统一 24px、正文统一 20px，均为 4 的倍数。
  字重仅 400 / 500 / 600 三级，禁止 700 及以上、斜体、
  用颜色深浅代替字重层级。
- **尺寸**：控件 32、chip 24、表格行 40、表头 20、概览卡 96、
  按钮最小宽 88、图标 32，全为 4 的倍数；同类组件必须同尺寸。
- **灰阶**：中性灰 50–950 共 11 级（R=G=B），亮暗主题共用一套、
  角色反相，不维护第二套色板。层级由灰阶 + 间距 + 字重表达，
  品牌色只承担主动作 / 选中 / 焦点语义，读 `qconfig.themeColor`，不硬编码。
- **圆角与阴影**：R12 弹窗 / R8 卡片 / R4 控件 / 胶囊标签，
  满足 `内圆角 = 外圆角 − 4px`；阴影仅 e1 静止 / e2 上浮 / e3 弹窗三级，
  禁止多层、彩色、内阴影。表格、列表行、内嵌卡不加阴影。
- **分隔线**：全站唯一 `1px Gray-300`（暗色 Gray-700），
  仅出现在卡片之间、表头下、页脚上；表格行改交替底色，不画行线；
  高 DPI 按逻辑像素 1px，禁用 0.5px。
- **动效**：统一 `ease-out` `250ms`。hover 上浮统一
  `translateY(-2px)` + e1→e2 + 边框 Gray-300→Gray-400，
  所有可 hover 元素同一组参数，禁止逐个微调。
  禁止弹窗 opacity 淡入、循环动画、弹性回弹、表格滚动动画。
  动画开关关闭时 hover 只换边框色，不做位移。
- **Qt 实现约束**：QSS 不解析 `line-height`，`ui_qss()` 只发字号与字重，
  行高由 `UI_V2["type"][*]["line"]` 在布局层约束；`ui_qss()` 不写 color，
  避免覆盖 qfluentwidgets 主题色。
- **qfw 尺寸覆盖**：`ZhDatePicker.show()` 会把最小宽压到 240、高压到 30；
  `Pivot/SegmentedWidget` 的 `SetMinimumSize` 约束会拿内部 item 最小高（26）
  反写父控件，只锁外层会被冲回。修法见 `windows/aftersale/form.py`
  的 `showEvent()` 与 `windows/aftersale/common.py` 的 `YesNoSegment`。
- **自检**：改动样式值后调用
  `python -c "from core.design_tokens import ui_self_check; print(list(ui_self_check()))"`，
  要求返回空列表（间距 / 尺寸 / 行高 / 圆角均为 4 的倍数、
  灰阶 11 级且中性、同心圆角成立、阴影三级、动效 250ms、线宽 1px）。
  不通过不提交。
