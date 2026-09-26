// 地下城 Beta 音效适配层的纯逻辑回归（不需要浏览器）。
// Run: node tests/test_dungeon_beta_audio.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DUNGEON_DIR = path.join(__dirname, '../assets/js/dungeon');

async function loadAdapter() {
  const source = fs.readFileSync(path.join(DUNGEON_DIR, 'dgn-beta-audio.js'), 'utf8');
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

/** 抓出所有作为 sound()/sfx() 参数出现的字符串字面量（含三元表达式里的两个分支）。 */
function cuesUsedIn(file) {
  const source = fs.readFileSync(path.join(DUNGEON_DIR, file), 'utf8');
  const found = new Set();
  for (const call of source.matchAll(/\b(?:sound|sfx)\(([^;\n]*)\)/g)) {
    for (const literal of call[1].matchAll(/"([a-zA-Z]+)"/g)) found.add(literal[1]);
  }
  return found;
}

/** 共享引擎 action cue 表里的音效名。 */
function engineActionCues() {
  const source = fs.readFileSync(path.join(__dirname, '../assets/js/game-audio.js'), 'utf8');
  const start = source.indexOf('function synthesizeActionCue');
  const end = source.indexOf('export function playActionSound');
  assert.ok(start > 0 && end > start, '共享引擎必须保留 synthesizeActionCue 动作音效表');
  const block = source.slice(start, end);
  return new Set([...block.matchAll(/case "([a-zA-Z]+)":/g)].map((match) => match[1]));
}

async function main() {
  const { createDungeonAudio, COALESCED_CUES, DUNGEON_CUES } = await loadAdapter();

  // 目录名与共享引擎动作音效表必须一致：任一端口写错名字都会被这里拦下。
  const engineCues = engineActionCues();
  for (const cue of DUNGEON_CUES) {
    assert.ok(engineCues.has(cue), `共享引擎缺少音效 ${cue}`);
  }
  for (const cue of engineCues) {
    assert.ok(DUNGEON_CUES.includes(cue), `DUNGEON_CUES 未列出共享引擎的 ${cue}`);
  }
  for (const cue of COALESCED_CUES) {
    assert.ok(DUNGEON_CUES.includes(cue), `合并表里的 ${cue} 不在音效目录中`);
  }
  assert.equal(Object.isFrozen(DUNGEON_CUES), true);
  assert.deepEqual([...cuesUsedIn('dgn-beta-arena.js')].filter((cue) => !DUNGEON_CUES.includes(cue)), [],
    '竞技场引用了未登记的音效');
  assert.deepEqual([...cuesUsedIn('dgn-beta-shop.js')].filter((cue) => !DUNGEON_CUES.includes(cue)), [],
    '商店引用了未登记的音效');
  assert.deepEqual([...cuesUsedIn('dgn-beta-main.js')].filter((cue) => !DUNGEON_CUES.includes(cue)), [],
    '入口引用了未登记的音效');
  // 关键事件必须真的被接线，避免重构时静默丢失。
  for (const cue of ['waveStart', 'swing', 'shoot', 'hit', 'kill', 'hurt', 'pickup', 'levelup']) {
    assert.ok(cuesUsedIn('dgn-beta-arena.js').has(cue), `竞技场必须触发 ${cue}`);
  }
  for (const cue of ['buy', 'reroll', 'craft', 'error', 'select']) {
    assert.ok(cuesUsedIn('dgn-beta-shop.js').has(cue), `商店必须触发 ${cue}`);
  }

  // 转发与返回值。
  const played = [];
  let clock = 0;
  const audio = createDungeonAudio((cue) => { played.push(cue); return true; }, { clock: () => clock });
  assert.equal(audio.sfx('levelup'), true);
  assert.deepEqual(played, ['levelup']);

  // 合并：同一窗口内重复的高频事件只响一次，窗口之后恢复。
  played.length = 0;
  audio.reset();
  clock = 1000;
  assert.equal(audio.sfx('hit'), true);
  clock = 1020;
  assert.equal(audio.sfx('hit'), false);
  assert.equal(audio.sfx('hit'), false);
  clock = 1060;
  assert.equal(audio.sfx('hit'), true);
  assert.deepEqual(played, ['hit', 'hit'], '一帧内多次命中只应发出一次声音');

  // 非高频音效从不被合并。
  played.length = 0;
  audio.reset();
  clock = 2000;
  for (let i = 0; i < 5; i += 1) {
    clock += 1;
    assert.equal(audio.sfx('victory'), true);
  }
  assert.equal(played.length, 5);

  // 不同音效互不干扰。
  played.length = 0;
  audio.reset();
  clock = 3000;
  assert.equal(audio.sfx('swing'), true);
  assert.equal(audio.sfx('hit'), true);
  assert.equal(audio.sfx('kill'), true);
  assert.equal(audio.sfx('pickup'), true);
  assert.deepEqual(played, ['swing', 'hit', 'kill', 'pickup']);

  // 发声层没就绪时返回 false，但仍然节流，避免就绪后瞬间补响一串。
  played.length = 0;
  let ready = false;
  const pending = createDungeonAudio((cue) => { if (!ready) return false; played.push(cue); return true; },
    { clock: () => clock });
  clock = 4000;
  assert.equal(pending.sfx('pickup'), false);
  clock = 4010;
  assert.equal(pending.sfx('pickup'), false);
  ready = true;
  clock = 4060;
  assert.equal(pending.sfx('pickup'), true);
  assert.deepEqual(played, ['pickup']);

  // reset 之后的第一次一定会播放。
  audio.reset();
  clock = 5000;
  assert.equal(audio.sfx('hit'), true);
  audio.reset();
  assert.equal(audio.sfx('hit'), true);

  // 自定义窗口生效。
  const slow = createDungeonAudio(() => true, { clock: () => clock, windowMs: 0 });
  clock = 6000;
  assert.equal(slow.sfx('hit'), true);
  assert.equal(slow.sfx('hit'), true, '窗口为 0 时不应合并');

  console.log('Beta audio adapter OK');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
