# CHANGELOG — 版本更新记录

> 本文件按**阶段**记录项目演进，条目来源为 git 提交历史（314 次提交，
> 2026-08-04 ~ 2026-09-23）与已落地的设计文档，不逐条罗列提交。
> 版本号规则见 `core/version.py`：`{BASE_VERSION}.{git 提交数}`，
> 当前 BASE_VERSION = **3.13**。
> 作业规范见根目录 [AGENTS.md](../AGENTS.md)，接口契约见 [API.md](API.md)。

状态图例：**已发布** = 已进打包分发 ｜ **已落地** = 已进代码未单独发版 ｜
**进行中** = 当前正在推进

---

## 3.13（2026-09-19 ~ 2026-09-23，进行中）— 远程二期 + frps 感知 + 仓库治理

### 远程 / frp
- 远程页 RemoteHub 由三视图扩为**四视图**：会话总览 │ 连接 │ **连接质量**（新增）│ 隧道配置（`windows/remote_session/remote_hub.py`，2026-09-21）。
- **远程页 TCP 直连双模化**（2026-09-24，设计稿 `design/remote_hub_tcp_linkage_v1.html`）：①视图 2「P2P 访客」更名**「连接」**，顶部 XTCP│TCP Segmented 切换——TCP 模式为直连表单 + 保存服务器列表（`settings.tcp_servers` 与主面板远程菜单同键同源）；`RemoteSessionManager.open_direct_session()` 直连任意 host:port 开 SSH/SFTP（复用主面板 ssh_user/ssh_pass）；②frps 代理视图去孤岛化：xtcp 页签联动本地注册表三态（已注册/已断开/未注册），行内动作——已注册 SSH/SFTP 直连、已断开 重连/删注册、未注册 ＋注册并连；tcp 页签行内 SSH/SFTP 直连（目标 = frps serverAddr:remotePort）+ ⊕存服务器；③模式记忆 `remote_conn_mode`，弹出窗口左导航同步更名。
- frps 在线感知：进入远程页即探测 frps 管理 API，会话总览新增 **frps 概览卡**（`core/frps_admin.py`）；连接质量视图每 30s 一轮探测 visitor 打洞质量。
- frpc visitor **热重载**（`core/frp_remote.py`，2026-09-21）：注册/删除隧道不再整进程重启 frpc。
- **frp 总开关**（2026-09-22）：配置页一键启停全部隧道；首页日志工作台支持排除 frp 日志。
- **隧道断开保留注册**（2026-09-22）：断开 = 置 disabled + apply（落 sidecar `frpc_xtcp_disabled.json`，绝不进 TOML）；删除 = 注册 + TOML + sidecar 全清。
- 开机静默预连（2026-09-23）：启动 6s 后 `autostart()`（仅启用隧道、异常全吞进日志），就绪轮询后 `prewarm_async()` 串行 connect 预热打洞；预连后点 SSH 秒连。详见 [frp-source-integration.md](frp-source-integration.md) 附录 G。
- **远程可靠性批次**（2026-09-23）：①frpc 意外退出退避自愈重启（5s/30s/2min，健康 ≥60s 清零）；②autostart 瞬态失败有界重试（60s/120s）；③质量探测冷洞首拍 3s 长超时不计失败；④会话打开/预热改 bindPort 就绪轮询（200ms 间隔/8s 上限）替代固定延时；⑤隧道失联联动已开会话面板提示条（`windows/remote_session/tunnel_notice.py`）；⑥预热成功状态列「已预热」（`prewarmedAt` 仅内存）；⑦远程页文件归口 `windows/remote_session/`（remote_hub/remote_mixin 迁入）。
- frp 0.65 管理 API 参考手册落档：[frp-065-api-reference.md](frp-065-api-reference.md)。
- **远程 SSH 凭据拆分：设备（XTCP）与直连主机（TCP）分开**（2026-10-04）：此前隧道路径与 TCP 直连共用 `ssh_user/ssh_pass`，而 XTCP 卡没有凭据输入口——一旦用 TCP 卡连过另一台主机（如 frps 服务器 `root`），设备凭据就被覆盖，隧道会话随即「认证失败，请检查用户名和密码」（设备账号如 `newbv`）。现：①`VisitorWork` XTCP 卡新增「SSH 账号/SSH 密码」（= `ssh_user/ssh_pass`，与主面板 P2P 表单/统一设置页同源，`textEdited` 脏标记防回填覆盖），添加并注册 / 添加并连接 SSH 时非空回写；②TCP 卡凭据改存 `tcp_ssh_user/tcp_ssh_pass`（`SENSITIVE_KEYS` 一并纳入 DPAPI），并经 `open_direct_session(..., username=, password=)` 显式传入；③`RemoteSessionManager._do_open` 凭据显式优先、`None` 才回退 settings（隧道路径行为不变）；④宿主支持 `_save_settings` 时走它写回，顺带修掉主窗口 `_settings_cache` 陈旧问题。回归 `tests/test_remote_cred_split.py`（14 例）+ `tests/test_remote_hub_linkage.py`（透传断言）。
- **远程页操作列移除 RDP + 新增「RDP」视图**（2026-10-04）：RemoteHub 扩为**六视图**（末位新增「RDP」，`windows/remote_session/remote_hub.py::RdpWork`）；会话总览操作列不再出现 RDP 链接（现为 SSH/SFTP/断开/删除）——visitor 的 `bindPort` 只映射远端 22（SSH/SFTP 复用），RDP 需要 3389 专用隧道（约定 `rdp_<serverName>`）属后续功能，此前点 RDP 必然连到 SSH 端口。新视图承载说明与状态（注册隧道数 / RDP 可用隧道 / 状态，当前恒「未开通」）+「打开会话中心」；`_on_ops_link` 仍接受 `'rdp'`，专用隧道落地后恢复入口零改动；弹出面板 `nav_icons["remoteHub"]` 同步补齐第 6 键（缺键会静默退回内嵌 Pivot 形态）。回归 `tests/test_remote_rdp_tab.py`（10 例，含 nav_icons 覆盖校验）。
- **SSH 终端全屏应用（nano/vim/less/top）渲染修复**（2026-10-05）：用户截图显示 nano 首屏「30 行正文塌成 1 行、每行多一个字面 `B`、光标方块显示成 `&nbsp;`、status 与快捷键行挤同一行」。根因（有真实会话字节流 + terminfo 双向证据）：自写解析器不认 ncurses 的行定位序列（`\E[<n>d` VPA / `\E[<n>E` CNL / `\E[L`/`\E[M` / 滚动区 / 擦删插字符），且 `\E(` 只吃两字节导致 `\E(B`（`sgr0` 复位字符集）漏字；另无固定网格、PTY 尺寸写死 120x40。修复：①新增 `core/vt_screen.py`（零 Qt 依赖的 VT100/xterm 屏幕模型，支持 VPA/CUP/EL/ED/IL/DL/ICH/DCH/ECH/SU/SD/DECSTBM/备用屏幕/SGR/ACS 字符集/**宽字符双宽**/回滚，可离线单测）；②`windows/remote_session/ansi_terminal.py` 重写为纯渲染/输入层（实测行高换算网格、选区与滚动位跨重渲染保持、DECCKM 应用光标键、修饰键组合、括号粘贴、SGR 鼠标上报）；③`ssh_terminal.py` 用控件实测网格开 PTY 并 `resize_pty`（120ms 去抖）、reader 线程增量 UTF-8 解码、会话日志 `\r` 归一化；④`windows/remote_session/__init__.py` 改 PEP 562 惰性导出（此前任何子模块导入都被 `qfluentwidgets` 连带）。回归 `tests/test_vt_screen.py`（40 例）+ `tests/test_ansi_terminal_render.py`（15 例）+ 冒烟 `tools/smoke/smoke_ansi_terminal_tui.py`（22 项）；设计与"序列→行为"对照表见 [终端全屏应用渲染修复.md](终端全屏应用渲染修复.md)。
- **远程会话凭据模型 / 传输可靠性 / 终端 cwd 联动**（2026-10-06）：①**凭据统一模型**（新增 `core/credentials.py`，零 Qt）——两类凭据（设备/XTCP = `ssh_user`/`ssh_pass`，直连主机/TCP = `tcp_ssh_user`/`tcp_ssh_pass`）由同一解析器按「表单值 → 进程内会话表 → 已保存设置」定源，三个输入口（主面板 P2P 表单、统一设置页、远程页 XTCP/TCP 卡）行为一致；卡片新增**「记住密码」勾选**（未勾选时写盘补丁构造上就不含密码键、只回写用户名，密码进 `SessionCredentialStore` 仅本进程有效）、**「凭据来源」实时提示**（已保存（DPAPI 加密）/ 本次输入 / 仅本次会话）、**「清除已保存凭据」**（二次确认后写空串）；②**传输成功判定按两侧大小校验**（新增 `core/transfer_verify.py`）——上传/下载完成后比对两端大小，不一致抛 `TransferSizeMismatch`（区分「远端/本地文件被截断」「比另一端大（传输期间被改写？）」，读不到大小时不谎报差值），目录传输逐文件判定并汇总为「部分失败」；③**未完成传输队列落盘**（新增 `core/transfer_queue.py`）——排队/进行/暂停的任务按 `host:port` 写进设置 `sftp_pending_queue`（只存 op/name/local_path/remote_path/size 五个键，**不含任何凭据**），下次连上同一目标问一次「恢复 / 忽略」，恢复仍受并发闸门约束，退出前强制落盘；④**终端 ↔ SFTP 位置联动**（P1-2 OSC 7，只消费不注入）——`core/vt_screen.py` 解析 `OSC 7` 得到远端 cwd，终端状态条新增 cwd chip 与「在 SFTP 中打开当前位置」按钮，SFTP 面板的「在终端中打开」反向落在该目录，SSH 会话恢复时一并保存 `remote_path`。回归 `tests/test_credentials.py`（21）+ `tests/test_transfer_queue.py`（13）+ `tests/test_transfer_verify.py`（21）+ `tests/test_vt_screen.py`（50，含 OSC 7）；冒烟 `tools/smoke/smoke_remote_ssh_creds.py`（8 组）、`smoke_sftp_size_verify.py`（14）、`smoke_sftp_queue_persist.py`（24）、`smoke_ssh_cwd_chip.py`（18）。**P0-5 实测**（`tools/probe/probe_sftp_transport_reuse.py`，30 任务 / 并发 3）：每任务一条 Transport = 30 次握手认证、墙钟 1.22s，共享 Transport + N 条 channel 模型 ≈0.06s —— 结论「值得改造，但排在并发闸门之后」。设计与已知边界见 [远程会话凭据与传输可靠性.md](远程会话凭据与传输可靠性.md)。

