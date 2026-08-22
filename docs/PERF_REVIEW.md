# 性能审查报告（AutoWork）

审查范围：`database/`（双后端数据层）、`workers/`（异步任务）、`windows/`（UI）的热点路径。  
结论：SQLite 模式下整体合理；**问题高度集中在 MySQL 模式的「每次调用都付出固定开销」**，以及少量批量/压缩类 CPU 浪费。

---

## 🔴 高优先级（每次调用都产生可测量开销）

### 1. MySQL 模式下 `executemany` 被改写成逐行 Python 循环

**位置**：`database/backend.py:224-234`（`MysqlConnectionAdapter.executemany`）

```python
def executemany(self, sql, seq_params):
    cur = self._conn.cursor()
    for p in seq_params:
        cur.execute(sql, p)   # 每一行一次网络往返
        ...
```

而 `create_mysql_connection` 用的是 `autocommit=True`（`backend.py:295`）。  
后果：`save_all` / `save_kd` / `save_xqzg` 的批量写入，在 MySQL 模式下变成 **N 行 = N 次网络往返 + N 次提交**。几千行球桌数据刷新会变成几千次往返，耗时从毫秒级退化到秒甚至分钟级。

**修复**：

- 直接委托给 pymysql 原生 `cursor.executemany(...)`（pymysql 内部走批量协议，比手搓循环快很多）；
- 且把批量写包裹在显式事务里：进入批量前 `conn.autocommit(False)` + 循环 `cur.execute` + 末尾 `conn.commit()`，写完再恢复。这样无论用不用原生 executemany，都从「每行一提交」降为「整批一提交」。

### 2. `_get_conn` 每次调用都对 MySQL 发 `ping`

**位置**：`database/table_db.py:480`

```python
conn._conn.ping(reconnect=True)
return conn
```

`ping()` 在每次 DB 操作（每个 `query_page`、每次 `insert`、每次搜索）前都向服务端发一个 COM_PING 包。  
后果：MySQL 模式下，**每一个 SQL 都额外多一次网络往返**。高延迟链路下搜索/翻页都会明显变慢。

**修复**：不要每次都 ping。只在以下时机检查连接：

- 连接首次建立后；
- 捕获到 `pymysql.OperationalError`（连接断开）时再 `reconnect()`。  
  可加一个 `_last_ping_ts`，每隔数秒才主动探活一次，或干脆依赖异常兜底重建。

### 3. `is_mysql_test_mode()` 每次调用都重新读文件 + DPAPI 解密 settings.json

**位置**：`database/backend.py:23-25` → `_load_mysql_settings():28-48`

`_get_conn()` 第一行就调用 `is_mysql_test_mode()`，而它每次都会：

1. `open(settings.json)` 读盘；
2. `json.load` 解析；
3. `decrypt_settings`（DPAPI 解密敏感字段）。

后果：**每次 DB 操作都触发一次文件读 + JSON 解析 + DPAPI 解密**。这是隐藏在热路径里、且与数据量无关的固定成本，在高频搜索/翻页时持续叠加。

**修复**：把 `enabled` 开关与解密后的 mysql 配置在内存缓存一份，`settings.json` 被改动时（已有的 `perf.invalidate_cache` 思路）再失效重载。注意区分「数据库配置」与 `perf` 配置，单独加一个缓存标志即可。

---

## 🟡 中优先级

### 4. ~~`sync_health_alerts`（SQLite 路径）逐行 SELECT + INSERT/UPDATE~~ ✅ 已修复（2026-08-23）

**位置**：`database/table_db.py`（`sync_health_alerts` / `_sync_health_alerts_mysql`）

已实施：
- 提取 `_filter_alert_items` 统一两条路径的过滤规则（同名设备多条以最后一条为准）；
- SQLite：一次 `SELECT name, resolved_health` 取回全部已处理标记，Python 侧分三组（新增 / 更新保持 / 更新清标记），`executemany` 批量写，N+1 → 1 次读 + 最多 3 次批量写；
- MySQL：过滤后收集参数列表，一次 `executemany` 批量提交（保留 `ON DUPLICATE KEY UPDATE` 原子语义，多用户并发安全不变）；
- 语义不变：过滤规则、已处理标记保留/清除、消失设备清理、返回未处理条数；
- 回归测试：`tests/test_health_alerts_sync.py`（8 用例，全套 84 passed）。

