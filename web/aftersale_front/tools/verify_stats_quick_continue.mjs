// verify_stats_quick_continue.mjs — 本地仿真验证：
// 1) 统计页 4 图点击 → 跳列表带筛选（region/occurred_at/is_our_problem/issue_type）
// 2) 表单常用句库（预置/填入/增删/localStorage）
// 3) 连续录入（新增成功弹窗不关、问题字段清空、填写人保留）
// 注意：连续录入用 route mock POST /api/records，不写真实生产库
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const CHROME = [
  "C:/Users/shen_zhe/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe",
  "C:/Users/shen_zhe/.agents/browsers/chrome-153.0.8010.36/chrome.exe"
].find(p => existsSync(p));

const port = process.argv[2] || "8899";
// 可选第二参数：目标站点（默认本地仿真器）。生产冒烟：node verify_stats_quick_continue.mjs 8899 49.235.34.253
const base = process.argv[3] ? `http://${process.argv[3]}` : `http://127.0.0.1:${port}`;
const results = [];
function ok(n, c, extra = "") {
  results.push({ n, pass: !!c });
  console.log(`${c ? "PASS" : "FAIL"}  ${n}${extra ? "  -- " + extra : ""}`);
}

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1700, height: 950 } });
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 200)));

// 记录列表 GET 请求参数（排除 loadFacets 的 page_size=200）
const listQueries = [];
let createPayload = null;
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
await page.route("**/api/tables/search*", async r => {
  return r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ rows: [{ name: "07-02", roomName: "唯一命中球房", snk_code: "snk_12345", city: "上海" }] }) });
});
await page.route("**/api/records", r => {
  if (r.request().method() === "POST") {
    createPayload = r.request().postDataJSON();
    return r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ id: 999901, creator: "admin" }) });
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

/**
 * SVG 路径点击辅助：renderer=svg，系列图形是 <path>。
 * getPointAtLength 取轮廓中点（必然落在图形上），换算视口坐标后点击。
 */
async function clickSvgPath(chartIdx, filter) {
  return await page.evaluate(({ chartIdx, filter }) => {
    const boxes = document.querySelectorAll(".chart-box");
    const box = boxes[chartIdx];
    if (!box) return { error: "no chart box" };
    const svg = box.querySelector("svg");
    if (!svg) return { error: "no svg" };
    const paths = [...svg.querySelectorAll("path")].filter(p => {
      const f = (p.getAttribute("fill") || "").toLowerCase().replace(/\s/g, "");
      if (f === "" || f === "none" || f === "rgb(0,0,0)" || f === "#000" || f === "black") return false;
      return filter ? filter(p) : true;
    });
    if (!paths.length) return { error: "no path" };
    // 选包围盒面积最大的 path（柱/扇区都比装饰线大）
    let best = null, bestArea = 0;
    for (const p of paths) {
      try {
        const b = p.getBoundingClientRect();
        const a = b.width * b.height;
        if (a > bestArea) { bestArea = a; best = p; }
      } catch { /* ignore */ }
    }
    if (!best || bestArea <= 0) return { error: "no sized path" };
    const len = best.getTotalLength();
    const pt = best.getPointAtLength(len / 2);
    const r = best.getBoundingClientRect(); // 屏幕坐标包围盒
    const bb = best.getBBox();              // 用户坐标包围盒
    // pt（用户坐标）在用户包围盒中的比例 → 映射到屏幕包围盒
    const bx = bb.width ? (pt.x - bb.x) / bb.width : 0.5;
    const by = bb.height ? (pt.y - bb.y) / bb.height : 0.5;
    return {
      x: r.left + bx * r.width,
      y: r.top + by * r.height,
      fill: best.getAttribute("fill"),
      area: bestArea
    };
  }, { chartIdx, filter });
}

