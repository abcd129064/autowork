# xqzg.newbv.cn API 接口清单

> 抓取时间：2026-09-20 18:40 · 方式：OpenAPI schema（`/api/schema/`，drf-spectacular 生成）+ 前端 JS 提取 + 逐端点 OPTIONS/GET 验证
> 账号：`sz`（Session 登录，角色 OPERATOR / OPERATOR LEADER）
> **总计 136 个路径，19 个命名空间；其中 `snooker_om` 21 个端点**

## 0. 基础信息

| 项 | 值 |
|---|---|
| Base URL | `https://xqzg.newbv.cn` |
| 框架 | Django + DRF（OpenAPI 3.0.3） |
| Schema | `GET /api/schema/`（全量 JSON，642KB） |
| Swagger UI | `/swagger/`、`/redoc/` |
| 登录 | `POST /api/rbac/auth/login/` `{"username","password"}` → Django Session |

**认证方式（两套）**：
1. **Session 认证**：登录后带 Cookie，适用绝大多数端点（桌面端 `SnookerOmFetchWorker` 在用）。
2. **签名认证（设备→服务器）**：`X-App-Key` + `X-Sign`（MD5(images + 10位时间戳 + snooker_images + METHOD大写 + /path/)），Session 无效。实测 `version/`、`ext/` 用 Session 返回 403 "Authentication credentials were not provided"（`todesk.sh` 推送 `to_desk/status/` 用的就是这套签名）。

---

## 1. snooker_om 命名空间（21 个端点，核心业务）

### 1.1 GET 端点（10 个）

| 端点 | 说明 | 参数 | 实测 |
|---|---|---|---|
| `/api/snooker_om/status/` | 设备状态列表（桌面端主数据源） | `page`、`pagesize`、`search`、`ordering`；**`file_path`（schema 未声明但实测有效，日期过滤 `yyyy/MM/dd`，如 `2026/08/20`）** | ✅ 200，total=990 |
| `/api/snooker_om/status/export/` | 设备状态导出 | （同上，返回文件） | 未实测（会生成文件） |
| `/api/snooker_om/use/` | 连续使用 | `page`、`pagesize`、`search`、`ordering` | ✅ 200，total=982，结构与 status 同套字段 |
| `/api/snooker_om/daily_data/` | 首页数据（总览大屏） | 无 | ✅ 200：`{date, online_devices:221, device_count:891, seven_days_new_data[7], daily_pay_count, daily_pay_money, new_user_count:22, user_count:15456, daily_scan_count, total_scan_count, half_month_data[]}` |
| `/api/snooker_om/device_health/` | 健康度数据 | 无（不分页） | ✅ 200：`[{device_code, table_id, club_name, health:4086.5}, ...]` |
| `/api/snooker_om/map_view/` | 地图数据（省份统计） | 无 | ✅ 200：`{data:{province_stats:{浙江省:{club_count,installed_count,paid_count,trial_count,free_count},...}}}` |
| `/api/snooker_om/week_total/` | 局数统计 | （分页） | ✅ 200，total=892：`{club_name, local_code, device_code, table_id, region, address, total, stat_days, total_seconds, duration_text, avg_seconds, avg_duration_text}` |
| `/api/snooker_om/week_total/export/` | 对局时间以及明细 | （返回文件） | 未实测 |
| `/api/snooker_om/wechat_mini/` | （微信小程序相关，schema 无描述） | 必填参数未知 | ⚠️ 500 `int(None)`——缺参数时后端未做校验 |
| `/api/snooker_om/ext/` | 拉取球桌扩展信息 | 无 | 🔒 403 需**签名认证**（Session 无效） |

### 1.2 POST 端点（11 个）