### 售后 / 跑视频
- 售后记录**连续录入模式**（2026-09-22）：提交后弹窗保持、表单清空，连续登记不关窗。
- 售后/跑视频记录页自动刷新（开关 + 间隔在统一设置页，2026-09-16）：QTimer + 数据指纹比对，刷新保留滚动位与选中行。

### 球桌 / 运维
- 球桌管理新增 **ToDesk 远程开关列**（2026-09-20）：worker 池按设备编码并发下发开/关，权威显示口径 = `todesk_action`。
- 导航栏新增**更新状态图标**（2026-09-20）：发现新版本才显示，位于「设置」上方。
- **设备状态页文件迁移即时回显**（2026-10-04）：xqzg 源下点「操作」列迁移一条到「使用」时，InfoBar 提示成功但文件面板清单不刷新（原来 3 条还是 3 条）、表格计数要等 5-10s。两个缺陷：①`FileListPanel.refresh_if_visible` 固定查 `kd_status`，xqzg 设备取不到行（或取回同名设备的 kd 行）；②迁移成功后只等接口全量往返（翻页拉全 + 整分区 DELETE/INSERT）。修复：按 `_active_source()` 分派 `query_xqzg_by_device`/`query_kd_by_device`；迁移成功先本地即时回显（`move_file_in_row` 动面板清单与表格计数 + `apply_file_move` 异步单行落库）再 `_silent_refresh` 对账。回归 `tests/test_status_file_move.py`（17 例）。

