const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(),
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.setDefaultTimeout(3000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://holdem.test/**', async route => {
      const file = path.resolve(ROOT, '.' + new URL(route.request().url()).pathname);
      if (!file.startsWith(ROOT + path.sep)) return route.fulfill({ status: 403 });
      try {
        const body = await fs.readFile(file);
        const contentType = {
          '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
          '.png': 'image/png', '.json': 'application/json', '.svg': 'image/svg+xml',
        }[path.extname(file)] || 'application/octet-stream';
        await route.fulfill({ body, contentType });
      } catch { await route.fulfill({ status: 404 }); }
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
    await page.goto('http://holdem.test/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      const rating = { score: 1000, tier: '白银', games: 5 };
      core.state.currentUser = { username: 'alice', nickname: '爱丽丝' };
      core.state.socket = { readyState: 1, send: data => sent.push(JSON.parse(data)) };
      core.state.myRoom = {
        room_id: 7, name: '好友桌', game_type: 'holdem', status: 'playing',
        owner: 'alice', owner_name: '爱丽丝', buy_in: 100, blind: 5,
        hand_no: 8, stage: 'showdown', pot: 40, to_act: null, turn_left: 0,
        board: [{ r: 14, s: 0 }, { r: 14, s: 1 }, { r: 9, s: 2 }, { r: 6, s: 3 }, { r: 2, s: 0 }],
        your_hole: [{ r: 13, s: 0 }, { r: 12, s: 0 }],
        players: [
          { username: 'alice', nickname: '爱丽丝', stack: 160, bet: 0, hand_bet: 0, rating },
          { username: 'bob', nickname: '鲍勃', stack: 40, bet: 0, hand_bet: 0, rating },
        ],
        result: {
          pot: 40, board: [], payouts: { alice: 40 },
          hands: [
            { username: 'alice', nickname: '爱丽丝', cards: [{ r: 13, s: 0 }, { r: 12, s: 0 }], hand_name: '一对', payout: 40, committed: 20 },
            { username: 'bob', nickname: '鲍勃', cards: [{ r: 8, s: 1 }, { r: 7, s: 1 }], hand_name: '', folded: true, payout: 0, committed: 20 },
          ],
        },
        hand_ready: { hand_no: 8, ready: [], total: 2, left: 10, ends_match: true },
      };
      core.renderGameView();
    });

    assert.match(await page.locator('.hand-continue-button').innerText(), /^结束本局（\d+）$/);
    assert.match(await page.locator('.hand-result-body').innerText(), /一对/);
    await page.locator('.hand-continue-button').click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'hand_continue' });

    await page.evaluate(() => {
      const room = core.state.myRoom;
      room.status = 'settled';
      delete room.hand_ready;
      room.match_result = {
        reason: '有玩家筹码不足盲注，本局结束', match_no: 1, hand_no: 8,
        blind: 5, buy_in: 100, votes: {}, total: 2, can_next: true,
        players: [
          { username: 'alice', nickname: '爱丽丝', stack: 160, paid: 100, net: 60, rating: room.players[0].rating, rating_delta: 8 },
          { username: 'bob', nickname: '鲍勃', stack: 40, paid: 100, net: -60, rating: room.players[1].rating, rating_delta: -4 },
        ],
      };
      core.renderGameView();
    });
    const settlementText = await page.locator('#gameMain').innerText();
    assert.equal(await page.locator('.match-hand').count(), 0);
    assert.doesNotMatch(settlementText, /最后一手/);
    assert.match(settlementText, /\+60/);
    assert.match(settlementText, /不会继承本局桌上筹码/);
    assert.deepEqual(errors, []);
    console.log('PASS: final-hand countdown ends match and settlement only shows match profit/loss');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
