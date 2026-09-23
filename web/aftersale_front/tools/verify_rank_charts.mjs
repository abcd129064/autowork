// verify_rank_charts.mjs — 统计页「球房/球桌售后排行 TOP10」验证（本地仿真）
// 前置：node tools/serve_dist_v2.mjs 8899（/v2 走本地 dist-v2，其余转发生产）
// 用法：node tools/verify_rank_charts.mjs [port]
// 断言：两卡渲染、跨球房同名桌分列、点击跳列表带 room_name(+table_no)、
//       列表请求带参、筛选标签显示与关闭回退。
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const CHROME = [
  "C:/Users/shen_zhe/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe",
  "C:/Users/shen_zhe/.agents/browsers/chrome-153.0.8010.36/chrome.exe"
].find(p => existsSync(p));

const port = process.argv[2] || "8899";
const base = `http://127.0.0.1:${port}`;
const results = [];
const ok = (n, c, extra = "") => {
  results.push({ n, pass: !!c });
  console.log(`${c ? "PASS" : "FAIL"}  ${n}${extra ? "  -- " + extra : ""}`);
};

// 与后端新口径同构的 mock（table_top 按 value DESC；两球房同名"3号桌"必须分列）
const CHARTS_MOCK = {
  region_dist: [
    { name: "四川", value: 40 },
    { name: "广东", value: 12 }
  ],
  daily: [
    { date: "2026-09-20", count: 5 },
    { date: "2026-09-21", count: 8 }
  ],
  our_problem: { yes: 30, no: 22 },
  issue_type_dist: [
    { name: "硬件问题", value: 30 },
    { name: "软件问题", value: 22 }
  ],
  aging: [
    { name: "当日", value: 2 },
    { name: "1-3天", value: 1 },
    { name: "4-7天", value: 0 },
    { name: "8-15天", value: 0 },
    { name: "15天以上", value: 1 }
  ],
  table_top: [
    { name: "甲球房·3号桌", value: 12, room_name: "甲球房", table_no: "3号桌" },
    { name: "乙球房·3号桌", value: 7, room_name: "乙球房", table_no: "3号桌" },
    { name: "丙球房·8号桌", value: 3, room_name: "丙球房", table_no: "8号桌" }
  ],
  room_top: [
    { name: "甲球房", value: 25 },
    { name: "乙球房", value: 14 },
    { name: "丙球房", value: 6 }
  ],
  total: 52
};

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1700, height: 950 } });
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 160)));

const listQueries = [];
await page.route("**/api/auth/login", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" }) })
);
await page.route("**/api/stats/charts**", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify(CHARTS_MOCK) })
);
await page.route("**/api/records?**", r => {
  if (r.request().method() === "GET") {
    listQueries.push(new URL(r.request().url()).searchParams);
  }
  return r.fallback();
});

// 登录
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

// 进统计页
await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(5000);
const bodyText = await page.locator("body").innerText();
ok("球房排行卡标题存在", bodyText.includes("球房售后排行 TOP10"));
ok("球桌排行卡标题存在", bodyText.includes("球桌售后排行 TOP10"));

/**
 * 在指定图表容器里定位第 idx 个彩色条形 path（按 fill 过滤），返回视口坐标。
 * ECharts series data 顺序 = DOM path 顺序；渲染时已 reverse，
 * idx=0 → 最底部（值最小），最后一个 → 顶部（值最大）。
 */
async function locateBar(boxIdx, fillColor, expectCount) {
  // 先滚动到图表容器（排名卡在页面下方，条形 rect 可能超出视口，
  // 不滚动直接 mouse.click 会落空）
  await page.evaluate((boxIdx) => {
    const el = document.querySelectorAll(".chart-box")[boxIdx];
    el?.scrollIntoView({ block: "center" });
  }, boxIdx);
  await page.waitForTimeout(400);
  return page.evaluate(({ boxIdx, fillColor, expectCount }) => {
    const el = document.querySelectorAll(".chart-box")[boxIdx];
    const svg = el?.querySelector("svg");
    if (!svg) return { error: "no svg" };
    const paths = [...svg.querySelectorAll("path")].filter(p => {
      const f = (p.getAttribute("fill") || "").toLowerCase().replace(/\s/g, "");
      return f === fillColor.toLowerCase();
    });
    if (paths.length < expectCount) {
      return { error: `paths=${paths.length} < ${expectCount}` };
    }
    const hits = paths.map(p => {
      const b = p.getBoundingClientRect();
      return { x: b.left + b.width / 2, y: b.top + b.height / 2, w: b.width * b.height };
    });
    return { hits };
  }, { boxIdx, fillColor, expectCount });
}

