// 地下城独立前端（dungeon.html + assets/js/dungeon/）主流程测试。
// 不需要真实后端：route 拦截提供静态文件，initScript 里的伪造 WebSocket
// 按协议应答（dungeon_state / dungeon_result / dungeon_events / dungeon_error）。
// 运行：npm run test:browser -- tests/test_dungeon_ui.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '..');

// 与 estate/dungeon/catalog.py 的联调目录一致的测试夹具。
const CATALOG = {
  config_version: 'dungeon-v0.1.0-foundation', simulation_version: 1,
  slots: ['weapon', 'helmet', 'chest', 'belt', 'boots', 'accessory'],
  qualities: ['normal', 'excellent', 'rare', 'epic'],
  item_templates: {
    starter_blade: { name: '练习短刃', slot: 'weapon', quality: 'normal', visual_id: 'starter_blade' },
    ruins_blade: { name: '遗迹短刃', slot: 'weapon', quality: 'excellent', visual_id: 'ruins_blade' },
  },
  challenges: [{
    challenge_id: 'ruins_slime_01', difficulty_id: 'normal', requires: [],
    reward_preview: { coins: 20, rolls: 1, possible_items: ['ruins_blade'] },
    enemy: {
      enemy_id: 'ruins_slime', name: '遗迹软泥', type: 'normal', visual_id: 'ruins_slime', phases: [],
      stats: { max_hp: 100, atk: 15, defense: 5, crit_bp: 0, crit_damage_bp: 15000, speed: 80 },
    },
  }],
};

function makeItem(itemId, templateId, name, quality, stats, location) {
  return { item_id: itemId, template_id: templateId, name, visual_id: templateId,
    slot: 'weapon', quality, stats, tags: [], effects: [], affixes: [],
    sell_coins: 12, locked: false, location, version: 1 };
}

function baseSnapshot() {
  return {
    phase: 'equipment', profile_version: 7, bag_capacity: 60, catalog: CATALOG,
    items: [
      makeItem('i-blade', 'starter_blade', '练习短刃', 'normal', { atk: 30 }, 'bag'),
      makeItem('i-drop', 'ruins_blade', '遗迹短刃', 'excellent', { atk: 38 }, 'bag'),
    ],
    loadout: { weapon: 'i-blade' },
    stats: { values: { max_hp: 300, atk: 30, defense: 20, crit_bp: 500, crit_damage_bp: 15000, speed: 100 },
      sources: [] },
    progress: [{ challenge_id: 'ruins_slime_01', difficulty_id: 'normal', clear_count: 0, unlocked: true }],
    pending_count: 0, active_job: null, coins: 1000, available_actions: [],
  };
}

function battlePublic(overrides = {}) {
  return {
    battle_id: 'b2', challenge_id: 'ruins_slime_01', difficulty_id: 'normal',
    status: 'running', revision: 1, playback_rate: 1, sim_time_us: 2000000,
    hp: { 'player:0': 300, 'enemy:0': 88 }, phase_index: null, last_sequence: 2, result: null,
    ...overrides,
  };
}

const EVENT_STARTED = seq => ({
  battle_id: 'b2', sequence_id: seq, battle_time_us: 0, event_type: 'BattleStarted',
  source: null, target: null, value: 'ruins_slime', player_hp: 300, enemy_hp: 100, phase_id: null });
const EVENT_DAMAGE = seq => ({
  battle_id: 'b2', sequence_id: seq, battle_time_us: 2000000, event_type: 'DamageApplied',
  source: 'player:0', target: 'enemy:0', value: 12, damage: 12, hp_loss: 12,
  hp_after: 88, is_critical: false, variance_bp: 10000, action_kind: 'normal' });
const EVENT_DIED = seq => ({
  battle_id: 'b2', sequence_id: seq, battle_time_us: 3600000, event_type: 'ActorDied',
  source: 'player:0', target: 'enemy:0', value: 'enemy:0' });
