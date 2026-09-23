// Local deploy/serve.py + Playwright; CHROME_PATH overrides the browser executable.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
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
    await page.evaluate(async () => { window.core = await import('/assets/js/core.js'); });
    const touch = await page.context().newCDPSession(page);

    // Native scroll input, followed by a coordinate tap. Locator.click/tap and
    // scrollIntoView would auto-scroll even an overflow:hidden page and mask this bug.
    async function swipeUp(width, height) {
      // Some Chrome versions acknowledge synthesizeScrollGesture without
      // delivering touch scrolling. Dispatch actual touch points across frames.
      const x = Math.round(width / 2);
      const startY = Math.round(height * 0.75);
      await touch.send('Input.dispatchTouchEvent', {
        type: 'touchStart', touchPoints: [{x, y: startY}],
      });
      for (let step = 1; step <= 8; step++) {
        await touch.send('Input.dispatchTouchEvent', {
          type: 'touchMove', touchPoints: [{x, y: startY - Math.round(height * 0.6 * step / 8)}],
        });
        await page.evaluate(() => new Promise(requestAnimationFrame));
      }
      await touch.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
      await page.evaluate(() => new Promise(requestAnimationFrame));
    }

    for (const game of ['holdem', 'uno']) {
      for (const [width, height] of [[320, 568], [390, 844], [430, 932], [844, 390]]) {
        await page.setViewportSize({width, height});
        await page.evaluate(game => {
          const rating = {score: 1008, tier: '白银', games: 1};
          const players = Array.from({length: 9}, (_, i) => ({username: `p${i}`,
            nickname: `玩家${i + 1}`, stack: 100, rating}));
          core.state.currentUser = {username: 'p0', nickname: '玩家1', rating};
          core.state.myRoom = {room_id: 1, name: '九人结算桌', game_type: game,
            status: 'playing', owner: 'p0', owner_name: '玩家1', buy_in: 100, blind: 5,
            players, settlement: {can_next: true, votes: {}, total: 9, blind: 5}, result: {
              board: [{r: 14, s: 0}, {r: 13, s: 1}, {r: 12, s: 2}, {r: 11, s: 3}, {r: 10, s: 0}],
              hands: players.map(p => ({...p, cards: [{r: 9, s: 0}, {r: 8, s: 1}], committed: 20})),
              payouts: {p0: 160}, winner: 'p0', penalties: {p0: 4},
              cards: Object.fromEntries(players.map(p => [p.username, Array.from({length: 7}, () => ({c: 'r', v: '3'}))])),
              ratings: Object.fromEntries(players.map(p => [p.username,
                {initial: 100, final: 120, delta: 8, rating}]))}};
          core.renderGameView();
          window.scrollTo(0, 0);
          window.sent = [];
        }, game);
        const again = page.getByRole('button', {name: '结算并再来一局', exact: true});
        const dissolve = page.getByRole('button', {name: '结算并解散房间', exact: true});
        assert.ok((await again.boundingBox()).y > height, `${game} ${width}px requires scrolling`);
        await swipeUp(width, height);
        assert.ok(await page.evaluate(() => window.scrollY > 0), `${game} ${width}px touch scroll is blocked`);
        for (let i = 0; i < 16; i++) {
          const rect = await dissolve.boundingBox();
          if (rect.y + rect.height < height - 16) break;
          await swipeUp(width, height);
        }
        for (const [button, expected] of [[again, {type: 'settle_vote', choice: 'next', blind: 5}],
          [dissolve, {type: 'settle_vote', choice: 'dissolve'}]]) {
          const rect = await button.boundingBox();
          assert.ok(rect.y > 0 && rect.y + rect.height < height, `${game} ${width}px button must be on screen`);
          await page.touchscreen.tap(rect.x + rect.width / 2, rect.y + rect.height / 2);
          assert.deepEqual(await page.evaluate(() => window.sent.at(-1)), expected);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        if (process.env.MOBILE_SETTLEMENT_SCREENSHOT) {
          await page.screenshot({path: `${process.env.MOBILE_SETTLEMENT_SCREENSHOT}-${game}-${width}.png`});
        }
      }
    }
    // The live page still owns a fixed viewport and scrolls inside its chat area.
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('http://localhost:8000/index.html');
    assert.deepEqual(await page.evaluate(() => ({overflow: getComputedStyle(document.body).overflowY,
      height: document.body.getBoundingClientRect().height})), {overflow: 'hidden', height: 844});
    assert.deepEqual(errors, []);
    console.log('PASS native touch scrolling and both settlement votes for 9-player Holdem/UNO at 320/390/430px portrait and landscape; live viewport preserved');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
