# AutoWork 程序内自动更新流程调研（2026-09-20）

> 目标：程序内「检查更新」按钮 → 下载更新包 → 自动更新（替换 + 重启）全链路设计。
> 结论先行：**推荐「整包 zip + 独立 updater 脚本」方案（方案 A）**，复用现有
> 49.235.34.253 nginx 静态分发 + paramiko SFTP 上传工具链；PyInstaller 生态的
> pyupdater/esky 均已归档，tufup 需改打包结构，均不适合现状。

## 1. 现状盘点

| 项 | 现状 | 对更新的影响 |
|---|---|---|
| 版本号 | `core/version.py`：`BASE_VERSION(3.11)` + git 提交数；**打包环境无 .git → 恒为 `3.11.0`** | ⚠️ 致命缺口：打包版无法区分版本，必须先修「构建期写入 version.json」 |
| 打包 | PyInstaller：`dist/AutoWork/`（onedir，**567MB**）+ `dist/aftersale.exe`（onefile，158MB）；`build_exe.py` 统一构建 | onedir 整目录替换；onefile 单 exe 替换更简单 |
| 分发通道 | 自有服务器 49.235.34.253（nginx + aftersale-web）；已有 `tools/upload_aftersale_dist.py`（paramiko SFTP + 远端备份范式）；设置里有 `upload_host/upload_remote_dir`（SFTP 上传视频） | 更新包放 nginx 静态目录即可，上传工具照抄范式 |
| HTTP 库 | `requests>=2.28` 已在依赖 | 检查/下载直接用 |
| 现有更新功能 | 无（grep「检查更新」无相关实现） | 从零做 |
| 关于页 | `main_window/hub_pages.py::AboutPage` + `ui_mixin` 关于弹窗（显示 APP_VERSION） | 「检查更新」按钮落点 |
| 子进程占用 | 主程序会从 exe 目录拉起 `frpc.exe`（P2P 远程） | ⚠️ updater 替换前必须一并结束 frpc，否则目录锁 |

## 2. 外部调研结论（PyInstaller 自更新生态，2026-09）

- **pyupdater / esky 已归档停维护**；Stack Overflow 2023+ 共识推荐 **tufup**
  （TUF 签名 + 补丁更新，活跃维护）。但 tufup 要求按它的 releases 目录结构
  重组打包产物（app 目录搬迁 + 仓库元数据），对现有 onedir 567MB 产物是
  结构性改造，收益（签名/增量）对内部自用分发不划算 → **不选**。
- **Windows 文件锁硬约束**（多方实证）：
  - 运行中的 exe 映像被内存映射锁定：**不可覆盖、不可删除**；
  - 但**同卷内 rename 不受锁限制** → 「先改名备份、再落新文件」是标准手法；
  - 兜底：`MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)`（重启时替换，需管理员）；
  - OS 标准件 Restart Manager / `RegisterApplicationRestart` 主要服务 MSI 场景，自研更新器用不上全套。
- **工业界主流模式 = 独立 updater 进程（launcher 模式）**：主程序只负责
  下载 + 拉起 updater + 退出；替换动作由外部进程在主程序退出后完成。
  VS Code / Slack 同模式。
- **高频事故模式**（dev.to 复盘多个 AI 工具自更新翻车）：
  1. updater 在旧进程仍持锁时替换并**谎报成功**（exit 0 ≠ 落盘成功）；
  2. updater 的「占用扫描」漏掉自己/父进程；
  3. `.update-incomplete` 标记残留 → 每次启动重试失败死循环。
  → 设计必须：替换前等主进程真退出、替换后**验证落盘**、标记文件启动即清。

## 3. 方案对比