// 图表顺序（stats 页 chart-box）：0 地区 1 每日 2 我方 3 类型 4 aging 5 球房 6 球桌
// ⚠️ SVG renderer 下条形 path 的 fill 是 #rrggbb 十六进制（非 rgb()）
// 5. 球房排行：fill=PALETTE[2] #597ef7
const roomPos = await locateBar(5, "#597ef7", 3).catch(() => null);
ok("球房排行条形渲染（3 条）", !!roomPos && !roomPos.error,
  roomPos ? JSON.stringify(roomPos).slice(0, 80) : "null");

// 6. 球桌排行：fill=PALETTE[5] #ff7a45；两球房同名"3号桌"= 3 条分列
const tblPos = await locateBar(6, "#ff7a45", 3).catch(() => null);
ok("球桌排行条形渲染（跨球房同名桌分列=3 条）", !!tblPos && !tblPos.error,
  tblPos ? JSON.stringify(tblPos).slice(0, 80) : "null");

// y 轴标签含联合名（"甲球房·3号桌" 等），证明名称口径
const yLabels = await page.evaluate(() => {
  const el = document.querySelectorAll(".chart-box")[6];
  return [...(el?.querySelectorAll("svg text") || [])].map(t => t.textContent);
});
ok("球桌排行 y 轴显示「球房·桌号」联合名",
  yLabels.some(t => t.includes("甲球房·3号桌")) && yLabels.some(t => t.includes("乙球房·3号桌")),
  JSON.stringify(yLabels).slice(0, 120));

// ---- 7. 点球桌排行最大条形（reverse 渲染后 DOM 最后一个 = 甲球房·3号桌） ----
if (tblPos && tblPos.hits) {
  const top = tblPos.hits[tblPos.hits.length - 1];
  await page.mouse.click(top.x, top.y);
  await page.waitForTimeout(3500);
  const url = decodeURIComponent(page.url());
  ok("球桌排行点击跳列表", /aftersale\/list/.test(page.url()), page.url());
  ok("URL 带 room_name=甲球房", url.includes("room_name=甲球房"), url);
  ok("URL 带 table_no=3号桌", url.includes("table_no=3号桌"), url);
  const q = listQueries.filter(x => x.get("page_size") !== "200").pop();
  ok("列表请求带 room_name+table_no 参数",
    q && q.get("room_name") === "甲球房" && q.get("table_no") === "3号桌",
    q ? q.toString().slice(0, 140) : "none");
  const t2 = await page.locator("body").innerText();
  ok("列表页显示「球房/球桌」筛选标签", t2.includes("球房/球桌"), t2.slice(0, 80));

  // 关闭标签 → 参数清除（限定筛选栏内：页面别处也有隐藏的 warning 标签）
  await page.locator(".el-tag:visible").filter({ hasText: "甲球房" }).first().locator(".el-tag__close").click();
  await page.waitForTimeout(2500);
  const url2 = decodeURIComponent(page.url());
  ok("关闭标签后 room_name/table_no 已清除",
    !url2.includes("room_name") && !url2.includes("table_no"), page.url());
}

// ---- 8. 点球房排行最大条形 → 只带 room_name ----
await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
// ⚠️ SVG renderer 下条形 path 的 fill 是 #rrggbb 十六进制（与上方第 138 行一致），
// 不是 rgb()——此前误用 rgb() 致 locateBar 匹配 0 条、下面 if 块断言被静默跳过
const roomPos2 = await locateBar(5, "#597ef7", 3).catch(() => null);
if (roomPos2 && roomPos2.hits) {
  const top = roomPos2.hits[roomPos2.hits.length - 1];
  await page.mouse.click(top.x, top.y);
  await page.waitForTimeout(3500);
  const url = decodeURIComponent(page.url());
  ok("球房排行点击跳列表带 room_name=甲球房",
    /aftersale\/list/.test(page.url()) && url.includes("room_name=甲球房"), page.url());
  ok("球房排行跳转不带 table_no", !url.includes("table_no"), url);
}

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
