// Run: npm run test:browser -- tests/test_estate_presence.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(ROOT, '.venv', 'bin', 'python');
const fixture = JSON.parse(execFileSync(python, ['-B', '-c', `
import json, sqlite3, time
from estate import init_estate, estate_state
conn = sqlite3.connect(':memory:')
conn.execute('CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL NOT NULL)')
conn.execute("INSERT INTO users VALUES ('alice',10000)")
init_estate(conn)
print(json.dumps(estate_state(conn, 'alice', int(time.time()))))
`], { cwd: ROOT, encoding: 'utf8' }));

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
    await page.route('http://estate.test/**', async route => {
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
      window.spriteDraws = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
      const original = CanvasRenderingContext2D.prototype.drawImage;
      CanvasRenderingContext2D.prototype.drawImage = function(image, ...args) {
        if (image.src?.includes('/characters/') && this.canvas.classList.contains('estate-canvas')) {
          spriteDraws.push(image.src);
          if (spriteDraws.length > 200) spriteDraws.shift();
        }
        return original.call(this, image, ...args);
      };
    });

    await page.goto('http://estate.test/game.html');
    await page.evaluate(async snapshot => {
      window.core = await import('/assets/js/core.js');
      core.setSignedIn({ username: 'alice', nickname: 'Alice', coins: 10000 });
      core.state.hallPage = 'estate';
      core.renderGameView();
      core.handleServerMessage({ type: 'estate_state', ...snapshot, players: [] });
    }, fixture);

    const bobEstate = structuredClone(fixture);
    bobEstate.owner_username = 'bob';
    bobEstate.profile = { ...bobEstate.profile, username: 'bob' };
    await page.evaluate(snapshot => core.handleServerMessage({
      type: 'estate_visit_state', ...snapshot,
      players: [{ username: 'bob', skin_id: 'steve', x: 360, y: 440,
        direction: 'down', walking: false }],
    }), bobEstate);
    await page.waitForFunction(() => spriteDraws.some(src => src.includes('/steve/')));

    await page.evaluate(snapshot => core.handleServerMessage({
      type: 'estate_state', ...snapshot, players: [],
    }), fixture);
    assert.equal(await page.evaluate(async () => (await import('/assets/js/estate/state.js')).estateStore.players.size), 0,
      'returning home must replace the previous estate presence snapshot');

    await page.evaluate(() => core.handleServerMessage({
      type: 'estate_visit_moved', owner_username: 'bob', username: 'bob', skin_id: 'steve',
      x: 380, y: 440, direction: 'right', walking: true,
    }));
    assert.equal(await page.evaluate(async () => (await import('/assets/js/estate/state.js')).estateStore.players.size), 0,
      'a delayed event from the previous estate must not recreate a ghost player');
    assert.deepEqual(errors, []);
    console.log('PASS: remote equipped skin renders and estate switches reject stale presence');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
