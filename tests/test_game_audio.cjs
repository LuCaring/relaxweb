// Browser regression for shared sound controls and authoritative room updates.
// Run: npm run test:browser -- tests/test_game_audio.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

const players = Array.from({length: 4}, (_, i) => ({username: `p${i}`, nickname: `玩家 ${i + 1}`, stack: 100, cards: 7, in_hand: true}));
function snapshot(game, extra = {}) {
  return {room_id: 1, name: '音效验证桌', game_type: game, status: 'playing',
    owner: 'p0', owner_name: '玩家 1', players, buy_in: 100, blind: 5, hand_no: 1,
    to_act: 'p1', turn_left: 30, stage: 'preflop', phase: 'discard',
    board: [], pot: 15, deck_left: 80, wall_count: 80, your_options: {}, last_action: null, ...extra};
}

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.sent = [];
      window.audioStarts = [];
      window.audioStops = [];
      window.audioContexts = [];
      window.audioGains = [];
      window.masterAudioGains = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; this.listeners = {}; }
        send(data) { sent.push(JSON.parse(data)); }
        close() {}
        addEventListener(name, fn) { this.listeners[name] = fn; }
      };
      const NativeAudioContext = window.AudioContext;
      window.AudioContext = class extends NativeAudioContext {
        constructor(...args) { super(...args); audioContexts.push(this); }
        createGain() {
          const node = super.createGain(); audioGains.push(node);
          const connect = node.connect.bind(node);
          node.connect = (...args) => {
            if (args[0] === this.destination) masterAudioGains.push(node);
            return connect(...args);
          };
          return node;
        }
        trackSource(node, kind) {
          const start = node.start.bind(node);
          const stop = node.stop.bind(node);
          node.start = (...args) => { audioStarts.push({kind, when: args[0] ?? this.currentTime}); return start(...args); };
          node.stop = (...args) => { audioStops.push({kind, when: args[0] ?? this.currentTime, calledAt: this.currentTime}); return stop(...args); };
          return node;
        }
        createOscillator() { return this.trackSource(super.createOscillator(), 'tone'); }
        createBufferSource() { return this.trackSource(super.createBufferSource(), 'noise'); }
      };
    });
    await page.goto('http://localhost:8000/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      const registry = await import('/assets/js/registry.js');
      // Keep real message routing and audio code; game rendering is covered by
      // test_desktop/test_uno_ui and need not obscure these sound transitions.
      for (const id of ['holdem', 'uno', 'guandan', 'mahjong']) {
        registry.registerGame(id, {...registry.gameView(id), renderTable() {}, renderReview() { return document.createElement("div"); }});
      }
      core.setSignedIn({username: 'p0', nickname: '玩家 1', coins: 1000});
    });
    const deliver = data => page.evaluate(data => core.handleServerMessage(data), data);
    const update = data => deliver({type: 'game_update', ...data});
    const joined = data => deliver({type: 'game_joined', room: data});
    const starts = () => page.evaluate(() => audioStarts.length);
    const settleAudio = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    async function silentDuring(action, label) {
      const before = await starts();
      await action();
      await settleAudio();
      assert.equal(await starts(), before, label);
    }
    async function soundsDuring(action, label) {
      const before = await starts();
      await action();
      await page.waitForFunction(before => audioStarts.length > before, before, {timeout: 3000});
      assert.ok(await starts() > before, label);
    }

    const modal = page.locator('#gameAudioSettingsModal');
    const enabled = page.getByRole('checkbox', {name: '音效', exact: true});
    const volume = page.getByRole('slider', {name: '音效音量', exact: true});
    const preview = page.getByRole('button', {name: '试听音效', exact: true});
    async function setVolume(value) {
      await volume.evaluate((node, value) => {
        node.value = String(value);
        node.dispatchEvent(new Event('input', {bubbles: true}));
        node.dispatchEvent(new Event('change', {bubbles: true}));
      }, value);
      await settleAudio();
    }
    await page.locator('#hallAudioSettingsButton').click();
    assert.equal(await modal.isVisible(), true);
    assert.equal(await enabled.isChecked(), true);
    assert.equal(await volume.inputValue(), '60');
    await soundsDuring(() => preview.click(), 'preview should produce actual WebAudio sources');
    assert.equal(await page.evaluate(() => audioContexts.length), 1, 'one shared audio context');
    await page.keyboard.press('Escape');
    assert.equal(await modal.isVisible(), false);

    const action = (username, text) => ({username, nickname: username, text});
    const poker = snapshot('holdem', {last_action: action('p1', '跟注 5.00')});
    await silentDuring(() => joined(poker), 'join must not replay the existing action');
    const bet = {...poker, pot: 55, last_action: action('p2', '加注到 20.00')};
    await soundsDuring(() => update(bet), 'poker bet');
    await silentDuring(() => update(bet), 'duplicate state');
    await silentDuring(() => update({...bet, turn_left: 19}), 'countdown update');
    await silentDuring(() => page.evaluate(() => core.renderGameView()), 'DOM redraw');
    await silentDuring(() => update({...bet, players: [...players, {username: 'p4', nickname: '新玩家', stack: 100}]}), 'joining player must not replay a bet');
    const check = {...bet, last_action: action('p3', '看牌')};
    await soundsDuring(() => update(check), 'poker check');
    const fold = {...check, last_action: action('p2', '弃牌')};
    await soundsDuring(() => update(fold), 'poker fold');
    const myTurn = {...fold, to_act: 'p0', your_options: {check: true}};
    await soundsDuring(() => update(myTurn), 'my turn');
    await silentDuring(() => update({...myTurn, turn_left: 18}), 'same turn should not repeat');
    await silentDuring(() => update({...myTurn, paused: true, last_action: action('p3', '加注到 30.00')}), 'paused room');
    await silentDuring(() => update({...myTurn, paused: false}), 'resume must not replay paused actions');
    await silentDuring(() => deliver({type: 'room_closed'}), 'room exit');
    await joined(snapshot('holdem', {status: 'waiting', hand_no: 0}));
    await soundsDuring(() => update(snapshot('holdem')), 'new hand deal');

    const uno = snapshot('uno', {action_event: {id: 1, kind: 'play', username: 'p1', card: {c: 'r', v: '3'}},
      last_action: action('p1', '出 红3')});
    await silentDuring(() => joined(uno), 'UNO initial action');
    let currentUno = uno;
    for (const [index, value] of ['rev', 'skip', 'd2', 'wild', 'wd4'].entries()) {
      currentUno = {...uno, action_event: {id: index + 2, kind: 'play', username: 'p1', card: {c: value.startsWith('w') ? 'w' : 'r', v: value}},
        last_action: action('p1', `出 ${value}`)};
      await soundsDuring(() => update(currentUno), `UNO ${value}`);
      await silentDuring(() => update({...currentUno, turn_left: 20}), 'UNO event deduplication');
    }
    const draw = {...currentUno, action_event: {id: 7, kind: 'draw', username: 'p1', count: 1}, last_action: action('p1', '摸牌')};
    await soundsDuring(() => update(draw), 'UNO draw');
    await soundsDuring(() => update({...draw, last_action: action('p1', '留下摸的牌'), to_act: 'p2'}), 'UNO pass without a new event id');
    await soundsDuring(() => update({...draw, action_event: {id: 8, kind: 'uno', username: 'p1'}, last_action: action('p1', '喊出 UNO！')}), 'UNO call');

    const gd = snapshot('guandan', {bombs: 0, last_action: action('p1', '出 单张')});
    await silentDuring(() => joined(gd), 'guandan initial snapshot');
    const bomb = {...gd, bombs: 1, standing: {type: 'bomb', by: 'p2'}, last_action: action('p2', '出 炸弹')};
    await soundsDuring(() => update(bomb), 'guandan bomb');
    await soundsDuring(() => update({...bomb, last_action: action('p3', '不出')}), 'guandan pass');

    let mj = snapshot('mahjong', {wall_count: 60, players: players.map(p => ({...p, concealed: 13})), last_action: action('p1', '打出 一万')});
    await silentDuring(() => joined(mj), 'mahjong initial snapshot');
    for (const text of ['打出 二万', '碰 二万', '吃 三四五万', '明杠 六筒', '暗杠 九条']) {
      mj = {...mj, last_action: action('p2', text)};
      await soundsDuring(() => update(mj), `mahjong ${text}`);
    }
    mj = {...mj, wall_count: 59, players: mj.players.map(p => ({...p, concealed: p.username === 'p1' ? 14 : 13}))};
    await soundsDuring(() => update(mj), 'mahjong draw');
    const settled = {...mj, to_act: null, your_options: {}, result: {winner: 'p0', zimo: true, fans: [], fan_total: 8},
      settlement: {votes: {}, total: 4, can_next: true, blind: 5}};
    await soundsDuring(() => update(settled), 'mahjong settlement');
    await silentDuring(() => update({...settled, settlement: {...settled.settlement, votes: {p1: {choice: 'next'}}}}), 'settlement vote must not replay sound');

    // Mahjong can require a response without assigning to_act to this user.
    const claimBase = snapshot('mahjong', {phase: 'claim', to_act: null, your_options: {}, claim: {by: 'p1', tile: 0, waiting: ['p2']}});
    await joined(claimBase);
    await soundsDuring(() => update({...claimBase, your_options: {claim: {peng: true}, passed: false}, claim: {...claimBase.claim, waiting: ['p0', 'p2']}}), 'mahjong claim opportunity');

    // Estate acknowledgements carry request IDs; snapshots and duplicate replies are silent.
    await deliver({type: 'room_closed'});
    await page.evaluate(async () => {
      const protocol = await import('/assets/js/estate/protocol.js');
      window.estateCommand = protocol.estateCommand;
      core.state.hallPage = 'estate';
    });
    const estateReply = {type: 'estate_state', coins: 1000, server_time: 1000, result: {harvested: 1}};
    await silentDuring(() => deliver(estateReply), 'estate initial snapshot');
    const harvestId = await page.evaluate(() => estateCommand('estate_harvest', {plot_id: 'plot_1'}));
    await soundsDuring(() => deliver({...estateReply, request_id: harvestId}), 'estate confirmed harvest');
    await silentDuring(() => deliver({...estateReply, request_id: harvestId}), 'duplicate estate acknowledgement');
    const failedId = await page.evaluate(() => estateCommand('estate_plant', {plot_id: 'plot_1', crop_id: 'carrot'}));
    await silentDuring(() => deliver({type: 'estate_error', request_id: failedId, message: '测试失败'}), 'failed estate command');
    await page.locator('.live-dialog-confirm').click();
    await page.evaluate(() => { core.state.hallPage = null; });
    await joined(poker);

    await page.locator('#roomAudioSettingsButton').click();
    await soundsDuring(() => preview.click(), 'preview before muting');
    const stoppedBeforeMute = await page.evaluate(() => audioStops.length);
    await enabled.uncheck();
    assert.ok(await page.evaluate(before => audioStops.slice(before).some(stop => stop.when <= stop.calledAt + 0.03), stoppedBeforeMute), 'mute cancels scheduled sources immediately');
    await joined(poker);
    await silentDuring(() => update(bet), 'disabled sounds');
    await enabled.check();
    await setVolume(0);
    assert.equal(await preview.isDisabled(), true);
    await silentDuring(() => preview.evaluate(node => node.click()), 'zero volume preview');
    await silentDuring(() => update(check), 'zero volume actions');
    await setVolume(30);
    await soundsDuring(() => preview.click(), 'preview at lower volume');
    await page.waitForFunction(() => Math.abs(masterAudioGains[0].gain.value - 0.216) < 0.01);
    const quietGain = await page.evaluate(() => masterAudioGains[0]?.gain.value);
    await setVolume(85);
    await soundsDuring(() => preview.click(), 'preview at higher volume');
    await page.waitForFunction(() => Math.abs(masterAudioGains[0].gain.value - 0.612) < 0.01);
    const louderGain = await page.evaluate(() => masterAudioGains[0]?.gain.value);
    assert.ok(louderGain > quietGain && quietGain > 0, 'volume updates the actual shared output gain');
    await soundsDuring(() => preview.click(), 'audible after restoring volume');
    // A delayed browser audio unlock must not replay a preview after muting.
    await page.evaluate(async () => {
      const ctx = audioContexts[0];
      await ctx.suspend();
      const resume = ctx.resume.bind(ctx);
      let release;
      const pending = new Promise(resolve => { release = resolve; });
      ctx.resume = () => pending;
      window.releaseAudioResume = async () => { ctx.resume = resume; await resume(); release(); };
    });
    const beforeDelayedPreview = await starts();
    await preview.click();
    await enabled.uncheck();
    await page.evaluate(() => releaseAudioResume());
    await settleAudio();
    assert.equal(await starts(), beforeDelayedPreview, 'mute invalidates a preview waiting for resume');
    await enabled.check();

    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable: true, value: true});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await silentDuring(() => update({...bet, last_action: action('p1', '全下 100.00')}), 'background game updates');
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
    await soundsDuring(() => preview.click(), 'preview works after returning to foreground');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('gameAudioSettings'))), {enabled: true, volume: 85});
    for (const width of [320, 390, 1024, 1440]) {
      await page.setViewportSize({width, height: 800});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `sound controls fit ${width}px`);
      const bounds = await modal.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    }
    await page.keyboard.press('Escape');
    await page.reload();
    await page.locator('#hallAudioSettingsButton').click();
    assert.equal(await enabled.isChecked(), true);
    assert.equal(await volume.inputValue(), '85');
    await enabled.uncheck();
    await page.reload();
    await page.locator('#hallAudioSettingsButton').click();
    assert.equal(await enabled.isChecked(), false);
    assert.equal(await volume.inputValue(), '85');

    // Legacy opt-out remains respected; new preferences take precedence.
    for (const preferences of [
      {legacy: 'off', saved: null, expected: {enabled: false, volume: 60}},
      {legacy: 'on', saved: {enabled: false, volume: 35}, expected: {enabled: false, volume: 35}},
    ]) {
      const other = await browser.newPage();
      await other.addInitScript(({legacy, saved}) => {
        localStorage.setItem('pokerTurnSound', legacy);
        if (saved) localStorage.setItem('gameAudioSettings', JSON.stringify(saved));
        window.WebSocket = class {static OPEN = 1; constructor() {this.readyState = 1;} send() {} addEventListener() {} close() {}};
      }, preferences);
      await other.goto('http://localhost:8000/game.html');
      await other.locator('#hallAudioSettingsButton').focus();
      await other.keyboard.press('Enter');
      assert.equal(await other.getByRole('checkbox', {name: '音效', exact: true}).isChecked(), preferences.expected.enabled);
      assert.equal(await other.getByRole('slider', {name: '音效音量', exact: true}).inputValue(), String(preferences.expected.volume));
      for (let i = 0; i < 8; i++) {
        await other.keyboard.press('Tab');
        assert.equal(await other.evaluate(() => document.getElementById('gameAudioSettingsModal').contains(document.activeElement)), true, 'focus stays inside settings');
      }
      await other.keyboard.press('Escape');
      assert.equal(await other.locator('#hallAudioSettingsButton').evaluate(node => node === document.activeElement), true);
      await other.close();
    }
    const unsupported = await browser.newPage({viewport: {width: 320, height: 568}});
    const unsupportedErrors = [];
    unsupported.on('pageerror', error => unsupportedErrors.push(error.message));
    await unsupported.addInitScript(() => {
      window.AudioContext = undefined; window.webkitAudioContext = undefined;
      window.WebSocket = class {static OPEN = 1; constructor() {this.readyState = 1;} send() {} addEventListener() {} close() {}};
    });
    await unsupported.goto('http://localhost:8000/game.html');
    await unsupported.locator('#hallAudioSettingsButton').click();
    await unsupported.getByRole('button', {name: '试听音效', exact: true}).click();
    assert.deepEqual(unsupportedErrors, [], 'WebAudio absence degrades gracefully');
    assert.equal(await unsupported.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await unsupported.close();
    assert.deepEqual(errors, []);
    console.log('PASS shared game audio');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
