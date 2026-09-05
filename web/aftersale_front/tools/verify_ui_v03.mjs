// v0.4 UI 验收：① 无 emoji 图标 ② 表格加长 ③ 手机端适配 ④ 侧边栏折叠 ⑤ 无"售"logo/MySQL 字样
// 用法：node tools/verify_ui_v03.mjs  （从 web/aftersale_front 目录运行）
import { chromium } from 'playwright-core';

const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const URL = process.env.SITE_URL || 'http://49.235.34.253/';

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
const results = [];
const ok = (name, cond, extra = '') => {
  results.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  [' + extra + ']' : ''}`);
};
try {
  // ---------- 桌面端 ----------
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(2800);

  const body = await page.locator('body').innerText();

  // ⑤ 旧字样已删除
  ok('无 "MySQL 已连接" 字样', !body.includes('MySQL 已连接'));
  ok('无 "v0.2" 字样', !body.includes('v0.2'));
  const icCount = await page.locator('.topbar .logo .ic').count();
  ok('"售" 圆形图标已删除', icCount === 0);
  const logoText = await page.locator('.topbar .logo').innerText().catch(() => '');
  ok('logo 保留 "AutoWork 售后"', logoText.includes('AutoWork 售后'), logoText.trim());
  ok('面包屑保留', body.includes('工作台 / 售后面板'));

  // ① 无 emoji 图标
  const emojiRe = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2139}]/u;
  ok('页面无 emoji 图标（搜索/图表统计/保存视图/提示）', !emojiRe.test(body));
  const btn = page.locator('.topbar .collapse-btn');
  ok('折叠按钮存在', (await btn.count()) === 1);
  await page.screenshot({ path: '../../docs/验收_v04_桌面.png' });

  // ② 表格加长（桌面视口 900 高 → 表格高度应 > 560）
  const tblH = await page.locator('.el-table').first().evaluate(el => el.getBoundingClientRect().height).catch(() => 0);
  ok('表格高度已加长 (>560px)', tblH > 560, `h=${Math.round(tblH)}px`);

  // ⑥ 图表自适应：窗口缩小时无溢出且 canvas 跟随容器
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.waitForTimeout(500);
  const chartSync = await page.evaluate(() => {
    const overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    const diffs = [...document.querySelectorAll('.chart-box')].map(box => {
      const cv = box.querySelector('canvas');
      return cv ? Math.round(cv.getBoundingClientRect().width - box.clientWidth) : 0;
    });
    return { overflow, maxDiff: Math.max(...diffs.map(Math.abs)), boxW: Math.round(document.querySelector('.chart-box').clientWidth) };
  });
  ok('缩窗 1200px：页面无横向溢出', chartSync.overflow <= 1, `overflow=${chartSync.overflow}px`);
  ok('缩窗 1200px：canvas 与容器同步', chartSync.maxDiff <= 2 && chartSync.boxW < 550, `maxDiff=${chartSync.maxDiff} box=${chartSync.boxW}`);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.waitForTimeout(500);

  // ⑦ 扁平直角风格
  const itemRadius = await page.locator('.sidebar .item.on').evaluate(el => getComputedStyle(el).borderRadius).catch(() => '?');
  ok('菜单选中项无圆角', parseFloat(itemRadius) === 0, `r=${itemRadius}`);
  const btnRadius = await page.locator('.el-button--primary').first().evaluate(el => getComputedStyle(el).borderRadius).catch(() => '?');
  ok('Element Plus 按钮已直角化(≤2px)', parseFloat(btnRadius) <= 2, `r=${btnRadius}`);
  const panelRadius = await page.locator('.charts-panel').evaluate(el => getComputedStyle(el).borderRadius).catch(() => '?');
  ok('图表面板无圆角', parseFloat(panelRadius) === 0, `r=${panelRadius}`);

  // ④ 折叠/展开
  await btn.click();
  await page.waitForTimeout(300);
  const w1 = await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width);
  ok('折叠后侧边栏宽 ≈56px', Math.abs(w1 - 56) < 3, `w=${w1}`);
  const shortText = await page.locator('.sidebar .item .short').allInnerTexts().catch(() => []);
  ok('折叠后显示首字（填/记/设）', JSON.stringify(shortText) === JSON.stringify(['填', '记', '设']), shortText.join('/'));
  await page.locator('.topbar .collapse-btn').click();
  await page.waitForTimeout(300);
  const w3 = await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width);
  ok('展开恢复 200px', Math.abs(w3 - 200) < 3, `w=${w3}`);
  const canvasW = await page.locator('.chart-box canvas').first().evaluate(el => el.getBoundingClientRect().width).catch(() => 0);
  ok('ECharts 已随宽度自适应', canvasW > 100, `canvas=${Math.round(canvasW)}px`);
  await page.close();

  // ---------- 手机端（375×667）----------
  const m = await browser.newPage({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true });
  await m.goto(URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await m.waitForTimeout(2800);
  const mBody = await m.locator('body').innerText();
  ok('手机端：页面正常加载（KPI 可见）', mBody.includes('售后总数'));

  // 无横向溢出
  const overflow = await m.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok('手机端：无横向溢出', overflow <= 1, `overflow=${overflow}px`);

  // 侧栏默认收起为抽屉（translateX 移出视口，用 bounding box 判断）
  const sbOff = await m.locator('.sidebar').evaluate(el => el.getBoundingClientRect().right).catch(() => 999);
  ok('手机端：侧栏默认收起', sbOff <= 1, `right=${Math.round(sbOff)}`);
  // 点开抽屉
  await m.locator('.topbar .collapse-btn').click();
  await m.waitForTimeout(350);
  const sbVisible2 = await m.locator('.sidebar').isVisible().catch(() => false);
  const sbW = await m.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width).catch(() => 0);
  ok('手机端：点按钮展开抽屉（200px 悬浮）', sbVisible2 && Math.abs(sbW - 200) < 3, `w=${sbW}`);
  const drawerOverlap = await m.evaluate(() => {
    const sb = document.querySelector('.sidebar').getBoundingClientRect();
    const main = document.querySelector('.main').getBoundingClientRect();
    return main.width >= document.documentElement.clientWidth - 25; // 主区仍近乎全宽 → 抽屉不占布局
  });
  ok('手机端：抽屉不挤压主区', drawerOverlap);
  await m.screenshot({ path: '../../docs/验收_v04_手机端_抽屉.png' });
  await m.locator('.topbar .collapse-btn').click(); // 收起

  // KPI 两列
  const kpiCols = await m.locator('.kpi-row').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  ok('手机端：KPI 两列布局', kpiCols === 2, `cols=${kpiCols}`);
  // 图表单列
  const chartCols = await m.locator('.charts-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  ok('手机端：图表单列布局', chartCols === 1, `cols=${chartCols}`);
  // 表格高度（手机 58vh ≈ 387）
  const mTblH = await m.locator('.el-table').first().evaluate(el => el.getBoundingClientRect().height).catch(() => 0);
  ok('手机端：表格高度按视口自适应', mTblH > 300 && mTblH < 480, `h=${Math.round(mTblH)}px`);
  await m.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await m.waitForTimeout(400);
  await m.screenshot({ path: '../../docs/验收_v04_手机端_表格.png' });
  await m.close();

  console.log(results.join('\n'));
  const fails = results.filter(r => r.startsWith('FAIL')).length;
  console.log(`\n${results.length - fails}/${results.length} passed`);
  process.exitCode = fails ? 1 : 0;
} finally {
  await browser.close();
}
