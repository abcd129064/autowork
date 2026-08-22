# MySQL 主 + SQLite 兜底（自动降级 + 自动合并）设计方案

> 目标：MySQL 为唯一主库；MySQL 不可用时应用**自动透明回退**本地 SQLite 继续可用；
> MySQL 恢复后**自动把兜底期间 SQLite 的增量合并回 MySQL**。
> 放弃此前 P1 的「SQLite 主 + 镜像推 MySQL」方向——那是相反的数据流。

---

## 0. 当前差距（P0 修复后）

- `enabled=true` 时 `_get_conn()` 直连 MySQL，**MySQL 连接失败直接抛异常**，操作失败，无回退。
- 主模式下 SQLite 停在切换前陈旧快照、不被写入 → 当前根本没有"兜底"能力。
- `push_*` 已被 P0 守卫拦住（主模式不推送）→ 合并需新写「按主键/业务键 upsert、不 TRUNCATE」的回写路径。

## 1. 后端状态机（新增，`backend.py`）

```
        ┌─────────┐  MySQL 连接失败   ┌──────────┐
        │ ONLINE  │ ───────────────▶ │ DEGRADED │
        │ (MySQL) │ ◀─────────────── │ (SQLite) │
        └─────────┘  探测恢复+合并回写 └──────────┘
```

- 状态保存在模块级（`_state`，初值 ONLINE），线程读写加锁。
- `get_state()` / `mark_degraded()` / `mark_online()`。
- **探测方式（无独立线程）**：`_get_conn()` 在 DEGRADED 时先尝试建一次 MySQL 连接——成功则 `mark_online()` 并发出"恢复"事件（触发合并），失败则继续返回 SQLite。ONLINE 时正常走 MySQL，连接失败则 `mark_degraded()` 并返回 SQLite。这样每次操作自带探测，无需后台定时器，开销可控（仅 DEGRADED 期间多一次失败连接尝试）。

## 2. `_get_conn()` 改造（`table_db.py:412`）

```python
def _get_conn():
    if backend.is_mysql_test_mode():
        if backend.get_state() == backend.STATE_ONLINE:
            try:
                return _get_or_create_mysql_conn()   # 含 ping(reconnect)
            except Exception as e:
                backend.mark_degraded()
                conn_logger.error(...)               # 记降级
                # 落到 SQLite 兜底
        else:  # DEGRADED：试探恢复
            if _try_resume_mysql():                  # 建一次连接成功
                backend.mark_online()
                _trigger_merge_back()                # 触发合并（异步 worker）
                return _get_or_create_mysql_conn()
            # 仍不可用 → SQLite
        return _get_sqlite_conn()                    # 兜底
    return _get_sqlite_conn()
```

- 关键：MySQL 失败**不再抛异常给业务**，而是降级到 SQLite，操作继续。
- `_get_sqlite_conn()` 复用现有 SQLite 单连接逻辑（含 WAL/busy_timeout）。
- 降级/恢复通过 `core.utils.show_info_bar` 提示用户（worker 线程需用 `QMetaObject` 或信号转发到主线程，避免跨线程 UI）。

## 3. 兜底期间的数据落点

- 降级期间所有 `table_db`/`aftersale_db` 读写经 `_get_conn()` 走 SQLite。
- **起点数据**：降级瞬间 SQLite 仍是陈旧快照。已用「周备份」解决——`database/fallback_backup.py` 每周把 MySQL 全量拉到本地 SQLite（DELETE + INSERT OR REPLACE，按列名取交集防御），作兜底基线（≤7 天新鲜度）。仅 ONLINE 时执行；降级期间不备份（避免覆盖兜底增量）。✅ 模块已实施；启动/定时集成待接（调 `maybe_backup()`）。与阶段二 LWW 合并互补：周备份保基线新鲜，合并保兜底增量不丢。

## 4. 自动合并（恢复后，SQLite 增量 → MySQL）

恢复触发：`_get_conn()` 探测 MySQL 成功 → `mark_online()` → 启动 `MergeBackWorker`。

合并逻辑（新写，**不走 `push_all`/TRUNCATE**，改用安全 upsert）：

| 表 | 主键/业务键 | 合并 SQL 形态 |
|---|---|---|
| `billiard_tables` | `id` | `INSERT ... ON DUPLICATE KEY UPDATE`（按冲突策略决定是否更新） |
| `xqzg_status` / `kd_status` | `id` | 同上 |
| `device_mapping` | `device_code` | 同上 |
| `submission_log` | `id`（AUTO_INCREMENT，两侧撞车） | 按 `(device_code, created_at, file_name)` 业务键 INSERT IGNORE |
| `aftersale_records` | `(created_at, creator, table_no, problem)` | 复用 `push_aftersale` 语义但**绕过 P0 守卫**（合并是显式回写，非镜像推送） |

合并完成后清空/标记 SQLite 兜底增量（可选：保留作快照）。

## 5. 合并冲突策略（✅ 已定：C 时间戳 LWW）