| 方案 | 原理 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 整包 zip + 外部 updater（bat/robocopy）** | 下载 zip → 解压 staging → 拉起 updater → 主程序退出 → updater 等退出/杀 frpc → rename 备份 + robocopy 覆盖 → 重启主程序 | 实现直白、与现有产物/通道零改造、回滚天然（rename 备份） | 全量下载 567MB（内网/自有服务器可接受） | ✅ **一期采用** |
| B. tufup | TUF 签名 + 二进制补丁 | 签名、增量、活跃维护 | 需重组打包结构与服务器仓库 | ❌ 改造大 |
| C. 文件级增量 manifest | manifest 比对只下变化文件 | 下载量小 | 实现/失败面复杂，需维护每版 manifest | ⏸ 二期优化 |
| D. side-by-side + junction 切换 | 版本目录并存、符号链接切 active | 原子切换零停机 | 需管理员建链接、UAC 弹窗 | ❌ 过重 |

## 4. 推荐方案 A 详细设计

### 4.1 服务器侧（49.235.34.253）

- nginx 静态目录（如 `/opt/aftersale-web/dist/update/` 或独立 `/update/`，
  try_files 已覆盖子目录，零 nginx 改动）：
  - `latest.json`：`{version, url, sha256, size, notes, min_version, released_at}`
  - `AutoWork-<version>.zip`（整包，排除 config/logs/database 用户数据）
  - 保留最近 2 个版本 zip 供回退下载
- 发布工具 `tools/publish_update.py`（照抄 `upload_aftersale_dist.py` 范式）：
  paramiko SFTP 上传 zip + **先传 zip 后原子 mv latest.json**（避免客户端读到
  半截 manifest）；远端备份旧包。

### 4.2 客户端链路（四步）

1. **检查更新**（关于页按钮 + 设置项「启动时自动检查」可选）
   - `requests.get(<update_base>/latest.json, timeout=5)`，QThread 不阻塞 UI；
   - 版本比较：`packaging.version` 语义比较（或元组比较，避免新依赖）；
   - ⚠️ 前置修复：`build_exe.py` 构建后写 `dist/AutoWork/version.json`
     （内容=构建时 `core.version.get_app_version()`），`core/version.py`
     打包环境优先读 exe 旁 version.json，解决「打包版恒 3.11.0」。
2. **下载**（更新对话框：版本/大小/更新说明 + 进度条）
   - `requests` stream 下载到 `%TEMP%/autowork_update/AutoWork-<ver>.zip.part`；
   - 完成后 **sha256 校验**（对不上即删 .part 报错，绝不进入安装）；
   - zip 完整性校验（`zipfile.testzip()`）后解压到 staging 目录。
3. **安装**（用户点「立即更新」）
   - 生成 `update.bat`（或独立 updater.exe，二期）于 exe 目录外（%TEMP%）：
     ```
     :wait  → tasklist 轮询等主 PID 退出（上限 30s）
     taskkill /IM frpc.exe /F        ← 子进程持锁
     ren  "<install>" "<install>_old" ← 同卷 rename 不受锁限制=备份+让位
     robocopy "<staging>" "<install>" /E /XD config logs database /NFL /NDL
     start "" "<install>\autowork.exe"
     rd /s /q "<install>_old"  &  del 自身 & 写 update_result.log
     ```
   - 主程序写 `.update-pending` 标记 → `QApplication.quit()` 退出；
   - **保护用户数据**：robocopy `/XD` 排除 `config/ logs/ database/`（用户库、
     分域配置、日志不覆盖；新版 config 缺省键由配置门面首启迁移补齐）。
4. **重启与回执**
   - 下次启动：见 `.update-pending` → 读 `update_result.log`：
     成功 → InfoBar「已更新到 x.y.z」；失败/缺失 → 提示并**自动清理
     staging/_old/标记**（防死循环，针对事故模式 3）；
   - updater 落盘验证：robocopy 返回码 + 关键文件（autowork.exe 大小>0、
     version.json 版本==目标）写进 result.log，**exit 0 不等于成功**。

### 4.3 aftersale.exe（onefile 单文件）

同链路简化版：zip 内仅单 exe；updater 等退出后 rename 旧 exe + copy 新 exe
即可（单文件无目录锁问题，只锁自身）。可与主程序共用 `latest.json` 的
`channels: {autowork: {...}, aftersale: {...}}` 结构。