| 端点 | 说明 | 请求体 | 实测/用途 |
|---|---|---|---|
| `/api/snooker_om/migrate_image/` | 迁移对局文件 | multipart form：`src_path`、`dest_path`、`file_name`（如 `media/2026/08/20/<设备码>/except/` → `.../pending/`） | 桌面端在用 ✅ |
| `/api/snooker_om/update_health/` | 修改健康度 | | 桌面端在用 ✅ |
| `/api/snooker_om/upload_image/` | 上传比赛截图 | JSON（全必填）：`{id:int, device_code, file_name, path, image, upload_time, description}` | |
| `/api/snooker_om/device_photo/` | 照片上传 | | |
| `/api/snooker_om/camera_ip/` | 相机 IP 上报 | | |
| `/api/snooker_om/frp/status/` | frp 开关状态上报（写 Redis） | `datacode` + `action`（**30=开启 / 70=关闭**）+ `status`（true/false，失败不改状态） | 设备端上报 |
| `/api/snooker_om/to_desk/status/` | todesk 开关状态上报（写 Redis） | `datacode` + `action`（**20=开启 / 80=关闭**）+ `status`（true/false） | `todesk.sh` 在用（签名认证）✅ |
| `/api/snooker_om/value/` | 控制指令下发（**开/关 todesk、frp、重启、更新提醒唯一入口**） | `datacode`（设备码）、`datatype`、`datavalue`（指令码 10/90/20/80/30/70） | 见 1.3 控制链路 |
| `/api/snooker_om/version/` | 压缩包版本号 | | 🔒 403 需**签名认证** |
| `/api/snooker_om/videos/query/` | 日期 + 设备号，从 Redis 取视频路径 | | |
| `/api/snooker_om/room_sales_transfer/` | 销售交接（按球房号） | | |

### 1.3 todesk / frp 远程控制链路（2026-09-20 补充深挖）

**不是每开关一对独立 POST——开/关指令全部走同一个端点，用指令码区分**：

```
┌─ 下发（Session+CSRF）─┐   ┌────── 设备执行 ──────┐   ┌─ 结果上报（签名认证）──────┐
│ POST /value/          │ → │ 设备读到待执行指令后  │ → │ POST /to_desk/status/     │
│ {datacode,datatype,   │   │ 开/关 todesk 或 frp  │   │ POST /frp/status/         │
│  datavalue}           │   └──────────────────────┘   │ {datacode,action,status}  │
└───────────────────────┘                               └───────────────────────────┘
```

| 环节 | 端点 | 参数 | 认证 |
|---|---|---|---|
| **指令下发**（唯一入口） | `POST /api/snooker_om/value/` | 必填 `datacode`（设备码）、`datatype`、`datavalue`（指令码：**10=更新提醒 / 90=重启 / 20=开 todesk / 80=关 todesk / 30=开 frp / 70=关 frp**）。空 POST 实测返回 `{"code":400,"msg":"缺少参数：datacode、datatype、datavalue 不能为空"}` | Session + CSRF（需 `X-CSRFToken` + `Referer: https://xqzg.newbv.cn/`） |
| **todesk 状态上报** | `POST /api/snooker_om/to_desk/status/` | `datacode` + `action`（**20=开启 / 80=关闭**）+ `status`（true=成功 / false=失败不改状态）→ 写 Redis | 签名（X-App-Key/X-Sign，Session 无效 403；`todesk.sh` 在用） |
| **frp 状态上报** | `POST /api/snooker_om/frp/status/` | `datacode` + `action`（**30=开启 / 70=关闭**）+ `status`（true/false）→ 写 Redis | 同上签名 |
| **状态/号码读取** | `GET /api/snooker_om/status/` | 每行含 7 个控制链路字段（见下） | Session |

**`status/` 行内控制链路字段**（实测样本，null=设备未上报）：

| 字段 | 含义 |
|---|---|
| `todesk_action` | 下发中的 todesk 指令（待执行） |
| `todesk_status` | todesk 实际运行状态 |
| `todesk_id` | **todesk 号**（ToDesk 识别码；`remark` 字段另有文本记录，如「我的识别码:370085625」） |
| `frp_action` | 下发中的 frp 指令 |
| `frp_status` | frp 实际运行状态 |
| `version` | 压缩包版本号（对应 `version/` 上报） |
| `camera_ip` | 相机 IP（对应 `camera_ip/` 上报） |

指令码在 `value/`（下发）与 `to_desk/status/`、`frp/status/`（上报）之间**对称复用**：20/80 既是 todesk 的开/关指令也是上报的 action；30/70 同理对应 frp。

前端 Web 控制台 JS 完全不调用这些端点（0 次出现）——控制链路属桌面端/设备侧专用。

### 1.4 发现的接口侧问题

1. **`wechat_mini/` 缺参数直接 500**：`int() argument must be ... not 'NoneType'`，后端未校验必填参数。
2. **`daily_data/` 的 `seven_days_new_data` 七条记录 date 全是同一天**（均为 `2026-09-20`），疑似后端聚合 bug。
3. **`status/` 的 `target_directory` 部分设备无日期段**：如 `7YKXRT2CNPE1008BS0ILI` 返回 `/opt/rbac-SnookerOm/backend/media//7YKXRT2CNPE1008BS0ILI`（空日期），而 `use/` 里 `393WJ13CNPE10099M0E78` 返回带日期但用**反斜杠** `2026\09\20`——这正是旧代码误判「xqzg 无日期分区」的来源（8 月 22 日已按 file_path 参数修正对接）。

