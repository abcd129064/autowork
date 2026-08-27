# 售后系统规模化压力测试套件

针对售后系统的规模化压力测试：**内存级模拟数据**（不新建物理库），覆盖
**售后工单 / 运维面板 / 视频业务** 三类场景，输出**响应时间分位数、QPS/TPS、
RSS/CPU 占用与资源趋势**，并内置**内存护栏**防止 1GB 受限服务器 OOM。

## 一、数据生成逻辑（内存级，不落盘）

| 规模 | 记录数 | 说明 |
|---|---|---|
| `10k` | 10,000 | 快速验证 / CI 冒烟 |
| `50k` | 50,000 | 中等规模回归 |
| `100k` | 100,000 | 规模化瓶颈暴露 |

- 数据由**生成器逐条产出**（`data_gen.iter_aftersale_records`），**绝不一次性
  构造 10 万条列表**，峰值内存 ≈ 单批(5000 条) + 数据库本身；
- 装载进 **`sqlite3 :memory:`**（`data_gen.build_memory_db`），表结构复用
  `database/schema.py` 的 DDL（与生产 `aftersale_records` 字段/索引完全一致）；
  关键 PRAGMA：`journal_mode=MEMORY`、`synchronous=OFF`、`temp_store=MEMORY`
  （大 GROUP BY/ORDER BY 临时表也走内存，避免受限环境 "unable to open database
  file"）；
- 运维面板用 `data_gen.build_ops_dataset` 造 kd_status 口径大表（含 file_path
  索引），视频负载用 `make_video_bytes` 生成**可压缩内存字节流**（不生成真实
  大视频文件，临时小视频用完即删）；
- 实测装载耗时：10k~0.13s / 50k~0.96s / 100k~1.9s；内存库常驻估算
  `estimate_memory_mb(n) ~ n×0.7/1024 MB`。

## 二、测试执行流程

```
预检(内存/磁盘自检 + 规模内存估算)
  → 逐规模：装载内存数据(分批+批间 gc)
  → 逐场景执行（ResourceSampler 后台采样 RSS/CPU，超 rss-limit 即中止并标记）
  → 场景间 metrics.recycle() 强制回收
  → 汇总：report.save() 输出 JSON + Markdown 到 results/
```

运行命令：

```bash
# 单规模快速验证（推荐先跑）
python tools/stress_test/run_stress.py --scale 10k

# 全量三档 × 全场景
python tools/stress_test/run_stress.py --scale 10k,50k,100k --scenarios aftersale,ops,video --repeat 30

# 只做数据装载与资源预检（不跑场景）
python tools/stress_test/run_stress.py --scale 100k --dry-run

# 指定场景 / 收紧内存护栏
python tools/stress_test/run_stress.py --scale 50k --scenarios aftersale --rss-limit 400
```

参数：`--scale`（10k/50k/100k，逗号分隔）、`--scenarios`（aftersale/ops/video）、
`--repeat`（每项操作次数）、`--rss-limit`（内存护栏 MB，默认 500）、`--out`（报告标签）、
`--dry-run`。

> Windows 下建议用托管 venv + 纯净 PATH 运行（PySide6 DLL 冲突处理，见
> 项目 memory「环境坑」）：`env -i PATH=<venv PySide6 目录>:System32 python ...`
> 或直接 `QT_QPA_PLATFORM=offscreen` 后正常启动（本机 PySide6 可加载时）。

## 三、场景与指标

### 场景 1：售后工单（scenarios/aftersale.py）

跑在**真实业务函数**上（`database/aftersale_db` 的 insert_record / query_page /
query_with_stats / mark_resolved_batch），仅把 `_conn()` 注入内存库：

| 操作 | 说明 |
|---|---|
| `create` | 单条工单创建（TPS） |
| `query_all` / `query_keyword` / `query_type` / `query_midpage` | 四种口径分页查询（响应分位数 + QPS） |
| `query_with_stats` | 列表 + 指标卡统计（两次全表 + Python 聚合） |
| `batch_resolve_N` | 批量标记已解决（50/200/1000 条） |

**已知风险（压测目的之一）**：`query_page` **无 SQL LIMIT**——全量取回后
Python 侧周期过滤 + 切片。实测 10k→100k p50 线性 31.8→338.9ms、内存峰值
79→363MB（1GB 约束下 100k 已接近上限），是唯一规模化瓶颈；对照 `ops_page_limited`
（SQL LIMIT）恒 0.0ms，证明正确范式。

### 场景 2：运维面板（scenarios/ops_panel.py）

