// Real DOM/CSS with mocked WebSocket; use an existing static server via RATING_TEST_URL.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { existsSync } = require('node:fs');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox'],
    executablePath: process.env.CHROME_PATH || ['/usr/bin/google-chrome', '/opt/google/chrome/chrome'].find(existsSync)});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.sent = [];
      window.sockets = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
        send(data) { sent.push(JSON.parse(data)); }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        close() {}
      };
    });
    await page.goto(`${process.env.RATING_TEST_URL || 'http://localhost:8000'}/game.html`);
    const deliver = data => page.evaluate(data => sockets.at(-1).listeners.message({data: JSON.stringify(data)}), data);
    const request = () => page.evaluate(() => sent.filter(data => data.type === 'get_asset_leaderboard').at(-1));
    const refresh = () => page.getByRole('button', {name: '刷新排行', exact: true}).click();
    const user = {type: 'resume_success', username: 'alice', nickname: '爱丽丝', coins: 12.34};
    const own = {username: 'alice', nickname: '爱丽丝', coins: 12.34, rank: 106};
    const entries = Array.from({length: 100}, (_, i) => ({username: `player${i}`, nickname: i === 2
      ? '<img src=x onerror=alert(1)>' : i === 3 ? '很长的名字'.repeat(20) : `玩家 ${i}`,
      coins: i < 2 ? 12345678.9 : 1000 - i, rank: i < 2 ? 1 : i + 1}));
    const board = {type: 'asset_leaderboard', total: 106, offset: 0, limit: 100, entries, self: own};
    const tail = {...board, offset: 100, entries: [own]};
    const rows = page.locator('.rating-ranking-row');
    await deliver(user);
    await page.getByRole('button', {name: '查看资产排行 →'}).click();
    const initial = await request();
    assert.equal(initial.offset, 0);
    assert.match(await page.locator('.asset-leaderboard').innerText(), /正在读取排行/);
    await deliver(board); // Missing request ID must not overwrite a pending page.
    assert.equal(await rows.count(), 0);
    await deliver({...tail, request_id: initial.request_id}); // Wrong page must be ignored too.
    assert.equal(await rows.count(), 0);
    await deliver({...board, request_id: initial.request_id});
    assert.equal(await rows.count(), 100);
    assert.deepEqual((await page.locator('.rating-place').allTextContents()).slice(0, 3), ['1', '1', '3']);
    assert.match(await page.locator('.rating-own-rank').innerText(), /第 106 名.*12.34 金币/s);
    assert.equal(await page.locator('.rating-player img').count(), 0);
    assert.match(await rows.first().innerText(), /12,345,678.90 金币/);
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (process.env.ASSET_SCREENSHOT) await page.screenshot({path: `${process.env.ASSET_SCREENSHOT}-${width}.png`});
    }
    await page.getByRole('button', {name: '下一页 →', exact: true}).first().click();
    const next = await request();
    assert.equal(next.offset, 100);
    assert.equal(await rows.count(), 0);
    await deliver({...board, request_id: initial.request_id});
    assert.equal(await rows.count(), 0);
    await deliver({...tail, request_id: next.request_id});
    assert.equal(await rows.count(), 1);
    assert.equal(await page.locator('.rating-ranking-row.is-self').count(), 1);
    await refresh();
    const oldRefresh = await request();
    await refresh();
    const currentRefresh = await request();
    await deliver({...tail, self: {...own, coins: 9999}, request_id: oldRefresh.request_id});
    assert.doesNotMatch(await page.locator('.rating-own-rank').innerText(), /9,999/);
    await deliver({...tail, request_id: currentRefresh.request_id});
    await deliver(user); // Same-account reconnect keeps the page and makes a new request.
    assert.equal((await request()).offset, 100);
    await deliver({...tail, request_id: (await request()).request_id});
    await refresh();
    const leaving = await request();
    await page.getByRole('button', {name: '← 游戏厅', exact: true}).click();
    await deliver({...tail, request_id: leaving.request_id});
    assert.equal(await page.locator('.asset-leaderboard').count(), 0);
    await page.getByRole('button', {name: '查看段位排行 →'}).click();
    assert.equal(await page.locator('.rating-leaderboard').count(), 1);
    await page.getByRole('button', {name: '← 游戏厅', exact: true}).click();
    await page.getByRole('button', {name: '查看资产排行 →'}).click();
    assert.equal((await request()).offset, 100);
    await deliver({...board, total: 1, entries: [own], request_id: (await request()).request_id}); // Clamp deleted pages.
    assert.match(await page.locator('.rating-page-number').first().innerText(), /第 1 \/ 1 页/);
    await deliver({...user, username: 'bob'});
    assert.equal((await request()).offset, 0);
    await deliver({...board, total: 0, entries: [], self: null, request_id: (await request()).request_id});
    assert.match(await page.locator('.asset-leaderboard').innerText(), /暂无排行数据/);
    assert.deepEqual(errors, []);
    console.log('PASS asset leaderboard: balances, pagination, ties, XSS, mobile layout, stale responses, refresh, reconnect, account switching, empty state and rating navigation');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
