# TODO — 待办事项与未来计划

> 优先级：**P0** = 阻塞/高危，尽快处理 ｜ **P1** = 计划内，排期推进 ｜
> **P2** = 可做可不做，机会窗口处理。
> 条目来源：[AGENTS.md](../AGENTS.md) §7 既有债务、各设计文档的「待拍板/未落地」
> 章节、近期会话结论。完成一项就**划掉并注明落地位置**，不要默默删除。
> 最后核对：2026-09-23。

---

## 1. 桌面端

### P0
- [ ] **`build/` `dist/` `out/` 约 2.1GB 构建产物清理**（AGENTS.md §7 已登记，待用户决定；
      agent 不得自行处理）。
- [ ] **`config/database.json` 含 DPAPI 密文与公网 IP 仍入库** —— 是否改为模板 +
      gitignore 真实文件（AGENTS.md §7 已登记，待用户决定）。

### P1
- [x] **统一设置面板重构方案核对**（2026-09-23）：实际 `SettingsHubPage` 已落地为
      「SegmentedWidget 7 段」（应用配置/远程连接/工具/性能/数据库/面板设置/外观），
      设计稿第 7 节三个「待拍板决策」均按「另开分页」方向解决；已在设计稿头部补
      「落地核对」块，状态从「待拍板」改为「已落地」，字段级规格保留为权威对照。
- [x] **mermaid 图表刷新**（2026-09-23）：`docs/class-diagram.mermaid`、
      `docs/sequence-diagram.mermaid` 已对齐当前代码——镜像推送下线（删 `MysqlSync.push_*`、
      `MysqlSyncWorker`→`MysqlTestWorker`）、`Schema.TABLE_META`→`TABLE_COLUMNS/TABLE_INDEXES`、
      `sqlite_io` 已被 `merge_back` 实际引用（去掉「目标态」标注）、补 `data_retention` 清理链路、
      修序列图未声明参与者（`FB`/`TablePanel`）与周备份触发时序（延迟 5s+24h）。
- [x] **售后面板 UI 改进方案核对**（2026-09-23）：方案 A/B/C 绝大多数条目已随 09 月
      `windows/aftersale/` 拆包重构落地（指标卡/批量操作/行内操作列/一键解决/字段级校验/
      QCompleter/空状态/loading）；已在文档头部补「落地核对」块，剩余为 P2 视觉微调
      （存量对话框尺寸收敛、QSS 排版刻度换 `TYPE_SCALE_PX`、三页间距走 `SPACE` 令牌）。
- [x] **架构评审与 SQL 优化方案核对**（2026-09-23）：文档第 10 节已载明 T01-T05 全部
      落地并通过 QA；已在头部补状态标注，README 索引状态从「部分落地」改为「已落地」。
- [x] **`docs/API.md` 审计基线落档**（2026-09-23）：审计结果与盲区解读已记入
      [DEVELOPMENT.md](DEVELOPMENT.md) §7（`missing_modules` 26 + `undocumented` 18 为真实债务；
      `deleted` / `no_source` 多为工具对模块级常量/类名标题的盲区）。
- [x] **API.md 补全**（2026-09-24 完成）：26 个 `missing_modules` 全部补齐
      （core 7 / main_window 11 / windows 7 / workers.update_worker——含 shim 模块与
      update_dialog 新章节），18 模块 `undocumented` 34 条清零（各 Worker 类改挂
      `#### 类 \`X\`` 标题、todesk/Adapter/记忆日期与自动刷新函数/query_rank/OpPlain 等全部登记）；
      windows/main_window 段的类名伪模块标题改挂真实模块锚点（`no_source` 50→5，
      剩 5 条为配置域小节标题的工具盲区，故意保留——降级会把配置键误挂 p2p 模块）；
      顺带修正过期内容：MainWindow 基类补 UpdateMixin/FluentWindow、stat_charts 的
      open_aftersale/open_ledger 改为真实 API `open_analysis`、RemoteHub 三视图→多视图、
      BASE_VERSION 3.11→3.13、TodeskToggleWorker 真实构造签名。
      重跑审计：`missing_modules` 0 / `undocumented` 0 / `deleted` 35（全部为已正确
      登记的模块级常量与 Signal——审计工具只提取函数/类的既有盲区）；check_refs rc=0。