| 操作 | 说明 |
|---|---|
| `ops_page_limited` | 带 LIMIT 分页（正确范式基准） |
| `ops_group_by` | 全量聚合 GROUP BY（面板统计口径，全表扫描） |
| `ops_realtime_refresh` | 模拟 QTimer 周期刷新 ×30 轮，输出每轮耗时序列与**前后段漂移**（累积劣化检测） |
| `ops_chart_render` | offscreen QPainter 渲染 5000 点折线（图表渲染开销） |

### 场景 3：视频业务（scenarios/video.py）

| 操作 | 说明 |
|---|---|
| `video_upload_zip` | 8MB 内存负载 → zip 打包（模拟打包上传），吞吐 MB/s + 压缩比 |
| `video_transcode` | numpy 内存帧 resize + JPEG 编码（模拟转码），FPS |
| `video_playback` | 临时小视频(120帧 320x240)逐帧读取（模拟播放），FPS，用完即删 |
| `video_storage_io` | 内存 → 临时文件写 + 读回校验，吞吐 MB/s，用完即删 |

## 四、指标定义

- **耗时**：每次操作 wall-clock（perf_counter），输出 min / p50 / p95 / p99 / max / avg；
- **QPS/TPS**：样本数 ÷ 总耗时（耗时项按场景内 repeat 限流，避免总时长失控）；
- **RSS(MB) / CPU(%)**：`metrics.ResourceSampler` 后台线程每 100ms 采样
  （psutil 进程级），输出峰值/均值/结尾值 + **10 段趋势**（观察是否单调增长）；
- **实时刷新漂移**：refresh_series 前 1/3 段均值 vs 后 1/3 段均值，
  漂移 > 25% 判定存在累积劣化。

## 五、资源约束与内存回收

1. **数据全在内存库**：进程退出即释放全部占用，不新增物理数据库文件；
2. **RSS 护栏**：默认 500MB（1GB 服务器的一半预算），采样器超限即中止当前
   场景并标记 `aborted_by_rss_guard`，报告如实呈现；
3. **分批 + 批间回收**：数据装载 batch=5000，批间 `gc.collect()`；
   场景间 `metrics.recycle()` 强制回收，实测回收后 RSS 回落（100k 场景后
   363→181MB）；
4. **视频临时文件**：播放/存储用临时小文件（几百 KB），`finally` 中
   `os.remove`，实测 0 残留；
5. **开跑前预检**：打印可用内存/磁盘/各规模内存估算，资源不足给出警告。

## 六、结果分析与输出

- 控制台：逐场景耗时 + 内存峰值 + 护栏状态；
- `results/stress_<时间戳>[_tag].json`：机读完整结果（timers 分位数 + resources + 趋势 + 结论）；
- `results/stress_<时间戳>[_tag].md`：人读报告，含跨规模对比表、场景明细、
  内存趋势与**瓶颈分析与优化建议**（scenarios 各 `analyze()` 自动生成）。

## 七、扩展指南

新增场景：在 `scenarios/` 下建模块，提供 `run(scale, **kwargs) -> dict` 与
`analyze(result) -> list[str]`，再在 `run_stress.py` 的 `runners` 注册即可；
数据装载如需新表，在 `data_gen.py` 增加 build 函数（内存库 + temp_store=MEMORY）。

## 八、扩展：pytest 接入与真机回归工具

- **pytest 冒烟**（`tests/test_stress_smoke.py`）：2k 规模跑售后/运维场景断言
  返回结构 + 基本指标 + 护栏未触发；`get_cycle_options` 速度与口径抽查；
  **P0 改造 SQL 的双后端兼容性检查**（参数占位符统一 `?`、无 SQLite/MySQL
  专属语法）——4 用例 <1s，CI 可直接跑。
- **真机回归工具**（`tools/regression_aftersale_ui.py`）：offscreen 渲染真实
  `RecordsPage`，注入 10 万行内存库，走真实异步 worker 链路测端到端耗时
  （首次加载/翻页/关键词筛选/统计弹窗/周期下拉）。实测全部毫秒级：
  首次加载 117ms、翻页 152ms、筛选 130ms、统计弹窗 139ms、周期下拉 50ms。
- **查询口径等价性工具**（`tools/verify_aftersale_paging.py`）：612 项对照
  （4 种周期模式 × 周期 × 筛选 × 列表/统计/详情/周期选项），SQL 分页化
  与旧 Python 实现完全等价，防回归。
- **MySQL 后端对照**：无真实 MySQL 环境时由 `test_paging_sql_mysql_compatible`
  保证查询 SQL 语法双方言通用；`SUM(resolved='是')` / `LIMIT ? OFFSET ?`
  在 pymysql 下语义一致。如需真机对照，在装有 MySQL 的服务器上直接运行
  `run_stress.py`（`_conn` 注入点替换为 `table_db.get_conn()` 即走双后端路由）。
