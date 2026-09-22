// Exercise both real finance renderers without a running service or account data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const cases = [
  {kind: 'estate_purchase', label: '庄园支出', amount: -20, detail: '休闲庄园购买：小麦种子 ×1'},
  {kind: 'estate_sale', label: '庄园出售收入', amount: 30, detail: '休闲庄园出售：小麦 ×1'},
  {kind: 'estate_pet_defense', label: '宠物防守结算', amount: -50, detail: '偷菜失败，被邻居的豆豆发现'},
  {kind: 'estate_pet_defense', label: '宠物防守结算', amount: 50, detail: '豆豆阻止邻居偷菜'},
  {kind: 'holdem_daily_reward', label: '每日德扑流水奖励', amount: 20, detail: '每日德扑下注流水满 100 奖励'},
];

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || undefined, args: ['--no-sandbox']});
  try {
    const failures = [];
    for (const name of ['index.html', 'game.html']) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://finance.test/**', async route => {
        const file = path.resolve(root, '.' + new URL(route.request().url()).pathname);
        if (!file.startsWith(root + path.sep)) return route.fulfill({status: 403});
        const contentType = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
          '.png': 'image/png', '.json': 'application/json'}[path.extname(file)] || 'application/octet-stream';
        try { await route.fulfill({body: await fs.readFile(file), contentType}); }
        catch { await route.fulfill({status: 404}); }
      });
      await page.addInitScript(() => {
        window.sockets = [];
        window.WebSocket = class {
          static OPEN = 1;
          constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
          addEventListener(kind, callback) { this.listeners[kind] = callback; }
          send() {}
          close() {}
        };
      });
      await page.goto(`http://finance.test/${name}`);
      await page.evaluate(() => { window.MediaMTXWebRTCReader = class { close() {} }; });
      const deliver = data => page.evaluate(data => sockets[0].listeners.message({data: JSON.stringify(data)}), data);
      await deliver({type: 'resume_success', username: 'alice', coins: 1000});
      await deliver({type: 'finance', coins: 1000, transactions: cases.map(({label, ...row}) =>
        ({...row, balance: 1000, created_at: 1000}))});
      const labels = await page.locator('#financeList .finance-row-kind').allTextContents();
      try { assert.deepEqual(labels, cases.map(row => row.label)); }
      catch { failures.push(`${name}: ${JSON.stringify(labels)}`); }
      for (const [index, item] of cases.entries()) {
        const row = page.locator('#financeList .finance-row').nth(index);
        assert.ok((await row.locator('.finance-row-detail').textContent()).startsWith(item.detail));
        assert.equal(await row.locator('.finance-row-amount').textContent(),
          `${item.amount >= 0 ? '+' : ''}${item.amount.toFixed(2)}`);
        assert.equal(await row.locator('.finance-row-balance').textContent(), '余额 1,000.00');
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    assert.deepEqual(failures, [], 'Both finance pages must translate every estate transaction kind');
    console.log('PASS both finance pages: Chinese estate labels, existing details, signs, balances and reward labels');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
