// verify_required.mjs — 必填拦截验证：空表单/缺项提交被拦 + 星号显示
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
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

// mocks：登录放行；创建请求若发出即算违规（计数）
let postCount = 0;
await page.route("**/api/auth/login", r =>
  r.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ token: "fake.jwt.token", user: "admin" }) })
);
await page.route("**/api/records", r => {
  if (r.request().method() === "POST") postCount++;
  return r.fallback();
});

// 登录
await page.goto(`${base}/v2/`, { waitUntil: "domcontentloaded" });
await page.waitForURL(/login/, { timeout: 15000 }).catch(() => {});
await page.waitForSelector('input[type="text"]', { timeout: 15000 });
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

// 打开新增弹窗
await page.goto(`${base}/v2/#/aftersale/list`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: /新增/ }).first().click();
await page.waitForTimeout(1200);
const dlg = page.locator(".el-dialog").last();

// ---- 1. 必填星号显示 ----
const starCount = await dlg.locator(".el-form-item.is-required").count();
ok("必填项带红星标记（类型/球房/地区/问题/是否解决）", starCount >= 5, `is-required=${starCount}`);

// ---- 2. 空表单提交：被拦 + 弹窗不关 + 无请求 ----
await dlg.getByRole("button", { name: /确 ?定/ }).first().click();
await page.waitForTimeout(1200);
ok("空表单提交弹窗不关闭", await dlg.isVisible().catch(() => false));
const errCount = await dlg.locator(".el-form-item__error:visible").count();
ok("缺项字段显示红字错误提示", errCount >= 2, `error labels=${errCount}`);
ok("空表单提交未发出创建请求", postCount === 0, `postCount=${postCount}`);

// ---- 3. 只填部分：仍拦（缺地区） ----
const room = dlg.locator('.el-form-item:has(label:text-is("球房")) input').first();
await room.fill("拦截验证球房");
await dlg.locator('.el-form-item:has(label:text-is("问题")) textarea').fill("必填拦截验证");
await dlg.getByRole("button", { name: /确 ?定/ }).first().click();
await page.waitForTimeout(1200);
ok("缺地区提交弹窗不关闭", await dlg.isVisible().catch(() => false));
ok("缺地区提交未发出创建请求", postCount === 0, `postCount=${postCount}`);

// ---- 4. 填齐后放行 ----
await dlg.locator('.el-form-item:has(label:text-is("地区")) input').first().click();
await page.waitForTimeout(600);
await page.locator(".el-select-dropdown__item:visible").first().click();
await page.waitForTimeout(400);
const typeItem = dlg.locator(".el-form-item").filter({ hasText: "问题类型" }).first();
await typeItem.locator("input").first().click();
await page.waitForTimeout(600);
await page.locator(".el-select-dropdown__item:visible").first().click();
await page.waitForTimeout(400);
await dlg.getByRole("button", { name: /确 ?定/ }).first().click();
await page.waitForTimeout(1800);
ok("填齐后创建请求发出", postCount === 1, `postCount=${postCount}`);

const passed = results.filter(r => r.pass).length;
console.log(`\n===== ${passed}/${results.length} passed =====`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
