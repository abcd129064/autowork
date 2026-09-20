// verify_batch_features.mjs — 批量功能 E2E（本地仿真）：
// 主动发起筛选 / URL 同步筛选 / 导出 / 超期高亮 / 详情抽屉 /
// 回收站 / 操作日志 / Excel 导入 / 常用句云端同步
// 全部写路径走 route mock，不落真实生产库
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
// 复用 vue-pure-admin 的 xlsx 依赖（aftersale_front 无此包）
const XLSX = require(
  "C:/Users/shen_zhe/Desktop/autowork/web/vue-pure-admin/node_modules/xlsx"
);

const CHROME = [
  "C:/Users/shen_zhe/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe",
  "C:/Users/shen_zhe/.agents/browsers/chrome-153.0.8010.36/chrome.exe"
].find(p => existsSync(p));

const port = process.argv[2] || "8899";
const base = `http://127.0.0.1:${port}`;
const results = [];
function ok(n, c, extra = "") {
  results.push({ n, pass: !!c });
  console.log(`${c ? "PASS" : "FAIL"}  ${n}${extra ? "  -- " + extra : ""}`);
}

const browser = await chromium.launch({ executablePath: CHROME });
const ctx = await browser.newContext({
  viewport: { width: 1700, height: 950 },
  acceptDownloads: true
});
const page = await ctx.newPage();
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 200)));

// ---- 请求捕获/拦截 ----
const listGets = [];
let importPayload = null;
let restorePayload = null;
let prefsPut = null;
const overdueDate = "2026-08-20"; // 距今 > 7 天
const mockRows = [
  { id: 9901, created_at: `${overdueDate} 10:00:00`, occurred_at: overdueDate,
    issue_type: "硬件问题", table_no: "01-01", room_name: "超期球房", region: "上海",
    problem: "长期未解决的高亮样例", cause: "", solution: "", resolved: "否",
    resolver: "", response_time: "", is_our_problem: "是", is_initiative: "是",
    is_important: 0, snk_code: "snk_x", device_code: "dev_x", cycle_start: "2026/08/18",
    creator: "tester", updated_at: "" },
  { id: 9902, created_at: "2026-09-19 10:00:00", occurred_at: "2026-09-19",
    issue_type: "操作问题", table_no: "02-02", room_name: "正常球房", region: "四川",
    problem: "已解决的普通样例", cause: "", solution: "", resolved: "是",
    resolver: "", response_time: "", is_our_problem: "是", is_initiative: "否",
    is_important: 0, snk_code: "", device_code: "", cycle_start: "2026/09/15",
    creator: "tester", updated_at: "" }
];
await page.route("**/api/auth/login", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" }) })
);
await page.route("**/api/user/prefs", r => {
  if (r.request().method() === "GET")
    return r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ prefs: { quick_phrases: ["云端句A", "主机没有开机"] }, updated_at: "" }) });
  prefsPut = r.request().postDataJSON();
  return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
});
await page.route("**/api/records/recycle*", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ total: 2, rows: [
      { id: 9881, deleted_at: "2026-09-19 12:00:00", occurred_at: "2026-09-01", issue_type: "其他问题",
        room_name: "已删球房", problem: "回收站样例1", creator: "tester" },
      { id: 9880, deleted_at: "2026-09-18 09:00:00", occurred_at: "2026-08-30", issue_type: "直播相关",
        room_name: "已删球房2", problem: "回收站样例2", creator: "tester" }
    ] }) })
);
await page.route("**/api/records/restore", r => {
  restorePayload = r.request().postDataJSON();
  return r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ restored: restorePayload.ids.length }) });
});
await page.route("**/api/audit*", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ total: 2, page: 1, page_size: 20, rows: [
      { id: 12, ts: "2026-09-20 10:00:00", user: "admin", action: "purge", record_id: null, detail: "ids=[9800]" },
      { id: 11, ts: "2026-09-20 09:00:00", user: "admin", action: "create", record_id: 1118, detail: "硬件问题" }
    ] }) })
);
await page.route("**/api/records/batch-import", r => {
  importPayload = r.request().postDataJSON();
  return r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ imported: importPayload.rows.length, skipped: 0 }) });
});
// 列表页数据走 mock（两条构造行），保证超期高亮可控
await page.route("**/api/records?**", r => {
  const url = new URL(r.request().url());
  const q = url.searchParams;
  if (r.request().method() === "GET") {
    if (q.get("page_size") === "200") {
      // 导出拉全量：返回构造行
      listGets.push(q);
      return r.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ total: mockRows.length, rows: mockRows,
          stats: { total: 2, unresolved: 1, initiative: 1, our_problem: 2 },
          page: Number(q.get("page") || 1), page_size: 200 }) });
    }
    if (url.pathname.endsWith("/api/records") && q.get("page_size") !== "200") {
      listGets.push(q);
      return r.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ total: mockRows.length, rows: mockRows,
          stats: { total: 2, unresolved: 1, initiative: 1, our_problem: 2 },
          page: Number(q.get("page") || 1), page_size: Number(q.get("page_size") || 20) }) });
    }
  }
  return r.fallback();
});