- [ ] **`frpc.exe`（16MB，tracked 但被 .gitignore 命中）是否 `git rm --cached`**（AGENTS.md §7）。
- [ ] **`tools/*/_archive/` 未入库一次性脚本**：提交还是删除（AGENTS.md §7）。

### P2
- [ ] 摸鱼中心扫雷/贪吃蛇成绩云端同步（当前仅 `moyu_state.json` 本地留存）。
- [ ] 健康度告警阈值（4000/5000/40 万）可配置化（当前写死）。

## 2. Web 端（售后面板）

### P1
- [ ] **v2（vue-pure-admin）demo 页面裁剪**：当前策略「先不裁剪，只加售后页」，
      上线稳定后评估裁剪范围（登录页/首页/权限 demo 路由）。
- [ ] **v2 数据看板排名图回归**：`table_top`（TRIM(room_name)+TRIM(table_no) 联合分组）
      与 `room_top` 的 ECharts SVG 点击筛选已上线（2026-09-23，E2E `verify_rank_charts.mjs`
      12 断言），后续图表迭代注意 SVG 点击坐标坑（饼图扇区 getPointAtLength 中点落圆心）。
- [ ] v2 录入弹窗「记住上次填写」（localStorage）字段范围随桌面端 form.py 变更需同步。

### P2
- [ ] v1/v2 并行期结束条件与 v1 下线计划（入口选择页 `/v1/` 的退役时间点）。
- [ ] `users.json` 单管理员 → 多用户/角色（当前 admin 单账号 + bcrypt）。

## 3. 文档与仓库治理

### P0
- [x] **补齐项目文档树主干**（2026-09-23 本次完成）：CHANGELOG / TODO / ARCHITECTURE /
      DEVELOPMENT / DEPLOYMENT / DESIGN 六篇落档 `docs/`，并挂进 `docs/README.md`。

### P1
- [ ] **`overview.md`（仓库根）内容整体过期**：指向已不存在的 smoke 脚本
      （AGENTS.md §7 债务表 + check_refs KNOWN_STALE 已登记）。该文件**已入库**，
      按 AGENTS.md §3.2 不由 agent 删除；历史结论已由 CHANGELOG.md 3.12 节承接，
      建议 `git mv` 归档（移入归档目录或删除）—— **待用户拍板**。
      归档后可从 `tools/check_refs.py` KNOWN_STALE 移除 `overview.md` 条目。
- [x] **README.md「项目结构」段与实况核对**（2026-09-23）：已修正 BASE_VERSION（3.11→3.13）、
      远程页三视图→四视图、测试基线表述、MySQL「需手动 ALTER」过时说法（现两侧自动迁移）、
      `core/` 目录树补 5 个漏列模块、Web 验证脚本清单对齐实际文件名；后续目录变动仍需同步
      （AGENTS.md §3.3 第 5 条）。

### P2
- [ ] `tools/_scratch/` 约 5MB 抓取物清空（AGENTS.md §7，用户统一处理）。
- [ ] 历史性能调查报告（2026-09-06/07 三篇）中已落地项与未落地项的逐条状态标注。

## 4. 跨项目关联（非本仓库，仅备忘）

- pymkui 视频监控平台两条故障链（`pull_proxy.py` 全局 cursor 竞态 SIGSEGV /
  `startLive.service` 缺 `LD_LIBRARY_PATH`）等待打包现场证据后修复 —— 在 pymkui 仓库跟进。
- live.newbv.cn「记住我」cookie 误驱动列表过滤的前后端不一致 —— 在该仓库跟进。

---

## 维护约定

- 新待办按 P0/P1/P2 归入对应分组；完成后 `- [x]` + 一句话落地位置，保留一个版本周期再删。
- AGENTS.md §7「既有债务」变化时同步本文件 §1/§3。
- 与 [CHANGELOG.md](CHANGELOG.md) 的关系：TODO 记「还没做的」，CHANGELOG 记「已经做了的」。
