// 自定义图表验收：5 canvas + 维度切换交互 + 视图保存持久化
import { chromium } from 'playwright-core';

const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1700 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto('http://49.235.34.253/', { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(3500);

  const canvases = await page.locator('.chart-box canvas').count();
  console.log('ECharts canvas 数量 =', canvases);

  // 自定义图默认配置（issue_type + count + bar）
  const cust = await page.locator('.cust-toolbar').count();
  console.log('配置器存在 =', cust === 1);

  // 交互：切换维度 → 地区
  await page.click('.cust-toolbar .el-select >> nth=0');
  await page.waitForTimeout(400);
  await page.click('.el-select-dropdown__item:has-text("地区")');
  await page.waitForTimeout(1200); // 防抖 250ms + 请求
  const custCanvas = await page.locator('.chart-card:has(.cust-toolbar) canvas').count();
  console.log('切换维度后自定义图 canvas =', custCanvas);

  // 保存视图（Element Plus 自绘 prompt：真实输入框 + 确定按钮）
  await page.click('button:has-text("保存视图")');
  await page.waitForSelector('.el-message-box__input input', { timeout: 5000 });
  await page.fill('.el-message-box__input input', '测试视图A');
  await page.click('.el-message-box__btns .el-button--primary');
  await page.waitForTimeout(800);
  const savedViews = await page.evaluate(() => JSON.parse(localStorage.getItem('aftersale_chart_views') || '[]'));
  console.log('保存后视图数 =', savedViews.length, savedViews[0] ? JSON.stringify(savedViews[0].name) : '');

  // 刷新页面 → localStorage 视图仍在
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const viewCount = await page.evaluate(() => (JSON.parse(localStorage.getItem('aftersale_chart_views') || '[]').length));
  console.log('reload 后 localStorage 视图数 =', viewCount);

  // KPI/表格回归
  const kpi = await page.locator('.kpi .num').allInnerTexts();
  const rows = await page.locator('.el-table__body-wrapper tbody tr').count();
  console.log('KPI =', JSON.stringify(kpi), ' 表格行数 =', rows);
  console.log('JS 错误数 =', errors.length, errors.length ? JSON.stringify(errors.slice(0, 2)) : '');
  await page.screenshot({ path: '../../docs/验收_售后面板Web_自定义图表.png' });
  console.log('截图 docs/验收_售后面板Web_自定义图表.png');
} finally {
  await browser.close();
}
