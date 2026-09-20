// verify_dialog_v2.mjs — 本地仿真验证：窄弹窗 + 字段对齐 + 球房带出 + 记住上次
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

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
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 200)));

// mocks：登录 + 球桌搜索（唯一命中/多候选两种）+ 创建
await page.route("**/api/auth/login", r =>
  r.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" })
  })
);
await page.route("**/api/tables/search*", async r => {
  const url = new URL(r.request().url());
  const kw = url.searchParams.get("room") || "";
  if (kw.includes("唯一")) {
    return r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ rows: [{ name: "07-02", roomName: "唯一命中球房", snk_code: "snk_12345", city: "上海" }] }) });
  }
  return r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ rows: [
      { name: "01-01", roomName: "多候选球房A店", snk_code: "snk_a", city: "四川" },
      { name: "02-02", roomName: "多候选球房B店", snk_code: "snk_b", city: "江苏" }
    ] }) });
});
await page.route("**/api/records", r => {
  if (r.request().method() === "POST")
    return r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ id: 999901, creator: "admin" }) });
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
await page.waitForTimeout(500);
await page.locator('button:has-text("登录")').first().click();
await page.waitForTimeout(4000);
ok("登录成功", !/login/.test(page.url()), page.url());

// ---- 打开新增弹窗 ----
await page.goto(`${base}/v2/#/aftersale/list`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: /新增/ }).first().click();
await page.waitForTimeout(1200);
const dlg = page.locator(".el-dialog").last();
ok("弹窗打开", await dlg.isVisible().catch(() => false));

// ---- 断言1：弹窗宽度 ≤ 780px ----
const box = await dlg.boundingBox();
ok("弹窗宽度 720px（不再是 62% 宽屏）", box && box.width > 600 && box.width < 780, `w=${Math.round(box?.width || 0)}`);

// ---- 断言2：字段集对齐桌面端（有球房/桌号/响应时间，无账期/设备码/SNK 手输）----
const dlgText = await dlg.innerText();
ok("含「球房」标签", /球房/.test(dlgText));
ok("含「桌号」标签", /桌号/.test(dlgText));
ok("含「响应时间」", /响应时间/.test(dlgText));
ok("不含「账期」", !dlgText.includes("账期"));
ok("不含「设备码」", !dlgText.includes("设备码"));
ok("不含「SNK 码」输入标签", !/SNK 码/.test(dlgText));

// ---- 断言3：默认值（是否解决=是 / 我方问题=是 / 主动发起=否，对齐桌面端）----
const checkedYes = await dlg.locator(".el-radio.is-checked").allInnerTexts();
const checkedText = checkedYes.map(s => s.trim()).join(",");
ok("三组单选默认 是/是/否（对齐桌面端）", checkedText === "是,是,否", `checked=[${checkedText}]`);
ok("「是否解决」默认是", checkedText.startsWith("是"));

// ---- 断言4：球房搜索唯一命中静默带出 ----
const roomInput = dlg.locator(".el-autocomplete input").first();
await roomInput.fill("唯一命中球房");
await page.waitForTimeout(1200);
const tblVal = await dlg.locator(".el-form-item").filter({ hasText: "桌号" }).locator("input").inputValue();
const snkBar = await dlg.innerText();
ok("唯一命中自动带出桌号", tblVal === "07-02", `table_no=${tblVal}`);
ok("关联条显示 SNK", snkBar.includes("snk_12345"));
const regionText = await dlg
  .locator(".el-form-item")
  .filter({ hasText: "地区" })
  .first()
  .innerText();
ok("城市带出地区", regionText.includes("上海"), `regionText=${regionText.replace(/\n/g, " ")}`);

// ---- 断言5：多候选点选带出 ----
await roomInput.fill("多候选");
await page.waitForTimeout(1200);
const popper = page.locator(".el-autocomplete-suggestion li:visible").first();
const hasPop = await popper.isVisible().catch(() => false);
ok("多候选弹出选择列表", hasPop);
if (hasPop) {
  await popper.click();
  await page.waitForTimeout(600);
  const tblVal2 = await dlg.locator(".el-form-item").filter({ hasText: "桌号" }).locator("input").inputValue();
  ok("点选候选带出桌号", tblVal2 === "01-01", `table_no=${tblVal2}`);
}

// ---- 断言6：必填校验（球房/地区新加入）----
// ---- 断言7：填必填项提交成功 + 记住上次 ----
await dlg.locator('.el-form-item:has(label:text-is("问题"))').locator("textarea").fill("preflight 表单改造验证");
await dlg.locator('.el-form-item:has(label:text-is("解决人"))').locator("input").fill("e2e-resolver");
const typeItem = dlg.locator(".el-form-item").filter({ hasText: "问题类型" }).first();
await typeItem.locator("input").first().click();
await page.waitForTimeout(500);
await page.locator(".el-select-dropdown__item:visible").first().click();
await page.waitForTimeout(400);
await dlg.getByRole("button", { name: /确 ?定/ }).first().click();
await page.waitForTimeout(1500);
ok("创建成功（toast）", await page.locator('.el-message:has-text("已新增售后记录")').isVisible().catch(() => false));
const lastUsed = await page.evaluate(() => localStorage.getItem("aftersale-last-used") || "{}");
ok("记住上次填写（本次填的 resolver 已持久化）", lastUsed.includes("resolver"), lastUsed.slice(0, 80));

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await page.screenshot({ path: "C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots/local_dialog_v2.png" });
await browser.close();
process.exit(passed === results.length ? 0 : 1);
