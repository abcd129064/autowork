// vue-pure-admin 本地运行渲染验收
// 用法（从 web/aftersale_front 目录运行以复用其 playwright-core）：
//   node ../vue-pure-admin/tools/verify_pure_admin.mjs
import { chromium } from 'playwright-core';
import { mkdirSync } from 'node:fs';

const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-153.0.8010.36/chrome.exe';
const BASE = process.env.SITE_URL || 'http://localhost:8848';
const OUT = 'C:/Users/shen_zhe/Desktop/autowork/tools/_pa_shots';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--lang=zh-CN'],
});

try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });

  await page.goto(BASE + '/#/login', { waitUntil: 'domcontentloaded', timeout: 40000 });
  await page.waitForTimeout(4000); // 等 Vue 挂载 + 动画

  console.log('===== URL =====', page.url());
  console.log('===== TITLE =====', await page.title());

  const text = await page.locator('body').innerText().catch(() => '');
  console.log('===== PAGE TEXT =====');
  console.log(text.slice(0, 1200));

  await page.screenshot({ path: `${OUT}/01_login.png` });
  console.log('screenshot: 01_login.png');

  // 尝试登录（默认账号 admin / admin123）
  const userInput = page.locator('input[type="text"], input:not([type="password"]):not([type="checkbox"])').first();
  const pwdInput = page.locator('input[type="password"]').first();
  if (await userInput.count() && await pwdInput.count()) {
    await userInput.fill('admin');
    await pwdInput.fill('admin123');
    await page.screenshot({ path: `${OUT}/02_login_filled.png` });
    console.log('screenshot: 02_login_filled.png');

    const loginBtn = page.locator('button:has-text("登录"), button:has-text("Login")').first();
    if (await loginBtn.count()) {
      await loginBtn.click();
      await page.waitForTimeout(6000); // 等验证码/接口/跳转
      console.log('===== AFTER LOGIN URL =====', page.url());
      await page.screenshot({ path: `${OUT}/03_after_login.png` });
      const t2 = await page.locator('body').innerText().catch(() => '');
      console.log('===== AFTER LOGIN TEXT =====');
      console.log(t2.slice(0, 1200));
      console.log('screenshot: 03_after_login.png');
    } else {
      console.log('login button NOT FOUND');
    }
  } else {
    console.log('login inputs NOT FOUND');
  }

  console.log('===== ERRORS (' + errors.length + ') =====');
  errors.slice(0, 20).forEach(e => console.log(e));
} finally {
  await browser.close();
}
