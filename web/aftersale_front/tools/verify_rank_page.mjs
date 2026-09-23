// verify_rank_page.mjs — 售后排行独立页 /aftersale/rank 验证（本地仿真）
// 前置：node tools/serve_dist_v2.mjs 8899（/v2 走本地 dist-v2，其余转发生产）
// 用法：node tools/verify_rank_page.mjs [port]
// 断言：菜单入口、概览指标、默认近90天、球房榜渲染与徽章、切球桌榜（level=table）、
//       下钻（带 room_name + 面包屑）、返回全局、图表点击跳列表、表格明细跳列表、
//       榜单深度/排序参数、时间范围「全部」start=2000-01-01、导出 CSV 下载。
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const CHROME = [
  "C:/Users/shen_zhe/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe",
  "C:/Users/shen_zhe/.agents/browsers/chrome-153.0.8010.36/chrome.exe"
].find(p => existsSync(p));

// 用法：node tools/verify_rank_page.mjs [port|完整baseURL]
//   port（默认 8899）= 本地仿真 serve_dist_v2；传 http://... 则直连目标环境（如生产）
const arg = process.argv[2] || "8899";
const base = arg.startsWith("http") ? arg.replace(/\/$/, "") : `http://127.0.0.1:${arg}`;
const results = [];
const ok = (n, c, extra = "") => {
  results.push({ n, pass: !!c });
  console.log(`${c ? "PASS" : "FAIL"}  ${n}${extra ? "  -- " + extra : ""}`);
};

// ---- mock 数据：球房榜 / 全局球桌榜 / 下钻球桌榜 ----
const ROOM_ROWS = [
  { rank: 1, room_name: "甲球房", table_no: "", name: "甲球房", total: 25, share: 48.1, unresolved: 3, our_problem: 20, initiative: 5, last_occurred: "2026-09-22" },
  { rank: 2, room_name: "乙球房", table_no: "", name: "乙球房", total: 14, share: 26.9, unresolved: 1, our_problem: 9, initiative: 2, last_occurred: "2026-09-21" },
  { rank: 3, room_name: "丙球房", table_no: "", name: "丙球房", total: 6, share: 11.5, unresolved: 0, our_problem: 4, initiative: 1, last_occurred: "2026-09-18" },
  { rank: 4, room_name: "丁球房", table_no: "", name: "丁球房", total: 4, share: 7.7, unresolved: 0, our_problem: 3, initiative: 0, last_occurred: "2026-09-15" }
];
const TABLE_ROWS = [
  { rank: 1, room_name: "甲球房", table_no: "3号桌", name: "甲球房·3号桌", total: 12, share: 23.1, unresolved: 2, our_problem: 10, initiative: 3, last_occurred: "2026-09-22" },
  { rank: 2, room_name: "乙球房", table_no: "3号桌", name: "乙球房·3号桌", total: 7, share: 13.5, unresolved: 0, our_problem: 5, initiative: 1, last_occurred: "2026-09-20" },
  { rank: 3, room_name: "丙球房", table_no: "8号桌", name: "丙球房·8号桌", total: 3, share: 5.8, unresolved: 1, our_problem: 2, initiative: 0, last_occurred: "2026-09-17" }
];
const DRILL_ROWS = [
  { rank: 1, room_name: "甲球房", table_no: "3号桌", name: "3号桌", total: 12, share: 23.1, unresolved: 2, our_problem: 10, initiative: 3, last_occurred: "2026-09-22" },
  { rank: 2, room_name: "甲球房", table_no: "5号桌", name: "5号桌", total: 8, share: 15.4, unresolved: 1, our_problem: 6, initiative: 2, last_occurred: "2026-09-21" },
  { rank: 3, room_name: "甲球房", table_no: "1号桌", name: "1号桌", total: 5, share: 9.6, unresolved: 0, our_problem: 4, initiative: 0, last_occurred: "2026-09-16" }
];
const SUMMARY = { rooms: 4, tables: 9, total: 52, unresolved: 4 };

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1700, height: 950 }, acceptDownloads: true });
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 200)));

