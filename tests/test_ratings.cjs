// Run against deploy/serve.py with Playwright available (same setup as test_desktop.cjs).
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      window.sent = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { window.sent.push(JSON.parse(data)); }
        close() {}
        addEventListener() {}
      };
    });
    await page.goto('http://localhost:8000/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      window.rating = {score: 1000, tier: '白银', games: 0, next_score: 1200, next_tier: '黄金'};
      core.handleServerMessage({type: 'resume_success', username: 'alice', nickname: '爱丽丝',
        coins: 900, rating});
    });
    const ratingCard = page.locator('.rating-card:not(.asset-card)');
    assert.match(await ratingCard.innerText(), /白银 1000/);
    assert.equal(await page.evaluate(() => sent.some(m => m.type === 'get_rating_history')), true);
    await page.getByText('积分怎么算？', {exact: true}).click();
    assert.match(await ratingCard.innerText(), /盈利 × 40，亏损 × 20/);
    await page.evaluate(() => core.handleServerMessage({type: 'rating_history',
      rating: {...rating, score: 1008, games: 1}, entries: [{game_type: 'uno',
        room_name: '<img src=x onerror=alert(1)>', hand_no: 1, initial: 100, final: 120,
        delta: 8, rating: {...rating, score: 1008, games: 1}, created_at: 1000}]}));
    await page.getByText('最近 20 局积分明细', {exact: true}).click();
    assert.match(await page.locator('.rating-history-row').innerText(), /\+8 分/);
    assert.equal(await page.locator('.rating-history-row img').count(), 0);
    await page.evaluate(() => {
      core.state.myRoom = {room_id: 1, name: '测试桌', game_type: 'holdem', status: 'waiting',
        owner: 'alice', owner_name: '爱丽丝', buy_in: 100, blind: 5,
        players: [{username: 'alice', nickname: '爱丽丝', stack: 100, rating},
          {username: 'bob', nickname: '鲍勃', stack: 100, rating}]};
      core.renderGameView();
    });
    assert.equal(await page.locator('.waiting-seat[data-username] .rating-badge').count(), 2);
    await page.evaluate(() => {
      core.state.myRoom.status = 'playing';
      core.state.myRoom.settlement = {can_next: true, votes: {}, total: 2, blind: 5};
      core.state.myRoom.result = {board: [], hands: [], payouts: {}, ratings: {
        alice: {initial: 100, final: 120, delta: 8, rating: {...rating, score: 1008, games: 1}},
        bob: {initial: 100, final: 80, delta: -4, rating: {...rating, score: 996, games: 1}}}};
      core.renderGameView();
    });
    for (const [width, height] of [[1440, 900], [390, 844], [844, 390]]) {
      await page.setViewportSize({width, height});
      assert.equal(await page.locator('.rating-result-row').count(), 2);
      assert.match(await page.locator('.rating-results').innerText(), /收益率 \+20\.0%/);
      assert.match(await page.locator('.rating-results').innerText(), /-4 分/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.evaluate(() => { core.state.myRoom.game_type = 'uno'; core.renderGameView(); });
    assert.equal(await page.locator('.rating-result-row').count(), 2);
    await page.evaluate(() => {
      core.state.myRoom = null;
      core.handleServerMessage({type: 'rating_update', username: 'alice',
        rating: {...rating, score: 1200, tier: '黄金', games: 20, next_score: 1600, next_tier: '铂金'}});
      core.renderGameView();
    });
    assert.match(await ratingCard.innerText(), /黄金 1200/);
    assert.match(await page.locator('#dropdownName').textContent(), /黄金 1200/);
    assert.deepEqual(errors, []);
    console.log('PASS rating rules, history, identity updates, roster, both settlements, mobile layouts and safe text');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
