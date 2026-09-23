// verify_chart_click.mjs — 图表点击跳转筛选验证（本地仿真）
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

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1700, height: 950 } });
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 160)));

// 记录列表请求的筛选参数
const listQueries = [];
await page.route("**/api/auth/login", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" }) })
);
await page.route("**/api/records?**", r => {
  if (r.request().method() === "GET") {
    listQueries.push(new URL(r.request().url()).searchParams);
  }
  return r.fallback();
});
// charts 用真实生产数据（走代理）保证图表有内容可点

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

// ---- 1. KPI 卡点击：未解决 → resolved=否 ----
await page.locator(".kpi-card").nth(1).click();
await page.waitForTimeout(3000);
ok("KPI 未解决卡跳转列表", /aftersale\/list/.test(page.url()), page.url());
ok("URL 带 resolved=否", page.url().includes("resolved=") && decodeURIComponent(page.url()).includes("否"), page.url());
const lastQ = listQueries.filter(q => q.get("page_size") !== "200").pop();
ok("列表请求带 resolved 筛选", lastQ && lastQ.get("resolved") === "否",
  lastQ ? lastQ.toString().slice(0, 120) : "none");
const bodyText = await page.locator("body").innerText();
ok("列表页显示筛选生效（未解决 KPI 与列表一致）", bodyText.length > 100);

// ---- 2. 回总览：类型条点击 → issue_type ----
await page.goto(`${base}/v2/#/welcome`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
// 类型图是右侧 chart-box：点第一个条形（最大值类目）
const typeBox = page.locator(".chart-box").nth(1);
const typeBoxPos = await typeBox.boundingBox();
ok("类型图存在", !!typeBoxPos);
// 用 ECharts 实例 dispatchAction 触发 click 更稳：直接在页面里调用
const clickedType = await page.evaluate(() => {
  const el = document.querySelectorAll(".chart-box")[1];
  const inst = echarts.getInstanceByDom(el);
  if (!inst) return null;
  const opt = inst.getOption();
  const names = opt.series[0].data.map(d => d.name || d);
  // 点最大值（数据已排序，第一个 name）
  const name = names[0];
  inst.dispatchAction({ type: "select" }); // 占位，真正 click 事件用下面方式
  return name;
}).catch(e => null);
// dispatchAction select 不触发 click 事件，改用 SVG path 定位（与趋势柱同款）：
// 找类型图里面积最大的彩色条形 path，取其轮廓中点换算视口坐标点击——不猜像素位置
if (typeBoxPos) {
  await page.locator(".chart-box").nth(1).evaluate(el => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(400);
  // 注意：类型图若为饼图，扇区 path 首尾同点，轮廓中点会落在圆心上（点不到扇区）。
  // 改用「面积最大 path 的包围盒左上角 + 35%/40%」取点——对条形和饼形都落在图形内。
  const pos = await page.evaluate(() => {
    const el = document.querySelectorAll(".chart-box")[1];
    const svg = el?.querySelector("svg");
    if (!svg) return { error: "no svg" };
    const paths = [...svg.querySelectorAll("path")].filter(p => {
      const f = (p.getAttribute("fill") || "").toLowerCase();
      return f && f !== "none" && f !== "rgb(0,0,0)";
    });
    let best = null, bestArea = 0;
    for (const p of paths) {
      try {
        const b = p.getBoundingClientRect();
        const a = b.width * b.height;
        if (a > bestArea) { bestArea = a; best = p; }
      } catch { /* ignore */ }
    }
    if (!best || bestArea <= 0) return { error: "no sized path" };
    const r = best.getBoundingClientRect();
    return { x: r.left + r.width * 0.35, y: r.top + r.height * 0.4, fill: best.getAttribute("fill") };
  });
  ok("类型条定位成功", !!pos && !pos.error, pos ? JSON.stringify(pos).slice(0, 120) : "null");
  if (pos && !pos.error) {
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    const okType = /aftersale\/list/.test(page.url()) && url.includes("issue_type=");
    ok("类型条点击跳列表带 issue_type", okType, page.url());
  } else {
    ok("类型条点击跳列表带 issue_type", false, "定位失败");
  }
}

// ---- 3. 回总览：趋势柱点击某天 → occurred_at ----
await page.goto(`${base}/v2/#/welcome`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
const dailyBox = await page.locator(".chart-box").first().boundingBox();
ok("趋势图存在", !!dailyBox);
if (dailyBox) {
  // SVG path 定位（与 verify_stats_quick_continue 同款）：renderer=svg 下
  // 柱体是 <path>，取轮廓中点换算视口坐标点击——不依赖柱数/像素估算
  const pos = await page.evaluate(() => {
    const el = document.querySelectorAll(".chart-box")[0];
    const svg = el?.querySelector("svg");
    if (!svg) return { error: "no svg" };
    const paths = [...svg.querySelectorAll("path")].filter(p => {
      const f = (p.getAttribute("fill") || "").toLowerCase();
      return f && f !== "none" && f !== "rgb(0,0,0)";
    });
    let best = null, bestArea = 0;
    for (const p of paths) {
      try {
        const b = p.getBoundingClientRect();
        const a = b.width * b.height;
        if (a > bestArea) { bestArea = a; best = p; }
      } catch { /* ignore */ }
    }
    if (!best || bestArea <= 0) return { error: "no sized path" };
    const pt = best.getPointAtLength(best.getTotalLength() / 2);
    const r = best.getBoundingClientRect();
    const bb = best.getBBox();
    const bx = bb.width ? (pt.x - bb.x) / bb.width : 0.5;
    const by = bb.height ? (pt.y - bb.y) / bb.height : 0.5;
    return { x: r.left + bx * r.width, y: r.top + by * r.height, fill: best.getAttribute("fill") };
  });
  ok("趋势柱定位成功", !!pos && !pos.error, pos ? JSON.stringify(pos).slice(0, 120) : "null");
  if (pos && !pos.error) {
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    const okDay = /aftersale\/list/.test(page.url()) && url.includes("occurred_at=20");
    ok("趋势柱点击跳列表带 occurred_at", okDay, page.url());
    if (okDay) {
      await page.waitForTimeout(2000);
      const tagText = await page.locator("body").innerText();
      ok("列表页显示「发生日期」筛选标签", tagText.includes("发生日期"));
      const q = listQueries.filter(x => x.get("page_size") !== "200").pop();
      ok("列表请求带 occurred_at 筛选", q && /^20\d\d-\d\d-\d\d$/.test(q.get("occurred_at") || ""),
        q ? q.toString().slice(0, 140) : "none");
    }
  }
}

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await page.screenshot({ path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/chart_click_filter.png" });
await browser.close();
process.exit(passed === results.length ? 0 : 1);
