// 地下城 Beta 音效浏览器回归：真实 WebAudio 输出、事件接线、静音持久化与无 WebAudio 降级。
// 需要先启动预览服务：python scripts/preview_game.py --no-open --no-replace --port 8020
// Run: node tests/test_dungeon_beta_audio_ui.cjs
const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');

const URL = process.env.BETA_URL || 'http://127.0.0.1:8020/dungeon-beta.html';

/** 记录真实 WebAudio 声源，用来证明“确实发声”而不是只调用了函数。 */
function instrumentAudio() {
  window.audioStarts = [];
  window.audioContexts = [];
  window.masterAudioGains = [];
  const Native = window.AudioContext || window.webkitAudioContext;
  window.AudioContext = class extends Native {
    constructor(...args) { super(...args); window.audioContexts.push(this); }
    createGain() {
      const node = super.createGain();
      const connect = node.connect.bind(node);
      node.connect = (...args) => {
        if (args[0] === this.destination) window.masterAudioGains.push(node);
        return connect(...args);
      };
      return node;
    }
    track(node) {
      const start = node.start.bind(node);
      node.start = (...args) => { window.audioStarts.push('source'); return start(...args); };
      return node;
    }
    createOscillator() { return this.track(super.createOscillator()); }
    createBufferSource() { return this.track(super.createBufferSource()); }
  };
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(instrumentAudio);
    await page.goto(URL);
    const starts = () => page.evaluate(() => window.audioStarts.length);

    // 1. 没有用户手势之前不得出声（浏览器自动播放策略）。
    await page.waitForTimeout(400);
    assert.equal(await starts(), 0, '首次用户手势前不应创建任何音频声源');

    // 2. 标题屏按钮打开的是全站共享的音效设置，并沿用已保存的音量。
    const modal = page.locator('#gameAudioSettingsModal');
    const enabled = page.getByRole('checkbox', { name: '音效', exact: true });
    const volume = page.locator('#gameAudioVolume');
    await page.locator('#dungeonAudioSettingsButton').click();
    assert.equal(await modal.isVisible(), true, '音效按钮必须能打开共享设置弹窗');
    assert.equal(await enabled.isChecked(), true);
    assert.equal(await volume.inputValue(), '60');
    await page.waitForFunction(() => window.audioContexts[0]?.state === 'running');

    // 3. 试听播放地下城音效，且全页只有一个 AudioContext 与一个总音量节点。
    const beforePreview = await starts();
    await page.getByRole('button', { name: '试听音效', exact: true }).click();
    await page.waitForTimeout(250);
    assert.ok(await starts() > beforePreview, '试听必须产生真实 WebAudio 声源');
    assert.equal(await page.evaluate(() => window.audioContexts.length), 1, '全页只应复用一个 AudioContext');
    assert.equal(await page.evaluate(() => window.masterAudioGains.length), 1, '只应有一个总音量节点');
    await page.keyboard.press('Escape');
    assert.equal(await modal.isVisible(), false);

    // 4. 目录里的每个音效都必须真的发声：名字与共享引擎写歪会在这里暴露。
    const cueProbe = await page.evaluate(async () => {
      const module = await import('/assets/js/dungeon/dgn-beta-audio.js');
      await module.initDungeonAudio();
      const missing = [];
      for (const cue of module.DUNGEON_CUES) {
        const before = window.audioStarts.length;
        module.sfx(cue);
        await new Promise((resolve) => setTimeout(resolve, 180));
        if (window.audioStarts.length === before) missing.push(cue);
      }
      return { missing, loaded: module.dungeonAudioLoaded() };
    });
    assert.equal(cueProbe.loaded, true, '共享音频引擎必须能被原型页加载');
    assert.deepEqual(cueProbe.missing, [], '每个登记的音效都必须产生声源');

    // 5. 事件接线：用真实类驱动，确认游戏事件确实被翻译成了音效。
    const wiring = await page.evaluate(async () => {
      const [{ Arena }, { Shop }, { createState }] = await Promise.all([
        import('/assets/js/dungeon/dgn-beta-arena.js'),
        import('/assets/js/dungeon/dgn-beta-shop.js'),
        import('/assets/js/dungeon/dgn-beta-state.js'),
      ]);
      const canvas = document.createElement('canvas');
      canvas.width = 960;
      canvas.height = 600;
      const arenaCues = [];
      const arena = new Arena(canvas, { onSound: (cue) => arenaCues.push(cue) });
      arena.start(createState(), 1);
      arena.stop();
      const started = arenaCues.slice();
      arenaCues.length = 0;
      const victim = arena.spawnEnemy('slime', 1, 1);
      victim.x = arena.player.x + 20;
      victim.y = arena.player.y;
      arena.hitEnemy(victim, 999, 0);
      const killed = arenaCues.slice();
      arenaCues.length = 0;
      arena.pickups.push({ x: arena.player.x, y: arena.player.y, magnet: false });
      arena.updatePickups(0.016);
      const picked = arenaCues.slice();
      arenaCues.length = 0;
      const attacker = arena.spawnEnemy('slime', 1, 1);
      attacker.x = arena.player.x + 2;
      attacker.y = arena.player.y;
      attacker.attackCd = 0;
      arena.enemies = [attacker];
      arena.updateEnemies(0.016);
      const hurt = arenaCues.slice();
      arenaCues.length = 0;
      attacker.hp = 999;
      attacker.x = arena.player.x + 30;
      arena.enemies = [attacker];
      arena.player.attackTimers = {};
      arena.updateWeapons(0.5);
      const swing = arenaCues.slice();

      const shopCues = [];
      const run = createState();
      run.materials = 500;
      document.getElementById('dgn-screen-shop').classList.remove('dgn-hidden');
      const shop = new Shop({ onSound: (cue) => shopCues.push(cue), onError: () => {} });
      shop.open(run);
      shop.selectGear('weapon', 0);
      const select = shopCues.slice();
      shopCues.length = 0;
      shop.reroll();
      const reroll = shopCues.slice();
      shopCues.length = 0;
      shop.useCurrency('transmutation');
      const crafted = shopCues.slice();
      shopCues.length = 0;
      run.shopOffers = [{ kind: 'currency', id: 'transmutation' }];
      shop.render();
      shop.buy(0);
      const buy = shopCues.slice();
      shopCues.length = 0;
      shop.useCurrency('exalted');
      const error = shopCues.slice();
      return { started, killed, picked, hurt, swing, select, reroll, crafted, buy, error };
    });
    assert.deepEqual(wiring.started, ['waveStart'], '开始波次必须发出开波音');
    assert.ok(wiring.swing.includes('swing'), '挥砍必须发声');
    assert.ok(wiring.killed.includes('hit'), '命中必须发声');
    assert.ok(wiring.killed.includes('kill'), '击杀必须发声');
    assert.ok(wiring.picked.includes('pickup'), '拾取材料必须发声');
    assert.ok(wiring.hurt.includes('hurt'), '玩家受击必须发声');
    assert.ok(wiring.select.includes('select'), '选择装备必须发声');
    assert.ok(wiring.reroll.includes('reroll'), '重抽必须发声');
    assert.ok(wiring.crafted.includes('craft'), '使用通货必须发声');
    assert.ok(wiring.buy.includes('buy'), '购买必须发声');
    assert.ok(wiring.error.includes('error'), '失败操作必须发声');

    // 6. 真机路径：点“进入遗迹”后战斗循环要持续发声且没有脚本错误。
    const beforeWave = await starts();
    await page.locator('#dgn-btn-start').click();
    await page.waitForFunction((before) => window.audioStarts.length > before, beforeWave, { timeout: 5000 });
    const afterStart = await starts();
    await page.waitForFunction((before) => window.audioStarts.length > before + 2, afterStart, { timeout: 25000 });
    assert.equal(await page.locator('#dgn-screen-game').isVisible(), true, '战斗屏必须正常显示');

    // 7. 静音立即生效、写在按钮上、写入与游戏厅共用的设置，并在刷新后保持。
    const audioButton = page.locator('#dungeonAudioSettingsButton');
    await audioButton.click();
    await enabled.uncheck();
    assert.equal((await audioButton.innerText()).trim(), '🔇', '静音后按钮必须显示已关闭');
    const afterMute = await starts();
    const mutedStarts = await page.evaluate(async () => {
      const module = await import('/assets/js/dungeon/dgn-beta-audio.js');
      module.sfx('levelup');
      module.sfx('victory');
      module.sfx('craft');
      return window.audioStarts.length;
    });
    assert.equal(mutedStarts, afterMute, '静音后不应再产生任何声源');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('gameAudioSettings'))),
      { enabled: false, volume: 60 }, '静音必须写入全站共享设置');
    await page.keyboard.press('Escape');
    await page.reload();
    await page.locator('#dungeonAudioSettingsButton').click();
    assert.equal(await enabled.isChecked(), false, '刷新后仍然保持静音');
    assert.equal(await volume.inputValue(), '60');
    assert.equal((await audioButton.innerText()).trim(), '🔇', '刷新后按钮仍显示已关闭');

    // 8. 取消静音后音量直接作用在共享总音量节点上（30% -> 0.216）。
    await enabled.check();
    await volume.evaluate((node) => {
      node.value = '30';
      node.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForFunction(() => Math.abs((window.masterAudioGains[0]?.gain.value ?? 0) - 0.216) < 0.01);
    assert.equal((await audioButton.innerText()).trim(), '🔊', '取消静音后按钮必须恢复');
    assert.match(await audioButton.getAttribute('title'), /30%/, '按钮标题应显示当前音量');
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('gameAudioSettings'))),
      { enabled: true, volume: 30 });
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, [], '整个流程不应出现脚本错误');

    // 9. 没有 WebAudio 时静默降级：不报错，玩法照常。
    const unsupported = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const unsupportedErrors = [];
    unsupported.on('pageerror', (error) => unsupportedErrors.push(error.message));
    await unsupported.addInitScript(() => {
      window.AudioContext = undefined;
      window.webkitAudioContext = undefined;
    });
    await unsupported.goto(URL);
    await unsupported.locator('#dungeonAudioSettingsButton').click();
    assert.equal(await unsupported.locator('#gameAudioSettingsModal').isVisible(), true);
    await unsupported.keyboard.press('Escape');
    await unsupported.locator('#dgn-btn-start').click();
    await unsupported.waitForTimeout(1500);
    assert.deepEqual(unsupportedErrors, [], '缺少 WebAudio 时必须静默降级');
    assert.equal(await unsupported.locator('#dgn-screen-game').isVisible(), true, '没有音频也要能正常开打');
    await unsupported.close();

    console.log('Beta dungeon audio UI OK');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