### 界面 / 主题
- **端口微调框数字被上下箭头遮挡修复**（2026-10-04）：qfw `SpinBox` 关掉 Qt 原生按钮、把自绘上下按钮叠在右侧，**Qt 因此不给文本预留空间**，而项目里普遍 `setFixedWidth(100~150)` —— 字号一大（统一设置页可运行时 `QApplication.setFont`）数字就钻到箭头下面（用户截图：TCP 端口 "9897" 只看见一半）。新增 `core/spin_fit_patch`（`spin_width_for()` + `FittedSpinBox`：按当前字体算「文本 + 间隙 + 按钮区 71px」，并在 `FontChange` 时重算），替换 7 处多位数字微调框：远程页 TCP 端口/XTCP 本地端口/隧道配置 serverPort、工具页端口占用、`windows/tools/port_fake`、售后台账周期天数、`make_spinbox`（设置页数字，原 `width` 降为下限）。实测 16pt 下旧写法文本右缘 43px > 按钮左缘 39px（复现），新写法 51 ≤ 61。回归 `tests/test_spin_fit.py`（9 例）+ 冒烟 `tools/smoke/smoke_port_spin_fit.py`。
- **Mica 云母环境兜底**（2026-09-24）：RDP 会话 / 系统透明效果关闭（省电模式自动关）时 DWM 静默不渲染 backdrop，而 qfw 已把窗口背景置全透明——表现为"同一份产物有的电脑没云母"。启动时探测（`core/perf.py patch_mica_policy`），命中则双层短路回退纯主题色背景。判定与诊断：`mica_block_reason()`。

