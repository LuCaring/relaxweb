// Run: node tests/test_voice_mic.cjs
// 语音偏好与噪声门纯逻辑单测：无浏览器依赖，用 data: URL 直接导入
// assets/js/voice-mic.js（该模块把浏览器 API 全部约束在函数体内）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '../assets/js/voice-mic.js'), 'utf8');

(async () => {
  const mic = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`);
  const T = 0.08;

  // —— 迟滞门控状态机 ——
  // 高于阈值立即开门
  assert.deepEqual(mic.gateNext({ open: false, belowSince: null }, 0.2, T, 1000),
    { open: true, belowSince: null });
  // 中间区（0.6T–T）保持当前状态
  assert.equal(mic.gateNext({ open: true, belowSince: null }, T * 0.8, T, 1000).open, true,
    'middle zone holds an open gate');
  assert.equal(mic.gateNext({ open: false, belowSince: 900 }, T * 0.8, T, 1000).open, false,
    'middle zone holds a closed gate');
  // 低于关门线须持续 GATE_RELEASE_MS 才关门（不咬字尾）
  assert.equal(mic.gateNext({ open: true, belowSince: 900 }, 0, T, 900 + mic.GATE_RELEASE_MS - 1).open, true);
  assert.equal(mic.gateNext({ open: true, belowSince: 900 }, 0, T, 900 + mic.GATE_RELEASE_MS).open, false);
  // 关门后回到中间区仍关，重新高于阈值立即开
  assert.equal(mic.gateNext({ open: false, belowSince: 900 }, 0, T, 2000).open, false);
  assert.equal(mic.gateNext({ open: false, belowSince: 900 }, T, T, 2000).open, true);
  // 阈值 0 恒开
  assert.equal(mic.gateNext({ open: false, belowSince: null }, 0, 0, 0).open, true);

  // —— 噪声门设置：归一化与默认值 ——
  assert.deepEqual(mic.micGateSettings(), { gateEnabled: false, gateThreshold: 8 },
    'defaults without persisted settings');
  let settings = mic.setMicGateSettings({ gateEnabled: true, gateThreshold: 999 });
  assert.deepEqual(settings, { gateEnabled: true, gateThreshold: 50 }, 'threshold clamps to max');
  settings = mic.setMicGateSettings({ gateThreshold: -3 });
  assert.equal(settings.gateThreshold, 0, 'threshold clamps to min');
  settings = mic.setMicGateSettings({ gateEnabled: 'yes', gateThreshold: 12.6 });
  assert.deepEqual(settings, { gateEnabled: false, gateThreshold: 13 }, 'boolean + rounding rules');

  // —— 无管线时的安全取值 ——
  assert.equal(mic.micPipelineActive(), false);
  assert.equal(mic.micLevel(), 0);
  assert.equal(mic.micGateOpen(), true, 'gate passes through without a pipeline');
  assert.equal(mic.micGateTrack(), null);
  mic.stopMicPipeline(); // 幂等

  // —— 按人音量：默认值、归一化与 LRU 上限 ——
  assert.equal(mic.getPeerVolume('alice'), 100);
  assert.equal(mic.PEER_VOLUME_MAX, 150, 'volume cap allows amplification');
  assert.equal(mic.setPeerVolume('alice', 130), 130, 'boost above 100 persists');
  assert.equal(mic.setPeerVolume('alice', 999), 150, 'clamps above the 150 cap');
  assert.equal(mic.getPeerVolume('alice'), 150);
  assert.equal(mic.setPeerVolume('alice', -5), 0, 'clamps below 0');
  assert.equal(mic.getPeerVolume('alice'), 0);
  assert.equal(mic.setPeerVolume('alice', '42'), 42, 'accepts numeric strings');
  assert.equal(mic.getPeerVolume(''), 100, 'blank username keeps default');
  for (let i = 0; i < 205; i += 1) mic.setPeerVolume(`u${i}`, 10);
  assert.equal(mic.getPeerVolume('u0'), 100, 'oldest entry evicted beyond the cap');
  assert.equal(mic.getPeerVolume('u204'), 10, 'recent entry survives');

  console.log('PASS voice-mic unit: gate state machine, settings clamps, peer volume store');
})().catch((error) => { console.error(error); process.exit(1); });