## 5. 风险清单与对策

| 风险 | 对策 |
|---|---|
| 打包版版本号恒 3.11.0 | 构建期写 version.json（4.2-1 前置修复） |
| 运行中 exe/目录锁 | 独立 updater + 等退出 + 杀 frpc + rename 让位 |
| 更新中断留半截 | .part + sha256 + testzip 三重校验后才进安装 |
| 失败死循环重试 | 标记启动即清 + result.log 回执 + staging/_old 自动清理 |
| 用户数据被覆盖 | robocopy /XD 排除 config/logs/database |
| 更新包被篡改/误传 | sha256 入 latest.json；发布工具先包后 manifest 原子切换 |
| 567MB 下载慢/断 | 一期接受（自有服务器）；二期文件级增量 manifest（方案 C） |
| 多实例同时更新 | updater 启动时单实例检查（mutex/标记文件） |

### 5.1 实施阶段新发现的三个风险（2026-09-21，均已修）

| 风险 | 触发条件 | 后果 | 对策 |
|---|---|---|---|
| **vbs 模板语法错误** | Python raw 三引号 `r"""...""""..."""` 把 VBScript 的 `""""` 转义吃掉 | wscript 静默失败 → updater **根本没跑**，程序退出后什么都没发生，用户以为更新失败 | 改用 `Chr(34)` 拼引号；`logs/sim_vbs_launcher.py` 用 cscript 同步跑一遍捕获语法错 |
| **PATH 污染穿透等待循环** | 环境里存在 GNU coreutils（conda/Git Bash/便携工具），`find`/`timeout` 解析到非 Windows 版 | 等待循环瞬间失效 → **主程序还在运行就开始 rename 安装目录** → 安装目录被写坏 | bat 内所有外部命令走 `%SystemRoot%\System32\` 绝对路径；`timeout` 换成 `ping -n 2`（不依赖 console stdin）；`logs/sim_updater_hardening.py` 在污染环境下实测等待 9s 生效 |
| **nginx SPA 回退伪装成 200** | `try_files $uri $uri/ /index.html`：任何不存在的路径都返回 **200 + text/html** | latest.json 缺失/包未传完时，客户端拿到 HTML，报「sha256 校验失败」或「Expecting value」，查不出真因 | `fetch_latest` 与 `download_file` 双重内容类型守卫：`json not in ctype and not body.startswith(b"{")` → 明确报「疑似 nginx 回退到首页 HTML」；`logs/sim_prod_update_source.py` 对生产实测拦截生效 |

> 注：第三个风险不影响正常发布——文件真实存在时 `$uri` 直接命中，nginx 返回
> `200 + application/json`（已在生产实测确认），所以「零 nginx 改动」结论依然成立。
> 守卫的价值是把「发布流程出错」从含糊报错变成可定位的明确提示。

## 6. 实施拆分（建议顺序）

1. S1 版本号落盘：`build_exe.py` 写 version.json + `core/version.py` 读取（半天内，独立可验）；
2. S2 服务器侧：nginx 静态目录 + `tools/publish_update.py` + latest.json 范式；
3. S3 客户端检查+下载：关于页按钮、更新对话框、QThread 下载/校验（offscreen 可测：mock latest.json 本地 http）；
4. S4 updater：update.bat 生成 + 退出/重启/回执/清理；真机回归（锁场景必须真机）；
5. S5 二期：文件级增量 manifest、updater.exe 化（去 bat 黑窗）、tufup 签名评估。

---

## 7. 落地记录（2026-09-21，S1–S5 全部实施完成）

### 7.1 交付文件清单

| 层 | 文件 | 职责 |
|---|---|---|
| 版本 | `core/version.py` | frozen 优先读 exe 旁 `version.json` → git 实时 → 回退 `{BASE}.0` |
| 构建 | `build_exe.py` | 两 spec 构建后写 `dist/AutoWork/version.json` + `dist/version.json`，产物校验清单已加 |
| **纯逻辑** | `core/updater.py` | 版本比较 / fetch+守卫 / check / 下载+sha256 / 解压+zip-slip 防护 / 顶级目录归一化 / manifest diff / bat+vbs 生成 / pending 标记 / 回执消费 / 拉起 updater（**无 Qt 依赖，可单测**） |
| 线程 | `workers/update_worker.py` | `UpdateCheckWorker`（found/up_to_date/error）、`UpdateDownloadWorker`（stage/progress/ready/error/cancelled，取消走 `requestInterruption`） |
| UI | `windows/update_dialog.py` | `UpdateDialog`：found → downloading → ready → error 状态机，运行期禁止关闭 |
| UI | `main_window/hub_pages.py` | `AboutPage` 加「检查更新」`PrimaryPushButton` + 状态文案 + `set_checking/show_check_result` |
| 编排 | `main_window/update_mixin.py` | `UpdateMixin`：起 worker / 弹对话框 / InfoBar / 安装拉起 / 退出 / 回执消费 / 启动自检 |
| 入口 | `main.py` | 窗口 show 后 `singleShot(0)` 消费回执 + 按配置静默自检 |
| 配置 | `core/app_settings.py` | misc 域新增 `update_base_url`、`update_auto_check` |
| **发布** | `tools/publish_update.py` | 打包 zip + sha256 + manifest → SFTP 上传 → **先包后 latest.json 原子切换** → 保留最近 3 版 |
| 验证 | `logs/sim_updater_e2e.py` | bat 端到端真机仿真（full/incremental/失败路径） |
| 验证 | `logs/sim_updater_hardening.py` | 等待循环真实性 + 含空格路径 + System32 绝对路径静态扫描 |
| 验证 | `logs/sim_publish_and_client.py` | 发布 → 本地 http 源 → 客户端全链路集成（含 sha256 篡改/取消） |
| 验证 | `logs/sim_vbs_launcher.py` | vbs 隐藏窗口拉起链路 |
| 验证 | `logs/sim_prod_update_source.py` | 真实客户端代码打**生产** URL |
| 测试 | `tests/test_updater.py` | pytest 单测（版本比较/解压/manifest/bat 生成/回执往返） |

### 7.2 验证结果（全绿）

| 验证 | 结果 |
|---|---|
| `logs/sim_updater_e2e.py` | **29/29 PASS**（真机 cmd 执行 bat） |
| `logs/sim_updater_hardening.py` | **20/20 PASS**（PATH 污染环境实测等待 9s 生效） |
| `logs/sim_publish_and_client.py` | **50/50 PASS**（本地 http 源，全链路） |
| `logs/sim_vbs_launcher.py` | PASS（marker 生成，含空格路径） |
| `logs/sim_prod_update_source.py` | **21/21 PASS**（生产 URL） |
| `tests/test_updater.py` | 见 pytest 回归 |

关键实测点：
- **full 换位**：`config/logs/database` 与 `.update-pending` 全部回迁到新目录，旧 dll 不残留，`_old`/`_new`/staging 清理干净，bat 自删；
- **失败不破坏**：staging 缺主 exe 时安装目录仍是旧版、config 完好，回执 `{"ok": false, "error": "main exe missing in update package"}`，且 pending 被消费后**绝不重试**；
- **增量正确**：`manifest_diff` 只挑出 3 个变更文件，未变更的 `b.pyd` 不下载、合并后保留；
- **onedir zip 归一化**：带 `AutoWork/` 顶级目录的包被 `flatten_extract_root` 正确展平；
- **sha256 篡改被拒**：伪造哈希 → `ValueError`，且失败后**不留脏 staging**。

### 7.3 生产更新源现状（已就绪）

```
/opt/aftersale-web/dist/update/
├── latest.json      占位（version=0.0.0，任何客户端都不会误触发更新）
├── packages/        空，等待首次发布
└── files/           空，增量模式散文件目录
```

- HTTP 实测：`curl http://49.235.34.253/update/latest.json` → **200 + application/json**，386 字节，零 nginx 改动；
- 客户端实测：`check_update` 对本地 `3.11.273`/`9.9.9` 等一律返回 `None`（占位版本 `0.0.0` 永不新于任何真实版本）。

