// Run: npm run test:browser -- tests/test_ui_preview.cjs
// Starts its own loopback server on a free port; never uses production services.
const assert = require('node:assert/strict');
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.venv', 'bin', 'python');

(async () => {
  const server = spawn(python,
    ['scripts/preview_ui.py', '--no-open', '--no-replace', '--mobile', '--spectator', '--watch', 'p2', '--port', '0'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
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
    const invalid = spawnSync(python,
      ['scripts/preview_ui.py', '--spectator', '--scene', 'waiting', '--no-open', '--no-replace'],
      {cwd:root, encoding:'utf8', timeout:5000});
    assert.equal(invalid.status,2,'the CLI rejects spectating a game that has not started');
    const fixtureViews = await (await fetch(origin + '/__preview/fixtures')).json();
    const spectatorViews = await (await fetch(origin + '/__preview/spectators')).json();
    assert.deepEqual(Object.keys(fixtureViews.ludo), ['normal','dense','waiting','paused']);
    assert.equal(fixtureViews.ludo.normal.dice, 6);
    assert.ok(fixtureViews.ludo.normal.your_options.plane.length > 0);
    assert.equal(fixtureViews.liarsbar.normal.last_play.username, 'p3');
    assert.equal(fixtureViews.liarsbar.normal.your_options.can_challenge, true);
    assert.equal(fixtureViews.liarsbar.dense.stage, 'reveal');
    assert.ok(fixtureViews.liarsbar.dense.last_duel);
    for (const file of ['/config.json', '/users.db', '/scripts/preview_ui.py', '/assets/%2e%2e%2fconfig.json']) {
      assert.equal((await fetch(origin + file)).status, 404, `${file} is not served`);
    }
    browser = await chromium.launch({headless: true,
      executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1500, height: 1050}});
    const errors = [], sockets = [], external = [], failedRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('requestfailed', request => failedRequests.push(`${request.url()}: ${request.failure()?.errorText}`));
    page.on('websocket', socket => sockets.push(socket.url()));
    page.on('request', request => {
      if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== origin) external.push(request.url());
    });
    await page.goto(url);
    async function table(game) {
      const frame = page.frameLocator('#table');
      try {
        await page.waitForFunction(game => {
          const data = document.querySelector('#table').contentDocument?.documentElement?.dataset;
          const value = id => document.getElementById(id).value;
          return data?.previewReady === game && data.previewScene === value('scene')
            && data.previewPerspective === value('perspective')
            && (value('perspective') === 'player' || data.previewWatch === value('watch'));
        }, game);
      } catch (error) {
        const current = await page.locator('#table').evaluate(el => ({src: el.src, ready: el.contentDocument?.documentElement?.dataset.previewReady, scene: el.contentDocument?.documentElement?.dataset.previewScene}));
        throw new Error(`Preview failed for ${game}/${await page.locator('#scene').inputValue()}/${await page.locator('#perspective').inputValue()}/${await page.locator('#watch').inputValue()}: ${JSON.stringify(current)} frames=${page.frames().map(frame => frame.url()).join(',')} server=${server.exitCode} requests=${failedRequests.slice(-5).join('; ')} errors=${errors.join('; ')}`, {cause: error});
      }
      await frame.locator(`html[data-preview-ready="${game}"]`).waitFor();
      return frame;
    }
    let frame = await table('guandan');
    assert.equal(await page.locator('#perspective').inputValue(),'spectator');
    assert.equal(await page.locator('#watch').inputValue(),'p2');
    assert.match(await frame.locator('#spectateBadge').innerText(),/月下客/);
    await page.locator('#perspective').selectOption('player');
    frame = await table('guandan');
    assert.equal(await page.locator('#watch-label').isVisible(),false);
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

    const selectors = {guandan: '.gd-page', mahjong: '.mj-page', holdem: '.poker-table', uno: '.uno-table', ludo: '.ludo-table', liarsbar: '.liar-table'};
    for (const game of Object.keys(selectors)) {
      await page.locator('#game').selectOption(game);
      await table(game);
      for (const scene of ['normal', 'dense', 'waiting', 'paused']) {
        await page.locator('#scene').selectOption(scene);
        frame = await table(game);
        const current = await frame.locator('body').evaluate(async () => (await import('/assets/js/core.js')).state.myRoom);
        if (scene === 'waiting') assert.equal(current.status, 'waiting');
        else assert.ok(await frame.locator(selectors[game]).isVisible(), `${game}/${scene} table visible`);
        if (scene === 'paused') assert.equal(current.paused, true);
      }
    }
    await page.locator('#game').selectOption('ludo');
    await table('ludo');
    await page.locator('#scene').selectOption('normal');
    frame = await table('ludo');
    assert.equal(await frame.locator('.ludo-plane').count(), 16);
    await frame.locator('.ludo-plane.can-move').first().click();
    await page.locator('#feedback').filter({hasText: '"action":"move"'}).waitFor();
    await page.locator('#game').selectOption('liarsbar');
    frame = await table('liarsbar');
    assert.equal(await frame.locator('.liar-hand .liar-card').count(), 5);
    await frame.locator('.liar-actions .danger').click();
    await page.locator('#feedback').filter({hasText: '"action":"challenge"'}).waitFor();
    // Enabling spectating from a waiting room selects a playable sample.
    await page.locator('#game').selectOption('uno');
    await table('uno');
    await page.locator('#scene').selectOption('waiting');
    await table('uno');
    await page.locator('#perspective').selectOption('spectator');
    await table('uno');
    assert.equal(await page.locator('#scene').inputValue(),'normal');
    assert.equal(await page.locator('#scene option[value="waiting"]').evaluate(option=>option.disabled),true);
    for (const game of Object.keys(selectors)) {
      await page.locator('#game').selectOption(game);
      await table(game);
      assert.equal(spectatorViews[game].waiting,undefined);
      for (const scene of ['normal','dense','paused']) {
        await page.locator('#scene').selectOption(scene);
        await table(game);
        for (const watched of ['p0','p1','p2','p3']) {
          await page.locator('#watch').selectOption(watched);
          frame = await table(game);
          const actual = await frame.locator('body').evaluate(async () => {
            const {state,selfUsername} = await import('/assets/js/core.js');
            return {room:state.myRoom, account:state.currentUser.username, seat:selfUsername()};
          });
          const expected = spectatorViews[game][scene][watched];
          assert.equal(actual.account,'preview-watcher');
          assert.equal(actual.seat,watched);
          assert.equal(actual.room.spectator,true);
          assert.equal(actual.room.your_options,undefined);
          assert.deepEqual(actual.room.your_hand,expected.your_hand,`${game}/${scene}/${watched} private hand`);
          assert.deepEqual(actual.room.your_hole,expected.your_hole);
          assert.deepEqual(actual.room.your_flowers,expected.your_flowers);
          assert.equal(actual.room.my_team,expected.my_team);
          assert.equal(await frame.locator('.mj-hand-card:enabled,.gd-hand-card:enabled,.uno-hand-card:enabled,.mj-actions button:enabled,.gd-dock .action-bar button:enabled,.poker-dock .action-bar button:enabled,.ludo-side .ludo-btn:enabled,.ludo-plane:enabled,.liar-actions button:enabled,.liar-card.clickable').count(),0);
          assert.equal(await frame.locator('#roomManage').isVisible(),false);
        }
      }
      if (game === 'ludo') {
        assert.notDeepEqual(spectatorViews.ludo.normal.p0.players[0].planes,
          spectatorViews.ludo.normal.p0.players[1].planes);
      } else {
        assert.notDeepEqual(spectatorViews[game].normal.p0.your_hand || spectatorViews[game].normal.p0.your_hole,
          spectatorViews[game].normal.p1.your_hand || spectatorViews[game].normal.p1.your_hole,
          `${game} switching seats must not reuse p0's cards`);
      }
      await frame.locator('#spectateButton').click();
      await frame.locator('#spectateMenu .room-manage-item').nth(1).click();
      await page.waitForFunction(()=>document.querySelector('#watch').value==='p1');
      frame = await table(game);
      assert.equal(new URL(page.url()).searchParams.get('watch'),'p1');
      assert.equal(new URL(await page.locator('#direct').getAttribute('href'),origin).searchParams.get('watch'),'p1');
    }
    await frame.locator('#roomChatInput').fill('观战测试发言');
    await frame.locator('#roomChatInput').press('Enter');
    await frame.locator('.rc-message').filter({hasText:'观战测试发言'}).waitFor();
    assert.match(await frame.locator('.rc-spectator').last().innerText(),/本地观众（观战）/);
    assert.equal(await frame.locator('.seat-bubble').count(),0);
    await page.locator('#bubbles').click();
    await frame.locator('.seat-bubble').first().waitFor();
    assert.equal(await frame.locator('.seat-bubble').count(),4,'player bubbles still work in spectator mode');

    // The production hand-result overlay must retain a reachable spectator exit.
    await page.locator('#game').selectOption('holdem');
    frame = await table('holdem');
    await frame.locator('body').evaluate(async () => {
      const core = await import('/assets/js/core.js');
      core.handleServerMessage({...core.state.myRoom, type:'game_update', paused:false,
        result:{pot:4,board:[],hands:[],payouts:{}}, hand_ready:{hand_no:1,ready:[],total:4,left:60}});
    });
    assert.equal(await frame.locator('#handResult .hand-continue-button').count(),0);
    await frame.locator('#handResult').getByRole('button',{name:'退出观战',exact:true}).click();
    await frame.locator('.live-dialog-confirm').click();
    await frame.locator('#handResult').waitFor({state:'detached'});
    assert.equal(await frame.locator('body').evaluate(async () => (await import('/assets/js/core.js')).state.myRoom),null);
    await page.locator('#reset').click();
    await table('holdem');
    await page.locator('#game').selectOption('uno');
    await table('uno');
    for (const game of Object.keys(selectors)) {
      await page.locator('#game').selectOption(game);
      await table(game);
      await page.locator('#scene').selectOption('dense');
      for (const size of ['320x568','390x844']) {
        await page.locator('#size').selectOption(size);
        frame = await table(game);
        assert.ok(await frame.locator('#spectateButton').evaluate(button=>{
          const r=button.getBoundingClientRect();
          return r.left>=0&&r.right<=innerWidth&&r.top>=0
            &&button.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
        }),`${game}/${size} long spectator names leave the player menu reachable`);
        await frame.locator('#spectateButton').click();
        await frame.locator('#spectateMenu .room-manage-item').nth(0).click();
        await page.waitForFunction(()=>document.querySelector('#watch').value==='p0');
      }
    }
    await page.locator('#game').selectOption('uno');
    await table('uno');
    await page.locator('#scene').selectOption('paused');
    await table('uno');
    await page.locator('#watch').selectOption('p1');
    await page.locator('#size').selectOption('1440x900');
    await table('uno');
    // Auto refresh is exercised through the real file watcher without touching source files.
    await page.waitForTimeout(1400);
    const reloaded = page.waitForEvent('load');
    fs.writeFileSync(marker, 'reload probe');
    await reloaded;
    await table('uno');
    assert.equal(await page.locator('#scene').inputValue(), 'paused', 'refresh preserves selected scene');
    assert.equal(await page.locator('#perspective').inputValue(),'spectator');
    assert.equal(await page.locator('#watch').inputValue(),'p1');
    // Direct links and reloads preserve the target selected inside the real game menu.
    await page.goto(new URL(await page.locator('#direct').getAttribute('href'),origin).href);
    await page.locator('html[data-preview-ready="uno"]').waitFor();
    await page.locator('#spectateButton').click();
    await page.locator('#spectateMenu .room-manage-item').nth(2).click();
    await page.waitForURL(/watch=p2/);
    await page.reload();
    await page.locator('html[data-preview-ready="uno"][data-preview-watch="p2"]').waitFor();
    assert.deepEqual(errors, []);
    assert.deepEqual(sockets, [], 'preview never opens a network WebSocket');
    assert.deepEqual(external, [], 'all requests remain on the local preview server');
    console.log('PASS startup, 6 games × 4 player scenes and 3 spectator scenes × 4 seats, target switching, spectator chat/exit, viewport, reload and local-only requests');
  } finally {
    if (fs.existsSync(marker)) fs.unlinkSync(marker);
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
