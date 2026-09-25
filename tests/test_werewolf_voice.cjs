// Run: node tests/test_werewolf_voice.cjs
// 狼人杀语音端到端：本地 livekit-server --dev + chat_server + 静态服务，
// 四浏览器（假麦克风）建房后先在 lobby 频道互听，再真实开局：
// 夜晚狼人独占 wolf 频道、天亮全员进 day 频道并互听假麦音轨。
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ww-voice-'));
  const dbFile = path.join(tmp, 'voice-e2e.db');
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

    // 造 4 个账号并签发会话 token（浏览器经 localStorage 恢复会话）
    const bootstrap = `
import sys, json
sys.path.insert(0, ${JSON.stringify(ROOT)})
from server.schema import init_db
import server.database as storage
from server.accounts import Accounts
init_db(storage.database)
accounts = Accounts(storage.database)
names = ["va", "vb", "vc", "vd"]
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
    for (const name of ['va', 'vb', 'vc', 'vd']) {
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
        // 测试环境的假麦克风：WebAudio 振荡器顶替硬件采集（带增益节点，
        // 测试可随时把源调成真静音来驱动噪声门的关门路径），
        // 上麦（getUserMedia）拿到的是这条合成音轨
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
          throw new Error('voice e2e patched getUserMedia: audio only');
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

    // 两人先通过全局入口加入公共频道并自动开麦，再进入狼人杀房间。
    for (const name of ['va', 'vb']) {
      const page = players[name].page;
      await page.locator('#voiceQuickToggle').click();
      await page.locator('#voiceQuickJoin').click();
      await page.waitForFunction(() => core.state.voiceHub?.myChannel === 'default'
        && voice.voiceDebug().micPublished === true, null, { timeout: 20000, polling: 100 });
    }
    // 4 人局：1 狼 + 预言家 + 女巫 + 平民；角色由服务端随机分发
    await players.va.page.evaluate(() => core.send({
      type: 'create_room', game: 'werewolf', name: '语音局', buy_in: 100, blind: 5,
      rules: { board: ['werewolf', 'seer', 'witch', 'villager'] },
    }));
    await players.va.page.waitForFunction(() => core.state.myRoom?.room_id);
    const roomId = await players.va.page.evaluate(() => core.state.myRoom.room_id);
    await players.va.page.waitForFunction(() => voice.voiceDebug().room?.endsWith('-lobby'));
    assert.equal(await players.va.page.locator('.waiting-voice').isVisible(), true);
    await players.va.page.waitForFunction(() => document.querySelector('.waiting-voice-mic')?.disabled === false);
    for (const name of ['vb', 'vc', 'vd']) {
      await players[name].page.evaluate((id) => core.send({ type: 'join_room', room_id: id }), roomId);
      await players[name].page.waitForFunction((id) => core.state.myRoom?.room_id === id, roomId);
    }
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => voice.voiceDebug().room?.endsWith('-lobby'));
    }
    // 公共频道 → 游戏等待区自动切换且保留开麦，双方收到彼此音轨。
    for (const name of ['va', 'vb']) {
      await players[name].page.waitForFunction(() => voice.voiceDebug().micPublished);
    }
    for (const name of ['va', 'vb']) {
      await players[name].page.waitForFunction(() => voice.voiceDebug().remoteAudio >= 1
        && [...document.querySelectorAll('audio[data-voice-peer]')]
          .some((element) => !element.paused && element.readyState >= 2));
      assert.match(await players[name].page.locator('.waiting-voice-status').innerText(), /麦克风已开启/);
    }
    console.log('PASS lobby: public voice auto-switches into game voice with microphones published');

    // 按人音量齿轮菜单：除自己外每个成员条目一个齿轮；打开浮层调整后
    // 写入缓存并作用于已挂载音轨
    assert.equal(await players.va.page.locator('.waiting-seat .seat-volume-gear:visible').count(), 3,
      'va should see one volume gear per other player');
    assert.equal(await players.va.page.locator('.waiting-seat.is-me .seat-volume-gear').isVisible(), false,
      'own seat must not show a volume gear');
    await players.va.page.locator('.waiting-seat[data-username="vb"] .seat-volume-gear').click();
    assert.equal(await players.va.page.locator('.waiting-seat[data-username="vb"] .peer-volume-popover').isVisible(), true,
      'gear click opens the volume popover');
    await players.va.page.locator('.waiting-seat[data-username="vb"] .peer-volume-range').fill('40');
    assert.equal(await players.va.page.evaluate(() => {
      const saved = JSON.parse(localStorage.getItem('voicePeerVolumes') || '[]');
      const pair = saved.find(([name]) => name === 'vb');
      return pair ? pair[1] : null;
    }), 40, 'popover slider input persists the per-peer volume');
    await players.va.page.waitForFunction(() => {
      const element = document.querySelector('audio[data-voice-peer="vb"]');
      return element && Math.abs(element.volume - 0.4) < 0.01;
    }, null, { timeout: 5000, polling: 100 });
    // 缓存继承：刷新页面后重新打开浮层，滑杆恢复为保存的值
    await players.va.page.reload();
    await players.va.page.waitForFunction(
      () => Boolean(document.querySelector('.waiting-seat[data-username="vb"] .seat-volume-gear')),
      null, { timeout: 15000, polling: 100 });
    await players.va.page.locator('.waiting-seat[data-username="vb"] .seat-volume-gear').click();
    assert.equal(await players.va.page.locator('.waiting-seat[data-username="vb"] .peer-volume-range').inputValue(), '40',
      'volume popover restores persisted value after reload');
    // 恢复测试用的模块绑定（刷新后 window.voice/window.core 丢失）
    await players.va.page.evaluate(async () => {
      const main = [...document.scripts].find((script) => script.type === 'module'
        && script.src.includes('/assets/js/main.js'));
      await import(main.src);
      window.core = await import('/assets/js/core.js');
      window.voice = await import('/assets/js/room-voice.js');
      window.voiceMic = await import('/assets/js/voice-mic.js');
    });
    await players.va.page.waitForFunction(
      () => core.state.currentUser?.username && voice.voiceDebug().room?.endsWith('-lobby'),
      null, { timeout: 15000, polling: 100 });
    console.log('PASS mixer: per-peer volume gears render, persist and survive reload');

    // 本地电平表与阈值门控：刷新后重新上麦（假麦为 440Hz 振荡器，电平稳非零）
    for (const name of ['va', 'vb']) {
      await players[name].page.evaluate(async () => {
        if (!voice.voiceMicWanted()) await voice.toggleMic();
      });
      await players[name].page.waitForFunction(() => voice.voiceDebug().micPublished,
        null, { timeout: 10000, polling: 100 });
      const state = await players[name].page.evaluate(() => voice.voiceDebug());
      assert.equal(state.micProcessing, true, `${name} publishes through the local pipeline`);
      await players[name].page.waitForFunction(() => voice.voiceDebug().micLevel > 0.3,
        null, { timeout: 8000, polling: 100 });
      assert.equal(await players[name].page.locator('.waiting-voice-meter').isVisible(), true);
      const width = await players[name].page.locator('.voice-meter-fill')
        .evaluate((node) => parseFloat(node.style.width) || 0);
      assert.ok(width >= 60, `${name} meter shows level, got ${width}%`);
    }
    console.log('PASS meter: pipeline publishes and the level meter reads the synthetic mic');

    // 噪声门：高于阈值继续放行；源静默后门关闭但仍发布（发送静音不摘轨）。
    // 阈值滑块嵌在电平条上，与电平同刻度，拖动即持久化。
    await players.va.page.evaluate(() => voiceMic.setMicGateSettings({ gateEnabled: true, gateThreshold: 8 }));
    await players.va.page.waitForFunction(
      () => document.querySelector('.voice-meter-threshold')?.value === '8',
      null, { timeout: 8000, polling: 100 });
    assert.equal(await players.va.page.locator('.voice-meter-threshold').isDisabled(), false);
    await players.va.page.locator('.voice-meter-threshold').fill('20');
    assert.equal((await players.va.page.evaluate(() => voiceMic.micGateSettings())).gateThreshold, 20,
      'embedded slider persists the threshold');
    assert.match(await players.va.page.locator('.voice-gate-value').innerText(), /阈值 20%/);
    await players.va.page.waitForFunction(() => voice.voiceDebug().micGateOpen === true,
      null, { timeout: 8000, polling: 100 });
    assert.match(await players.va.page.evaluate(() => localStorage.getItem('voiceMicSettings')),
      /"gateEnabled":true/, 'gate setting persists');
    await players.va.page.evaluate(() => { window.__voiceSynthGain.gain.value = 0; });
    await players.va.page.waitForFunction(() => voice.voiceDebug().micLevel < 0.001,
      null, { timeout: 8000, polling: 100 });
    await players.va.page.waitForFunction(() => voice.voiceDebug().micGateOpen === false,
      null, { timeout: 8000, polling: 100 });
    assert.equal((await players.va.page.evaluate(() => voice.voiceDebug())).micPublished, true,
      'gated mic stays published sending silence');
    assert.ok((await players.vb.page.evaluate(() => voice.voiceDebug())).remoteAudio >= 1,
      'receiver still holds the gated silent track');
    await players.va.page.evaluate(() => { window.__voiceSynthGain.gain.value = 1; });
    await players.va.page.waitForFunction(() => voice.voiceDebug().micGateOpen === true,
      null, { timeout: 8000, polling: 100 });
    await players.va.page.evaluate(() => voiceMic.setMicGateSettings({ gateEnabled: false }));
    console.log('PASS gate: opens above threshold, closes on silence while staying published');

    // 开局 → 第一夜
    await players.va.page.evaluate(() => core.send({ type: 'start_game' }));
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => core.state.myRoom?.phase === 'night');
    }

    // 以各自私有视图确定角色（服务端投影，前端不推导）
    const roleOf = {};
    for (const name of Object.keys(players)) {
      roleOf[name] = await players[name].page.evaluate(() => core.state.myRoom.your_role.key);
    }
    const byRole = (key) => Object.keys(players).find((n) => roleOf[n] === key);
    const wolf = byRole('werewolf');
    const witch = byRole('witch');
    const seer = byRole('seer');
    assert.ok(wolf && witch && seer, `roles distributed: ${JSON.stringify(roleOf)}`);
    console.log('roles:', JSON.stringify(roleOf));

    // 夜晚：狼人独占 wolf 频道，其余人拿不到 token
    await players[wolf].page.waitForFunction(
      () => voice.voiceDebug().connected && voice.voiceDebug().room.endsWith('-wolf'));
    await sleep(600);
    for (const name of Object.keys(players)) {
      if (name === wolf) continue;
      const state = await players[name].page.evaluate(() => voice.voiceDebug());
      assert.equal(state.connected, false, `${name}(${roleOf[name]}) must hold no voice token at night`);
    }
    console.log(`PASS night: ${wolf}(werewolf) connected to wolf channel, others hold no token`);

    // 依次提交夜晚行动：狼空刀 → 女巫不救 → 预言家不验 → 天亮
    await players[wolf].page.evaluate(() => core.send({ type: 'poker_action', action: 'night', target: '' }));
    await players[witch].page.waitForFunction(() => core.state.myRoom?.night_role === '女巫');
    await players[witch].page.evaluate(() => core.send({ type: 'poker_action', action: 'witch', save: false }));
    await players[seer].page.waitForFunction(() => core.state.myRoom?.night_role === '预言家');
    await players[seer].page.evaluate(() => core.send({ type: 'poker_action', action: 'night', target: '' }));
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => core.state.myRoom?.phase === 'day',
        null, { timeout: 30000 });
    }

    // 白天：全员换到 day 频道（依次发言中）
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => voice.voiceDebug().connected
        && voice.voiceDebug().room.endsWith('-day'), null, { timeout: 30000 });
    }
    const speaker = await players.va.page.evaluate(() => core.state.myRoom.speech_current);
    const speakerRole = roleOf[speaker];
    console.log(`PASS day: all four connected to the day channel, speaker ${speaker}(${speakerRole})`);

    await players[speaker].page.evaluate(() => core.send({ type: 'room_chat', text: '白天发言测试' }));
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => core.state.roomChat
        .some((message) => message.text === '白天发言测试'), null,
      { timeout: 10000, polling: 100 });
    }
    console.log('PASS chat: active speaker text reaches all four game players');

    // 依次发言：只有当前发言人可发布麦克风，其余人只听
    for (const name of Object.keys(players)) {
      if (name === speaker) continue;
      const state = await players[name].page.evaluate(() => voice.voiceDebug());
      assert.equal(state.connected, true, `${name} stays in the day channel`);
    }
    await players[speaker].page.evaluate(async () => {
      const result = voice.voiceMicWanted() || await voice.toggleMic();
      return result;
    });
    const listener = Object.keys(players).find((n) => n !== speaker);
    try {
      await players[speaker].page.waitForFunction(() => voice.voiceDebug().micPublished === true,
        null, { timeout: 40000 });
      await players[listener].page.waitForFunction(() => voice.voiceDebug().remoteAudio >= 1
        && [...document.querySelectorAll('audio[data-voice-peer]')]
          .some((element) => !element.paused && element.readyState >= 2),
      null, { timeout: 40000 });
      const listenerState = await players[listener].page.evaluate(() => voice.voiceDebug());
      assert.equal(listenerState.micPublished, false,
        `listener ${listener} must not publish during another player's speech turn`);
    } catch (error) {
      for (const name of Object.keys(players)) {
        const dump = await players[name].page.evaluate(async () => {
          let gum = null;
          try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            gum = { ok: true, label: stream.getAudioTracks()[0]?.label };
            stream.getTracks().forEach((t) => t.stop());
          } catch (e) {
            gum = { ok: false, name: e.name, message: e.message };
          }
          return {
            voice: voice.voiceDebug(),
            wanted: voice.voiceMicWanted(),
            phase: core.state.myRoom?.phase,
            speaker: core.state.myRoom?.speech_current,
            secure: window.isSecureContext,
            hasMediaDevices: Boolean(navigator.mediaDevices),
            gum,
          };
        }).catch((e) => ({ evalError: e.message }));
        console.error(`[dump ${name}]`, JSON.stringify(dump));
      }
      throw error;
    }
    console.log(`PASS audio: ${speaker}(${speakerRole}) published, ${listener} listened without publishing`);
    assert.equal(processErrors.some((line) => /voice kick failed|voice room delete failed/.test(line)),
      false, 'LiveKit 管理 API should revoke old rooms without errors');

    for (const name of Object.keys(players)) assert.deepEqual(errors[name], [], `${name} page errors`);
    console.log('PASS: werewolf voice e2e (night wolf-only channel, day sequential speech audio)');
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