### 7.4 发布 SOP（首次正式发布）

```bash
# 0) 必须先重新构建（当前 dist/AutoWork 是 S1 之前的旧产物，无 version.json）
python build_exe.py

# 1) 本地自查：只打包不上传，确认排除项与 manifest 正确
python tools/publish_update.py --pack-only \
    --source dist/AutoWork --version 3.11.274 --notes "修复xxx"

# 2) 正式发布（先包后 latest.json，原子切换）
AFT_SSH_PASS='***' python tools/publish_update.py \
    --source dist/AutoWork --version 3.11.274 --notes "修复xxx；新增yyy"

# 3) 增量热修（需上一版 manifest 作基线，并给 min_version 让跨版客户端降级全量）
AFT_SSH_PASS='***' python tools/publish_update.py \
    --source dist/AutoWork --version 3.11.275 --mode incremental \
    --base-manifest out/update_manifest_3.11.274.json \
    --min-version 3.11.274 --notes "热修 dll 崩溃"

# 4) 发布后验证
curl -s http://49.235.34.253/update/latest.json | python -m json.tool
python logs/sim_prod_update_source.py
```

发布语义保证：包体先传并**远端 sha256 复核**通过，才覆盖 `latest.json`
（`.tmp` + `mv` 同目录 rename）。客户端要么看到旧版要么看到完整新版，
不会看到「指向半截包」的中间态。

