// 地下城 Beta 背景音乐的纯逻辑回归（不需要浏览器）。
// 覆盖：曲目表合法性、乐句可循环性、场景映射与回落、设置规范化、仓库清单与本地文件一致性。
// Run: node tests/test_dungeon_beta_bgm.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DUNGEON_DIR = path.join(__dirname, '../assets/js/dungeon');
const BGM_DIR = path.join(__dirname, '../assets/dungeon/beta/bgm');
const NOTE_KINDS = ["bass", "pad", "lead", "kick", "hat"];
const SCENE_ROLES = ["explore", "exploreAlt", "boss", "shop"];

async function loadModule(file) {
  const source = fs.readFileSync(path.join(DUNGEON_DIR, file), 'utf8');
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

function assertTrackShape(track) {
  assert.ok(track.id && track.name && track.desc, `曲目 ${track.id} 缺少展示字段`);
  assert.ok(track.tempo > 0, `${track.id} 缺少速度`);
  assert.ok(track.root > 0, `${track.id} 缺少基频`);
  assert.ok(track.progression.length > 0, `${track.id} 缺少和弦进行`);
  assert.ok(track.scale.length > 0, `${track.id} 缺少音阶`);
  assert.ok(track.gain > 0 && track.gain <= 1, `${track.id} 的 gain 应在 (0,1]`);
  for (const kind of ["bass", "pad", "lead"]) {
    const voice = track[kind];
    assert.ok(voice?.pattern?.length, `${track.id} 缺少 ${kind} 声部`);
    assert.ok(voice.gain > 0 && voice.gain < 1, `${track.id} 的 ${kind} 音量应在 (0,1)`);
    assert.ok(voice.duration > 0, `${track.id} 的 ${kind} 缺少时长`);
    for (const step of voice.pattern) {
      assert.ok(Number.isInteger(step) && step >= 0 && step < 16, `${track.id} 的 ${kind} 步号越界：${step}`);
    }
  }
  for (const chord of track.progression) {
    assert.ok(chord.length >= 2, `${track.id} 的和弦至少两个音`);
    for (const interval of chord) assert.ok(Number.isFinite(interval));
  }
}

async function main() {
  const bgm = await loadModule('dgn-beta-bgm.js');
  const lib = await loadModule('dgn-beta-bgm-library.js');

  // ---- 曲目表 ----
  const playable = bgm.BGM_TRACKS.filter((track) => !track.silent);
  assert.ok(playable.length >= 4, '至少要有 4 首可播放的合成曲');
  assert.ok(bgm.BGM_TRACKS.some((track) => track.id === 'off' && track.silent), '必须提供“关闭”选项');
  for (const track of playable) assertTrackShape(track);
  assert.equal(bgm.notesForStep(bgm.trackById('off'), 0).length, 0, '关闭项不应产生音符');
  assert.ok(bgm.trackById('brute-raid').perc.kick, '首领曲应有鼓点');

  // 每首合成曲都得被某个场景引用，否则玩家永远听不到。
  const referenced = new Set(Object.values(bgm.SCENE_TRACKS));
  for (const track of playable) {
    assert.ok(referenced.has(track.id), `${track.name} 没有被任何场景引用`);
  }

  // ---- 乐句：纯函数、可循环、音高合法 ----
  for (const track of playable) {
    const loop = bgm.loopSteps(track);
    assert.equal(loop, bgm.STEPS_PER_BAR * track.progression.length);
    let total = 0;
    for (let step = 0; step < loop; step += 1) {
      const notes = bgm.notesForStep(track, step);
      assert.deepEqual(notes, bgm.notesForStep(track, step + loop), `${track.id} 第 ${step} 步应能无缝循环`);
      assert.deepEqual(notes, bgm.notesForStep(track, step + loop * 3), `${track.id} 多圈后仍应一致`);
      for (const note of notes) {
        assert.ok(NOTE_KINDS.includes(note.kind), `${track.id} 出现未知音色 ${note.kind}`);
        assert.ok(Number.isFinite(note.freq) && note.freq > 0, `${track.id} 出现非法频率 ${note.freq}`);
        if (note.kind === 'kick') assert.ok(note.toFreq > 0 && note.toFreq < note.freq, '鼓点必须向下扫频');
      }
      total += notes.length;
    }
    assert.ok(total > 0, `${track.name} 一个循环里没有任何音符`);
  }

  // 和弦音程越大，实际频率应越高（音高映射正确）。
  const track = bgm.trackById('ruins-hall');
  const pads = bgm.notesForStep(track, 0).filter((note) => note.kind === 'pad');
  assert.equal(pads.length, track.progression[0].length, '铺底应弹满整个和弦');
  for (let i = 1; i < pads.length; i += 1) {
    assert.ok(pads[i].freq > pads[i - 1].freq, '和弦音高必须随音程递增');
  }

  // ---- 场景映射 ----
  assert.equal(bgm.sceneTrackForScene('boss'), 'brute-raid');
  assert.equal(bgm.sceneTrackForScene('shop'), 'campfire');
  assert.equal(bgm.sceneTrackForScene('menu'), 'ruins-hall');
  assert.equal(bgm.sceneTrackForScene('explore', 1), 'ruins-hall');
  assert.equal(bgm.sceneTrackForScene('explore', 2), 'deep-vault', '探索曲应按波次轮换');
  assert.equal(bgm.sceneTrackForScene('explore', 3), 'ruins-hall');
  assert.equal(bgm.sceneTrackForScene('未知场景'), 'ruins-hall', '未知场景回落到默认曲');

  const library = [{ id: 'lib-boss', scene: 'boss' }, { id: 'lib-explore', scene: 'explore' }, { scene: 'shop' }];
  assert.equal(bgm.sceneTrackForScene('boss', 3, { source: 'library', library }), 'lib-boss');
  assert.equal(bgm.sceneTrackForScene('explore', 1, { source: 'library', library }), 'lib-explore');
  assert.equal(bgm.sceneTrackForScene('shop', 1, { source: 'library', library }), 'campfire',
    '曲库缺少该场景时应回落到合成曲，不能变成没声音');
  assert.equal(bgm.sceneTrackForScene('boss', 3, { source: 'library', library: [] }), 'brute-raid');
  assert.equal(bgm.sceneTrackForScene('boss', 3, { source: 'library' }), 'brute-raid');
  assert.equal(bgm.sceneTrackForScene('boss', 3, { source: 'synth', library }), 'brute-raid',
    '合成曲源不受曲库影响');

  // ---- 设置规范化 ----
  assert.deepEqual(bgm.normalizeBgmSettings(null),
    { trackId: 'ruins-hall', volume: 55, auto: true, sceneSource: 'synth' });
  assert.deepEqual(bgm.normalizeBgmSettings('nonsense'),
    { trackId: 'ruins-hall', volume: 55, auto: true, sceneSource: 'synth' });
  assert.deepEqual(bgm.normalizeBgmSettings({ trackId: 'campfire', volume: 999, auto: false, sceneSource: 'library' }),
    { trackId: 'campfire', volume: 100, auto: false, sceneSource: 'library' });
  assert.equal(bgm.normalizeBgmSettings({ volume: -5 }).volume, 0);
  assert.equal(bgm.normalizeBgmSettings({ volume: 42.6 }).volume, 43);
  assert.equal(bgm.normalizeBgmSettings({ volume: 'abc' }).volume, 55, '坏音量要回落到默认值');
  assert.equal(bgm.normalizeBgmSettings({ trackId: '' }).trackId, 'ruins-hall');
  assert.equal(bgm.normalizeBgmSettings({ trackId: 'x'.repeat(200) }).trackId, 'ruins-hall', '超长 id 不可信');
  assert.equal(bgm.normalizeBgmSettings({ sceneSource: 'bogus' }).sceneSource, 'synth');
  assert.equal(bgm.normalizeBgmSettings({ auto: 'no' }).auto, true, '只有显式 false 才关闭自动切换');
  assert.equal(bgm.BGM_STORAGE_KEY, 'dungeonBetaBgm');

  // ---- 清单规范化 ----
  assert.equal(lib.normalizeManifestEntry(null), null);
  assert.equal(lib.normalizeManifestEntry({ id: 'local:x', file: 'a.mp3' }), null, 'local: 前缀是保留的');
  assert.equal(lib.normalizeManifestEntry({ id: 'a', file: '' }), null);
  assert.equal(lib.normalizeManifestEntry({ id: 'a', file: 'b.mp3', gain: 99 }).gain, 2, 'gain 必须被夹紧');
  assert.equal(lib.normalizeManifestEntry({ id: 'a', file: 'b.mp3', scene: 'nope' }).scene, '', '未知场景角色应被丢弃');
  assert.equal(lib.isLocalTrackId('local:abc'), true);
  assert.equal(lib.isLocalTrackId('ossuary-1-a-beginning'), false);

  // ---- 仓库里的真实清单 ----
  const manifest = JSON.parse(fs.readFileSync(path.join(BGM_DIR, 'library.json'), 'utf8'));
  const entries = manifest.tracks.map(lib.normalizeManifestEntry);
  assert.equal(entries.filter(Boolean).length, manifest.tracks.length, 'library.json 每条都必须能被规范化');
  assert.equal(new Set(entries.map((entry) => entry.id)).size, entries.length, '曲目 id 不能重复');
  for (const entry of entries) {
    assert.ok(SCENE_ROLES.includes(entry.scene), `${entry.id} 的 scene 非法：${entry.scene}`);
    assert.match(entry.file, /^assets\/dungeon\/beta\/bgm\/[A-Za-z0-9._-]+$/, `${entry.id} 的文件名应无空格与特殊字符`);
    assert.ok(entry.author && entry.license && entry.licenseUrl && entry.source,
      `${entry.id} 缺少署名信息（CC BY 要求界面保留作者与许可）`);
  }
  for (const role of SCENE_ROLES) {
    assert.ok(entries.some((entry) => entry.scene === role), `免费曲库缺少 ${role} 角色的曲目`);
  }
  // 目录里出现过的音频必须登记在清单里，否则玩家放进去了却选不到。
  const present = fs.readdirSync(BGM_DIR).filter((name) => /\.(mp3|ogg|oga|wav|m4a|flac|opus|webm)$/i.test(name));
  for (const name of present) {
    assert.ok(entries.some((entry) => entry.file === `assets/dungeon/beta/bgm/${name}`),
      `目录里的 ${name} 没有登记进 library.json`);
  }

  console.log('Beta BGM rules OK');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
