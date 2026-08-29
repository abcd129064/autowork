// 线上售后面板 Web 渲染验收（复用 agent-browser 已下载的 Chrome，--no-sandbox）
// 用法：node tools/verify_site.mjs  （从 web/aftersale_front 目录运行）
import { chromium } from 'playwright-core';
import { writeFileSync } from 'node:fs';

const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const URL = process.env.SITE_URL || 'http://49.235.34.253/';

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(2500); // 等 Vue 挂载 + API 返回
  const text = await page.locator('body').innerText();
  console.log('===== PAGE TEXT =====');
  console.log(text.slice(0, 1600));
  const shot = await page.screenshot({ path: '../../docs/验收_售后面板Web_线上.png' });
  console.log('===== SCREENSHOT =====', shot ? `${shot.length} bytes` : 'ok');
  // 统计断言：KPI 数字 / 表格行数
  const kpiTotal = await page.locator('.kpi .num.a').first().innerText().catch(() => 'N/A');
  const rowCount = await page.locator('.el-table__body-wrapper tbody tr').count();
  console.log(`KPI 总数=${kpiTotal}  表格行数=${rowCount}`);
  const healthText = await page.locator('.topbar .el-tag').innerText().catch(() => 'N/A');
  console.log(`数据源标签=${healthText}`);
} finally {
  await browser.close();
}
