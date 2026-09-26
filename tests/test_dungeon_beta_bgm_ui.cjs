// 地下城 Beta 背景音乐浏览器回归：真实 WebAudio 发声、音乐音量链路、免费曲库署名、
// 本地曲库（IndexedDB）增删与持久化、场景自动切换、与全站静音联动。
// 需要先启动预览服务：python scripts/preview_game.py --no-open --no-replace --port 8020
// Run: node tests/test_dungeon_beta_bgm_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const URL = process.env.BETA_URL || 'http://127.0.0.1:8020/dungeon-beta.html';
const BGM_DIR = path.join(__dirname, '../assets/dungeon/beta/bgm');
const FREE_TRACK_ID = 'mystery-bazaar';

function instrumentAudio() {
  window.audioStarts = [];
  window.audioContexts = [];
  const Native = window.AudioContext || window.webkitAudioContext;
  window.AudioContext = class extends Native {
    constructor(...args) { super(...args); window.audioContexts.push(this); }
    track(node) {
      const start = node.start.bind(node);
      node.start = (...args) => { window.audioStarts.push('source'); return start(...args); };
      return node;
    }
    createOscillator() { return this.track(super.createOscillator()); }
    createBufferSource() { return this.track(super.createBufferSource()); }
  };
}

/** 最小可解码的 16bit PCM 单声道 WAV，用来验证“从本地添加曲目”。 */
function makeWav(seconds = 1.5, frequency = 330, sampleRate = 44100) {
  const frames = Math.floor(seconds * sampleRate);
  const dataSize = frames * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(dataSize, 40);
  for (let i = 0; i < frames; i += 1) {
    buffer.writeInt16LE(Math.round(Math.sin((2 * Math.PI * frequency * i) / sampleRate) * 9000), 44 + i * 2);
  }
  return buffer;
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(instrumentAudio);
    await page.goto(URL);

    const starts = () => page.evaluate(() => window.audioStarts.length);
    const bgmState = () => page.evaluate(async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState());
    const call = (name, ...args) => page.evaluate(async ([fn, params]) => {
      const module = await import('/assets/js/dungeon/dgn-beta-bgm.js');
      return module[fn](...params);
    }, [name, args]);
    const waitPlaying = () => page.waitForFunction(
      async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().playing, null, { timeout: 8000 });
    const waitGain = (expected) => page.waitForFunction(async (target) => {
      const state = (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState();
      return Math.abs(state.gain - target) < 0.02;
    }, expected, { timeout: 5000 });

    // 1. 面板与曲库分组；免费曲目的署名必须出现在界面上（CC BY 要求）。
    await page.locator('#dungeonBgmSettingsButton').click();
    assert.equal(await page.locator('#dgnBgmPanel').isVisible(), true, '🎵 按钮必须能打开背景音乐面板');
    const panelText = await page.locator('#dgnBgmTracks').innerText();
    for (const expected of ['内置合成曲', '免费曲库', '我的本地曲目', '遗迹回廊', '巨物来袭', '关闭背景音乐']) {
      assert.ok(panelText.includes(expected), `面板应列出「${expected}」`);
    }
    assert.ok(panelText.includes('Kevin MacLeod') && panelText.includes('CC BY 4.0'),
      '免费曲库必须显示作者与许可');
    assert.ok(await page.locator('#dgnBgmTracks .dgn-bgm-track-meta a').count() >= 2,
      '每条免费曲目都应有许可与来源链接');
    await page.locator('#dgnBgmCloseButton').click();
    assert.equal(await page.locator('#dgnBgmPanel').isVisible(), false);

    // 2. 真正发声 + 音乐音量作用在音乐链路自己的增益上。
    await page.locator('#dungeonBgmSettingsButton').click();
    await page.waitForFunction(() => window.audioContexts[0]?.state === 'running');
    const beforePlay = await starts();
    await call('selectTrack', 'campfire', { auto: false });
    await page.waitForFunction((before) => window.audioStarts.length > before, beforePlay, { timeout: 6000 });
    await waitPlaying();
    assert.equal((await bgmState()).trackId, 'campfire');
    // campfire 的 gain 是 0.8，总输出余量 0.5：满音量应为 0.4，20% 应为 0.08。
    await page.locator('#dgnBgmVolume').evaluate((node) => {
      node.value = '100';
      node.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await waitGain(0.4);
    await page.locator('#dgnBgmVolume').evaluate((node) => {
      node.value = '20';
      node.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await waitGain(0.08);
    assert.equal((await bgmState()).volume, 20);

    // 3. 免费曲库曲目：文件缺失时跳过（音频文件按设计不入库）。
    if (fs.existsSync(path.join(BGM_DIR, `${FREE_TRACK_ID}.mp3`))) {
      await call('setBgmVolume', 80);
      await call('selectTrack', FREE_TRACK_ID, { auto: false });
      await waitPlaying();
      assert.equal((await bgmState()).trackId, FREE_TRACK_ID, '免费曲库曲目应能播放');
    } else {
      console.log(`跳过免费曲库播放：缺少 ${FREE_TRACK_ID}.mp3（见 assets/dungeon/beta/bgm/README.md）`);
    }

    // 4. 本地曲库：添加、选中、刷新后仍在、可删除。
    await page.locator('#dgnBgmFileInput').setInputFiles([
      { name: 'my-dungeon-tune.wav', mimeType: 'audio/wav', buffer: makeWav() },
      { name: 'not-audio.txt', mimeType: 'text/plain', buffer: Buffer.from('nope') },
    ]);
    await page.waitForFunction(() => document.getElementById('dgnBgmTracks').innerText.includes('my-dungeon-tune'));
    assert.match(await page.locator('#dgnBgmStatus').innerText(), /已添加 1 首/, '非音频文件必须被拒绝');
    const localPick = page.locator('#dgnBgmTracks .dgn-bgm-pick[data-track-id^="local:"]').first();
    const localId = await localPick.getAttribute('data-track-id');
    assert.ok(localId?.startsWith('local:'), '本地曲目应有 local: 前缀的 id');
    await localPick.click();
    await page.waitForFunction(async () => {
      const state = (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState();
      return state.trackId.startsWith('local:') && state.playing;
    }, null, { timeout: 8000 });

    await page.reload();
    await page.locator('#dungeonBgmSettingsButton').click();
    await page.waitForFunction(() => document.getElementById('dgnBgmTracks').innerText.includes('my-dungeon-tune'));
    assert.ok((await bgmState()).trackId.startsWith('local:'), '刷新后应记住本地曲目选择');
    await page.locator('#dgnBgmTracks .dgn-bgm-remove').first().click();
    await page.waitForFunction(() => !document.getElementById('dgnBgmTracks').innerText.includes('my-dungeon-tune'));
    await page.waitForFunction(async () => !(await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().trackId.startsWith('local:'),
      null, { timeout: 5000 });

    // 4b. 登记了但文件不在本地时，必须给出提示而不是静默无声（音频文件按设计不入库）。
    await call('selectTrack', 'local:not-installed');
    await page.waitForFunction(async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().missing !== null,
      null, { timeout: 5000 });
    assert.match(await page.locator('#dgnBgmStatus').innerText(), /曲目文件缺失/,
      '缺失的曲目必须给出可见提示');
    assert.equal((await bgmState()).playing, false, '缺失的曲目不应处于播放状态');

    // 5. 直接选“关闭”：按钮变暗，且不再产生任何声源。
    //    这一步刻意放在开打之前——此时竞技场没在跑，不会有战斗音效干扰计数。
    await call('selectTrack', 'off');
    await page.waitForFunction(async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().trackId === 'off');
    assert.equal(await page.locator('#dungeonBgmSettingsButton').evaluate((node) => node.classList.contains('dgn-audio-off')),
      true, '关闭背景音乐后按钮应变暗');
    const offStarts = await starts();
    await page.waitForTimeout(600);
    assert.equal(await starts(), offStarts, '关闭背景音乐后不应继续产生声源');

    // 6. 全站静音要能立刻掐掉背景音乐（同样在开打之前验证）。
    await call('selectTrack', 'brute-raid', { auto: false });
    await waitPlaying();
    await page.locator('#dgnBgmCloseButton').click();
    await page.locator('#dungeonAudioSettingsButton').click();
    const soundEnabled = page.getByRole('checkbox', { name: '音效', exact: true });
    await soundEnabled.uncheck();
    await page.waitForFunction(async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().playing === false,
      null, { timeout: 5000 });
    const mutedStarts = await starts();
    await page.waitForTimeout(600);
    assert.equal(await starts(), mutedStarts, '静音后背景音乐不应继续发声');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('gameAudioSettings'))),
      { enabled: false, volume: 60 });
    await soundEnabled.check();
    await page.keyboard.press('Escape');

    // 7. 跟随场景自动切换：点“进入遗迹”应从菜单曲切到探索曲。
    await call('setBgmSceneSource', 'synth', 1);
    await call('setBgmAuto', true, 1);
    await page.locator('#dgn-btn-start').click();
    await page.waitForFunction(async () => (await import('/assets/js/dungeon/dgn-beta-bgm.js')).bgmState().trackId === 'ruins-hall');
    const state = await bgmState();
    assert.equal(state.scene, 'explore', '开波应进入探索场景');
    assert.equal(state.auto, true);
    await waitPlaying();

    assert.deepEqual(errors, [], '整个流程不应出现脚本错误');
    console.log('Beta BGM UI OK');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
