// Run: npm run test:browser -- tests/test_voice_hub_ui.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

const member = (username, nickname) => ({ username, nickname, avatar: '' });
const baseChannels = () => [
  { id: 'default', name: '默认频道', custom: false, members: [] },
  { id: '1', name: '频道1', custom: false, members: [] },
  { id: '2', name: '频道2', custom: false, members: [] },
  { id: '3', name: '频道3', custom: false, members: [] },
  { id: '4', name: '频道4', custom: false, members: [] },
  { id: '5', name: '频道5', custom: false, members: [] },
];

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
    await page.route('http://voicehub.test/**', async route => {
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
      window.LIVE_CONFIG = { voice: { enabled: true, url: 'ws://mock' } };
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { window.sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
    });

    await page.goto('http://voicehub.test/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      core.setSignedIn({ username: 'alice', nickname: 'Alice', coins: 1000 });
    });

    // 1. 大厅渲染“语音聊天室”卡片；点击进入 voicehall 并发出订阅
    assert.equal(await page.locator('.hall-voice-card').count(), 1);
    assert.match(await page.locator('.hall-voice-card').innerText(), /语音聊天室/);
    assert.match(await page.locator('.hall-voice-card').innerText(), /随时开麦的语音频道/);
    await page.locator('.hall-voice-card').click();
    assert.equal(await page.evaluate(() => core.state.hallPage), 'voicehall');
    assert.equal(await page.locator('.voicehall').count(), 1);
    assert.deepEqual(await page.evaluate(() => window.sent.filter(msg => msg.type === 'voice_hub_state')),
      [{ type: 'voice_hub_state' }]);

    // 2. 无 my_channel 的快照 → 自动加入默认频道；完整快照 → 六行频道 + 当前高亮 + 成员条目
    await page.evaluate(snapshot => core.handleServerMessage(snapshot),
      { type: 'voice_hub_state', channels: baseChannels(), my_channel: null });
    assert.deepEqual(await page.evaluate(() => window.sent.at(-1)), { type: 'voice_hub_join', channel: 'default' });
    assert.equal(await page.evaluate(() => core.state.voiceHub.joinedOnce), true);
    const channels = baseChannels();
    channels[0].members = [member('alice', 'Alice'), member('bob', 'Bob')];
    channels[1].members = [member('carol', 'Carol')];
    await page.evaluate(snapshot => core.handleServerMessage(snapshot),
      { type: 'voice_hub_state', channels, my_channel: 'default' });
    assert.equal(await page.locator('.voicehub-channel').count(), 6);
    assert.equal(await page.locator('.voicehub-channel.is-current').count(), 1);
    assert.equal(await page.locator('.voicehub-channel.is-current').getAttribute('data-channel'), 'default');
    assert.equal(await page.locator('.voicehub-channel.is-current .voicehub-channel-leave').innerText(), '离开频道');
    assert.equal(await page.locator('.voicehub-channel.is-current .voicehub-channel-rename').count(), 0,
      '默认频道不能重命名');
    assert.equal(await page.locator('.voicehub-member').count(), 2);
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="bob"] .voicehub-member-name').innerText(), 'Bob');

    // 3. 点击“频道2”行 → join channel 2；新快照后高亮切换
    await page.locator('.voicehub-channel[data-channel="2"]').click();
    assert.deepEqual(await page.evaluate(() => window.sent.at(-1)), { type: 'voice_hub_join', channel: '2' });
    const switched = baseChannels();
    switched[0].members = [member('bob', 'Bob')];
    switched[2].members = [member('alice', 'Alice'), member('carol', 'Carol')];
    await page.evaluate(snapshot => core.handleServerMessage(snapshot),
      { type: 'voice_hub_state', channels: switched, my_channel: '2' });
    assert.equal(await page.locator('.voicehub-channel.is-current').getAttribute('data-channel'), '2');
    assert.equal(await page.locator('.voicehub-channel.is-current .voicehub-channel-rename').count(), 1);
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="alice"]').count(), 1);
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="carol"]').count(), 1);

    // 4. 重命名流：行内输入“开黑房”回车 → voice_hub_rename；custom 快照后显示新名与 ✎
    await page.locator('.voicehub-channel[data-channel="2"] .voicehub-channel-rename').click();
    const renameInput = page.locator('.voicehub-rename-input');
    assert.equal(await renameInput.count(), 1);
    assert.equal(await renameInput.getAttribute('maxlength'), '12');
    await renameInput.fill('开黑房');
    await renameInput.press('Enter');
    assert.deepEqual(await page.evaluate(() => window.sent.at(-1)), { type: 'voice_hub_rename', name: '开黑房' });
    const renamed = baseChannels();
    renamed[0].members = [member('bob', 'Bob')];
    renamed[2] = { id: '2', name: '开黑房', custom: true,
      members: [member('alice', 'Alice'), member('carol', 'Carol')] };
    await page.evaluate(snapshot => core.handleServerMessage(snapshot),
      { type: 'voice_hub_state', channels: renamed, my_channel: '2' });
    assert.match(await page.locator('.voicehub-channel[data-channel="2"] .voicehub-channel-name').innerText(), /开黑房/);
    assert.equal(await page.locator('.voicehub-channel[data-channel="2"] .voicehub-channel-mark').innerText(), '✎');

    // 5. 聊天：回车发送；本频道消息进列表，其他频道不进；历史整体替换；错误走 alertDialog
    await page.locator('.voicehall-chat .chat-input').fill('大家好');
    await page.locator('.voicehall-chat .chat-input').press('Enter');
    assert.deepEqual(await page.evaluate(() => window.sent.at(-1)), { type: 'voice_hub_chat', text: '大家好' });
    assert.equal(await page.locator('.voicehall-chat .chat-input').inputValue(), '');
    await page.evaluate(() => core.handleServerMessage({
      type: 'voice_hub_chat', channel: '2', username: 'alice', nickname: 'Alice', text: '大家好', time: 1758768000,
    }));
    assert.equal(await page.locator('.voicehall-chat .rc-message').count(), 1);
    assert.match(await page.locator('.voicehall-chat .rc-text').innerText(), /大家好/);
    await page.evaluate(() => core.handleServerMessage({
      type: 'voice_hub_chat', channel: '1', username: 'bob', nickname: 'Bob', text: '隔壁频道', time: 1758768001,
    }));
    assert.equal(await page.locator('.voicehall-chat .rc-message').count(), 1, '其他频道的消息不应进列表');
    await page.evaluate(() => core.handleServerMessage({
      type: 'voice_hub_chat_history', channel: '2', messages: [
        { username: 'bob', nickname: 'Bob', text: '旧消息一', time: 1758767900 },
        { username: 'alice', nickname: 'Alice', text: '旧消息二', time: 1758767950 },
      ],
    }));
    assert.equal(await page.locator('.voicehall-chat .rc-message').count(), 2);
    assert.match(await page.locator('.voicehall-chat .rc-message').first().innerText(), /旧消息一/);
    await page.evaluate(() => core.handleServerMessage({ type: 'voice_hub_error', message: '发送太快了' }));
    assert.match(await page.locator('#liveDialog').innerText(), /发送太快了/);
    await page.locator('.live-dialog-confirm').click();

    // 6. 齿轮：其他成员有，自己没有；拖动滑杆写入 localStorage voicePeerVolumes
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="carol"] .seat-volume-gear').count(), 1);
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="alice"] .seat-volume-gear').count(), 0);
    await page.locator('.voicehub-member[data-voice-peer="carol"] .seat-volume-gear').click();
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="carol"] .peer-volume-popover').isHidden(), false);
    await page.locator('.voicehub-member[data-voice-peer="carol"] .peer-volume-range')
      .evaluate(node => {
        node.value = '40';
        node.dispatchEvent(new Event('input', { bubbles: true }));
      });
    assert.equal(await page.locator('.voicehub-member[data-voice-peer="carol"] .peer-volume-value').innerText(), '40%');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('voicePeerVolumes') || '[]')),
      [['carol', 40]]);

    // 返回大厅：订阅保持（不重复发 voice_hub_state），标题行出现返回频道的胶囊
    await page.locator('.voicehall-head .hall-back').click();
    assert.equal(await page.evaluate(() => core.state.hallPage), null);
    assert.equal(await page.locator('.voicehall').count(), 0);
    assert.equal(await page.evaluate(() => window.sent.filter(msg => msg.type === 'voice_hub_state').length), 1,
      '订阅在整个会话保持，重进视图不重复发送');
    assert.match(await page.locator('.hall-voice-pill').innerText(), /开黑房 · 返回/);
    await page.locator('.hall-voice-pill').click();
    assert.equal(await page.evaluate(() => core.state.hallPage), 'voicehall');
    assert.equal(await page.locator('.voicehall').count(), 1);

    // 7. 窄屏视口（390x844）渲染不抛 pageerror
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => core.renderGameView());
    assert.equal(await page.locator('.voicehall').count(), 1);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(errors, []);

    console.log('PASS voice hall entry card, channel tree with members, rename flow, per-channel chat, peer volume gear, hall pill, narrow viewport');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