### 7.5 安全边界与已知限制

- **dev 环境拒绝安装**：`_install_update` 检测 `frozen=False` 直接拒绝并提示，
  防止把源码目录当安装目录替换掉（检查/下载仍可跑，便于联调）；
- **失败绝不重试**：`consume_update_receipt` 遇到「有 pending 无回执」只清理并
  提示，不重新发起安装——这是 dev.to 复盘的头号事故模式；
- **未做代码签名**：当前靠 sha256（HTTPS 通道为 HTTP，中间人可换包）。
  生产是自有内网分发 + IP 固定，一期接受；若后续对外分发，需加
  Authenticode 签名或改用 TUF（见 7.6）；
- **多实例并发更新未加锁**：同时开两个 AutoWork 各自更新会互相 rename，
  一期靠「单实例运行」的使用习惯规避，后续可加命名 mutex；
- **aftersale.exe（onefile）通道已留**：`CHANNEL_AFTERSALE` 与 channels 结构
  已支持，但 updater 的 full 换位逻辑是为 onedir 目录设计的，onefile 需另写
  单文件替换分支（rename 旧 exe + copy 新 exe），本期未实施。

### 7.6 tufup 评估结论（S5 二期项）

调研时已排除，落地后再次确认不引入：

| 维度 | tufup | 本方案 |
|---|---|---|
| 打包结构 | 要求 tar.bz2 + 自己的目录布局，需重组 PyInstaller 产物 | 直接用 onedir 产物打 zip，零改造 |
| 签名 | TUF 多密钥角色（root/targets/snapshot/timestamp），需建密钥管理体系 | sha256 校验（弱，但匹配自有内网分发场景） |
| 增量 | 支持 patch | 已自研 manifest diff 文件级增量（更贴合 onedir 的 3000+ 文件形态） |
| 维护 | 引入外部依赖与其升级风险 | 全部自有代码，21 个函数、可单测 |

**结论**：若将来需要强签名保证，优先给 `AutoWork.exe` 加 Authenticode 并在
updater 里用 `signtool verify` 校验，比整体迁移 tufup 成本低得多。
updater.exe 化（替代 bat，彻底去黑窗）当前由 vbs 隐藏窗口达成，非必需项。

