// 生产语音冒烟测试:两个假麦客户端直连 LiveKit,验证 WSS 信令 + ICE/UDP 媒体
// (独立于游戏层;不创建游戏房间,不碰 users.db)
// Run: LK_SECRET=<api_secret> node scripts/probe_voice.mjs
//   LK_URL    信令地址,默认 ws://127.0.0.1:7880;生产为 wss://<IP或域名>/lk
//   LK_KEY    API key,默认 devkey
//   LK_SECRET API secret,默认 livekit-server --dev 的默认密钥 devkey:secret
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { chromium } from 'playwright';

const LK_URL = process.env.LK_URL || 'ws://127.0.0.1:7880';
const KEY = process.env.LK_KEY || 'devkey';
const SECRET = process.env.LK_SECRET || 'secret';
const ROOM = `smoke-${Date.now()}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

setTimeout(() => { console.error('GLOBAL TIMEOUT after 70s'); process.exit(2); }, 70000).unref();

const mint = (id) => execFileSync('.venv/bin/python', ['-c', `
from livekit import api
t = api.AccessToken("${KEY}", "${SECRET}")
t = t.with_identity("${id}").with_grants(api.VideoGrants(room_join=True, room="${ROOM}"))
print(t.to_jwt())
`], { encoding: 'utf8' }).trim();

// 浏览器源站:本地空白页(与目标解耦;loopback 源访问公网 WSS 不受私网访问限制)
const blank = createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<html><body>voice smoke</body></html>'); });
await new Promise((r) => blank.listen(0, '127.0.0.1', r));
const ORIGIN = `http://127.0.0.1:${blank.address().port}/`;

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });

async function peer(id, token) {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.__pcs = [];
    const Orig = window.RTCPeerConnection;
    window.RTCPeerConnection = function (...args) {
      const pc = new Orig(...args);
      window.__pcs.push(pc);
      return pc;
    };
    window.RTCPeerConnection.prototype = Orig.prototype;
  });
  await page.goto(ORIGIN);
  await page.addScriptTag({ path: '/Users/lhy/proj/relaxweb/assets/vendor/livekit-client.umd.min.js' });
  return page.evaluate(async ({ url, token, id }) => {
    const room = new LivekitClient.Room();
    window.__room = room;
    // 连接前挂订阅监听,避免 connect 与监听之间的竞态
    window.__subscribed = new Promise((resolve) => {
      room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, pub, participant) => {
        if (track.kind === 'audio') resolve(participant.identity);
      });
    });
    // 合成音轨:振荡器 440Hz,模拟麦克风
    const ctx = new AudioContext();
    await ctx.resume();
    const osc = ctx.createOscillator();
    osc.frequency.value = 440;
    const dst = ctx.createMediaStreamDestination();
    osc.connect(dst);
    osc.start();
    const track = dst.stream.getAudioTracks()[0];
    window.__audioCtxState = ctx.state;

    await room.connect(url, token);
    if (id === 'p1') {
      const pub = await room.localParticipant.publishTrack(track, { source: LivekitClient.Track.Source.Microphone });
      window.__pubState = { kind: pub.kind, isUp: pub.isUp, muted: pub.isMuted };
    }
    return true;
  }, { url: LK_URL, token, id }).then(() => ({ page, id }));
}

let p1, p2;
try {
  [p1] = await Promise.all([peer('p1', mint('p1'))]);
  console.log(`PASS signaling: p1 connected via ${LK_URL} (audioCtx=${await p1.page.evaluate(() => window.__audioCtxState)})`);
  [p2] = await Promise.all([peer('p2', mint('p2'))]);
  console.log('PASS signaling: p2 connected');

  const from = await Promise.race([
    p2.page.evaluate(() => window.__subscribed),
    sleep(25000).then(() => null),
  ]);
  if (!from) {
    const dump = await p1.page.evaluate(() => ({
      pubState: window.__pubState,
      roomState: window.__room?.state,
    }));
    console.error('p1 publication dump:', JSON.stringify(dump));
    throw new Error('trackSubscribed timeout 25s');
  }
  console.log(`PASS subscribe: p2 subscribed audio from ${from}`);

  await sleep(4000); // 让媒体流一会儿
  const stats = await p2.page.evaluate(async () => {
    let bytes = 0, remoteCand = null, localCand = null;
    for (const pc of window.__pcs) {
      const report = await pc.getStats();
      let pair = null;
      report.forEach((s) => {
        if (s.type === 'inbound-rtp' && s.kind === 'audio') bytes += s.bytesReceived;
        if (s.type === 'candidate-pair' && s.state === 'succeeded' && s.nominated) pair = s;
      });
      if (pair) {
        report.forEach((s) => {
          if (s.id === pair.remoteCandidateId) remoteCand = `${s.protocol} ${s.ip}:${s.port} (${s.candidateType})`;
          if (s.id === pair.localCandidateId) localCand = `${s.protocol} ${s.ip}:${s.port} (${s.candidateType})`;
        });
      }
    }
    return { bytes, localCand, remoteCand };
  });
  console.log(`media path: local=${stats.localCand} <- remote(server)=${stats.remoteCand}`);
  console.log(`audio bytes received: ${stats.bytes}`);
  if (stats.bytes > 0) console.log('PASS media: real UDP audio flowing p1 -> p2');
  else { console.error('FAIL media: no audio bytes'); process.exitCode = 1; }
} catch (e) {
  console.error('FAIL:', e.message);
  process.exitCode = 1;
} finally {
  for (const p of [p1, p2]) {
    if (p) await p.page.evaluate(() => window.__room?.disconnect()).catch(() => {});
  }
  await browser.close().catch(() => {});
  blank.close();
  process.exit(process.exitCode || 0);
}
