// v0.3.1 UI 修改验收：① 无 MySQL 标签/版本号 ② 侧边栏可折叠（无动画瞬时切换）③ 无"售"logo 图标 ④ 菜单纯文本、折叠态显首字
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
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 25000 });
  await page.waitForTimeout(2800);

  const body = await page.locator('body').innerText();

  // ① 右上角字样已删除
  ok('无 "MySQL 已连接" 字样', !body.includes('MySQL 已连接'));
  ok('无 "数据库未连接" 字样', !body.includes('数据库未连接'));
  ok('无 "v0.2" 字样', !body.includes('v0.2'));

  // ③ logo 图标已删除（保留文字）
  const icCount = await page.locator('.topbar .logo .ic').count();
  ok('"售" 圆形图标已删除', icCount === 0);
  const logoText = await page.locator('.topbar .logo').innerText().catch(() => '');
  ok('logo 保留 "AutoWork 售后"', logoText.includes('AutoWork 售后'), logoText.trim());
  ok('面包屑保留', body.includes('工作台 / 售后面板'));

  // ② 折叠按钮存在
  const btn = page.locator('.topbar .collapse-btn');
  ok('折叠按钮存在', (await btn.count()) === 1);
  ok('菜单无 emoji 彩色图标', (await page.locator('.sidebar .item .ic').count()) === 0);
  await page.screenshot({ path: '../../docs/验收_v03_展开态.png' });

  // 展开 → 折叠
  await btn.click();
  await page.waitForTimeout(300);
  const w1 = await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width);
  ok('折叠后侧边栏宽 ≈56px', Math.abs(w1 - 56) < 3, `w=${w1}`);
  const tVisible = await page.locator('.sidebar .item .t').first().isVisible().catch(() => false);
  ok('折叠后菜单文字隐藏', !tVisible);
  const shortText = await page.locator('.sidebar .item .short').allInnerTexts().catch(() => []);
  ok('折叠后显示首字（填/记/设）', JSON.stringify(shortText) === JSON.stringify(['填', '记', '设']), shortText.join('/'));
  const footVisible = await page.locator('.sidebar .side-foot').isVisible().catch(() => false);
  ok('折叠后底部提示隐藏', !footVisible);
  await page.screenshot({ path: '../../docs/验收_v03_折叠态.png' });

  // localStorage 持久化：刷新后仍是折叠态
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const w2 = await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width);
  ok('刷新后折叠状态保持', Math.abs(w2 - 56) < 3, `w=${w2}`);

  // 折叠 → 展开
  await page.locator('.topbar .collapse-btn').click();
  await page.waitForTimeout(450);
  const w3 = await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width);
  ok('展开恢复 200px', Math.abs(w3 - 200) < 3, `w=${w3}`);

  // 图表在折叠/展开后正常 resize（canvas 宽度 > 0）
  const canvasW = await page.locator('.chart-box canvas').first().evaluate(el => el.getBoundingClientRect().width).catch(() => 0);
  ok('ECharts 已随宽度自适应', canvasW > 100, `canvas=${Math.round(canvasW)}px`);

  console.log(results.join('\n'));
  const fails = results.filter(r => r.startsWith('FAIL')).length;
  console.log(`\n${results.length - fails}/${results.length} passed`);
  process.exitCode = fails ? 1 : 0;
} finally {
  await browser.close();
}
