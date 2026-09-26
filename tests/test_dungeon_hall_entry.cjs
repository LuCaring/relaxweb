// 游戏厅的地下城入口：开关过滤、登录后真实渲染、点击跳转，以及未开启时不得出现。
// 需要 app 在运行：python chat_server.py + python deploy/serve.py
// Run: node tests/test_dungeon_hall_entry.cjs
const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');

const URL = process.env.APP_URL || 'http://127.0.0.1:8000/game.html';
const OTHER_ENTRIES = ['estate', 'holdem', 'uno', 'guandan', 'mahjong', 'ludo', 'liarsbar'];

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(() => {
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send() {}
        addEventListener() {}
        close() {}
      };
    });
    await page.goto(URL);

    // 1. 过滤是纯逻辑：关闭时不出现、打开时出现，且不受开关影响的入口照旧。
    const config = await page.evaluate(async () => {
      const module = await import('/assets/js/game-config.js');
      const ids = (flags) => module.hallGameTypes(flags).map((game) => game.id);
      const entry = module.GAME_TYPES.find((game) => game.id === 'dungeon');
      return {
        off: ids({}),
        on: ids({ dungeonBeta: true }),
        rooms: module.ROOM_GAME_TYPES.map((game) => game.id),
        entry,
        flag: window.LIVE_CONFIG?.dungeon?.beta_enabled === true,
      };
    });
    assert.equal(config.off.includes('dungeon'), false, '开关关闭时不得出现地下城入口');
    assert.equal(config.on.includes('dungeon'), true, '开关打开时应出现地下城入口');
    assert.equal(config.rooms.includes('dungeon'), false, '地下城不是牌桌游戏，不能进建房列表');
    assert.equal(config.entry.mode, 'link', '地下城是跳转入口，不占牌桌');
    assert.equal(config.entry.href, '/dungeon-beta.html');
    assert.equal(config.entry.action, '进入遗迹 →');
    for (const id of OTHER_ENTRIES) {
      assert.ok(config.on.includes(id) && config.off.includes(id), `${id} 不应受地下城开关影响`);
    }

    // 2. 登录后大厅的卡片必须与当前站点配置一致（未登录时大厅只显示登录提示）。
    await page.evaluate(async () => {
      const core = await import('/assets/js/core.js');
      window.core = core;
      core.setSignedIn({ username: 'hall-entry', nickname: '入口验证', coins: 1000 });
      core.state.myRoom = null;
      core.state.hallPage = null;
      core.renderGameView();
    });
    const cards = await page.evaluate(() => [...document.querySelectorAll('.hall-game-card')].map((card) => ({
      name: card.querySelector('.hall-game-name')?.textContent || '',
      action: card.querySelector('.hall-game-go')?.textContent || '',
    })));
    const dungeonCard = cards.find((card) => card.name.includes('遗迹深探'));
    assert.equal(Boolean(dungeonCard), config.flag, '卡片是否出现必须与 LIVE_CONFIG 的开关一致');
    assert.equal(cards.length, OTHER_ENTRIES.length + (config.flag ? 1 : 0), '入口数量应为 7 或 8');

    if (!dungeonCard) {
      console.log('当前站点未开启 dungeon.beta_enabled，跳过入口跳转验证');
    } else {
      assert.equal(dungeonCard.action, '进入遗迹 →');
      // 3. 点入口必须真的到达地下城原型页。
      await Promise.all([
        page.waitForURL(/dungeon-beta\.html/, { timeout: 15000 }),
        page.locator('.hall-game-card', { hasText: '遗迹深探' }).click(),
      ]);
      assert.equal(await page.locator('#dgn-btn-start').count(), 1, '应跳到地下城原型页');
      assert.equal(await page.locator('#dungeonBgmSettingsButton').count(), 1, '原型页的 BGM 入口应存在');
    }

    assert.deepEqual(errors, [], '整个流程不应出现脚本错误');
    console.log('Beta dungeon hall entry OK');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