const EVENT_ENDED = seq => ({
  battle_id: 'b2', sequence_id: seq, battle_time_us: 3600000, event_type: 'BattleEnded',
  source: null, target: null, value: 'victory' });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || undefined,
    args: ['--no-sandbox'],
  });
  const pageErrors = [];

  /**
   * 新建页面并注入伪造服务端。fixture 在每次导航前（addInitScript）安装，
   * 因此 dungeon.html 的模块加载时 window.__dgnServer 已就绪。
   */
  async function makePage({ token = 'tok-1', fixture } = {}) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on('pageerror', error => pageErrors.push(`pageerror: ${error.message}`));
    await page.route('http://dgn.test/**', async route => {
      const file = path.resolve(ROOT, '.' + new URL(route.request().url()).pathname);
      if (!file.startsWith(ROOT + path.sep)) return route.fulfill({ status: 403 });
      try {
        const body = await fs.readFile(file);
        const contentType = {
          '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png',
          '.json': 'application/json', '.svg': 'image/svg+xml',
        }[path.extname(file)] || 'application/octet-stream';
        await route.fulfill({ body, contentType });
      } catch { await route.fulfill({ status: 404 }); }
    });
    await page.addInitScript(({ tokenValue, fixtureValue }) => {
      if (tokenValue) localStorage.setItem('liveAuthToken', tokenValue);
      window.__sent = [];
      window.__dgnState = JSON.parse(JSON.stringify(fixtureValue));
      let revision = window.__dgnState.battle?.revision || 1;

      window.WebSocket = class {
        static OPEN = 1;
        constructor(url) {
          this.url = url;
          this.readyState = 1;
          this._handlers = {};
          queueMicrotask(() => (this._handlers.open || []).forEach(fn => fn({})));
        }
        addEventListener(type, fn) {
          if (type === 'message') window.__dgnDeliver = fn;
          (this._handlers[type] ||= []).push(fn);
        }
        send(data) {
          const message = JSON.parse(data);
          window.__sent.push(message);
          queueMicrotask(() => window.__dgnServer(message));
        }
        close() {}
      };

      window.__dgnServer = message => {
        const state = window.__dgnState;
        const send = payload => window.__dgnDeliver &&
          window.__dgnDeliver({ data: JSON.stringify(payload) });
        switch (message.type) {
          case 'resume':
            send({ type: 'resume_success', username: 'alice', role: 'user', nickname: '阿莉',
              avatar: '', coins: state.snapshot.coins, rating: null });
            break;
          case 'login':
            send({ type: 'login_success', username: message.username, role: 'user', token: 'tok-new',
              nickname: '阿莉', avatar: '', coins: 1000, rating: null });
            break;
          case 'get_dungeon':
            send({ type: 'dungeon_state', request_id: message.request_id, ...state.snapshot });
            break;
          case 'dungeon_start': {
            send({ type: 'dungeon_result', request_id: message.request_id, result_kind: 'action',
              result: { request_id: message.request_id, battle: state.battle,
                profile_version: 8, changed: true, replayed: false },
              state: { ...state.snapshot, profile_version: 8,
                active_job: { kind: 'battle', id: state.battle.battle_id } } });
            break;
          }
          case 'dungeon_sync': {
            const after = message.after_sequence || 0;
            const slice = state.events.filter(e => e.sequence_id > after).slice(0, 50);
            send({ type: 'dungeon_events', request_id: message.request_id, battle: state.battle,
              events: slice,
              next_cursor: slice.length ? slice[slice.length - 1].sequence_id : after,
              has_more: false, changed: false });
            break;
          }
          case 'dungeon_control': {
            revision += 1;
            const patch = { revision };
            if (message.command === 'pause') patch.status = 'paused';
            if (message.command === 'resume') patch.status = 'running';
            if (message.command === 'set_rate') patch.playback_rate = message.rate;
            state.battle = { ...state.battle, ...patch };
            send({ type: 'dungeon_result', request_id: message.request_id, result_kind: 'action',
              result: { request_id: message.request_id, battle: state.battle,
                changed: true, replayed: false },
              state: state.snapshot });
            break;
          }
          case 'dungeon_get_result':
            send({ type: 'dungeon_result', request_id: message.request_id, result_kind: 'lookup',
              battle: state.battle, result: state.result });
            break;
          case 'dungeon_claim_items':
            state.snapshot = { ...state.snapshot, pending_count: 0,
              items: state.snapshot.items.map(i => ({ ...i, location: 'bag' })) };
            send({ type: 'dungeon_result', request_id: message.request_id, result_kind: 'action',
              result: { request_id: message.request_id, item_ids: message.item_ids,
                profile_version: 9, changed: true, replayed: false },
              state: { ...state.snapshot, profile_version: 9 } });
            break;
          case 'dungeon_equip':
            if (state.failNextEquip) {
              state.failNextEquip = false;
              send({ type: 'dungeon_error', request_id: message.request_id,
                code: 'version_conflict', message: '地下城存档已更新，请刷新后重试',
                retryable: true });
              break;
            }
            state.snapshot = { ...state.snapshot,
              loadout: { ...state.snapshot.loadout, [message.slot]: message.item_id } };
            send({ type: 'dungeon_result', request_id: message.request_id, result_kind: 'action',
              result: { request_id: message.request_id, slot: message.slot,
                item_id: message.item_id, profile_version: 10, changed: true, replayed: false },
              state: { ...state.snapshot, profile_version: 10 } });
            break;
          case 'dungeon_compare_item':
            send({ type: 'dungeon_comparison', request_id: message.request_id,
              item_id: message.item_id, slot: 'weapon',
              profile_version: state.snapshot.profile_version,
              current: state.snapshot.stats.values,
              preview: { ...state.snapshot.stats.values, atk: 68 },
              delta: { max_hp: 0, atk: 38, defense: 0, crit_bp: 0, crit_damage_bp: 0, speed: 0 } });
            break;
          default:
            send({ type: 'dungeon_error', request_id: message.request_id ?? null,
              code: 'invalid_request', message: '未知消息', retryable: false });
        }
      };
    }, { tokenValue: token, fixtureValue: fixture });
    return page;
  }

  const sentOf = (page, type) => page.evaluate(
    t => window.__sent.filter(m => m.type === t), type);

  try {
    /* ---------- 用例 A：token 进入 → 准备页 → 起局 → 战斗 → 结算 → 领取 ---------- */
    {
      const page = await makePage({
        fixture: {
          snapshot: baseSnapshot(),
          battle: battlePublic(),
          events: [EVENT_STARTED(1), EVENT_DAMAGE(2)],
          result: null,
        },
      });
      await page.goto('http://dgn.test/dungeon.html');
      await page.waitForFunction(() => document.body.textContent.includes('遗迹软泥'), null, { timeout: 5000 });
      assert.equal(await page.locator('#dgnCoinsValue').textContent(), '1000', '金币应展示 dungeon_state.coins');
      assert.ok(await page.getByRole('button', { name: '发起挑战' }).isVisible(), '已解锁关卡应有起局按钮');

      // 390px 手机宽度下起局按钮仍可操作
      await page.setViewportSize({ width: 390, height: 760 });
      assert.ok(await page.getByRole('button', { name: '发起挑战' }).isVisible(), '390px 下起局按钮可见');
      await page.setViewportSize({ width: 1280, height: 800 });

      // 起局 → 战斗页：拿到 battle_id 才跳转
      await page.getByRole('button', { name: '发起挑战' }).click();
      await page.waitForFunction(() => location.hash.startsWith('#/battle/'), null, { timeout: 5000 });
      assert.equal(await page.evaluate(() => location.hash), '#/battle/b2');
      const starts = await sentOf(page, 'dungeon_start');
      assert.equal(starts.length, 1);
      assert.equal(starts[0].expected_version, 7, '起局必须携带最新 profile_version');
      // 权威检查点先恢复血量（enemy 88/100）
      await page.waitForFunction(() => {
        return [...document.querySelectorAll('.dgn-hp-numbers')]
          .some(t => t.textContent.includes('88'));
      }, null, { timeout: 5000 });
      await page.waitForFunction(() => document.querySelector('.dgn-log')?.children.length >= 2,
        null, { timeout: 5000 });

      // 暂停/倍速：命令带 expected_revision，且等服务端确认后按钮才变化
      await page.getByRole('button', { name: '暂停' }).click();
      await page.waitForFunction(() => document.body.textContent.includes('已暂停'), null, { timeout: 5000 });
      const pauses = await sentOf(page, 'dungeon_control');
      assert.equal(pauses[0].command, 'pause');
      assert.equal(pauses[0].expected_revision, 1, '控制命令应携带当前 revision');
      await page.getByRole('button', { name: /倍速 x1/ }).click();
      await page.waitForFunction(() => document.body.textContent.includes('倍速 x2'), null, { timeout: 5000 });

      // 服务端结算 → 下一次同步自动进入结算页
      await page.evaluate(({ extra, settledBattle }) => {
        const state = window.__dgnState;
        state.events = [...state.events, ...extra];
        state.battle = settledBattle;
        state.result = { outcome: 'victory', duration_us: 3600000, player_remaining_hp: 300,
          damage_dealt: 100, damage_taken: 0, coins_gained: 20,
          items: [{ ...state.snapshot.items[1], location: 'pending' }] };
        state.snapshot = { ...state.snapshot, pending_count: 1, active_job: null };
      }, {
        extra: [EVENT_DIED(3), EVENT_ENDED(4)],
        settledBattle: battlePublic({ status: 'settled', revision: 3, playback_rate: 2,
          last_sequence: 4, result: { outcome: 'victory' } }),
      });
      await page.waitForFunction(() => location.hash === '#/result/b2', null, { timeout: 8000 });
      await page.waitForFunction(() => document.body.textContent.includes('胜利'), null, { timeout: 5000 });
      assert.equal(await page.locator('.dgn-result-coins').textContent(), '金币 +20');
      assert.ok(await page.locator('.dgn-tag-pending').first().isVisible(), '待领取装备应有标记');

      await page.getByRole('button', { name: /领取待入包装备/ }).click();
      await page.waitForFunction(() => !document.querySelector('.dgn-tag-pending'), null, { timeout: 5000 });
      const claims = await sentOf(page, 'dungeon_claim_items');
      assert.equal(claims.length, 1);
      assert.deepEqual(claims[0].item_ids, ['i-drop']);

      await page.getByRole('button', { name: '返回准备页' }).click();
      await page.waitForFunction(() => location.hash === '#/prep', null, { timeout: 5000 });
      const abandons = (await sentOf(page, 'dungeon_control')).filter(m => m.command === 'abandon');
      assert.equal(abandons.length, 0, '全程不应发送 abandon');
      await page.close();
    }

    /* ---------- 用例 B：战斗中刷新 → token 续期 → 自动跳回战斗并恢复 ---------- */
    {
      const snapshot = baseSnapshot();
      snapshot.active_job = { kind: 'battle', id: 'b1' };
      const page = await makePage({
        fixture: {
          snapshot,
          battle: battlePublic({ battle_id: 'b1', revision: 2 }),
          events: [EVENT_STARTED(1), EVENT_DAMAGE(2)],
          result: null,
        },
      });
      await page.goto('http://dgn.test/dungeon.html');
      await page.waitForFunction(() => location.hash === '#/battle/b1', null, { timeout: 5000 });
      await page.waitForFunction(() => {
        return [...document.querySelectorAll('.dgn-hp-numbers')]
          .some(t => t.textContent.includes('88'));
      }, null, { timeout: 5000 });
      const abandons = (await sentOf(page, 'dungeon_control')).filter(m => m.command === 'abandon');
      assert.equal(abandons.length, 0, '重载恢复不应放弃战斗');
      await page.close();
    }

    /* ---------- 用例 C：装备对比 / 版本冲突处理 / 穿戴 ---------- */
    {
      const page = await makePage({ fixture: { snapshot: baseSnapshot(), battle: null, events: [], result: null } });
      await page.goto('http://dgn.test/dungeon.html');
      await page.waitForFunction(() => document.body.textContent.includes('遗迹软泥'), null, { timeout: 5000 });
      await page.evaluate(() => { location.hash = '#/loadout'; });
      await page.waitForFunction(() => document.body.textContent.includes('背包'), null, { timeout: 5000 });

      await page.locator('.dgn-item', { hasText: '遗迹短刃' }).getByRole('button', { name: '对比' }).click();
      await page.waitForFunction(() => document.body.textContent.includes('+38'), null, { timeout: 5000 });

      // 版本冲突：报错 → 自动刷新状态 → 可重试
      await page.evaluate(() => { window.__dgnState.failNextEquip = true; });
      await page.locator('.dgn-item', { hasText: '遗迹短刃' }).getByRole('button', { name: '穿戴' }).click();
      await page.waitForFunction(() => {
        const toast = document.querySelector('#dgnToast');
        return toast && !toast.hidden && toast.textContent.includes('存档已更新');
      }, null, { timeout: 5000 });
      assert.ok((await sentOf(page, 'get_dungeon')).length >= 2, '冲突后应重新拉取状态');

      await page.locator('.dgn-item', { hasText: '遗迹短刃' }).getByRole('button', { name: '穿戴' }).click();
      await page.waitForFunction(() => document.querySelector('.dgn-slot-grid')?.textContent.includes('遗迹短刃'),
        null, { timeout: 5000 });
      await page.close();
    }

    /* ---------- 用例 D：无 token → 登录表单 → 登录进入准备页 ---------- */
    {
      const page = await makePage({
        token: null,
        fixture: { snapshot: baseSnapshot(), battle: null, events: [], result: null },
      });
      await page.goto('http://dgn.test/dungeon.html');
      await page.waitForSelector('#dgnLogin:not([hidden])', { timeout: 5000 });
      await page.fill('#dgnUsername', 'alice');
      await page.fill('#dgnPassword', 'secret');
      await page.click('#dgnLoginForm button[type=submit]');
      await page.waitForFunction(() => document.body.textContent.includes('遗迹软泥'), null, { timeout: 5000 });
      assert.equal(await page.evaluate(() => localStorage.getItem('liveAuthToken')), 'tok-new');
      await page.close();
    }

    assert.deepEqual(pageErrors, [], `页面不应有未捕获异常：${pageErrors.join(' | ')}`);
    console.log('test_dungeon_ui: 全部断言通过');
  } finally {
    await browser.close();
  }
})();