---

## 2. 其余 18 个命名空间（115 个路径，概览）

| 命名空间 | 路径数 | 主要端点 | 说明 |
|---|---|---|---|
| `rbac` | 24 | `auth/login·logout·user-info·change-password·check-permission`、`menus`、`roles`、`users`、`permissions`、`organizations(/tree)`、`user-roles`、`user-organizations`、`dashboard`、`system/metrics` | 权限/组织/菜单 |
| `work_order` | 16 | `install_order`（CRUD + `approve/complete/reject/submit_finance/photos` + `storage/sales_customers/status_summary/external_material_stock`）、`knowledge_base`、`receipt`（+ `sign/transfer`） | 工单（安装单全流程） |
| `warehouse` | 12 | `warehouse`、`material`、`stock_balance`、`stock_transaction`（`purchase-in/return-in/aftersale-out/aftersale-return/scrap-out/transfer`） | 仓库/库存（含售后出入库） |
| `chat` | 6 | `messages`（CRUD + `conversations/with_user/users/mark_read/mark_all_read`） | 站内聊天 |
| `customer` | 5 | `customer`（CRUD + `region-options` + `abandon/claim` + `customer_excel`） | 客户管理 |
| `shop` | 5 | `orders`（CRUD + `query/{order_no}`）、`products` | 商城 |
| `office` | 4 | `documents`（CRUD + `upload` + `toggle_pin`）、`attachments/{id}/download` | 公文/文档 |
| `notification` | 4 | `messages`（`read_all/unread_count/{id}/read`） | 通知 |
| `visit` | 4 | `visit-record`（CRUD）+ `ocr` + `export` | 拜访记录 |
| `market` | 4 | `market_view(+export)`、`performance`(CRUD)、`work` | 市场绩效 |
| `system` | 5 | `settings`（CRUD + `bulk_update/by_category/get_by_key`） | 系统设置 |
| `audit` | 2 | `logs`、`login-logs` | 审计日志 |
| `report` | 3 | `excel_reconcile`、`payment_statistics`、`yearly_table_stats` | 报表 |
| `tasks` | 3 | `tasks`（CRUD + `run_now`） | 定时任务 |
| `game_time` | 2 | `create`、`get` | 对局时间 |
| `codegen` | 2 | `ai-schema`、`generate` | 代码生成（开发工具） |
| `curd` | 2 | `examples` | CURD 示例 |
| `common` | 1 | `upload` | 通用上传 |

> 各命名空间的 router 根（如 `GET /api/rbac/`）返回该空间全部子路由的 URL 清单，可用于快速枚举。

---

## 3. 与桌面端 AutoWork 的对接对照

| 端点 | 桌面端状态 |
|---|---|
| `status/` | ✅ 已对接（`SnookerOmFetchWorker`，含 `file_path` 日期分区） |
| `migrate_image/` | ✅ 已对接（`MigrateImageWorker`） |
| `update_health/` | ✅ 已对接（`UpdateHealthWorker`） |
| `to_desk/status/` | ✅ 已对接（生产机 `todesk.sh`，签名认证） |
| `use/` `daily_data/` `device_health/` `week_total/` `map_view/` `videos/query/` `upload_image/` `camera_ip/` `frp/status/` `value/` `room_sales_transfer/` `status/export/` `week_total/export/` | ❌ 未对接（潜在可用能力） |
| `ext/` `version/` | ❌ 未对接（需签名认证，属设备侧接口） |

---

## 4. 复现方式

```bash
# 全量探测（只读，约 1 分钟）
python tools/probe_xqzg_api.py        # 登录 + 根路由 + OPTIONS + 前端 JS 提取 + 字典探测
python tools/probe_xqzg_schema.py     # OpenAPI schema 全量 136 路径 + 命名空间根
python tools/probe_xqzg_detail.py     # snooker_om 参数详情 + GET 端点采样
```

凭据来自 `config/credentials.json` → `api_credentials.api1`（DPAPI 自动解密）。原始探测结果存于 `logs/xqzg_api_probe_result.json`。