// ================= 统计页图表点击 =================
await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(5000);
const svgCount = await page.locator(".chart-box svg").count();
ok("统计页图表渲染（SVG）", svgCount >= 4, `svg=${svgCount}`);
const hintText = await page.locator("body").innerText();
ok("图表标题带点击提示", hintText.includes("跳列表筛选") && hintText.includes("按发生日期筛选"));

// ---- 1. 地区环形图（第 1 个图）点击 → region ----
{
  const pos = await clickSvgPath(0);
  ok("地区图扇区定位", !!pos && !pos.error, JSON.stringify(pos || {}).slice(0, 120));
  if (pos && !pos.error) {
    listQueries.length = 0;
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    const m = url.match(/region=([^&]+)/);
    ok("地区扇区点击跳列表带 region", /aftersale\/list/.test(page.url()) && !!m, page.url());
    const q = listQueries.filter(x => x.get("page_size") !== "200").pop();
    ok("列表请求带 region 筛选", q && q.get("region") === (m ? m[1] : "~"),
      q ? q.toString().slice(0, 140) : "none");
    // 列表筛选栏地区下拉已选上该地区
    const regionSelectText = await page.locator('.el-form-item:has(label:text-is("地区："))').innerText().catch(() => "");
    ok("列表页地区下拉回显", !!m && regionSelectText.includes(m[1]), regionSelectText.replace(/\n/g, " "));
  }
  await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
}

// ---- 2. 每日柱状图（第 2 个图）点击 → occurred_at ----
{
  const pos = await clickSvgPath(1);
  ok("每日图柱体定位", !!pos && !pos.error, JSON.stringify(pos || {}).slice(0, 120));
  if (pos && !pos.error) {
    listQueries.length = 0;
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    const m = url.match(/occurred_at=(20\d\d-\d\d-\d\d)/);
    ok("每日柱点击跳列表带 occurred_at", /aftersale\/list/.test(page.url()) && !!m, page.url());
    const q = listQueries.filter(x => x.get("page_size") !== "200").pop();
    ok("列表请求带 occurred_at 筛选", q && q.get("occurred_at") === (m ? m[1] : "~"),
      q ? q.toString().slice(0, 140) : "none");
  }
  await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
}

// ---- 3. 我方问题环（第 3 个图）点击扇区 → is_our_problem ----
{
  const pos = await clickSvgPath(2);
  ok("我方问题环扇区定位", !!pos && !pos.error, JSON.stringify(pos || {}).slice(0, 120));
  if (pos && !pos.error) {
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    ok("我方问题环点击跳列表带 is_our_problem",
      /aftersale\/list/.test(page.url()) && /is_our_problem=(是|否)/.test(url), page.url());
  }
  await page.goto(`${base}/v2/#/aftersale/stats`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
}

// ---- 4. 问题类型条形图（第 4 个图）点击 → issue_type ----
{
  const pos = await clickSvgPath(3);
  ok("类型条定位", !!pos && !pos.error, JSON.stringify(pos || {}).slice(0, 120));
  if (pos && !pos.error) {
    await page.mouse.click(pos.x, pos.y);
    await page.waitForTimeout(2500);
    const url = decodeURIComponent(page.url());
    ok("类型条点击跳列表带 issue_type",
      /aftersale\/list/.test(page.url()) && /issue_type=[^&]+/.test(url), page.url());
  }
}

// ================= 常用句 + 连续录入（mock 写接口） =================
await page.goto(`${base}/v2/#/aftersale/list`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.evaluate(() => localStorage.setItem("aftersale-quick-phrases", JSON.stringify(["主机没有开机", "遥控器没反应"])));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: /新增/ }).first().click();
await page.waitForTimeout(1200);
const dlg = page.locator(".el-dialog").last();
ok("新增弹窗打开", await dlg.isVisible().catch(() => false));

// ---- 常用句弹层 ----
await dlg.locator('button:has-text("常用句")').first().click();
await page.waitForTimeout(800);
const qpItem = page.locator(".qp-item", { hasText: "主机没有开机" }).first();
ok("常用句弹层展示预置句", await qpItem.isVisible().catch(() => false));

