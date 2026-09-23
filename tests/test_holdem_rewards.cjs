// Run against deploy/serve.py with Playwright and Chrome. Both pages use the same panel.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    for (const path of ['game.html', 'index.html']) {
      const page = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true});
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        window.sent = [];
        window.sockets = [];
        window.WebSocket = class {
          static OPEN = 1;
          constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
          send(data) { sent.push(JSON.parse(data)); }
          addEventListener(type, fn) { this.listeners[type] = fn; }
          close() {}
        };
      });
      await page.goto(`http://localhost:8000/${path}`);
      await page.evaluate(() => { window.MediaMTXWebRTCReader = class { close() {} }; });
      const deliver = data => page.evaluate(data => sockets[0].listeners.message({data: JSON.stringify(data)}), data);
      const user = {type: 'resume_success', username: 'alice', nickname: 'Alice', coins: 1000};
      const status = {type: 'daily_rewards', username: 'alice', day: '2026-09-19',
        checked_in: false, tickets: 5, coins: 1000, daily_tickets: 5,
        server_time: 1000, next_reset_at: 87400, history: [], prize_tiers: []};
      const turnover = (amount, claimed = []) => ({amount,
        tiers: [[100, 20], [200, 40], [500, 100], [1000, 200]].map(([threshold, reward]) =>
          ({threshold, amount: reward, claimed: claimed.includes(threshold),
            claimable: amount >= threshold && !claimed.includes(threshold)}))});
      await deliver(user);
      await deliver({...status, holdem_turnover: turnover(99.99)});
      await page.locator('#userChip').click();
      await page.locator('#rewardsButton').click();
      await page.locator('#holdemRewardsTab').click();
      assert.equal(await page.locator('#holdemRewardsTab').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('#checkinRewardsPanel').isVisible(), false);
      assert.equal(await page.locator('.holdem-reward-tier').count(), 4);
      assert.equal(await page.locator('#holdemTurnoverAmount').innerText(), '99.99');
      assert.equal(await page.locator('[data-threshold="100"]').innerText(), '还差 0.01');
      assert.equal(await page.locator('.holdem-reward-tier button:enabled').count(), 0);
      await deliver({...status, holdem_turnover: turnover(200)});
      assert.equal(await page.locator('.holdem-reward-tier button:enabled').count(), 2);
      await page.locator('[data-threshold="100"]').click();
      assert.deepEqual(await page.evaluate(() => sent.at(-1)),
        {type: 'claim_holdem_reward', threshold: 100, day: '2026-09-19'});
      assert.equal(await page.locator('.holdem-reward-tier button:enabled').count(), 0);
      await deliver({...status, type: 'holdem_reward_result', threshold: 100, amount: 20,
        replayed: false, coins: 1020, holdem_turnover: turnover(200, [100])});
      assert.equal(await page.locator('[data-threshold="100"]').innerText(), '已领取');
      assert.equal(await page.locator('[data-threshold="200"]').isEnabled(), true);
      assert.match(await page.locator('#rewardsFeedback').innerText(), /已领取 20 金币/);
      assert.equal(await page.locator('#rewardsCoins').innerText(), '1,020.00');
      if (path === 'game.html') assert.equal(await page.locator('#coinBalance').innerText(), '1,020.00');
      await deliver({type: 'finance', coins: 1020, transactions: [{amount: 20, balance: 1020,
        kind: 'holdem_daily_reward', detail: '每日德扑下注流水满 100 奖励', created_at: 1000}]});
      assert.match(await page.locator('#financeList').textContent(), /每日德扑流水奖励/);

      const cdp = await page.context().newCDPSession(page);
      for (const [width, height] of [[320, 568], [390, 844], [844, 390], [1440, 900]]) {
        await page.setViewportSize({width, height});
        await deliver({...status, holdem_turnover: turnover(1000)});
        await page.locator('#rewardsDialog').evaluate(el => { el.scrollTop = 0; });
        assert.equal(await page.locator('#rewardsDialog').evaluate(el => {
          const r = el.getBoundingClientRect();
          return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight
            && el.scrollWidth === el.clientWidth;
        }), true, `${path} ${width}px panel fits without horizontal overflow`);
        // Native touch scroll + coordinate tap: no locator auto-scroll can conceal an inaccessible button.
        const target = page.locator('[data-threshold="1000"]');
        const bounds = await page.locator('#rewardsDialog').boundingBox();
        for (let i = 0; i < 4; i++) {
          const box = await target.boundingBox();
          if (box.y >= bounds.y && box.y + box.height <= bounds.y + bounds.height) break;
          const x = Math.round(bounds.x + bounds.width / 2);
          const startY = Math.round(bounds.y + bounds.height - 35);
          await cdp.send('Input.dispatchTouchEvent', {
            type: 'touchStart', touchPoints: [{x, y: startY}],
          });
          for (let step = 1; step <= 8; step++) {
            await cdp.send('Input.dispatchTouchEvent', {
              type: 'touchMove',
              touchPoints: [{x, y: startY - Math.round(220 * step / 8)}],
            });
            await page.evaluate(() => new Promise(requestAnimationFrame));
          }
          await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
          await page.evaluate(() => new Promise(requestAnimationFrame));
        }
        const box = await target.boundingBox();
        assert.ok(box.y >= bounds.y && box.y + box.height <= bounds.y + bounds.height,
          `${path} ${width}px last reward is reachable by touch`);
        await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
        assert.deepEqual(await page.evaluate(() => sent.at(-1)),
          {type: 'claim_holdem_reward', threshold: 1000, day: '2026-09-19'});
        await deliver({...status, type: 'holdem_reward_result', threshold: 1000, amount: 200,
          replayed: false, coins: 1200, holdem_turnover: turnover(1000, [1000])});
        assert.equal(await target.innerText(), '已领取');
      }
      await page.setViewportSize({width: 390, height: 844});
      await deliver({...status, holdem_turnover: turnover(650, [100, 200])});
      await page.locator('#rewardsDialog').evaluate(el => { el.scrollTop = 0; });
      if (process.env.HOLDEM_REWARDS_SCREENSHOT) {
        await page.screenshot({path: `${process.env.HOLDEM_REWARDS_SCREENSHOT}-${path}.png`});
      }
      // Expired-day claims refresh from the server, and yesterday's claimed state is discarded.
      await deliver({type: 'rewards_error', action: 'holdem', message: '日期已切换，请刷新后领取今日奖励'});
      assert.deepEqual(await page.evaluate(() => sent.at(-1)), {type: 'get_daily_rewards'});
      await deliver({...status, day: '2026-09-20', holdem_turnover: turnover(0)});
      assert.equal(await page.locator('#rewardsFeedback').innerText(), '');
      assert.equal(await page.locator('#holdemTurnoverAmount').innerText(), '0.00');
      assert.equal(await page.locator('.holdem-reward-tier button:enabled').count(), 0);
      assert.equal(await page.getByRole('button', {name: '已领取', exact: true}).count(), 0);
      await deliver(user);
      await deliver({...status, holdem_turnover: turnover(100, [100])});
      assert.equal(await page.locator('[data-threshold="100"]').innerText(), '已领取');
      await page.locator('#checkinRewardsTab').click();
      assert.equal(await page.locator('#dailyCheckin').isEnabled(), true);
      assert.equal(await page.locator('#lotteryDraw').isEnabled(), true);
      await deliver({type: 'auth_expired'});
      assert.equal(await page.locator('#rewardsDialog').isVisible(), false);
      await deliver({...status, holdem_turnover: turnover(1000)});
      assert.equal(await page.locator('#holdemTurnoverAmount').innerText(), '—');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS both daily reward panels, tier progress, claim locks/results, balances, reset/reconnect/logout and native touch claims at 320/390/844/1440px');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