const listQueries = [];
const rankQueries = [];
await page.route("**/api/auth/login", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" }) })
);
// rank：按 level / room_name 返回不同榜单
await page.route("**/api/stats/rank**", r => {
  const u = new URL(r.request().url());
  const q = u.searchParams;
  rankQueries.push(q);
  const level = q.get("level") || "room";
  const room = q.get("room_name") || "";
  let rows = level === "table" ? (room ? DRILL_ROWS : TABLE_ROWS) : ROOM_ROWS;
  // 模拟排序：unresolved 降序
  if (q.get("sort") === "unresolved") {
    rows = [...rows].sort((a, b) => b.unresolved - a.unresolved);
  }
  const limit = Number(q.get("limit") || 10);
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ level, sort: q.get("sort") || "total", limit,
      rows: rows.slice(0, limit), summary: SUMMARY }) });
});
await page.route("**/api/records?**", r => {
  if (r.request().method() === "GET") listQueries.push(new URL(r.request().url()).searchParams);
  return r.fallback();
});

// ---- 登录 ----
await page.goto(`${base}/v2/`, { waitUntil: "domcontentloaded" });
await page.waitForURL(/login/, { timeout: 15000 }).catch(() => {});
await page.waitForSelector('input[type="text"]', { timeout: 15000 });
await page.waitForTimeout(800);
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
await page.waitForTimeout(5000);
ok("登录成功", !/login/.test(page.url()), page.url());

// ---- 1. 菜单入口存在 ----
// 侧边菜单「售后记录」是折叠父级，未展开时 innerText 不含子项 → 先点开父级再断言
await page.locator(".el-sub-menu__title:has-text('售后记录'), .el-menu-item:has-text('售后记录')").first().click().catch(() => {});
await page.waitForTimeout(1200);
const hasRankMenu = await page.locator("a[href*='/aftersale/rank'], .el-menu-item:has-text('售后排行')").first().isVisible().catch(() => false);
const menuText = await page.locator(".sidebar-container, .el-menu").first().innerText().catch(() => "");
ok("侧边菜单含「售后排行」入口", hasRankMenu || menuText.includes("售后排行"),
  menuText.replace(/\n/g, "|").slice(0, 120));

