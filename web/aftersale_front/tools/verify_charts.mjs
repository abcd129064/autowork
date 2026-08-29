// 图表统计验收：4 个 ECharts canvas 渲染 + 页面无 JS 错误 + 截图
import { chromium } from 'playwright-core';

const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1500 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('http://49.235.34.253/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(3500); // 等 API + ECharts 渲染

  const canvases = await page.locator('.chart-box canvas').count();
  console.log('ECharts canvas 数量 =', canvases);
  const chartTitles = await page.locator('.chart-card .c-title').allInnerTexts();
  console.log('图表标题 =', JSON.stringify(chartTitles));
  // KPI 兜底验证
  const kpi = await page.locator('.kpi .num').allInnerTexts();
  console.log('KPI =', JSON.stringify(kpi));
  const rows = await page.locator('.el-table__body-wrapper tbody tr').count();
  console.log('表格行数 =', rows);
  console.log('JS 错误数 =', errors.length, errors.length ? JSON.stringify(errors.slice(0, 3)) : '');
  await page.screenshot({ path: '../../docs/验收_售后面板Web_图表.png' });
  console.log('截图已保存 docs/验收_售后面板Web_图表.png');
} finally {
  await browser.close();
}
