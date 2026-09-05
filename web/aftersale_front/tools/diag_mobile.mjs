import { chromium } from 'playwright-core';
const CHROME = 'C:/Users/shen_zhe/.agent-browser/browsers/chrome-152.0.7977.64/chrome.exe';
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] });
const m = await browser.newPage({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true });
await m.goto('http://49.235.34.253/', { waitUntil: 'domcontentloaded', timeout: 25000 });
await m.waitForTimeout(2800);
const off = await m.evaluate(() => {
  const vw = document.documentElement.clientWidth;
  const inScroller = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === 'auto' || o === 'scroll' || o === 'hidden') return true;
    }
    return false;
  };
  const bad = [];
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 5 && r.right > vw + 1 && !inScroller(el)) {
      bad.push(el.tagName.toLowerCase() + '.' + String(el.className).slice(0, 50) + ' right=' + Math.round(r.right) + ' w=' + Math.round(r.width));
    }
  });
  return { vw, scrollW: document.documentElement.scrollWidth, bad: bad.slice(0, 15) };
});
console.log(JSON.stringify(off, null, 1));
await browser.close();
