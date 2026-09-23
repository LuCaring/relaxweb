// Local deploy/serve.py + Playwright. Both pages share the same reward UI.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    for (const path of ['game.html', 'index.html']) {
      const page = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.sent = [];
        window.sockets = [];
        window.WebSocket = class {
          static OPEN = 1;
          constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
          send(data) { sent.push(JSON.parse(data)); }
          addEventListener(type, handler) { this.listeners[type] = handler; }
          close() {}
        };
      });
      await page.goto(`http://localhost:8000/${path}`);
      await page.evaluate(() => { window.MediaMTXWebRTCReader = class { close() {} }; });
      const deliver = data => page.evaluate(data => sockets[0].listeners.message({data: JSON.stringify(data)}), data);
      const user = {type: 'resume_success', username: 'alice', nickname: 'Alice', coins: 1000};
      const status = {type: 'daily_rewards', username: 'alice', day: '2026-09-18',
        checked_in: false, tickets: 0, coins: 1000, daily_tickets: 5,
        server_time: 1000, next_reset_at: 87400,
        prize_tiers: [{min: 20, max: 100, percent: 88}, {min: 101, max: 200, percent: 10},
          {min: 201, max: 500, percent: 2}], history: []};
      await deliver(user);
      await deliver(status);
      await page.locator('#userChip').click();
      await page.locator('#rewardsButton').click();
      assert.equal(await page.locator('#rewardsDialog').isVisible(), true);
      assert.equal(await page.locator('#lotteryDraw').isDisabled(), true);
      await page.locator('#dailyCheckin').click();
      assert.deepEqual(await page.evaluate(() => sent.at(-1)), {type: 'daily_checkin'});
      assert.equal(await page.locator('#dailyCheckin').isDisabled(), true);
      await deliver({...status, type: 'checkin_result', checked_in: true, tickets: 5, awarded: 5});
      assert.equal(await page.locator('#rewardsTickets').innerText(), '5');
      assert.match(await page.locator('#rewardsFeedback').innerText(), /签到成功/);
      assert.equal(await page.locator('#dailyCheckin').isDisabled(), true);
      await page.locator('#lotteryDraw').click();
      const request = await page.evaluate(() => sent.at(-1));
      assert.equal(request.type, 'draw_lottery');
      assert.match(request.request_id, /^[0-9a-f]{32}$/);
      assert.equal(await page.locator('#lotteryDraw').isDisabled(), true);
      // A connection resume retries the original uncertain draw, preserving its ID.
      await deliver(user);
      assert.deepEqual(await page.evaluate(() => sent.findLast(m => m.type === 'draw_lottery')), request);
      const result = {...status, type: 'lottery_result', checked_in: true, tickets: 4,
        coins: 1500, amount: 500, replayed: true, request_id: request.request_id,
        history: [{amount: 500, created_at: 1000}]};
      await deliver(result);
      assert.equal(await page.locator('#rewardsTickets').innerText(), '4');
      assert.equal(await page.locator('#rewardsCoins').innerText(), '1,500.00');
      assert.match(await page.locator('#rewardsFeedback').innerText(), /500 金币.*恢复上次结果/);
      assert.equal(await page.evaluate(() => sessionStorage.getItem('liveLotteryPending:alice')), null);
      if (path === 'game.html') assert.equal(await page.locator('#coinBalance').innerText(), '1,500.00');
      await page.getByText('奖池与规则', {exact: true}).click();
      await page.getByText('最近 10 次中奖记录', {exact: true}).click();
      assert.match(await page.locator('#rewardsPrizes').innerText(), /20–100 金币：88%/);
      assert.match(await page.locator('#rewardsPrizes').innerText(), /201–500 金币：2%/);
      assert.match(await page.locator('#rewardsHistory').innerText(), /\+500 金币/);
      for (const [width, height] of [[320, 568], [390, 844], [844, 390], [1440, 900]]) {
        await page.setViewportSize({width, height});
        assert.equal(await page.locator('#rewardsDialog').evaluate(el => {
          const r = el.getBoundingClientRect();
          return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight
            && el.scrollWidth === el.clientWidth;
        }), true, `${path} ${width}px modal fits and scrolls internally`);
        // Native dialog scrolling works even when the live page body is locked.
        await page.locator('#rewardsDialog').evaluate(el => { el.scrollTop = 0; });
        const button = await page.locator('#lotteryDraw').boundingBox();
        assert.ok(button.y + button.height <= height);
      }
      if (process.env.REWARDS_SCREENSHOT) {
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: `${process.env.REWARDS_SCREENSHOT}-${path}.png`});
      }
      await deliver({...status, checked_in: true, tickets: 9, coins: 1500});
      assert.equal(await page.locator('#rewardsTickets').innerText(), '9');
      // A new Beijing day allows check-in again without throwing away saved tickets.
      await deliver({...status, day: '2026-09-19', tickets: 9, coins: 1500});
      assert.equal(await page.locator('#dailyCheckin').isEnabled(), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#rewardsDialog').isVisible(), false);
      await page.locator('#userChip').click();
      await page.locator('#rewardsButton').click();
      await deliver({type: 'auth_expired'});
      assert.equal(await page.locator('#rewardsDialog').isVisible(), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS both reward entries, check-in, draw lock/retry, balance/history, cross-tab/day updates, logout and responsive dialog');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