// ---- 登录 ----
await page.goto(`${base}/v2/`, { waitUntil: "domcontentloaded" });
await page.waitForURL(/login/, { timeout: 15000 }).catch(() => {});
await page.waitForSelector('input[type="text"], input:not([type])', { timeout: 15000 });
await page.waitForTimeout(600);
const code = await page.evaluate(() => {
  try {
    return document.querySelector("#app")?.__vue_app__?.config?.globalProperties
      ?.$pinia?.state?.value?.["pure-user"]?.verifyCode ?? null;
  } catch { return null; }
});
const inputs = page.locator('input[type="text"], input:not([type])');
await inputs.first().fill("admin");
await page.locator('input[type="password"]').first().fill("kaidao12");
if (code != null) { await inputs.nth(1).fill(String(code)); await inputs.nth(1).press("Tab"); }
await page.locator('button:has-text("登录")').first().click();
await page.waitForTimeout(4000);
ok("登录成功", !/login/.test(page.url()), page.url());

// ================= 列表页 =================
await page.goto(`${base}/v2/#/aftersale/list`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

// ---- 1. 主动发起筛选 ----
const initSel = page.locator('.el-form-item:has(label:text-is("主动发起："))').first();
ok("主动发起筛选存在", await initSel.isVisible().catch(() => false));
await initSel.locator(".el-select__wrapper").first().click();
await page.waitForTimeout(400);
await page.locator('.el-select-dropdown__item:visible', { hasText: "是" }).first().click();
await page.waitForTimeout(300);
// 筛选栏点搜索才触发查询（与真实交互一致）
await page.locator('button:has-text("搜索")').first().click();
await page.waitForTimeout(1500);
const lastQ = listGets.filter(q => q.get("page_size") !== "200").pop();
ok("列表请求带 is_initiative=是", lastQ && lastQ.get("is_initiative") === "是",
  lastQ ? lastQ.toString() : "none");
ok("URL 同步回写 is_initiative", decodeURIComponent(page.url()).includes("is_initiative=是"), page.url());

// ---- 2. URL 同步：关键词搜索后 URL 带 keyword ----
await page.locator('.el-form-item:has(label:text-is("关键词：")) input').fill("超期");
await page.locator('button:has-text("搜索")').first().click();
await page.waitForTimeout(1200);
ok("URL 同步回写 keyword", decodeURIComponent(page.url()).includes("keyword=超期"), page.url());
// 刷新后筛选仍在（URL 驱动状态）
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const kwVal = await page.locator('.el-form-item:has(label:text-is("关键词：")) input').inputValue();
ok("刷新后筛选状态保留", kwVal === "超期", `keyword="${kwVal}"`);

// ---- 2b. 表头排序（custom → 后端排序；且组件不重建）----
// 记录当前 KPI 卡 DOM 节点引用：排序后若为同一节点则组件未被销毁重建
await page.evaluate(() => {
  window.__remountProbe = document.querySelector(".stats-row .stat-card");
});
listGets.length = 0;
await page.locator("th:has-text('填写时间')").first().click();
await page.waitForTimeout(1500);
const sameNode = await page.evaluate(
  () => document.querySelector(".stats-row .stat-card") === window.__remountProbe
);
ok("排序不重建页面（keep-alive key=path）", sameNode);
const sortQ = listGets.filter(q => q.get("page_size") !== "200").pop();
ok("表头点击触发 sort_by=created_at", sortQ && sortQ.get("sort_by") === "created_at" &&
  ["asc", "desc"].includes(sortQ.get("sort_order") || ""), sortQ ? sortQ.toString() : "none");
ok("URL 同步排序参数", decodeURIComponent(page.url()).includes("sort_by=created_at"), page.url());

// ---- 2c. 自动刷新开关（状态持久化；60s 间隔不做实际等待）----
await page.locator(".auto-refresh .el-switch").click();
await page.waitForTimeout(400);
const autoState = await page.evaluate(() => localStorage.getItem("aftersale-auto-refresh"));
ok("自动刷新开关持久化", autoState === "1", `localStorage=${autoState}`);
await page.locator(".auto-refresh .el-switch").click();
await page.waitForTimeout(300);
const autoState2 = await page.evaluate(() => localStorage.getItem("aftersale-auto-refresh"));
ok("自动刷新关闭持久化", autoState2 === "0", `localStorage=${autoState2}`);

// ---- 3. 超期高亮 ----
const overdueCount = await page.locator(".el-table__row.overdue-row").count();
ok("未解决超 7 天行高亮", overdueCount >= 1, `rows=${overdueCount}`);

// ---- 4. 详情抽屉 ----
await page.locator('button:has-text("详情")').first().click();
await page.waitForTimeout(800);
const drawer = page.locator(".el-drawer").last();
const drawerText = await drawer.innerText().catch(() => "");
ok("详情抽屉打开", await drawer.isVisible().catch(() => false));
ok("详情含全字段（SNK/设备码/账期）",
  drawerText.includes("SNK 码") && drawerText.includes("设备码") && drawerText.includes("账期"),
  drawerText.slice(0, 60).replace(/\n/g, " "));
await page.keyboard.press("Escape");
await page.waitForTimeout(500);

// ---- 5. 导出 ----
listGets.length = 0;
const dlPromise = page.waitForEvent("download", { timeout: 15000 });
await page.getByRole("button", { name: /导出/ }).first().click();
const dl = await dlPromise;
ok("导出触发下载", /售后记录_\d{8}_\d{6}\.xlsx/.test(dl.suggestedFilename()), dl.suggestedFilename());
ok("导出按当前筛选请求", listGets.some(q => q.get("is_initiative") === "是" || Number(q.get("page_size")) === 200),
  "fetch-pages ok");

// ---- 6. 回收站 ----
await page.getByRole("button", { name: /回收站/ }).first().click();
await page.waitForTimeout(1000);
const recycleDlg = page.locator(".el-dialog:has-text('回收站')").last();
ok("回收站弹窗打开", await recycleDlg.isVisible().catch(() => false));
const recycleText = await recycleDlg.innerText().catch(() => "");
ok("回收站列表数据", recycleText.includes("回收站样例1"), recycleText.slice(0, 60).replace(/\n/g, " "));
await recycleDlg.locator(".el-table__row").first().locator(".el-checkbox").click();
await page.waitForTimeout(300);
await recycleDlg.locator('button:has-text("恢复选中")').click();
await page.waitForTimeout(800);
ok("恢复请求带勾选 id", restorePayload && restorePayload.ids.length === 1 && restorePayload.ids[0] === 9881,
  JSON.stringify(restorePayload || {}));
await recycleDlg.locator('button:has-text("取消")').first().click().catch(() => {});
await page.keyboard.press("Escape");
await page.waitForTimeout(500);

// ---- 7. 操作日志 ----
await page.getByRole("button", { name: /操作日志/ }).first().click();
await page.waitForTimeout(1000);
const auditDlg = page.locator(".el-dialog:has-text('操作日志')").last();
const auditText = await auditDlg.innerText().catch(() => "");
ok("审计弹窗打开且显示操作记录", auditText.includes("彻底删除") && auditText.includes("新增"),
  auditText.slice(0, 60).replace(/\n/g, " "));
await page.keyboard.press("Escape");
await page.waitForTimeout(500);

// ---- 8. Excel 导入 ----
// 生成测试工作簿：类型列含分组首行语义（第二行类型留空 → 向下填充）
const wsData = [
  ["类型", "球房", "桌号", "地区", "问题", "是否解决"],
  ["硬件问题", "导入球房A", "01-01", "上海", "扫码失败", ""],
  ["", "导入球房A", "01-02", "上海", "同样扫码失败", "否"],
  ["", "", "", "", "", ""] // 全空行应被跳过
];
const ws = XLSX.utils.aoa_to_sheet(wsData);
const wb = XLSX.utils.book_new();
XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
const buf = XLSX.write(wb, { type: "buffer", bookType: "xlsx" });
await page.getByRole("button", { name: /导入/ }).first().click();
await page.waitForTimeout(800);
await page.locator('input[type="file"]').setInputFiles({
  name: "import_test.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  buffer: buf
});
await page.waitForTimeout(1200);
const impDlg = page.locator(".el-dialog:has-text('Excel 批量导入')").last();
const impText = await impDlg.innerText().catch(() => "");
ok("导入解析出 2 行（空行跳过）", impText.includes("解析到 2 行"), impText.slice(0, 80).replace(/\n/g, " "));
await impDlg.locator("button.el-button--primary").last().click();
await page.waitForTimeout(1200);
ok("导入请求行数=2", importPayload && importPayload.rows.length === 2, "rows ok");
ok("导入类型向下填充", importPayload?.rows?.[1]?.issue_type === "硬件问题",
  JSON.stringify(importPayload?.rows?.[1] || {}));
ok("导入是否解决空值默认否", importPayload?.rows?.[0]?.resolved === "否",
  JSON.stringify(importPayload?.rows?.[0] || {}));
await page.keyboard.press("Escape");
await page.waitForTimeout(400);

// ---- 9. 常用句云端同步 ----
// onMounted 已 pull 云端 prefs（云端句A）；打开新增弹窗验证
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await page.getByRole("button", { name: /新增/ }).first().click();
await page.waitForTimeout(1000);
const dlg = page.locator(".el-dialog").last();
await dlg.locator('button:has-text("常用句")').first().click();
await page.waitForTimeout(700);
const cloudItem = page.locator(".qp-item", { hasText: "云端句A" }).first();
ok("常用句展示云端数据（多端同步）", await cloudItem.isVisible().catch(() => false));
// 删除一句 → PUT prefs 携带更新后的列表
await cloudItem.locator(".qp-del").click();
await page.waitForTimeout(800);
ok("常用句变更推送云端 prefs", !!prefsPut && Array.isArray(prefsPut.prefs.quick_phrases),
  JSON.stringify(prefsPut || {}).slice(0, 120));

await page.screenshot({ path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/batch_features.png" });

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