> 用户已选 **C. 时间戳 last-write-wins**。约束：当前仅 `device_mapping` 有 `updated_at`；
> `aftersale_records`/运维表均无。阶段二需给 `aftersale_records` 补 `updated_at` 列并在
> insert/update 时维护（多用户共享表，LWW 才能避免兜底覆盖他人）；运维表（API 快照）无
> 时间戳则退化为 SQLite 优先（最新 API 覆盖，可接受）。

当 SQLite 兜底行的主键/业务键**已存在于 MySQL** 时（说明兜底期间 MySQL 也被他人写过，或该行是降级前就有、兜底期间被本地修改）：

| 选项 | 行为 | 代价 |
|---|---|---|
| **A. MySQL 优先，仅补新增**（推荐） | MySQL 已有的行**不覆盖**，只 INSERT MySQL 不存在的新行 | 兜底期间对**已有**记录的修改/删除不会回写 MySQL（需事后人工补） |
| **B. SQLite 优先，兜底覆盖** | 兜底期间所有写入（含修改）覆盖 MySQL 对应行 | 可能覆盖他人在 MySQL 的并发修改 |
| **C. 按时间戳 last-write-wins** | 有 `updated_at` 的表按时间戳新旧定胜负；无时间戳表退化为 B | 需表支持 `updated_at`（当前多数运维表无此列） |

推荐 A：兜底是异常态，「不丢他人数据」优先于「兜底修改全部生效」；兜底期间的少量修改可事后人工补，可逆。B 不可逆（覆盖他人），C 受限于表结构。

## 6. UI 反馈与风险

- 降级瞬间：InfoBar 警告「MySQL 不可用，已切换本地兜底」。
- 恢复瞬间：InfoBar 成功「MySQL 已恢复，正在合并兜底数据…」→ 合并完成提示条数。
- 风险：
  - 兜底期间读 SQLite = 陈旧数据（若无第 3 节回填）。需向用户明示"兜底模式数据可能非最新"。
  - 合并 worker 与正常写 MySQL 的并发：合并用单独连接 + autocommit，按行 upsert，冲突由策略决定。
  - `device_mapping`/运维表无 `updated_at` → 策略 C 不可用，退化为 B。
  - 删除语义：兜底期间本地删除的行，合并时**不反向删除** MySQL（单向 upsert 语义），与现有 `push_aftersale` 一致。

## 7. 分阶段实施计划

1. **阶段一 ✅ 已实施（2026-08-22）**：`backend.py` 状态机（STATE_ONLINE/DEGRADED + `get_state`/`mark_degraded`/`mark_online` + 日志）+ `table_db._get_conn` 降级回退（ONLINE 失败→mark_degraded→回退 SQLite；DEGRADED 操作前试探恢复→mark_online+`_trigger_merge_back` hook）+ `_get_sqlite_conn` 抽出 + `_log_degraded`。测试 `tests/test_backend_fallback.py`（4 用例：ONLINE 失败降级 / DEGRADED 恢复 / 仍不可用 / 非 MySQL 模式），全套 **45 passed**。UI 提示与阶段二合并待续。
2. **阶段二 ✅ 已实施（2026-08-22）**：`MergeBackWorker` + 各表安全 upsert 合并 + aftersale `updated_at` 列 + 单测。
   - `database/merge_back.py`：`merge_back()` 入口 + `merge_aftersale`（业务键匹配 + updated_at LWW：MySQL 不存在→INSERT，MySQL 较旧→UPDATE，MySQL 较新→跳过）/ `merge_device_mapping`（device_code 主键 LWW）/ `merge_ops_tables`（运维表无 updated_at 退化 SQLite 优先；submission_log INSERT IGNORE）。
   - `aftersale_records` 补 `updated_at` 列：SQLite DDL + `_ensure_initialized` 自动迁移 + MySQL `backend.MYSQL_DDL` + `mysql_sync` DDL + ALTER 补列；`insert_record`/`update_record` 写入时维护；`RECORD_FIELDS` 含 `updated_at`。
   - `workers/merge_back_worker.py`：`MergeBackWorker(QThread)` 异步合并；`result` 信号经 `QApplication.topLevelWidgets()` 找 MainWindow 弹 InfoBar，找不到降级 conn_logger。
   - `table_db._trigger_merge_back`：从占位接到真实 `MergeBackWorker` 启动；`_merge_back_workers` set 保活防 GC。
   - 测试 `tests/test_merge_back.py`（5 用例）。全套 **64 passed**。
3. **周备份（兜底基线刷新）✅ 模块+启动集成已实施（2026-08-22）**：`database/fallback_backup.py`（`backup_mysql_to_sqlite` / `is_backup_due` / `maybe_backup` / `get`-`set_last_backup_time`），7 天到期判定 + last_backup 持久化到 sync_meta。`workers/backup_worker.py`（`BackupWorker` 异步包装 `maybe_backup`）。`main_window` 启动延迟 5s 首检 + 24h QTimer 重复 + 后端状态 3s 轮询（降级/恢复 InfoBar 提示）；仅 MySQL 主模式触发。全套 **55 passed**。

> 阶段一与阶段二耦合点：恢复探测成功后是否立即触发合并。建议阶段一先把"恢复探测 + mark_online"做掉，合并触发点留 hook，阶段二接上 worker。可分两次交付、各自验证。
