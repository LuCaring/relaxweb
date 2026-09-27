// Integration checks for the Phaser client against the existing Holdem protocol.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..');

function playingRoom(count = 3) {
  return {
    room_id: 701, name: '重制版好友桌', game_type: 'holdem', status: 'playing',
    owner: 'alice', owner_name: '爱丽丝', buy_in: 200, blind: 5,
    hand_no: 2, stage: 'flop', pot: 45, to_act: 'alice', turn_left: 30,
    board: [{ r: 14, s: 0 }, { r: 10, s: 1 }, { r: 7, s: 2 }],
    your_hole: [{ r: 14, s: 1 }, { r: 13, s: 0 }],
    your_options: { check: false, call: true, call_amount: 10,
      can_raise: true, raise_min: 30, raise_max: 200, allin: false },
    players: Array.from({ length: count }, (_, i) => ({
      username: i === 0 ? 'alice' : `p${i}`, nickname: i === 0 ? '爱丽丝' : `玩家${i + 1}`,
      stack: 190, bet: i === 0 ? 10 : 20, hand_bet: i === 0 ? 10 : 20,
      in_hand: true, folded: false, allin: false, dealer: i === 1,
      rating: { tier: '白银', score: 1000, games: 5 },
    })),
  };
}

async function run() {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    const requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push(request.url()));
    await page.route('http://remaster.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      const file = path.resolve(ROOT, '.' + (pathname === '/game' ? '/game.html' : pathname));
      if (!file.startsWith(ROOT + path.sep)) return route.fulfill({ status: 403 });
      try {
        const body = await fs.readFile(file);
        const contentType = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
          '.png': 'image/png', '.json': 'application/json', '.svg': 'image/svg+xml',
          '.webp': 'image/webp' }[path.extname(file)] || 'application/octet-stream';
        await route.fulfill({ body, contentType });
      } catch { await route.fulfill({ status: 404 }); }
    });
    await page.addInitScript(() => {
      window.sent = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { window.sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
    });
    async function initialize() {
      await page.evaluate(async () => {
        window.core = await import('/assets/js/core.js');
        window.registry = await import('/assets/js/registry.js');
        core.state.currentUser = { username: 'alice', nickname: '爱丽丝', coins: 1000 };
        core.state.socket = { readyState: 1, send: data => sent.push(JSON.parse(data)) };
        core.renderGameView();
      });
    }
    async function update(room) {
      await page.evaluate(room => {
        registry.dispatchMessage({ type: 'game_update', ...room });
      }, room);
    }
    async function actionPayload() {
      return page.evaluate(() => sent.filter(item => item.type === 'poker_action').at(-1));
    }
    async function assertVectorCards(count = 7) {
      await page.waitForFunction(count => document.querySelectorAll('.poker-remastered-card svg').length === count, count);
      const cards = await page.locator('.poker-remastered-card').evaluateAll(nodes => nodes.map(node => ({
        paths: node.querySelectorAll('svg path').length,
        rasterContent: node.querySelectorAll('canvas, img, image').length,
        pointerEvents: getComputedStyle(node).pointerEvents,
      })));
      assert.equal(cards.length, count, 'redraws must replace old DOM cards without accumulating nodes');
      assert.ok(cards.every(card => card.paths > 0 && card.rasterContent === 0),
        'card faces must remain inline vector paths, including ranks and suits');
      assert.ok(cards.every(card => card.pointerEvents === 'none'), 'decorative cards must not intercept input');
    }
    async function assertVectorTable() {
      const art = page.locator('.poker-remastered-table-art');
      assert.equal(await art.count(), 1, 'one vector table survives updates and resize');
      assert.equal(await art.locator('svg').count(), 1, 'the table surface must be a native SVG');
      assert.equal(await art.locator('canvas, img, image').count(), 0, 'the table must not embed a bitmap');
      const geometry = await art.evaluate(node => {
        const stage = node.closest('.poker-remastered-stage').getBoundingClientRect();
        const box = node.getBoundingClientRect();
        return Math.abs(box.x - stage.x) < 3 && Math.abs(box.y - stage.y) < 3
          && Math.abs(box.width - stage.width) < 3 && Math.abs(box.height - stage.height) < 3;
      });
      assert.ok(geometry, 'native SVG table must fit the canvas viewport even when magnified');
    }
    async function assertCardAlignment() {
      const geometry = await page.locator('.poker-remastered-stage').evaluate(stage => {
        const box = stage.getBoundingClientRect();
        const nodes = [...stage.querySelectorAll('.poker-remastered-card')];
        const publicCards = nodes.slice(0, 5).map(node => node.getBoundingClientRect());
        const centers = publicCards.map(card => card.x + card.width / 2);
        const spacing = centers[1] - centers[0];
        const aligned = publicCards.every((card, i) =>
          Math.abs(card.y + card.height / 2 - box.y - box.height * .49) < 3
          && Math.abs(card.width / card.height - 80 / 112) < .02
          && (i === 0 || Math.abs(centers[i] - centers[i - 1] - spacing) < 3))
          && spacing > publicCards[0].width
          && Math.abs((centers[0] + centers[4]) / 2 - box.x - box.width / 2) < 3;
        const root = stage.closest('.poker-remastered');
        const actions = root.querySelector('.poker-remastered-actions').getBoundingClientRect();
        const rootBox = root.getBoundingClientRect();
        return { aligned, tableCards: nodes.length,
          holes: [...root.querySelectorAll('[data-role="hole"]')].map(node => {
          const card = node.getBoundingClientRect();
          return { outsideTable: !stage.contains(node), leftOfActions: card.right <= actions.left,
            alongsideActions: Math.min(card.bottom, actions.bottom) > Math.max(card.top, actions.top),
            fits: card.left >= rootBox.left && card.right <= rootBox.right && card.bottom <= rootBox.bottom,
            readable: card.width >= 60 && card.height >= 84 };
        }) };
      });
      assert.ok(geometry.aligned, 'SVG board cards must remain aligned after resizing and magnification');
      assert.equal(geometry.tableCards, 5, 'only the five community cards remain on the table');
      assert.deepEqual(geometry.holes, Array(2).fill({ outsideTable: true, leftOfActions: true,
        alongsideActions: true, fits: true, readable: true }),
      'private cards remain readable to the left of the actions, including after resize and zoom');
    }
    await page.goto('http://remaster.test/game.html');
    await initialize();
    assert.equal(requests.some(url => /\/assets\/(?:build|dist)\//.test(url)), false,
      'Phaser bundle must not load in the normal hall');
    await page.locator('.hall-game-card').filter({ hasText: '德扑重制版' }).click();
    assert.equal(await page.evaluate(() => core.state.currentGameId), 'holdem-remastered');
    await page.evaluate(() => {
      core.state.hallRooms = [{ id: 701, name: '经典同桌', game: 'holdem', status: 'waiting',
        owner: 'p1', owner_name: '玩家2', buy_in: 200, blind: 5, players: ['p1'] }];
      core.renderGameView();
    });
    await page.locator('.hall-room-row').getByRole('button', { name: '进入', exact: true }).click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'join_room', room_id: 701 });
    await page.getByRole('button', { name: '＋ 创建房间', exact: true }).click();
    await page.getByLabel('买入金币', { exact: true }).fill('200');
    await page.getByLabel('小盲注', { exact: true }).selectOption('5');
    await page.locator('.create-room-form').evaluate(form => form.requestSubmit());
    assert.deepEqual(await page.evaluate(() => sent.filter(item => item.type === 'create_room').at(-1)),
      { type: 'create_room', game: 'holdem', name: '', buy_in: 200, blind: 5 });

    let room = playingRoom();
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), room);
    await page.locator('.poker-remastered-stage canvas').waitFor();
    await page.waitForFunction(() => document.querySelector('.poker-remastered-actions [data-action="call"]'));
    await assertVectorCards();
    await assertVectorTable();
    assert.equal(await page.locator('.poker-remastered-head, .poker-remastered-title').count(), 0,
      'the redundant top title bar is removed');
    if (process.env.REMASTER_RESPONSIVE_ONLY) {
      await require('./holdem_remastered_responsive.cjs')(page, update, playingRoom);
      assert.deepEqual(errors, []);
      console.log('PASS remastered responsive desktop height, host resizing, readable controls and scroll stability');
      return;
    }
    if (process.env.REMASTER_SEATING_ONLY) {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.waitForTimeout(200);
      await require('./holdem_remastered_seating.cjs')(page, update, playingRoom);
      assert.deepEqual(errors, []);
      console.log('PASS remastered fixed entry-order seats, observer views, action ring and chip transfers');
      return;
    }
    if (process.env.REMASTER_CHIPS_ONLY) {
      await require('./holdem_remastered_chips.cjs')(page, update, playingRoom);
      assert.deepEqual(errors, []);
      console.log('PASS remastered chip tiers, money-sized transfers, split payouts and result lifecycle');
      return;
    }
    await page.evaluate(() => { window.firstCanvas = document.querySelector('.poker-remastered-stage canvas'); });
    await page.locator('[data-action="call"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'call' });
    assert.equal(await page.locator('[data-action="call"]').isDisabled(), true, 'submission locks controls');
    await page.evaluate(() => document.dispatchEvent(new CustomEvent('gameactionerror', { detail: { message: '重试' } })));
    assert.equal(await page.locator('[data-action="call"]').isDisabled(), false, 'server rejection unlocks controls');
    await page.getByRole('button', { name: '半池', exact: true }).click();
    assert.equal(await page.getByLabel('加注到的总额', { exact: true }).inputValue(), '47.5');
    await page.getByRole('button', { name: '底池', exact: true }).click();
    assert.equal(await page.getByLabel('加注到的总额', { exact: true }).inputValue(), '75');
    await page.getByLabel('加注到的总额', { exact: true }).fill('45.25');
    await update({ ...room, turn_left: 29 });
    assert.equal(await page.getByLabel('加注到的总额', { exact: true }).inputValue(), '45.25',
      'unrelated snapshots must preserve an in-progress raise');
    await page.locator('[data-action="raise"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'raise', raise_to: 45.25 });
    room = { ...room, pot: 80, to_act: 'p1', your_options: null };
    await update(room);
    assert.match(await page.locator('.poker-remastered-pot').innerText(), /80/,
      'the live DOM pot label tracks server snapshots');
    assert.equal(await page.evaluate(() => firstCanvas === document.querySelector('.poker-remastered-stage canvas')), true,
      'server snapshots must preserve the Phaser canvas');
    assert.equal(await page.locator('.poker-remastered-actions [data-action]').count(), 0);

    room = { ...playingRoom(), your_options: { check: true, call: false, can_raise: false, allin: true, allin_to: 25 } };
    await update(room);
    await page.locator('[data-action="check"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'check' });
    await update({ ...room, hand_no: 3 });
    await page.locator('[data-action="allin"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'raise', raise_to: 25 });
    await update({ ...room, hand_no: 4 });
    await page.locator('[data-action="fold"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'fold' });
    await update({ ...playingRoom(), hand_no: 5 });
    await page.getByLabel('加注到的总额', { exact: true }).fill('999999');
    await page.locator('[data-action="raise"]').click();
    assert.deepEqual(await actionPayload(), { type: 'poker_action', action: 'raise', raise_to: 200 },
      'out-of-range raises are clamped just like the classic table');
    await page.evaluate(() => document.dispatchEvent(new Event('gamesocketclose')));
    assert.equal(await page.locator('[data-action="call"]').isDisabled(), true);
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }),
      { ...playingRoom(), hand_no: 5 });
    assert.equal(await page.locator('[data-action="call"]').isDisabled(), false,
      'rejoining releases a pending action whose acknowledgement was lost');
    await update({ ...playingRoom(), hand_no: 5, turn_left: 0.2 });
    await page.waitForFunction(() => document.querySelector('[data-action="call"]').disabled);
    await page.evaluate(() => document.dispatchEvent(new CustomEvent('gameactionerror')));
    assert.equal(await page.locator('[data-action="call"]').isDisabled(), true,
      'an error must not reactivate an expired turn');
    await update({ ...playingRoom(), paused: true });
    assert.equal(await page.locator('.poker-remastered-actions [data-action]:enabled').count(), 0);
    await update({ ...playingRoom(), spectator: true, watching: 'p1', your_options: null });
    assert.equal(await page.locator('.poker-remastered-actions [data-action]').count(), 0);
    await page.locator('#spectateButton').click();
    await page.locator('#spectateMenu').getByRole('button', { name: '爱丽丝', exact: true }).click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'watch_player', username: 'alice' });

    for (const [width, height] of [[1280, 800], [1440, 900], [1920, 1080]]) {
      await page.setViewportSize({ width, height });
      await update({ ...playingRoom(9), board: [{ r: 10, s: 0 }, { r: 11, s: 1 },
        { r: 12, s: 2 }, { r: 13, s: 3 }, { r: 14, s: 0 }] });
      await page.waitForTimeout(800);
      await assertVectorCards();
      await assertCardAlignment();
      await assertVectorTable();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
        `${width}px table must not overflow horizontally`);
      assert.equal(await page.locator('.poker-remastered-stage canvas').count(), 1);
      const geometry = await page.locator('.poker-remastered-stage').evaluate(stage => {
        const box = stage.getBoundingClientRect();
        const canvas = stage.querySelector('canvas').getBoundingClientRect();
        const seats = [...stage.parentElement.querySelectorAll('.poker-remastered-seat')]
          .map(seat => seat.getBoundingClientRect());
        const cards = [...stage.querySelectorAll('.poker-remastered-card')]
          .map(card => card.getBoundingClientRect());
        const label = stage.querySelector('.poker-remastered-pot').getBoundingClientRect();
        return { canvasClipped: canvas.left < box.left - 1 || canvas.right > box.right + 1
            || canvas.top < box.top - 1 || canvas.bottom > box.bottom + 1,
          cardClipped: cards.some(card => card.width < 30 || card.height < 40 || card.left < box.left - 1
            || card.right > box.right + 1 || card.top < box.top - 1 || card.bottom > box.bottom + 1),
          seatClipped: seats.some(seat => seat.left < box.left - 1 || seat.right > box.right + 1
            || seat.top < box.top - 1 || seat.bottom > box.bottom + 1),
          seatOverlap: seats.some((a, i) => seats.slice(i + 1).some(b =>
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
            && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)),
          topLabelMisplaced: label.bottom > box.top + box.height * .12
            || Math.abs(label.left + label.width / 2 - box.left - box.width / 2) > 3,
          dealerBlocked: seats.some(seat => seat.right > box.left + box.width * .42
            && seat.left < box.left + box.width * .58 && seat.top < box.top + box.height * .25) };
      });
      assert.deepEqual(geometry, { canvasClipped: false, cardClipped: false, seatClipped: false, seatOverlap: false,
        topLabelMisplaced: false, dealerBlocked: false },
        `${width}px canvas and nine seats must fit without overlap or clipping`);
      const action = page.locator('[data-action="call"]');
      const box = await action.boundingBox();
      assert.ok(box && box.width >= 44 && box.height >= 40, `${width}px action must remain clearly usable`);
      assert.ok(box.y >= 0 && box.y + box.height <= height, `${width}px betting actions must fit the viewport`);
      if (process.env.REMASTER_SCREENSHOTS) {
        await page.screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-${width}.png`, fullPage: true });
      }
    }

    await require('./holdem_remastered_responsive.cjs')(page, update, playingRoom);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    for (let count = 2; count <= 9; count++) {
      await update(playingRoom(count));
      const seating = await page.locator('.poker-remastered-stage-wrap').evaluate(stage => {
        const box = stage.getBoundingClientRect();
        const seats = [...stage.querySelectorAll('.poker-remastered-seat')].map(node => node.getBoundingClientRect());
        const angles = seats.map(seat => {
          const angle = Math.atan2(seat.x + seat.width / 2 - box.x - box.width / 2,
            seat.y + seat.height / 2 - box.y - box.height / 2);
          return angle;
        });
        return { ordered: angles.every((angle, i) => i === 0 || angle > angles[i - 1]),
          overlap: seats.some((a, i) => seats.slice(i + 1).some(b =>
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)),
          dealerBlocked: seats.some(seat => seat.right > box.left + box.width * .42
            && seat.left < box.left + box.width * .58 && seat.top < box.top + box.height * .25) };
      });
      assert.deepEqual(seating, { ordered: true, overlap: false, dealerBlocked: false },
        `${count} seats must follow server seat order and reserve the dealer space`);
    }
    await require('./holdem_remastered_seating.cjs')(page, update, playingRoom);
    await update(playingRoom(9));
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 1920, height: 1080 });

    // Magnify the actual DOM layer rather than resampling a screenshot of the canvas.
    for (const zoom of [1.5, 2]) {
      await page.evaluate(zoom => { document.body.style.zoom = String(zoom); }, zoom);
      await page.waitForTimeout(500);
      await assertVectorCards();
      await assertCardAlignment();
      await assertVectorTable();
      if (process.env.REMASTER_SCREENSHOTS) {
        await page.locator('.poker-remastered-stage').screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-zoom-${zoom}.png` });
      }
    }
    await page.evaluate(() => { document.body.style.zoom = ''; });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await update({ ...playingRoom(), board: [], your_hole: [] });
    await assertVectorCards(5);
    await update({ ...playingRoom(), hand_no: 8 });
    await assertVectorCards();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.ok(await page.locator('.poker-remastered-card').evaluateAll(nodes =>
      nodes.every(node => getComputedStyle(node).opacity === '1')), 'reduced motion displays dealt cards immediately');
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    // Validate every vendor face and duplicate-card references, including court artwork.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const deck = Array.from({ length: 52 }, (_, i) => ({ r: 2 + i % 13, s: Math.floor(i / 13) }));
    for (let offset = 0; offset < deck.length; offset += 7) {
      const batch = Array.from({ length: 7 }, (_, i) => deck[(offset + i) % deck.length]);
      await update({ ...playingRoom(), board: batch.slice(0, 5), your_hole: batch.slice(5) });
      await assertVectorCards();
      const faces = await page.locator('.poker-remastered-card svg').evaluateAll(nodes => nodes.map(node => node.getAttribute('face')));
      assert.deepEqual(faces, batch.map(card => `${({ 10: 'T', 11: 'J', 12: 'Q', 13: 'K', 14: 'A' })[card.r] || card.r}${['S', 'H', 'D', 'C'][card.s]}`),
        'vendor artwork must map correctly to every server rank and suit');
    }
    await update({ ...playingRoom(), board: Array(5).fill({ r: 12, s: 1 }), your_hole: Array(2).fill({ r: 12, s: 1 }) });
    const references = await page.locator('.poker-remastered-card svg').evaluateAll(nodes => {
      const allIds = nodes.flatMap(svg => [...svg.querySelectorAll('[id]')].map(node => node.id));
      return { unique: new Set(allIds).size === allIds.length,
        local: nodes.every(svg => [...svg.querySelectorAll('use')].every(use => {
          const href = use.getAttribute('href') || use.getAttribute('xlink:href');
          return href?.startsWith('#') && [...svg.querySelectorAll('[id]')].some(node => node.id === href.slice(1));
        })) };
    });
    assert.deepEqual(references, { unique: true, local: true }, 'each card instance owns its SVG definitions');
    await page.emulateMedia({ reducedMotion: 'no-preference' });

    // Observe actual rendered frames: board cards turn over in place, never fly in again.
    const preflop = { ...playingRoom(), hand_no: 50, stage: 'preflop', board: [] };
    await update(preflop);
    await page.waitForTimeout(1200);
    async function traceBoard(first, followups = []) {
      return page.evaluate(async ({ first, followups }) => {
        const nodes = [...document.querySelectorAll('.poker-remastered-card')].slice(0, 5);
        const before = nodes.map(node => {
          const rect = node.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, width: rect.width };
        });
        const frames = [];
        registry.dispatchMessage({ type: 'game_update', ...first });
        const start = performance.now();
        while (performance.now() - start < 1300) {
          await new Promise(requestAnimationFrame);
          const t = performance.now() - start;
          for (const item of followups) {
            if (!item.sent && t >= item.after) {
              registry.dispatchMessage({ type: 'game_update', ...item.room });
              item.sent = true;
            }
          }
          frames.push(nodes.map(node => {
            const rect = node.getBoundingClientRect();
            return { t, connected: node.isConnected, x: rect.x + rect.width / 2,
              y: rect.y + rect.height / 2, width: rect.width,
              face: node.querySelector('svg')?.getAttribute('face') };
          }));
        }
        return { before, frames };
      }, { first, followups });
    }
    function assertFlip(trace, indices, faces) {
      for (let i = 0; i < 5; i++) {
        const samples = trace.frames.map(frame => frame[i]);
        const original = trace.before[i];
        assert.ok(samples.every(s => s.connected && (s.width < 1 ||
          (Math.abs(s.x - original.x) < 2 && Math.abs(s.y - original.y) < 2))),
          `board card ${i} must stay in its existing slot throughout the flip: ${JSON.stringify({ original,
            bad: samples.find(s => !s.connected || (s.width >= 1 && (Math.abs(s.x - original.x) >= 2 || Math.abs(s.y - original.y) >= 2))) })}`);
        if (indices.includes(i)) {
          assert.equal(samples[0].face, '1B', 'reveal begins with the card back');
          assert.ok(Math.min(...samples.map(s => s.width)) < original.width * .3, 'flip visibly narrows the card');
          const revealed = samples.find(s => s.face !== '1B');
          assert.ok(revealed && revealed.width < original.width * .35, 'face switches near the thin midpoint');
        }
        assert.equal(samples.at(-1).face, faces[i]);
        assert.ok(Math.abs(samples.at(-1).width - original.width) < 2, 'flip finishes at full width');
      }
    }
    const flop = { ...preflop, stage: 'flop', board: playingRoom().board };
    const flopTrace = await traceBoard(flop, [{ after: 90, room: { ...flop, pot: 60 } }]);
    assertFlip(flopTrace, [0, 1, 2], ['AS', 'TH', '7D', '1B', '1B']);
    const revealTimes = [0, 1, 2].map(i => flopTrace.frames.find(frame => frame[i].face !== '1B')[i].t);
    assert.ok(revealTimes[0] < revealTimes[1] && revealTimes[1] < revealTimes[2], 'flop reveals its three cards in order');
    const turn = { ...flop, stage: 'turn', board: [...flop.board, { r: 12, s: 3 }] };
    const river = { ...turn, stage: 'river', board: [...turn.board, { r: 2, s: 1 }] };
    const lateTrace = await traceBoard(turn, [{ after: 90, room: river }, { after: 180, room: river }]);
    assertFlip(lateTrace, [3, 4], ['AS', 'TH', '7D', 'QC', '2H']);
    // A new hand arriving during a reveal must cancel callbacks from the old hand.
    await update({ ...preflop, hand_no: 51 });
    await page.waitForTimeout(1200);
    await update({ ...flop, hand_no: 51 });
    await page.waitForTimeout(100);
    await update({ ...preflop, hand_no: 52 });
    await page.waitForTimeout(1200);
    assert.deepEqual(await page.locator('.poker-remastered-card svg').evaluateAll(nodes =>
      nodes.slice(0, 5).map(node => node.getAttribute('face'))), Array(5).fill('1B'));
    await update({ ...flop, hand_no: 52 });
    await page.setViewportSize({ width: 1500, height: 960 });
    await page.waitForTimeout(500);
    await assertCardAlignment();
    assert.deepEqual(await page.locator('.poker-remastered-card svg').evaluateAll(nodes =>
      nodes.slice(0, 5).map(node => node.getAttribute('face'))), ['AS', 'TH', '7D', '1B', '1B']);
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), { ...river, hand_no: 52 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await assertCardAlignment();
    assert.deepEqual(await page.locator('.poker-remastered-card svg').evaluateAll(nodes =>
      nodes.slice(0, 5).map(node => node.getAttribute('face'))), ['AS', 'TH', '7D', 'QC', '2H'],
      'rejoining at river shows the authoritative board immediately without replaying reveals');
    await update({ ...preflop, hand_no: 53 });
    await page.waitForTimeout(100);
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), { ...river, hand_no: 53 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await assertCardAlignment();

    await page.setViewportSize({ width: 1440, height: 960 });
    await require('./holdem_remastered_chips.cjs')(page, update, playingRoom);
    room = playingRoom();
    room.result = { pot: 45, board: room.board, payouts: { alice: 45 },
      hands: room.players.map(p => ({ ...p, cards: room.your_hole, hand_name: '一对', committed: 15 })) };
    room.hand_ready = { hand_no: 2, ready: [], total: 3, left: 10, ends_match: true };
    room.to_act = null;
    room.your_options = null;
    await update(room);
    assert.match(await page.locator('.hand-result-body').innerText(), /一对/);
    await page.locator('.hand-continue-button').click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'hand_continue' });
    delete room.hand_ready;
    room.status = 'settled';
    room.match_result = { reason: '本局结束', match_no: 1, hand_no: 2, blind: 5, buy_in: 200,
      votes: {}, total: 3, can_next: true,
      players: room.players.map(p => ({ ...p, paid: 200, net: p.stack - 200, rating_delta: 0 })) };
    await update(room);
    await page.waitForFunction(() => !document.querySelector('.poker-remastered-stage canvas'));
    assert.equal(await page.locator('.poker-remastered-card').count(), 0, 'settlement removes the SVG overlay');
    await page.getByRole('button', { name: '结算并再来一局', exact: true }).click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'settle_vote', choice: 'next', blind: 5 });
    await page.getByRole('button', { name: '结算并解散房间', exact: true }).click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), { type: 'settle_vote', choice: 'dissolve' });
    assert.match(await page.locator('#gameMain').innerText(), /不会继承本局桌上筹码/);
    await update({ ...playingRoom(), hand_no: 6 });
    await page.locator('.poker-remastered-stage canvas').waitFor();
    await page.locator('[data-action="call"]').waitFor();
    await assertVectorCards();
    await page.evaluate(() => registry.dispatchMessage({ type: 'room_closed' }));
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), playingRoom());
    await page.locator('.poker-remastered-stage canvas').waitFor();
    await page.locator('[data-action="call"]').waitFor();
    await assertVectorCards();

    // A resumed room still reports game_type=holdem; the preference must survive reload.
    await page.reload();
    await initialize();
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), playingRoom());
    await page.locator('.poker-remastered-stage canvas').waitFor();
    await page.evaluate(() => registry.dispatchMessage({ type: 'room_closed' }));
    assert.equal(await page.locator('.poker-remastered-stage canvas').count(), 0);
    await page.evaluate(() => { core.state.hallPage = null; core.renderGameView(); });
    await page.locator('.hall-game-card').filter({ hasText: '德州扑克 · 无限注' }).click();
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), playingRoom());
    await page.locator('.poker-table').waitFor();
    assert.equal(await page.locator('.poker-remastered').count(), 0, 'classic entry keeps the original renderer');
    assert.deepEqual(errors, []);
    console.log('PASS remastered Holdem: shared rooms/create payload, actions, locks, canvas lifecycle, spectator, desktop layouts, settlement, reconnect and classic coexistence');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
