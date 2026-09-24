// Run: node tests/test_werewolf_voice.cjs
// 狼人杀语音端到端：本地 livekit-server --dev + chat_server + 静态服务，
// 四浏览器（假麦克风）真实开局：夜晚狼人独占 wolf 频道、天亮全员进
// day 频道并互听对方假麦音轨。角色由服务端随机分发，断言以视图为准。
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
        // 测试环境的假麦克风：WebAudio 振荡器顶替硬件采集，
        // 上麦（LiveKit setMicrophoneEnabled 的 getUserMedia）拿到的是合成音轨
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        osc.frequency.value = 440;
        const dst = ctx.createMediaStreamDestination();
        osc.connect(dst);
        osc.start();
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

    // 4 人局：1 狼 + 预言家 + 女巫 + 平民；角色由服务端随机分发
    await players.va.page.evaluate(() => core.send({
      type: 'create_room', game: 'werewolf', name: '语音局', buy_in: 100, blind: 5,
      rules: { board: ['werewolf', 'seer', 'witch', 'villager'] },
    }));
    await players.va.page.waitForFunction(() => core.state.myRoom?.room_id);
    const roomId = await players.va.page.evaluate(() => core.state.myRoom.room_id);
    for (const name of ['vb', 'vc', 'vd']) {
      await players[name].page.evaluate((id) => core.send({ type: 'join_room', room_id: id }), roomId);
      await players[name].page.waitForFunction((id) => core.state.myRoom?.room_id === id, roomId);
    }

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
    for (const name of ['vb', 'vc', 'vd']) {
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

    // 白天：全员换到 day 频道
    for (const name of Object.keys(players)) {
      await players[name].page.waitForFunction(() => voice.voiceDebug().connected
        && voice.voiceDebug().room.endsWith('-day'), null, { timeout: 30000 });
    }
    console.log('PASS day: all four connected to the day channel');

    // 狼人与预言家上麦（假麦克风）：发布本机麦克风并订阅到对方音轨
    for (const name of [wolf, seer]) {
      const wanted = await players[name].page.evaluate(async () => {
        const result = await voice.toggleMic();
        return { wanted: result, debug: voice.voiceDebug() };
      });
      console.log(`[mic ${name}]`, JSON.stringify(wanted));
    }
    try {
      for (const name of [wolf, seer]) {
        await players[name].page.waitForFunction(() => voice.voiceDebug().micPublished === true
          && voice.voiceDebug().remoteAudio >= 1
          && [...document.querySelectorAll('audio[data-voice-peer]')]
            .some((element) => !element.paused && element.readyState >= 2),
        null, { timeout: 40000 });
      }
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
            secure: window.isSecureContext,
            hasMediaDevices: Boolean(navigator.mediaDevices),
            gum,
          };
        }).catch((e) => ({ evalError: e.message }));
        console.error(`[dump ${name}]`, JSON.stringify(dump));
      }
      throw error;
    }
    console.log(`PASS audio: ${wolf} and ${seer} published and played attached remote tracks`);
    assert.equal(processErrors.some((line) => /voice kick failed|voice room delete failed/.test(line)),
      false, 'LiveKit 管理 API should revoke old rooms without errors');

    for (const name of Object.keys(players)) assert.deepEqual(errors[name], [], `${name} page errors`);
    console.log('PASS: werewolf voice e2e (night wolf-only channel, day shared channel, mutual audio)');
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
