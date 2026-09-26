// Run: node tests/test_werewolf_ui.cjs（或 npm run test:browser -- tests/test_werewolf_ui.cjs）
// 狼人杀牌桌 UI：阶段横幅、身份卡、座位状态、四类决策与结算的渲染与交互。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

function baseView(overrides = {}) {
  return {
    room_id: 9, name: '月夜桌', game_type: 'werewolf', status: 'playing',
    owner: 'alice', owner_name: '爱丽丝', buy_in: 100, blind: 5, paused: false,
    rules: { board: null, win_mode: 'bian', witch_self_save: 'first',
      guard_continuous: false, hunter_shot_on_poison: false,
      last_words: 'first', tie: 'revote', speak_seconds: 30,
      vote_seconds: 30, night_seconds: 25 },
    turn_seq: 1,
    players: [
      { username: 'alice', nickname: '爱丽丝', stack: 100, alive: true, role: null },
      { username: 'bob', nickname: '鲍勃', stack: 100, alive: true, role: null },
      { username: 'carol', nickname: '卡萝', stack: 100, alive: true, role: null },
      { username: 'dave', nickname: '大卫', stack: 100, alive: true, role: null },
      { username: 'eve', nickname: '伊芙', stack: 100, alive: false, role: null },
      { username: 'frank', nickname: '弗兰克', stack: 100, alive: true, role: null },
    ],
    match_no: 1, day_no: 1, phase: 'night', night_role: '狼人',
    last_words_current: null, turn_left: 0, vote: {}, vote_round: 1,
    candidates: null, last_night: {},
    history: [{ day: 1, text: '第 1 夜降临，全员闭眼', kind: 'system' }],
    last_action: { text: '第 1 夜降临' }, result: null,
    your_role: { key: 'werewolf', name: '狼人', faction: 'wolf',
      faction_name: '狼人阵营', teammates: [{ username: 'bob', nickname: '鲍勃' }] },
    your_options: null, voice: { channel: 'wolf', can_speak: true },
    ...overrides,
  };
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(),
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(3000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://wolf.test/**', async route => {
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
      window.LIVE_CONFIG = { voice: { enabled: true } };
      window.sent = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
    });
    await page.goto('http://wolf.test/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      core.state.currentUser = { username: 'alice', nickname: '爱丽丝' };
      core.state.socket = { readyState: 1, send: data => sent.push(JSON.parse(data)) };
    });
    const show = view => page.evaluate(view => {
      window.sent.length = 0;
      core.state.myRoom = view;
      core.renderGameView();
    }, view);
    const sent = () => page.evaluate(() => sent.at(-1));

    // —— 夜晚·狼人行动：横幅、身份卡、狼队标记、刀口提交与空刀 ——
    await show(baseView({
      turn_left: 25,
      your_options: { kind: 'night', role: '狼人', allow_skip: true, current: null,
        submitted: false,
        targets: [
          { username: 'carol', nickname: '卡萝' }, { username: 'dave', nickname: '大卫' },
          { username: 'frank', nickname: '弗兰克' }],
      },
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /🌙.*第 1 夜 · 狼人请行动/s);
    assert.match(await page.locator('.ww-phase-timer').innerText(), /\d+ 秒/);
    assert.equal(await page.locator('.ww-voice').isDisabled(), true);
    assert.ok(await page.locator('.ww-role-card.f-wolf').isVisible());
    assert.match(await page.locator('.ww-role-card').innerText(), /狼人阵营/);
    assert.match(await page.locator('.ww-role-card').innerText(), /鲍勃/);
    assert.equal(await page.locator('.ww-seat.mate[data-username="bob"]').count(), 1);
    assert.equal(await page.locator('.ww-seat.dead').count(), 1);
    assert.match(await page.locator('.ww-seat.dead .ww-seat-unknown').innerText(), /未翻牌/);
    assert.equal(await page.locator('.ww-seat.dead .ww-role-chip').count(), 0);
    // 出局但遗言未了结：仍在局内，只看得到自己的身份
    await show(baseView({
      last_words_current: 'eve',
      players: baseView().players.map(p => p.username === 'alice'
        ? { ...p, alive: false }
        : p.username === 'eve' ? { ...p, role: '平民' } : p),
    }));
    assert.doesNotMatch(await page.locator('.ww-seats-head').innerText(), /上帝视角/);
    assert.match(await page.locator('.ww-role-out-note').innerText(), /身份仍在保密中/);
    assert.equal(await page.locator('.ww-seat[data-username="carol"] .ww-role-chip').count(), 0);
    assert.equal(await page.locator('#roomChatInput').isDisabled(), false);
    assert.match(await page.locator('#roomChatInput').getAttribute('placeholder'), /死者频道/);
    // 遗言了结转上帝视角：全场身份公开，聊天框只读
    await show(baseView({
      god_view: true,
      players: baseView().players.map(p => p.username === 'alice'
        ? { ...p, alive: false }
        : p.username === 'eve' ? { ...p, role: '平民', alive: false }
        : p.username === 'carol' ? { ...p, role: '预言家' } : p),
    }));
    assert.match(await page.locator('.ww-seats-head').innerText(), /上帝视角/);
    assert.match(await page.locator('.ww-role-out-note').innerText(), /上帝视角观战/);
    assert.equal(await page.locator('.ww-seat[data-username="carol"] .ww-role-chip').count(), 1);
    assert.equal(await page.locator('#roomChatInput').isDisabled(), true);
    assert.match(await page.locator('#roomChatInput').getAttribute('placeholder'), /仅可阅读/);
    // 恢复夜晚狼人行动视图
    await show(baseView({
      turn_left: 25,
      your_options: { kind: 'night', role: '狼人', allow_skip: true, current: null,
        submitted: false,
        targets: [
          { username: 'carol', nickname: '卡萝' }, { username: 'dave', nickname: '大卫' },
          { username: 'frank', nickname: '弗兰克' }],
      },
    }));
    await page.locator('.ww-target[data-username="carol"]').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'night', target: 'carol' });
    // 服务器回显新视图（actionLock 解除，刀口已定），狼人可改为空刀
    await show(baseView({
      your_options: { kind: 'night', role: '狼人', allow_skip: true, current: 'carol',
        submitted: false,
        targets: [
          { username: 'carol', nickname: '卡萝' }, { username: 'dave', nickname: '大卫' },
          { username: 'frank', nickname: '弗兰克' }],
      },
    }));
    assert.equal(await page.locator('.ww-target.selected[data-username="carol"]').count(), 1);
    assert.equal(await page.locator('.ww-target.selected[data-username="carol"]').getAttribute('aria-pressed'), 'true');
    await page.locator('button:has-text("改为空刀")').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'night', target: '' });

    // —— 夜晚·女巫两段决策：确认前锁定，解药+确认一次提交 ——
    await show(baseView({
      night_role: '女巫',
      your_role: { key: 'witch', name: '女巫', faction: 'god',
        faction_name: '神职阵营', teammates: [] },
      your_options: { kind: 'witch', submitted: false, can_save: true,
        can_poison: true, self_save: false,
        kill_target: { username: 'dave', nickname: '大卫' },
        poison_targets: [
          { username: 'bob', nickname: '鲍勃' }, { username: 'carol', nickname: '卡萝' },
          { username: 'frank', nickname: '弗兰克' }],
      },
      witch_stock: { save: true, poison: true },
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /女巫请行动/s);
    assert.match(await page.locator('.ww-action-title').innerText(), /大卫 被杀/);
    const confirm = page.locator('button:has-text("确认女巫决策")');
    assert.equal(await confirm.isDisabled(), true);
    await page.locator('button:has-text("使用解药")').first().click();
    assert.equal(await confirm.isDisabled(), false);
    await confirm.click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'witch', save: true, poison: null });

    // —— 白天·投票：得票角标、选人确认、平票重投文案 ——
    await show(baseView({
      phase: 'vote', night_role: null, turn_left: 25, turn_seq: 7,
      vote: { bob: 'alice', carol: 'alice' },
      your_options: { kind: 'vote', voted: null,
        targets: [
          { username: 'bob', nickname: '鲍勃' }, { username: 'carol', nickname: '卡萝' },
          { username: 'dave', nickname: '大卫' }, { username: 'frank', nickname: '弗兰克' }] },
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /投票放逐/s);
    assert.match(await page.locator('.ww-seat[data-username="alice"] .ww-vote-badge').innerText(), /2 票/);
    await page.locator('.ww-target[data-username="dave"]').click();
    await page.locator('button:has-text("投出 大卫")').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'vote', target: 'dave' });

    // 服务器回显已投票视图：按钮全部锁定，倒计时基线不因重渲染刷新
    const baseline = await page.evaluate(() => core.state.hallDeadlineAt);
    await show(baseView({
      phase: 'vote', night_role: null, turn_left: 21, turn_seq: 7,
      vote: { bob: 'alice', carol: 'alice', alice: 'dave' },
      your_options: { kind: 'vote', voted: 'dave',
        targets: [
          { username: 'bob', nickname: '鲍勃' }, { username: 'carol', nickname: '卡萝' },
          { username: 'dave', nickname: '大卫' }, { username: 'frank', nickname: '弗兰克' }] },
    }));
    const confirmVote = page.locator('.ww-action.primary');
    assert.equal(await confirmVote.isDisabled(), true);
    assert.match(await confirmVote.innerText(), /已投给 大卫/);
    assert.equal(await page.locator('.ww-target:not([disabled])').count(), 0);
    assert.equal(await page.evaluate(() => core.state.hallDeadlineAt), baseline);
    await show(baseView({
      phase: 'vote', night_role: null, turn_left: 19, turn_seq: 8,
      vote: { bob: 'alice', carol: 'alice', alice: 'dave' },
      your_options: { kind: 'vote', voted: 'dave',
        targets: [
          { username: 'bob', nickname: '鲍勃' }, { username: 'carol', nickname: '卡萝' },
          { username: 'dave', nickname: '大卫' }, { username: 'frank', nickname: '弗兰克' }] },
    }));
    assert.notEqual(await page.evaluate(() => core.state.hallDeadlineAt), baseline);

    // —— 白天·依次发言：当前发言人高亮，非发言人聊天框锁定 ——
    await show(baseView({
      phase: 'day', night_role: null, your_options: null, vote: {},
      speech_current: 'carol', turn_left: 18.4, turn_seq: 9,
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /第 1 天 · 卡萝 发言中/s);
    assert.match(await page.locator('.ww-seat[data-username="carol"] .ww-seat-status').innerText(), /发言中/);
    assert.equal(await page.locator('#roomChatInput').isDisabled(), true);
    await show(baseView({
      phase: 'day', night_role: null, your_options: null, vote: {},
      speech_current: 'alice', turn_left: 30, turn_seq: 9,
    }));
    assert.equal(await page.locator('#roomChatInput').isDisabled(), false);
    assert.match(await page.locator('#roomChatInput').getAttribute('placeholder'), /轮到你发言/);
    assert.ok(await page.locator('.ww-dock .countdown').isVisible());

    // —— 白天·语音等待：倒计时不出现，连接后由前端上报 speech_ready ——
    await show(baseView({
      phase: 'day', night_role: null, your_options: null, vote: {},
      speech_current: 'alice', awaiting_voice: true, turn_left: 0, turn_seq: 10,
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /连接语音中/s);
    assert.match(await page.locator('.ww-dock-title').innerText(), /连接语音后开始计时/s);
    assert.equal(await page.locator('.ww-dock .countdown').count(), 0);
    await page.locator('button:has-text("结束发言")').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'speech_end' });

    // —— 遗言：轮到死者发言时可提前结束遗言 ——
    await show(baseView({
      phase: 'last_words', night_role: null, your_options: null, vote: {},
      speech_current: null, awaiting_voice: false,
      last_words_current: 'alice', turn_left: 15, turn_seq: 11,
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /遗言 · 爱丽丝/s);
    await page.locator('button:has-text("结束遗言")').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'speech_end' });

    // —— 猎人开枪：放弃即空枪提交 ——
    await show(baseView({
      phase: 'shot', night_role: null, vote: {},
      your_role: { key: 'hunter', name: '猎人', faction: 'god',
        faction_name: '神职阵营', teammates: [] },
      your_options: { kind: 'shot',
        targets: [
          { username: 'bob', nickname: '鲍勃' }, { username: 'carol', nickname: '卡萝' }] },
    }));
    assert.match(await page.locator('.ww-phase').innerText(), /猎人开枪/s);
    await page.locator('button:has-text("放弃开枪")').click();
    assert.deepEqual(await sent(), { type: 'poker_action', action: 'shoot', target: '' });

    // 观战者可读公开聊天，但输入框不得暗示可向死者频道发言。
    await show(baseView({ phase: 'day', night_role: null, spectator: true,
      your_role: null, your_options: null }));
    assert.equal(await page.locator('#roomChatInput').isDisabled(), true);
    assert.match(await page.locator('#roomChatInput').getAttribute('placeholder'), /仅可阅读/);

    // —— 结算：胜利横幅、全员翻牌与净收益 ——
    await show(baseView({
      phase: 'showdown', night_role: null, your_options: null,
      settlement: { votes: {}, total: 6, can_next: true, blind: 5 },
      result: {
        match_no: 1, days: 3, winner: 'good', reason: '狼人全部出局',
        winner_text: '好人阵营获胜：狼人全部出局', payout: 'ante',
        payouts: { bob: 5 }, gains: { alice: 2.5 },
        roles: { alice: '狼人', bob: '狼人', carol: '预言家', dave: '女巫',
          eve: '猎人', frank: '平民' },
        players: [
          { username: 'alice', nickname: '爱丽丝', role: '狼人', alive: false, net: -5 },
          { username: 'bob', nickname: '鲍勃', role: '狼人', alive: false, net: -5 },
          { username: 'carol', nickname: '卡萝', role: '预言家', alive: true, net: 2.5 },
          { username: 'dave', nickname: '大卫', role: '女巫', alive: true, net: 2.5 },
          { username: 'eve', nickname: '伊芙', role: '猎人', alive: true, net: 2.5 },
          { username: 'frank', nickname: '弗兰克', role: '平民', alive: true, net: 2.5 },
        ],
        ratings: {},
      },
    }));
    assert.match(await page.locator('.ww-result.good-win .ww-result-banner').innerText(),
      /好人阵营获胜 · 狼人全部出局/);
    assert.equal(await page.locator('.ww-result-row').count(), 6);
    assert.match(await page.locator('.ww-result-row.was-wolf').first().innerText(), /狼人/);
    assert.match(await page.locator('.ww-result-net.win').first().innerText(), /\+2\.5/);

    assert.deepEqual(errors, []);
    console.log('PASS: werewolf table renders phases, roles, votes, actions and settlement');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