// ---- 2. 进排行页（默认球房榜 / 近90天）----
await page.goto(`${base}/v2/#/aftersale/rank`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
const t1 = await page.locator("body").innerText();
ok("排行页加载（标题「球房售后排行」）", t1.includes("球房售后排行"), t1.slice(0, 60));
ok("概览指标「覆盖球房」显示 4", t1.includes("覆盖球房") && t1.includes("4"), "");
ok("概览指标「覆盖球桌」显示 9", t1.includes("覆盖球桌") && t1.includes("9"), "");
ok("概览指标「记录总数」显示 52", t1.includes("记录总数") && t1.includes("52"), "");
ok("概览指标「未解决」显示 4", t1.includes("未解决") && t1.includes("4"), "");

// 默认时间范围=近 90 天（radio 选中态）
const rangeChecked = await page.evaluate(() => {
  const els = [...document.querySelectorAll(".el-radio-button")];
  const on = els.find(e => e.classList.contains("is-active") || e.querySelector(".is-active"));
  return on ? on.textContent.trim() : null;
});
ok("默认时间范围=近 90 天", rangeChecked === "近 90 天", String(rangeChecked));

// 首个 rank 请求参数
const q0 = rankQueries[0];
ok("首次请求 level=room & limit=10 & sort=total",
  q0 && q0.get("level") === "room" && q0.get("limit") === "10" && q0.get("sort") === "total",
  q0 ? q0.toString().slice(0, 120) : "none");
ok("近 90 天 → start/end 都带上（非空）",
  q0 && /^\d{4}-\d{2}-\d{2}$/.test(q0.get("start") || "") && /^\d{4}-\d{2}-\d{2}$/.test(q0.get("end") || ""),
  q0 ? `start=${q0.get("start")} end=${q0.get("end")}` : "none");

// ---- 3. 表格渲染 4 行 + 前三名徽章 ----
const rowCount = await page.locator(".rank-table tbody tr").count();
ok("明细表渲染 4 行", rowCount === 4, `rows=${rowCount}`);
const medalCount = await page.locator(".rank-table .medal").count();
ok("前三名金银铜徽章 3 个", medalCount === 3, `medals=${medalCount}`);
const tbl1 = await page.locator(".rank-table").innerText();
ok("表格含占比与未解决列数据", tbl1.includes("48.1%") && tbl1.includes("25"), tbl1.replace(/\n/g, "|").slice(0, 140));

// ---- 4. 图表条形渲染（球房色 PALETTE[2]=#597ef7）----
const barInfo = await page.evaluate(() => {
  const el = document.querySelector(".chart-box");
  el?.scrollIntoView({ block: "center" });
  const svg = el?.querySelector("svg");
  if (!svg) return { error: "no svg" };
  const paths = [...svg.querySelectorAll("path")].filter(p => {
    const f = (p.getAttribute("fill") || "").toLowerCase().replace(/\s/g, "");
    return f === "#597ef7";
  });
  return { n: paths.length };
});
await page.waitForTimeout(400);
ok("球房榜条形图渲染 4 条（fill=#597ef7）", barInfo.n === 4, JSON.stringify(barInfo));

// 干净状态整页截图（默认球房榜 / 近90天 / 无筛选）供设计确认
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(400);
await page
  .screenshot({
    path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/rank_page_default.png",
    fullPage: true
  })
  .catch(() => {});

// ---- 5. 切「球桌排行」→ level=table，联合名分列 ----
await page.locator(".el-radio-button:has-text('球桌排行')").first().click();
await page.waitForTimeout(3000);
const qTable = rankQueries[rankQueries.length - 1];
ok("切球桌榜请求 level=table", qTable && qTable.get("level") === "table", qTable ? qTable.toString().slice(0, 100) : "none");
const t2 = await page.locator(".rank-table").innerText();
ok("球桌榜显示「球房·桌号」联合名",
  t2.includes("甲球房·3号桌") && t2.includes("乙球房·3号桌"), t2.replace(/\n/g, "|").slice(0, 140));
const t2b = await page.locator("body").innerText();
ok("标题切为「球桌售后排行」", t2b.includes("球桌售后排行"), "");

// ---- 6. 回球房榜 → 点「下钻」→ 带 room_name + 面包屑 ----
await page.locator(".el-radio-button:has-text('球房排行')").first().click();
await page.waitForTimeout(2500);
await page.locator(".rank-table tbody tr").first().locator("button:has-text('下钻')").click();
await page.waitForTimeout(3000);
const qDrill = rankQueries[rankQueries.length - 1];
ok("下钻请求 level=table & room_name=甲球房",
  qDrill && qDrill.get("level") === "table" && qDrill.get("room_name") === "甲球房",
  qDrill ? qDrill.toString().slice(0, 130) : "none");
const t3 = await page.locator("body").innerText();
ok("面包屑显示下钻球房「甲球房」", t3.includes("排行范围") && t3.includes("甲球房"), t3.slice(0, 80));
const t3tbl = await page.locator(".rank-table").innerText();
ok("下钻榜显示纯桌号（不带球房前缀）",
  t3tbl.includes("3号桌") && t3tbl.includes("5号桌") && !t3tbl.includes("甲球房·"), t3tbl.replace(/\n/g, "|").slice(0, 120));
const drillTitle = await page.locator(".rank-card").innerText();
ok("下钻标题为「「甲球房」内球桌排行」", drillTitle.includes("内球桌排行"), "");

// ---- 7. 面包屑「全部球房」返回全局榜 ----
await page.locator(".crumb a:has-text('全部球房'), .crumb .el-link:has-text('全部球房')").first().click();
await page.waitForTimeout(3000);
const qBack = rankQueries[rankQueries.length - 1];
ok("返回全局榜请求不带 room_name", qBack && !qBack.get("room_name"), qBack ? qBack.toString().slice(0, 110) : "none");

// ---- 8. 榜单深度 TOP 20 → limit=20 ----
await page.locator(".rank-toolbar .el-select").first().click();
await page.waitForTimeout(800);
await page.locator(".el-select-dropdown__item:visible").filter({ hasText: "TOP 20" }).first().click();
await page.waitForTimeout(2500);
const qLimit = rankQueries[rankQueries.length - 1];
ok("榜单深度切 TOP 20 → limit=20", qLimit && qLimit.get("limit") === "20", qLimit ? qLimit.toString().slice(0, 110) : "none");

// ---- 9. 排序切「未解决」→ sort=unresolved ----
await page.locator(".rank-toolbar .el-select").nth(1).click();
await page.waitForTimeout(800);
await page.locator(".el-select-dropdown__item:visible").filter({ hasText: "未解决" }).first().click();
await page.waitForTimeout(2500);
const qSort = rankQueries[rankQueries.length - 1];
ok("排序切「未解决」→ sort=unresolved", qSort && qSort.get("sort") === "unresolved", qSort ? qSort.toString().slice(0, 110) : "none");

// ---- 10. 时间范围切「全部」→ start=2000-01-01 且无 end ----
await page.locator(".el-radio-button:has-text('全部')").first().click();
await page.waitForTimeout(2800);
const qAll = rankQueries[rankQueries.length - 1];
ok("时间范围「全部」→ start=2000-01-01",
  qAll && qAll.get("start") === "2000-01-01", qAll ? qAll.toString().slice(0, 120) : "none");
ok("「全部」不带 end（不截断上界）", qAll && !qAll.get("end"), qAll ? `end=${qAll.get("end")}` : "none");

// ---- 11. 切「近 30 天」→ start/end 跨度约 30 天 ----
await page.locator(".el-radio-button:has-text('近 30 天')").first().click();
await page.waitForTimeout(2800);
const q30 = rankQueries[rankQueries.length - 1];
const span = q30 ? Math.round((new Date(q30.get("end")) - new Date(q30.get("start"))) / 86400000) : -1;
ok("「近 30 天」起止跨度=29 天（含首尾）", span === 29, `span=${span} start=${q30?.get("start")} end=${q30?.get("end")}`);

// ---- 12. 表格「明细」→ 跳列表带 room_name ----
listQueries.length = 0;
await page.locator(".rank-table tbody tr").first().locator("button:has-text('明细')").click();
await page.waitForTimeout(3500);
const url4 = decodeURIComponent(page.url());
ok("表格「明细」跳记录列表", /aftersale\/list/.test(page.url()), page.url());
ok("URL 带 room_name=甲球房", url4.includes("room_name=甲球房"), url4);
const lq = listQueries.filter(x => x.get("page_size") !== "200").pop();
ok("列表请求带 room_name 参数", lq && lq.get("room_name") === "甲球房", lq ? lq.toString().slice(0, 120) : "none");

// ---- 13. 图表条目点击 → 跳列表 ----
await page.goto(`${base}/v2/#/aftersale/rank`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
const clickPos = await page.evaluate(() => {
  const el = document.querySelector(".chart-box");
  el?.scrollIntoView({ block: "center" });
  const svg = el?.querySelector("svg");
  if (!svg) return null;
  const paths = [...svg.querySelectorAll("path")].filter(p =>
    (p.getAttribute("fill") || "").toLowerCase().replace(/\s/g, "") === "#597ef7");
  if (!paths.length) return null;
  // 条形 path 取包围盒内一点（reverse 渲染后最后一个=最大值，这里取第一个即可验证跳转）
  const b = paths[0].getBoundingClientRect();
  return { x: b.left + b.width * 0.6, y: b.top + b.height / 2 };
});
await page.waitForTimeout(500);
if (clickPos) {
  listQueries.length = 0;
  await page.mouse.click(clickPos.x, clickPos.y);
  await page.waitForTimeout(3500);
  const url5 = decodeURIComponent(page.url());
  ok("图表条目点击跳记录列表", /aftersale\/list/.test(page.url()), page.url());
  ok("图表跳转 URL 带 room_name", url5.includes("room_name="), url5);
} else {
  ok("图表条目点击跳记录列表", false, "定位失败");
  ok("图表跳转 URL 带 room_name", false, "定位失败");
}

// ---- 14. 导出 CSV（触发下载）----
await page.goto(`${base}/v2/#/aftersale/rank`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
const dl = page.waitForEvent("download", { timeout: 15000 }).catch(() => null);
await page.locator("button:has-text('导出 CSV')").first().click();
const downloaded = await dl;
ok("导出 CSV 触发下载", !!downloaded, downloaded ? downloaded.suggestedFilename() : "no download");
ok("下载文件名含 rank 与时间戳",
  !!downloaded && /售后排行_room_\d{8}_\d{6}\.csv/.test(downloaded.suggestedFilename()),
  downloaded ? downloaded.suggestedFilename() : "none");

// ---- 15. 关键字/地区筛选带参 ----
await page.locator(".search-form input[placeholder='球房/桌号/问题']").first().fill("甲球");
await page.waitForTimeout(400);
await page.locator("button:has-text('查询')").first().click();
await page.waitForTimeout(2800);
const qKw = rankQueries[rankQueries.length - 1];
ok("关键字筛选带 keyword=甲球", qKw && qKw.get("keyword") === "甲球", qKw ? qKw.toString().slice(0, 120) : "none");

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await page.screenshot({ path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/rank_page.png", fullPage: true }).catch(() => {});
await browser.close();
process.exit(passed === results.length ? 0 : 1);
