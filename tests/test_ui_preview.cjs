// NODE_PATH=<playwright node_modules> node tests/test_ui_preview.cjs
// Starts its own loopback server on a free port; never uses production services.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');

(async () => {
  const server = spawn(process.env.PYTHON || 'python3',
    ['scripts/preview_ui.py', '--no-open', '--no-replace', '--mobile', '--port', '0'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  let browser;
  const marker = path.join(root, 'scripts/ui_preview', `.reload-test-${process.pid}`);
  try {
    const url = await new Promise((resolve, reject) => {
      let output = '';
      const timeout = setTimeout(() => reject(new Error('preview startup timed out')), 10000);
      server.on('error', error => { clearTimeout(timeout); reject(error); });
      server.on('exit', code => { clearTimeout(timeout); reject(new Error(`server exited: ${code}\n${output}`)); });
      server.stderr.on('data', chunk => { output += chunk; });
      server.stdout.on('data', chunk => {
        output += chunk;
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/\?[^\s]+/);
        if (match) { clearTimeout(timeout); resolve(match[0]); }
      });
    });
    const origin = new URL(url).origin;
    for (const file of ['/config.json', '/users.db', '/scripts/preview_ui.py', '/assets/%2e%2e%2fconfig.json']) {
      assert.equal((await fetch(origin + file)).status, 404, `${file} is not served`);
    }
    browser = await chromium.launch({headless: true,
      executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1500, height: 1050}});
    const errors = [], sockets = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('websocket', socket => sockets.push(socket.url()));
    page.on('request', request => {
      if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== origin) external.push(request.url());
    });
    await page.goto(url);
    async function table(game) {
      const frame = page.frameLocator('#table');
      await frame.locator(`html[data-preview-ready="${game}"]`).waitFor();
      return frame;
    }
    let frame = await table('guandan');
    assert.equal(await page.locator('#size').inputValue(), '390x844');
    assert.equal(await page.locator('#table').evaluate(el => el.contentWindow.innerWidth), 390);
    assert.equal(await page.locator('#table').evaluate(el => el.contentWindow.innerHeight), 844);
    assert.ok(await frame.locator('.gd-seat.me .casual-avatar img').isVisible());
    await frame.locator('.joker-big img').first().evaluate(image => image.decode());
    await page.locator('#rotate').click();
    assert.equal(await page.locator('#table').evaluate(el => el.contentWindow.innerWidth), 844);
    assert.equal(await page.locator('#table').evaluate(el => el.contentWindow.innerHeight), 390);
    await page.locator('#mobile').click();
    assert.equal(await page.locator('#table').evaluate(el => el.contentWindow.innerWidth), 390);
    await page.locator('#size').selectOption('1440x900');
    await page.locator('#bubbles').click();
    await frame.locator('.seat-bubble').first().waitFor();
    assert.equal(await frame.locator('.seat-bubble').count(), 4);
    await frame.locator('#roomChatInput').fill('本地聊天测试');
    await frame.locator('#roomChatInput').press('Enter');
    await frame.locator('.seat-bubble[data-username="p0"]').filter({hasText: '本地聊天测试'}).waitFor();
    await frame.getByRole('button', {name: '提示', exact: true}).click();
    await frame.getByRole('button', {name: /出牌 ·/}).click();
    await page.locator('#feedback').filter({hasText: '已捕获操作'}).waitFor();

    const selectors = {guandan: '.gd-page', mahjong: '.mj-page', holdem: '.poker-table', uno: '.uno-table'};
    for (const game of Object.keys(selectors)) {
      await page.locator('#game').selectOption(game);
      for (const scene of ['normal', 'dense', 'waiting', 'paused']) {
        await page.locator('#scene').selectOption(scene);
        frame = await table(game);
        const current = await frame.locator('body').evaluate(async () => (await import('/assets/js/core.js')).state.myRoom);
        if (scene === 'waiting') assert.equal(current.status, 'waiting');
        else assert.ok(await frame.locator(selectors[game]).isVisible(), `${game}/${scene} table visible`);
        if (scene === 'paused') assert.equal(current.paused, true);
      }
    }
    // Auto refresh is exercised through the real file watcher without touching source files.
    await page.waitForTimeout(1400);
    const reloaded = page.waitForEvent('load');
    fs.writeFileSync(marker, 'reload probe');
    await reloaded;
    await table('uno');
    assert.equal(await page.locator('#scene').inputValue(), 'paused', 'refresh preserves selected scene');
    assert.deepEqual(errors, []);
    assert.deepEqual(sockets, [], 'preview never opens a network WebSocket');
    assert.deepEqual(external, [], 'all requests remain on the local preview server');
    console.log('PASS one-command startup, 4 games × 4 scenes, avatar/joker, viewport, bubbles/chat, actions, auto reload and local-only requests');
  } finally {
    if (fs.existsSync(marker)) fs.unlinkSync(marker);
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
