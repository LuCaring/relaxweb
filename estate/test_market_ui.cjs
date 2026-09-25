// Run with NODE_PATH pointing to the bundled node_modules containing Playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || 'python';
const snapshot = JSON.parse(execFileSync(python, ['-B', '-c', `
import json, sqlite3, time
from estate import init_estate, estate_state
conn=sqlite3.connect(':memory:')
conn.execute('CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)')
conn.execute("INSERT INTO users VALUES ('alice',10000)")
init_estate(conn)
print(json.dumps(estate_state(conn,'alice',int(time.time()))))
`], { cwd: root, encoding: 'utf8' }));

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1100, height: 800}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://estate-market.test/**', async route => {
      const file = path.resolve(root, '.' + new URL(route.request().url()).pathname);
      if (!file.startsWith(root + path.sep)) return route.fulfill({status: 403});
      try {
        const body = await fs.readFile(file);
        const contentType = {'.html': 'text/html', '.js': 'text/javascript',
          '.css': 'text/css', '.png': 'image/png', '.json': 'application/json'}[path.extname(file)] || 'application/octet-stream';
        await route.fulfill({body, contentType});
      } catch { await route.fulfill({status: 404}); }
    });
    await page.addInitScript(() => {
      window.sent = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
    });
    await page.goto('http://estate-market.test/game.html');
    await page.evaluate(async data => {
      window.core = await import('/assets/js/core.js');
      core.setSignedIn({username: 'alice', nickname: 'Alice', coins: 10000});
      core.state.hallPage = 'estate'; core.renderGameView();
      core.handleServerMessage({type: 'estate_state', ...data});
      window.marketUi = (await import('/assets/js/estate/ui.js')).createEstateUI(document.querySelector('.estate-root'));
      marketUi.interact({kind: 'lottery'});
    }, snapshot);
    assert.equal(await page.locator('.estate-circus-sidebar button').count(), 2);
    await page.getByRole('button', {name: '模拟交易'}).click();
    await page.waitForFunction(() => sent.some(message => message.type === 'estate_market_get'));
    const request = await page.evaluate(() => sent.find(message => message.type === 'estate_market_get'));
    const market = {name: '星潮模拟指数', price: 1000, quote_minute: 123,
      available: true, source: '游戏内模拟', source_kind: 'simulated', fee_rate: 0.005, shares: 0,
      cost_basis: 0, market_value: 0, realized_pnl: 0,
      history: [{time: 60, price: 995}, {time: 120, price: 1000}],
      candles: {
        minute: [{time: 60, open: 990, high: 995, low: 990, close: 995},
          {time: 120, open: 995, high: 1002, low: 995, close: 1000}],
        hour: [{time: 0, open: 990, high: 1002, low: 990, close: 1000}],
        day: [{time: 0, open: 990, high: 1002, low: 990, close: 1000}],
      }};
    await page.evaluate(({request, market}) => core.handleServerMessage({
      type: 'estate_market_state', request_id: request.request_id, market,
    }), {request, market});
    assert.match(await page.locator('.estate-market .estate-sheet-note').innerText(), /当前使用游戏内模拟走势/);
    assert.equal(await page.getByRole('img', {name: '分钟K线图'}).count(), 1);
    await page.getByRole('button', {name: '小时K线'}).click();
    assert.equal(await page.getByRole('img', {name: '小时K线图'}).count(), 1);
    await page.getByRole('button', {name: '日K线'}).click();
    assert.equal(await page.getByRole('img', {name: '日K线图'}).count(), 1);
    await page.getByRole('spinbutton', {name: '交易份额'}).fill('0.125');
    assert.match(await page.locator('.estate-market-estimate').innerText(), /125/);
    await page.getByRole('button', {name: '买入'}).click();
    const trade = await page.evaluate(() => sent.find(message => message.type === 'estate_market_trade'));
    assert.deepEqual([trade.side, trade.quantity], ['buy', '0.125']);
    await page.evaluate(({snapshot, trade, market}) => core.handleServerMessage({
      type: 'estate_state', ...snapshot, request_id: trade.request_id,
      result: {action: 'market_trade', side: 'buy', quantity: 0.125, price: 1000, amount: 125.63,
        market: {...market, shares: 0.125, cost_basis: 125.63, market_value: 125}},
    }), {snapshot, trade, market});
    await page.waitForFunction(() => document.querySelector('.estate-market-holdings')?.textContent.includes('0.125'));
    assert.match(await page.locator('.estate-market-message').innerText(), /买入 0.125 份/);
    await page.evaluate(async () => {
      const {estateStore} = await import('/assets/js/estate/state.js');
      estateStore.snapshot.profile.pet_level = 4;
      estateStore.snapshot.profile.penguin_level = 1;
      estateStore.snapshot.profile.active_pet = 'stinky_penguin';
      marketUi.interact({kind: 'general_store'});
    });
    assert.equal(await page.getByRole('button', {name: '设为出场宠物'}).count(), 1);
    await page.getByRole('button', {name: '设为出场宠物'}).click();
    assert.equal((await page.evaluate(() => sent.at(-1))).pet, 'doudou');
    await page.evaluate(() => marketUi.interact({kind: 'lottery'}));
    await page.getByRole('button', {name: '查看抽奖记录'}).click();
    await page.waitForFunction(() => sent.some(message => message.type === 'estate_lottery_history'));
    const historyRequest = await page.evaluate(() => sent.find(message => message.type === 'estate_lottery_history'));
    await page.evaluate(requestId => core.handleServerMessage({type: 'estate_lottery_history',
      request_id: requestId, history: [{created_at: 2000000000, award: 'coins_250', coins_awarded: 250}]}),
    historyRequest.request_id);
    assert.match(await page.locator('.estate-lottery-history').innerText(), /250 金币/);
    assert.deepEqual(errors, []);
    console.log('PASS circus sidebar, minute quote, fractional trade request and holdings');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
