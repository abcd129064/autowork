# newbv 运营后台「仓库管理 → 库存查询」字段抓取文档

- 系统：上海新球视界科技有限公司运营后台（Struts2/Spring + ExtJS 3 老架构）
- 入口：`http://140.207.44.27:57081/newbv/myaccount/system/index!loginsuccess.action`
- 页面：左侧 ExtJS 树 → 仓库管理 → 库存查询（主文档内 TabPanel 渲染，非 iframe）
- 抓取方式：Playwright 持久化会话登录 → 展开树 → 点击「库存查询」→ 读取 ExtJS Grid（`ext-comp-1006`）colModel + Store 原始数据
- 抓取时间：2026-09-13（账号 luoting）

## 一、查询工具栏（筛选条件）

| 控件 | 类型 | 参数名 / ID | 说明 |
|---|---|---|---|
| 关键字 | 文本框 | `filter_LIKES_product.productName_OR_product.productCode_OR_product.specModel` | 模糊匹配 存货名称/货号/规格 |
| 仓库 | 下拉(Ext combo) | 隐藏域 `storage.id`（combo id=`ext-comp-1031`） | 选「总部仓库」等；提交的是仓库 ID（UUID） |
| 查询 | 按钮 | — | 触发 grid store reload |
| 分页 | 底栏 | — | 每页 20 条，共 30 页（全仓合计约 127 条为筛选后计数，见"疑点"） |

## 二、表格列（可见列，colModel 原样）

| # | 表头 | dataIndex | 宽度 | 备注 |
|---|---|---|---|---|
| 0 | （行号） | `numberer` | 23 | RowNumberer |
| 1 | （勾选） | `checker` | 20 | CheckboxSelectionModel |
| 2 | 仓库 | `storage_storageName` | 80 | 对象 {text,value}，显示 text |
| 3 | SKU(货号) | `product_productCode` | 80 | 对象 {code,specModel,text,value}，显示 code |
| 4 | 数量 | `qty` | 60 | 库存数量 |
| 5 | 保留 | `rqty` | 60 | 保留数量 |
| 6 | 借出 | `lendQty` | 60 | |
| 7 | 借入 | `borrowQty` | 60 | |
| 8 | 入转出 | `borrow2lendQty` | 60 | |
| 9 | 存货分类 | `ptname` | 80 | 如"计算机设备" |
| 10 | 存货规格 | `product_specModel` | 120 | |
| 11 | 存货名称 | `product_productName` | 220 | |

## 三、后端返回但**无可见列**的隐藏字段（重点）

Store 每行 record 里还携带以下字段，colModel 中**没有**对应列，全部返回空串：

| 字段 | 推测含义 |
|---|---|
| `soqty` | 销售订单数量（Sales Order qty） |
| `sopayqty` | 销售已出库/已结数量 |
| `sonopayqty` | 销售未出库/未结数量 |
| `popayqty` | 采购已入库数量（Purchase Order） |
| `ponopayqty` | 采购未入库数量 |

> 用户截图中出现的「**待处理**」列不在这 12 个可见列里——极可能由上述 `soqty/sonopayqty` 类字段渲染（销售待处理），或该截图来自另一视图配置。本次抓取（总部仓库+SKU 0001 路径）中这些字段全为空串。

## 四、样例数据（未筛选状态，SKU 0001 × 各仓库，20 行）

| 仓库 | 数量qty | 保留 | 借出 | 借入 | 入转出 |
|---|---|---|---|---|---|
| 总部仓库 | 65 | 0 | **63** | 0 | 0 |
| 新疆仓库 | 53 | 0 | 0 | 0 | 0 |
| 西藏仓库 | 14 | 0 | 0 | 0 | 0 |
| 武汉仓库 | 1 | 0 | 0 | 0 | 0 |
| 长沙仓库 | 2 | 0 | 0 | 0 | 0 |
| 重庆仓库 | 3 | 0 | 0 | 0 | 0 |
| 成都仓库 | 20 | 0 | 0 | 0 | 0 |
| 陕西仓库 | 0 | 0 | 0 | 0 | 0 |
| 安徽仓库 | 3 | 0 | 0 | 0 | 0 |
| 江浙沪/北京/广东/江西/贵州/福建/山东/供应商售后/天津 | 0 | 0 | 0 | 0 | 0 |
| 软件部内部使用/测试仓库 | 13 | 0 | 0 | 0 | 0 |
| 泰国 | 1 | 0 | 0 | 0 | 0 |

注意：总部仓库 qty=65 且借出=63——"借出"若不计入可用库存，实际可用仅 2，这类口径差异是常见的"误判"来源。

## 五、与用户截图的对照疑点

用户截图（选择总部仓库后）显示每行数量/保留/借出/借入/入转出全为 0，但存货名称有值（交换机、付箱、领料…），共 127 条。可能原因：

1. **仓库过滤未生效于数量聚合**：选择总部仓库后，数量列按"该仓"聚合返回 0，但名称列仍显示全部商品——即 WHERE 与 GROUP BY 口径不一致（后端 SQL 问题）；
2. **隐藏字段渲染错位**：「待处理」等列绑定的 dataIndex 与返回 JSON 键名不匹配（如 `soqty` vs `sOqty` 大小写），ExtJS 渲染为空/0；
3. **分页计数误判**：底栏"显示第1-20行，共127条"——若 store.total 与 rows 的仓库过滤条件不同步，也会显示误判。

## 六、技术备注（复现用）

- Grid 组件 ID：`ext-comp-1006`；仓库 combo：`ext-comp-1031`，隐藏域 `storage.id`
- 总部仓库 ID：`40288ae4988879b0019888ffa6bb01d9`（SKU 0001 行内值）
- 登录：Spring Security `j_spring_security_check` + jcaptcha 验证码（会话约 15-20 分钟过期）
- 抓取脚本：`tools/_pw_phaseD.js`（持久化 profile `node/workspace/pw_profile`，验证码经 `logs/_captcha_code.txt` 中转）
- 原始数据：`logs/inv_dump.json`（colModel+20行完整 record）、`logs/inv_body.html`（页面 DOM）
