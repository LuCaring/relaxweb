// Run: node tests/test_voice_hub_e2e.cjs
// 语音聊天室端到端：本地 livekit-server --dev + chat_server + 静态服务，
// 三个浏览器页（假麦克风）从大厅进入语音聊天室：默认频道互听假麦音轨、
// 换频道后语音与聊天按频道隔离、重命名对订阅者可见、刷新后断线恢复。
// livekit-server 不在 PATH 时 SKIP。
const assert = require('node:assert/strict');
const { execFileSync, spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(ROOT, '.venv', 'bin', 'python');
const NAMES = ['ua', 'ub', 'uc'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 全局看门狗：卡死时打印退出点，避免无限等待
setTimeout(() => {
  console.error('GLOBAL TIMEOUT after 150s — test stuck');
  process.exit(2);
}, 150000).unref();

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitForPort(port, tries = 60) {
  for (let i = 0; i < tries; i += 1) {
    const ok = await new Promise((resolve) => {
      const probe = net.connect({ port, host: '127.0.0.1' });
      probe.once('connect', () => { probe.destroy(); resolve(true); });
      probe.once('error', () => resolve(false));
    });
    if (ok) return;
    await sleep(200);
  }
  throw new Error(`port ${port} never opened`);
}

async function waitForHttp(url, tries = 60) {
  for (let i = 0; i < tries; i += 1) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch { /* 等待服务监听 */ }
    await sleep(200);
  }
  throw new Error(`${url} never ready`);
}

async function main() {
  let lk;
  try {
    lk = execFileSync('which', ['livekit-server'], { encoding: 'utf8' }).trim();
  } catch {
    console.log('SKIP: livekit-server 不在 PATH（brew install livekit 后重跑）');
    return;
  }

  const lkPort = await freePort();
  const lkUdpPort = await freePort();
  const lkTcpPort = await freePort();
  const chatPort = await freePort();
  const webPort = await freePort();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'voicehub-e2e-'));
  const dbFile = path.join(tmp, 'voicehub-e2e.db');
  const env = {
    ...process.env,
    LIVE_DB_FILE: dbFile,
    LIVE_CHAT_HOST: '127.0.0.1',
    LIVE_CHAT_PORT: String(chatPort),
    LIVE_WEB_PORT: String(webPort),
    VOICE_ENABLED: '1',
    VOICE_URL: `ws://127.0.0.1:${lkPort}`,
    VOICE_API_URL: `http://127.0.0.1:${lkPort}`,
    VOICE_API_KEY: 'devkey',
    VOICE_API_SECRET: 'secret',
  };
  console.log(`livekit :${lkPort} chat :${chatPort} web :${webPort}`);

  const procs = [];
  const processErrors = [];
  let browser = null;
  const livekit = spawn(lk, ['--dev', '--bind', '127.0.0.1', '--port', String(lkPort),
    '--udp-port', String(lkUdpPort), '--rtc.tcp_port', String(lkTcpPort)],
    { stdio: ['ignore', 'ignore', 'pipe'] });
  livekit.stderr.on('data', (chunk) => processErrors.push(`livekit: ${chunk}`));
  procs.push(livekit);
  const chat = spawn(python, ['chat_server.py'], { cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe'] });
  chat.stderr.on('data', (chunk) => processErrors.push(`chat: ${chunk}`));
  procs.push(chat);
  const web = spawn(python, ['deploy/serve.py'], { cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe'] });
  web.stderr.on('data', (chunk) => processErrors.push(`web: ${chunk}`));
  procs.push(web);
  try {
    await waitForPort(lkPort);
    // 端口被旧实例占用时新进程会启动失败，快速失败而不是对着旧服务测试
    await sleep(500);
    assert.equal(livekit.exitCode, null, 'livekit-server exited (端口被占用？)');
    await waitForPort(chatPort);
    await waitForHttp(`http://127.0.0.1:${webPort}/game.html`);

    // 造 3 个账号并签发会话 token（浏览器经 localStorage 恢复会话）
    const bootstrap = `
import sys, json
sys.path.insert(0, ${JSON.stringify(ROOT)})
from server.schema import init_db
import server.database as storage
from server.accounts import Accounts
init_db(storage.database)
accounts = Accounts(storage.database)
names = ["ua", "ub", "uc"]
for name in names:
    with storage.database() as conn, conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?, '', '', 0, 1000)",
            (name,))
print(json.dumps({n: accounts.create_session(n) for n in names}))
`;
    const { stdout } = spawnSync(python, ['-c', bootstrap], { cwd: ROOT, env, encoding: 'utf8' });
    const tokens = JSON.parse(stdout.trim().split('\n').pop());

    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.CHROME_PATH || chromium.executablePath(),
      args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'],
    });
    const players = {};
    const errors = {};
    const consoleErrors = {};
    for (const name of NAMES) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      errors[name] = [];
      consoleErrors[name] = [];
      page.on('pageerror', (e) => errors[name].push(e.message));
      page.on('console', (message) => {
        if (message.type() === 'error') {
          consoleErrors[name].push(`${message.location().url}: ${message.text()}`);
        }
      });
      page.on('requestfailed', (request) => {
        // 本测试不启动直播推流，直播页入口打开时 WHEP 探测失败是预期行为。
        if (request.url().endsWith('/live/whep')) return;
        errors[name].push(`${request.url()}: ${request.failure()?.errorText}`);
      });
      await page.route(`http://127.0.0.1:${webPort}/assets/**`, (route) => {
        const file = path.resolve(ROOT, '.' + new URL(route.request().url()).pathname);
        if (!file.startsWith(path.join(ROOT, 'assets') + path.sep)) {
          return route.fulfill({ status: 403 });
        }
        const contentType = {
          '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
          '.png': 'image/png', '.json': 'application/json', '.jpg': 'image/jpeg',
          '.webp': 'image/webp',
        }[path.extname(file)] || 'application/octet-stream';
        try {
          return route.fulfill({ body: fs.readFileSync(file), contentType });
        } catch {
          return route.fulfill({ status: 404 });
        }
      });
      await page.addInitScript((token) => {
        localStorage.setItem('liveAuthToken', token);
        // 测试环境的假麦克风：WebAudio 振荡器顶替硬件采集，
        // 上麦（getUserMedia）拿到的是这条 440Hz 合成音轨
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        osc.frequency.value = 440;
        const gain = ctx.createGain();
        const dst = ctx.createMediaStreamDestination();
        osc.connect(gain).connect(dst);
        osc.start();
        window.__voiceSynthGain = gain;
        const synth = dst.stream;
        navigator.mediaDevices.getUserMedia = async (constraints) => {
          if (constraints && constraints.audio && !constraints.video) {
            return synth.clone();
          }
          throw new Error('voice hub e2e patched getUserMedia: audio only');
        };
      }, tokens[name]);
      await page.goto(`http://127.0.0.1:${webPort}/game.html`);
      try {
        await page.evaluate(async () => {
          const main = [...document.scripts].find((script) => script.type === 'module'
            && script.src.includes('/assets/js/main.js'));
          await import(main.src);
          window.core = await import('/assets/js/core.js');
          window.voice = await import('/assets/js/room-voice.js');
          window.voiceMic = await import('/assets/js/voice-mic.js');
        });
      } catch (error) {
        console.error('module errors:', consoleErrors[name], errors[name]);
        console.error('web 404:', processErrors.filter((line) => line.includes(' 404 ')).slice(-10));
        console.error('process state:', { livekit: livekit.exitCode, chat: chat.exitCode,
          web: web.exitCode });
        console.error('web logs:', processErrors.filter((line) => line.startsWith('web:')).join('').slice(-2000));
        throw error;
      }
      try {
        await page.waitForFunction(() => core.state.currentUser?.username, null, { timeout: 30000 });
      } catch (error) {
        console.error('page:', name, await page.evaluate(() => ({
          socket: core.state.socket?.readyState,
          token: Boolean(localStorage.getItem('liveAuthToken')),
          chatPort: core.CHAT_PORT,
          user: core.state.currentUser?.username,
        })));
        console.error('page errors:', errors[name]);
        console.error('console errors:', consoleErrors[name]);
        console.error('chat logs:', processErrors.filter((line) => line.startsWith('chat:')).join('').slice(-5000));
        throw error;
      }
      players[name] = { name, page };
    }

    try {
      // 1. 分别验证直播页全局入口、游戏厅顶栏快捷加入、详细页面自动加入。
      await players.ua.page.goto(`http://127.0.0.1:${webPort}/index.html`);
      await players.ua.page.locator('.voicehall-nav').click();
      await players.ua.page.waitForURL('**/game.html?view=voicehall');
      await players.ua.page.evaluate(async () => {
        const main = [...document.scripts].find((script) => script.type === 'module'
          && script.src.includes('/assets/js/main.js'));
        await import(main.src);
        window.core = await import('/assets/js/core.js');
        window.voice = await import('/assets/js/room-voice.js');
        window.voiceMic = await import('/assets/js/voice-mic.js');
      });
      await players.ua.page.waitForFunction(() => core.state.currentUser?.username === 'ua'
        && Boolean(document.querySelector('.voicehall')));

      await players.ub.page.locator('#voiceQuickToggle').click();
      await players.ub.page.locator('#voiceQuickChannel').selectOption('default');
      await players.ub.page.locator('#voiceQuickJoin').click();
      await players.ub.page.waitForFunction(() => core.state.voiceHub?.myChannel === 'default'
        && voice.voiceDebug().micPublished === true, null, { timeout: 20000, polling: 100 });
      assert.equal(await players.ub.page.locator('.voicehall').count(), 0,
        '顶栏快捷加入后无需进入详细页面');
      await players.ub.page.locator('#voiceQuickDetail').click();

      await players.uc.page.locator('#voiceQuickToggle').click();
      await players.uc.page.locator('#voiceQuickDetail').click();
      for (const name of NAMES) {
        await players[name].page.waitForFunction(() => core.state.voiceHub?.myChannel === 'default',
          null, { timeout: 15000, polling: 100 });
      }
      // 加入后自动发布假麦音轨，三人在同一 LiveKit 房间互通。
      for (const name of NAMES) {
        await players[name].page.waitForFunction(() => voice.voiceDebug().micPublished === true,
          null, { timeout: 20000, polling: 100 });
      }
      await players.ua.page.waitForFunction(() => {
        const button = document.querySelector('#voiceQuickToggle');
        return button.classList.contains('transmitting')
          && Number(button.style.getPropertyValue('--voice-glow')) > 0;
      }, null, { timeout: 10000, polling: 100 });
      for (const name of NAMES) {
        const debug = await players[name].page.evaluate(() => voice.voiceDebug());
        assert.equal(debug.connected, true, `${name} should be connected`);
        assert.equal(debug.room, 'vh-default', `${name} should stay in the default channel room`);
      }
      console.log('PASS entry: live page, header quick join, detailed page all auto-publish microphones');

      // 2. A 页存在 B 的远端 audio 元素且音量 > 0，且 B、C 两路都可播放
      await players.ua.page.waitForFunction(() => {
        const element = document.querySelector('audio[data-voice-peer="ub"]');
        return element && element.volume > 0 && !element.paused && element.readyState >= 2;
      }, null, { timeout: 20000, polling: 100 });
      await players.ua.page.waitForFunction(() => voice.voiceDebug().remoteAudio >= 2,
        null, { timeout: 10000, polling: 100 });
      console.log('PASS audio: A hears B (and C) with a live remote audio element at volume > 0');

      // 3. C 点击「频道 2」换频道：房间切到 vh-2，A 的默认频道不再有 C 的身影
      await players.uc.page.locator('.voicehub-channel[data-channel="2"]').click();
      await players.uc.page.waitForFunction(() => core.state.voiceHub?.myChannel === '2',
        null, { timeout: 15000, polling: 100 });
      await players.uc.page.waitForFunction(
        () => voice.voiceDebug().connected && voice.voiceDebug().room === 'vh-2',
        null, { timeout: 20000, polling: 100 });
      await players.uc.page.waitForFunction(() => voice.voiceDebug().micPublished === true,
        null, { timeout: 15000, polling: 100 });
      await players.ua.page.waitForFunction(
        () => !document.querySelector('.voicehub-member[data-voice-peer="uc"]'),
        null, { timeout: 15000, polling: 100 });
      const defaultMembers = await players.ua.page.evaluate(
        () => core.state.voiceHub.channels.find((channel) => channel.id === 'default').members
          .map((member) => member.username).sort());
      assert.deepEqual(defaultMembers, ['ua', 'ub'], 'A 的默认频道成员列表不应再含 C');
      await players.ua.page.waitForFunction(() => voice.voiceDebug().peers === 1
        && !document.querySelector('audio[data-voice-peer="uc"]'),
        null, { timeout: 15000, polling: 100 });
      console.log('PASS switch: C moved to vh-2, A no longer sees C in members or remote peers');

      // 4. 分频道聊天：A 发默认频道 B 收到、C 收不到；C 发频道 2 A/B 收不到
      await players.ua.page.locator('.voicehall-chat .chat-input').fill('大家好');
      await players.ua.page.locator('.voicehall-chat .chat-input').press('Enter');
      for (const name of ['ua', 'ub']) {
        await players[name].page.waitForFunction(
          (text) => [...document.querySelectorAll('.voicehall-chat .rc-text')]
            .some((node) => node.textContent === text), '大家好',
          { timeout: 15000, polling: 100 });
      }
      const cTexts = await players.uc.page.locator('.voicehall-chat .rc-text').allInnerTexts();
      assert.ok(!cTexts.includes('大家好'), 'C 不应收到默认频道的消息');
      await players.uc.page.locator('.voicehall-chat .chat-input').fill('频道2见');
      await players.uc.page.locator('.voicehall-chat .chat-input').press('Enter');
      await players.uc.page.waitForFunction(
        (text) => [...document.querySelectorAll('.voicehall-chat .rc-text')]
          .some((node) => node.textContent === text), '频道2见',
        { timeout: 15000, polling: 100 });
      for (const name of ['ua', 'ub']) {
        const texts = await players[name].page.locator('.voicehall-chat .rc-text').allInnerTexts();
        assert.ok(!texts.includes('频道2见'), `${name} 不应收到频道 2 的消息`);
        assert.equal(texts.filter((text) => text === '大家好').length, 1);
      }
      console.log('PASS chat: messages stay inside their own channel');

      // 5. 重命名联动：A 切到频道 1 并重命名为「开黑房」，仍在默认频道的 B 立即可见
      await players.ua.page.locator('.voicehub-channel[data-channel="1"]').click();
      await players.ua.page.waitForFunction(() => core.state.voiceHub?.myChannel === '1',
        null, { timeout: 15000, polling: 100 });
      await players.ua.page.locator('.voicehub-channel[data-channel="1"] .voicehub-channel-rename').click();
      await players.ua.page.locator('.voicehub-rename-input').fill('开黑房');
      await players.ua.page.locator('.voicehub-rename-input').press('Enter');
      await players.ub.page.waitForFunction(
        () => document.querySelector('.voicehub-channel[data-channel="1"] .voicehub-channel-name')
          ?.textContent.includes('开黑房'),
        null, { timeout: 15000, polling: 100 });
      const renamed = await players.ub.page.evaluate(
        () => core.state.voiceHub.channels.find((channel) => channel.id === '1'));
      assert.equal(renamed.name, '开黑房', 'B 的快照里频道 1 应显示 custom 名');
      assert.equal(renamed.custom, true);
      assert.equal(await players.ub.page.locator('.voicehub-channel[data-channel="1"] .voicehub-channel-mark')
        .innerText(), '✎');
      assert.equal(await players.ub.page.evaluate(() => core.state.voiceHub.myChannel), 'default',
        'B 应仍在默认频道');
      console.log('PASS rename: subscriber B sees the custom channel name immediately');

      // 6. 同一页面的聊天 WebSocket 重连：重新订阅频道并补发 LiveKit 授权
      await players.ub.page.evaluate(async () => {
        await voice.leaveVoice();
        core.state.socket.close();
      });
      await players.ub.page.waitForFunction(() => core.state.socket?.readyState === WebSocket.OPEN
        && core.state.voiceHub?.subscribed && voice.voiceDebug().connected
        && voice.voiceDebug().room === 'vh-default',
      null, { timeout: 30000, polling: 100 });
      assert.equal(await players.ub.page.locator('.voicehall-hint').innerText(), '语音已连接');
      console.log('PASS reconnect: same page resubscribes and regains LiveKit token');

      // 7. 断线兜底：C 刷新后自动 resume，重新拿到状态且频道树渲染、页面不抛错
      await players.uc.page.reload();
      await players.uc.page.evaluate(async () => {
        const main = [...document.scripts].find((script) => script.type === 'module'
          && script.src.includes('/assets/js/main.js'));
        await import(main.src);
        window.core = await import('/assets/js/core.js');
        window.voice = await import('/assets/js/room-voice.js');
        window.voiceMic = await import('/assets/js/voice-mic.js');
      });
      await players.uc.page.waitForFunction(() => core.state.currentUser?.username, null, { timeout: 30000 });
      await players.uc.page.locator('#voiceQuickToggle').click();
      await players.uc.page.locator('#voiceQuickDetail').click();
      await players.uc.page.waitForFunction(() => Boolean(core.state.voiceHub?.myChannel)
        && document.querySelectorAll('.voicehub-channel').length === 6,
        null, { timeout: 15000, polling: 100 });
      const restored = await players.uc.page.evaluate(() => core.state.voiceHub.myChannel);
      assert.ok(restored === '2' || restored === 'default',
        `C 刷新后应恢复频道状态（宽限期保住成员资格或重进默认频道），got ${restored}`);
      assert.equal(await players.uc.page.locator('.voicehub-channel.is-current')
        .getAttribute('data-channel'), restored);
      assert.deepEqual(errors.uc, [], 'C 刷新后页面不应抛错');
      console.log(`PASS reload: C resumes and restores channel ${restored} without errors`);
    } catch (error) {
      for (const name of NAMES) {
        const dump = await players[name].page.evaluate(() => ({
          voice: voice.voiceDebug(),
          myChannel: core.state.voiceHub?.myChannel,
          hallPage: core.state.hallPage,
          channels: core.state.voiceHub?.channels?.map((channel) => channel.id),
          chat: document.querySelectorAll('.voicehall-chat .rc-text').length,
        })).catch((e) => ({ evalError: e.message }));
        console.error(`[dump ${name}]`, JSON.stringify(dump));
      }
      console.error('process state:', { livekit: livekit.exitCode, chat: chat.exitCode, web: web.exitCode });
      console.error('chat logs:', processErrors.filter((line) => line.startsWith('chat:')).join('').slice(-3000));
      throw error;
    }

    assert.equal(processErrors.some((line) => /voice kick failed|voice room delete failed/.test(line)),
      false, 'LiveKit 管理 API should revoke old rooms without errors');
    for (const name of NAMES) assert.deepEqual(errors[name], [], `${name} page errors`);
    console.log('PASS: voice hub e2e (audio, channel isolation, chat, rename, socket reconnect, reload resume)');
  } finally {
    await browser?.close().catch(() => {});
    for (const proc of procs.reverse()) {
      if (proc.exitCode === null) proc.kill('SIGTERM');
    }
    await sleep(300);
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
