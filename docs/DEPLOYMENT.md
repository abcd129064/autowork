# DEPLOYMENT — 构建、分发与生产部署

> 本文覆盖三类分发物：**桌面端更新包**（程序内自动更新）、**售后面板 Web**
> （nginx + uvicorn，生产 49.235.34.253）、**离线安装包**（onedir/onefile 手工分发）。
> 发版前的构建与仿真步骤见 [DEVELOPMENT.md](DEVELOPMENT.md) §5。
> ⚠️ `tools/deploy/` 下脚本会改动生产服务器：执行前必须向用户确认目标、
> 范围、回滚方式；密码只从环境变量 `AFT_SSH_PASS` 读取（AGENTS.md §3.4）。

---

## 1. 生产环境一览

| 角色 | 位置 | 说明 |
| --- | --- | --- |
| 生产服务器 | 49.235.34.253（SSH 端口 19955） | nginx + uvicorn + MySQL + 更新源静态目录 |
| Web 入口 | `http://49.235.34.253/` | 入口选择页 → `/v1/` 老系统、`/v2/` 新系统 |
| Web 后端 | `aftersale-web.service`（uvicorn，`/opt/aftersale-web/`） | FastAPI `web/aftersale_api/app.py`；`.env` 控 AUTH/WRITE 开关 |
| 前端产物根 | `/opt/aftersale-web/dist`（含 `v1/` `v2/` 子目录与入口 index.html） | nginx 既有 `try_files $uri $uri/ /index.html` 直接服务子目录 SPA，**零 nginx 改动** |
| MySQL | 生产机本地 | 桌面端与 Web 端同库；Web 用受限账号 `aftersale_ro`（GRANT SELECT/INSERT/UPDATE/DELETE ON autowork.aftersale_records） |
| 更新源 | `http://49.235.34.253/update` | nginx 静态目录；`latest.json` + 版本 zip（保留 3 版） |
| 桌面端本地 Web | `http://localhost:8787`（`core/local_web_server`） | 托管 v1 产物 + `/api/*` 反代云端，与线上同数据源 |

## 2. 桌面端分发（离线安装包）

```bash
python build_exe.py
```

产出两个**相互独立**的分发物：

1. **完整版** `dist/AutoWork/`（onedir，`autowork.exe`）：内置售后/运维/远程/工具全部面板。
2. **单文件售后面板** `dist/aftersale.exe`（onefile）：仅售后面板，数据落 exe 旁 `database/tables.db`。

构建脚本自动复制 `config/` 到产物旁（旧 `settings.json` 一并复制作迁移源）、
复制 `frpc.exe` 到完整版目录。分发 = 整目录拷贝，无安装器。

## 3. 售后面板 Web 部署（v1/v2 并行）

```bash
# 只读侦察（先看现状再动写操作）
AFT_SSH_PASS='...' python tools/deploy/prod_ssh.py '<command>'

# v1 产物整包
AFT_SSH_PASS='...' python tools/deploy/upload_aftersale_dist.py
# v1+v2 并行布局整包（幂等；不认 --help，传参即执行）
AFT_SSH_PASS='...' python tools/deploy/deploy_parallel_v1v2.py
# 后端 app.py + 重启 aftersale-web.service
AFT_SSH_PASS='...' python tools/deploy/deploy_aftersale_api.py
```

要点与已知坑：

- v2 构建三件套：`VITE_PUBLIC_PATH` + logo + 页面 Title；`vite outDir` 需预建空目录；
  `welcome/index.vue` 用 JSX 必须 `lang="tsx"`；useECharts `renderer:"svg"`（断言查 `.chart-box svg`）。
- 后端 **pymysql 必须 `autocommit=True`**（否则 INSERT 静默回滚）；列默认空串 `''`，修数据禁 COALESCE。
- 登录 JWT 12h，token 存 cookie `authorized-token`；users.json = {admin: bcrypt}，chmod 600。
- nginx SPA 回退 200 + text/html → 客户端做双内容类型守卫（避免把 index.html 当 JSON 解析）。
- 验证脚本：`web/aftersale_front/tools/verify_*.mjs`（产物预检/本地仿真/线上巡检）、
  `web/vue-pure-admin/tools/verify_pure_admin.mjs`；Playwright executablePath 指本地 Chrome。

## 4. 程序内自动更新（方案 A：整包 zip + 外部 bat/vbs updater）

链路（SOP 见 [auto_update_research.md](auto_update_research.md) §7.4）：

```
检查更新(core/updater.py, 关于页/启动自动检查)
  → 下载 zip + sha256 校验 → staging 解压
  → launch_updater（bat/vbs）→ 主程序退出
  → updater 等旧 PID 退出 → 三向原子换位（旧包/新包/备份）→ 用户数据回迁
  → 重启主程序 → 回执消费(update_result.log，防死循环，绝不自动重试)
```

发布：

```bash
AFT_SSH_PASS='...' python tools/deploy/publish_update.py
# latest.json 走 .tmp + mv 原子切换；服务器保留最近 3 版
```

- 更新源地址 = 配置 `update_base_url`（misc 域），默认 `http://49.235.34.253/update`；
  自动检查开关 `update_auto_check`。
- 三坑：vbs 引号必须 `Chr(34)` 拼接；bat 内外部命令必须 `%SystemRoot%\System32\`
  绝对路径 + 用 ping 代替 timeout；首次发布前必须重新 `build_exe.py`（updater 随包）。
- 本地仿真：`tools/update_sim/sim_*.py`（e2e / vbs 启动器 / 加固 / 生产源仿真）。

## 5. 回滚

| 分发物 | 回滚方式 |
| --- | --- |
| 桌面更新包 | updater 三向换位自带备份目录；客户端不自动重试，失败留 `update_result.log` 人工介入 |
| Web 前端 | 部署脚本整包覆盖式，回滚 = 用上一版 dist 重跑对应 deploy 脚本 |
| Web 后端 | `deploy_aftersale_api.py` 覆盖 `app.py` 后重启 service；回滚 = 重跑上一版 |
| 数据库 | 表结构只增列不删列（schema.py 迁移单向）；数据回滚走 MySQL 备份，不在本链路内 |

## 6. 相关运维（生产机旁路）

- ToDesk 状态上报脚本 `todesk.sh` + `todeskd.service`（root + `Restart=on-failure`；
  GUI 仅 SIGKILL 可停；sudoers 仅授 `systemctl start/stop todeskd.service` NOPASSWD）。
- frp 服务端/穿透链路与开机预连见 [frp-source-integration.md](frp-source-integration.md)。
