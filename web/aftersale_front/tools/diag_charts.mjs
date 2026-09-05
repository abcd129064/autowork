// 诊断：ECharts canvas 宽度 vs 容器宽度（初始 / 窗口缩放 / 侧栏折叠）
import { chromium } from 'playwright-core';
const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const URL = process.env.SITE_URL || 'http://49.235.34.253/';

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
await page.waitForTimeout(2800);

async function measure(label) {
  const rows = await page.evaluate(() => {
    return [...document.querySelectorAll('.chart-box')].map((box, i) => {
      const cv = box.querySelector('canvas');
      return {
        i,
        box: Math.round(box.clientWidth),
        canvas: cv ? Math.round(cv.getBoundingClientRect().width) : 0,
        diff: cv ? Math.round(cv.getBoundingClientRect().width - box.clientWidth) : 0,
      };
    });
  });
  console.log(label, JSON.stringify(rows));
}

await measure('初始 1600px:');
await page.setViewportSize({ width: 1200, height: 800 });
await page.waitForTimeout(500);
await measure('缩到 1200px:');
await page.setViewportSize({ width: 1600, height: 900 });
await page.waitForTimeout(500);
await measure('放大回 1600px:');
await page.locator('.topbar .collapse-btn').click();
await page.waitForTimeout(400);
await measure('折叠侧栏后:');
await browser.close();