### 5. `upsert_kd` 逐行先 UPDATE 再判断 rowcount

**位置**：`database/table_db.py:1211-1219`

```python
cur = conn.execute("UPDATE kd_status SET ... WHERE file_path=? AND device_code=?", ...)
if cur.rowcount: ...
else: inserts.append(...)
```

每条记录一次 `UPDATE` 网络往返（MySQL 模式下），命中率低时几乎全是空 UPDATE。  
**修复**：直接用 `INSERT ... ON DUPLICATE KEY UPDATE`（或 SQLite `ON CONFLICT`）单语句 upsert，`executemany` 批量执行，避免先 UPDATE 试探。

### 6. `ZipUploadWorker` 对所有文件用 `ZIP_DEFLATED`

**位置**：`workers/collect_worker.py:341`

```python
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
```

上传目录主要是 `.mp4`/`.log` 等**已压缩**内容，deflate 几乎压不下来，却要白白吃掉大量 CPU，大目录打包会明显卡顿。

**修复**：对视频/已压缩扩展名改用 `ZIP_STORED`；或按扩展名分两类——媒体走 STORED，文本/未压缩走 DEFLATED。`ZipFile.writestr` + 每文件 `compress_type` 即可控制。

### 7. 分页查询每页都跑 `COUNT(*)` + 数据查询两条 SQL

**位置**：`database/table_db.py:757-764`（`query_page`）、`:1877-1886`（`_query_status_page`）

每次搜索（防抖逐字触发）都先 `COUNT(*)` 全量过滤集再 `LIMIT/OFFSET` 取页。过滤条件多 / 表大时 `COUNT` 是全扫描。  
**优化**：`total` 仅在翻页/筛选条件变化时重算，搜索中间态可复用上一次 total；或用 `EXPLAIN`/近似行数。对当前分页规模属「可接受但可优化」。

---

## 🟢 低优先级 / 细节

- **`save_all` 每次全表 `SELECT name, snk_code`**：`table_db.py:680` 为保留手动 snk，每次同步整表扫一遍。可只取 `snk_code=''` 的行或首次缓存。
- **`_convert_sql` 每条 SQL 跑 6 次正则**：`backend.py:151-159`。短串正则开销极小，但高频查询下可预编译正则 / 按 SQL 类型跳过无关转换。
- **`find_kd_file_status` 8 列 LIKE on LONGTEXT**：`table_db.py:1404`。已被 `device_code+file_path` 索引收窄，但 8 个 JSON 列 LIKE 仍偏重；必要时可加生成列+索引。
- **UI 表格逐格 `setItem`**：`management_panel.py:1288-1308` 等。受分页大小约束，QTableWidget 可接受；若未来页大小放大，建议换 `QTableView` + model。

---

## ✅ 已有的好实践（保持）

- 重活（视频生成、整理、SFTP 上传、数据库同步）全部在 `QThread` worker 中跑，不阻塞 UI。
- SQLite 单连接 + WAL + `busy_timeout`，多 worker 并发读写安全。
- 列表页只查轻量字段，文件 JSON 按 id 懒加载（`get_kd_row_full`），避免每页反序列化大 JSON。
- FTS5 trigram 索引 + 短关键词回退 LIKE，并有 `fts_built` 标记避免重复 rebuild。
- 批量打包/上传循环有 `isInterruptionRequested()` 取消检查。

---

## 建议的修复顺序

1. 先做 **#3（缓存 mysql 配置）** 和 **#2（去掉每次 ping）**——纯热路径无风险收益，改动小。
2. 再做 **#1（executemany 批量 + 显式事务）**——MySQL 批量写入性能质变。
3. 然后 **#6（zip 压缩分级）**——上传体验直接改善。
4. 最后优化 **#4 / #5** 的批量写入（SQLite+MySQL 双路径都受益）。