// 点击填入问题框（popover 保持打开）
await qpItem.click();
await page.waitForTimeout(400);
const problemVal = await dlg.locator('.el-form-item:has(label:text-is("问题")) textarea').inputValue();
ok("点击常用句填入问题框", problemVal === "主机没有开机", `problem="${problemVal}"`);

// 再点一句 → 以「；」追加（popover 未关，直接点第二条）
await page.locator(".qp-item", { hasText: "遥控器没反应" }).first().click();
await page.waitForTimeout(400);
const problemVal2 = await dlg.locator('.el-form-item:has(label:text-is("问题")) textarea').inputValue();
ok("再次点击以「；」追加", problemVal2 === "主机没有开机；遥控器没反应", `problem="${problemVal2}"`);

// 添加新句 → localStorage 持久化
const addInput = page.locator(".qp-add input");
await addInput.fill("E2E临时测试句");
await page.locator(".qp-add button").click();
await page.waitForTimeout(500);
const stored = await page.evaluate(() => localStorage.getItem("aftersale-quick-phrases") || "");
ok("添加常用句并持久化", stored.includes("E2E临时测试句"), stored.slice(0, 120));

// 删除该句 → 从 localStorage 移除
const delItem = page.locator(".qp-item", { hasText: "E2E临时测试句" }).first();
const visible = await delItem.isVisible().catch(() => false);
if (visible) {
  await delItem.locator(".qp-del").click();
  await page.waitForTimeout(400);
}
const stored2 = await page.evaluate(() => localStorage.getItem("aftersale-quick-phrases") || "");
ok("删除常用句生效", !stored2.includes("E2E临时测试句"), stored2.slice(0, 120));
await page.keyboard.press("Escape");

// ---- 连续录入：填必填项提交，弹窗不关、字段清空、解决人保留 ----
const roomInput = dlg.locator(".el-autocomplete input").first();
await roomInput.fill("唯一命中球房");
await page.waitForTimeout(1200);
const typeItem = dlg.locator(".el-form-item").filter({ hasText: "问题类型" }).first();
await typeItem.locator("input").first().click();
await page.waitForTimeout(500);
await page.locator(".el-select-dropdown__item:visible").first().click();
await page.waitForTimeout(400);
await dlg.locator('.el-form-item:has(label:text-is("解决人")) input').fill("cont-resolver");

createPayload = null;
await dlg.getByRole("button", { name: /确 ?定/ }).first().click();
await page.waitForTimeout(1800);
ok("创建成功 toast（可连续录入）",
  await page.locator('.el-message:has-text("可连续录入")').isVisible().catch(() => false));
ok("弹窗保持打开（连续录入）", await dlg.isVisible().catch(() => false));
ok("写请求 payload 带问题内容", !!createPayload && (createPayload.problem || "").includes("主机没有开机"),
  createPayload ? JSON.stringify(createPayload).slice(0, 120) : "null");
const problemAfter = await dlg.locator('.el-form-item:has(label:text-is("问题")) textarea').inputValue().catch(() => "");
ok("问题框已清空等待下一条", problemAfter === "", `problem="${problemAfter}"`);
const roomAfter = await roomInput.inputValue().catch(() => "?");
ok("球房/关联已清空", roomAfter === "", `room="${roomAfter}"`);
const resolverAfter = await dlg.locator('.el-form-item:has(label:text-is("解决人")) input').inputValue().catch(() => "");
ok("解决人保留（连续录入记忆）", resolverAfter === "cont-resolver", `resolver="${resolverAfter}"`);
const checkedAfter = (await dlg.locator(".el-radio.is-checked").allInnerTexts().catch(() => [])).map(s => s.trim()).join(",");
ok("判定恢复默认 是/是/否", checkedAfter === "是,是,否", `checked=[${checkedAfter}]`);
await page.screenshot({ path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/continue_entry.png" });

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