### 工程 / 仓库治理
- **四目录整合**（2026-09-22）：`tests/` `tools/` `design/` `docs/` 职责边界落档为 [AGENTS.md](../AGENTS.md)（硬约束）；`tools/` 按运行方式拆 `deploy/ smoke/ perf/ probe/ stress_test/ update_sim/ _scratch/`。
- 构建修复：PATH 污染导致的 SSL 库冲突（2026-09-22）；conda 构建安全防护与深色主题下售后状态显示（2026-09-21）。
- 文档树补齐（2026-09-23）：新增本文件与 [TODO.md](TODO.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[DEVELOPMENT.md](DEVELOPMENT.md)、[DEPLOYMENT.md](DEPLOYMENT.md)、[DESIGN.md](DESIGN.md)。

## 3.12（2026-09-06 ~ 2026-09-19，已落地）— FluentWindow 主窗口重构

- **主窗口重构为 FluentWindow 单窗口**（2026-09-06 起，`main_window/`）：左侧导航 工作台 / 运维管理 / 售后 / 跑视频 / 远程 / 工具，底部 设置 / 关于；原各独立面板窗口降层为「Pivot 二级导航 + 工作区」容器页（`main_window/pivot_page.py`）。
- **统一设置页**（Watt Toolkit 式：左标题 + 右 SegmentedWidget 七分页）：应用配置 │ 远程连接 │ 工具 │ 性能 │ 数据库 │ 面板设置 │ 外观；菜单栏整体移入设置页，各面板内部设置页全部迁出（`main_window/hub_pages.py`、`main_window/setting_cards.py`）。
- 大面板按 `windows/<module>/` 子包拆分 + re-export shim 兼容：`windows/aftersale/`、`windows/run_video/`、`windows/management/`、`windows/remote_session/`。
- 工具页 ToolHub 四工作区（单杆视频 / 端口占用 / 上传清单 / 视频与日志批量整理）；远程页 RemoteHub 上线（设计稿 `design/remote_page_v2.html` → v3 → v3_065）。
- Hub 可弹出为独立窗口（`main_window/hub_popout.py`，左侧子导航形态）。
- 启动闪屏 + **程序内自动更新**上线（方案 A：整包 zip + 外部 bat/vbs updater，`core/updater.py` + `tools/deploy/publish_update.py`），入口 = 关于页 + 启动自动检查。
- 默认启动页面可配置（设置 → 应用配置 → 启动，2026-09-19）。
- vue-pure-admin v2 前端工程整合入库（去嵌套 git 仓库，2026-09-19）。
- 摸鱼中心新增扫雷（含自定义难度 + 自动标雷，2026-09-20/21）。

## 3.11（2026-08-29 ~ 2026-09-07，已发布）— 售后面板 Web 化 + 性能基线

- **售后面板 Web 端上线**（2026-08-29 ~ 09-02）：FastAPI 后端 `web/aftersale_api/app.py` + Vue3/Vite v1 前端；生产部署 `http://49.235.34.253/`（入口选择页 → `/v1/`）；JWT + bcrypt 认证、WRITE 开关受环境变量控制。
- 桌面端内置本地 Web 服务 `core/local_web_server.py`（默认 `http://localhost:8787`，托管 v1 产物 + 反代云端 API）。
- **售后性能专项**（2026-09-06 ~ 09-07）：周期归属物化落库 + 覆盖索引，10 万条记录周期筛选 338.9ms → 0.14ms、内存 -74%；DB worker 查询/写槽分离 + 代数守卫修竞争条件；报告见 [售后面板性能调查报告2026-09-06.md](售后面板性能调查报告2026-09-06.md)。
- 设置页控件内联化重构（2026-09-07）；售后「记住发生日期」、自动刷新首版。
- 配置按域拆分 `config/*.json`（2026-09-06，门面 `core/app_settings.py`，敏感域 DPAPI）。

## 3.10（2026-08-18 ~ 2026-08-28，已发布）— 售后系统 + 双后端架构

- **售后管理系统**（2026-08-19 起）：填写录入 / 记录与统计 / 周期管理 / 批量操作 / 导入导出 xlsx / pygwalker 统计图表。
- **MySQL 主库 + SQLite 兜底双后端**（2026-08-18 ~ 08-23）：不可用自动降级、恢复后 LWW 合并回写（`database/backend.py`、`merge_back.py`、`fallback_backup.py`）；设计见 [MySQL兜底降级设计.md](MySQL兜底降级设计.md)。
- **DDL 单一来源治理**（2026-08-23）：`database/schema.py` 收敛 9 张表列/索引/迁移元数据，双方言 DDL 生成；镜像推送机制下线。
- 数据保留自动清理（2026-08-25，`database/data_retention.py`）：过期分区 + 按大小清理，双后端同一套。
- 售后面板独立打包（AfterSale.spec onefile，2026-08-25）。

## 3.9 及以前（2026-08-04 ~ 2026-08-18，已发布）— 远程会话与运维底座

- 远程会话中心：SSH 终端（PTY + ANSI 彩色）、SFTP 双面板文件管理、RDP 窗口嵌入、XTCP P2P visitor 注册（2026-08-04 ~ 08-18）。
- 运维管理面板：球桌管理（wechat2-billiard 接口）、设备状态（kd/xqzg 双源）、图片预览与分类迁移、设备健康度告警（2026-08-14）。
- 单杆视频生成（日志解析 + PIL 计分水印）、上传清单 SFTP 打包上传、端口占用模拟器（2026-08-04 ~ 08-14）。
- AI 智能分析取证报告（六家 OpenAI 兼容厂商，2026-08-08 ~ 08-13）。
- 主题强调色设置、日志高亮规则引擎、统一 `show_info_bar` 提示（2026-08-13 ~ 08-15）。
- 应用图标 v1（2026-08-04；2026-09-06 换 v4 定稿，见 [DESIGN.md](DESIGN.md)）。

---

## 维护约定

- 每个阶段（BASE_VERSION 提升或大特性集落地）补一节，** newest 在最上**。
- 条目写「做了什么 + 落在哪个文件/文档」，不贴 commit hash；需要溯源用
  `git log --format='%ad %s' --date=short` 自查。
- 发版动作（打更新包）记录在 [DEPLOYMENT.md](DEPLOYMENT.md) §4。
