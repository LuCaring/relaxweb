// No server required: Playwright serves this checkout through an intercepted test origin.
// Run: npm run test:browser -- tests/test_estate_wardrobe.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const ROOT = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(ROOT, '.venv', 'bin', 'python');
const fixture = JSON.parse(execFileSync(python, ['-B', '-c', `
import json, sqlite3, time
from estate import init_estate, estate_state
conn = sqlite3.connect(':memory:')
conn.execute('CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL NOT NULL)')
conn.execute("INSERT INTO users VALUES ('alice',10000)")
init_estate(conn)
print(json.dumps(estate_state(conn, 'alice', int(time.time()))))
`], { cwd: ROOT, encoding: 'utf8' }));

(async () => {
  const skinIds = Object.keys(fixture.catalog.skins);
  const stevePrice = fixture.catalog.skins.steve.price;
  fixture.skins.owned = skinIds;
  const { characterFrame } = await import(pathToFileURL(path.join(ROOT, 'assets/js/estate/characters.js')));
  const manifest = JSON.parse(await fs.readFile(path.join(ROOT, 'assets/estate/characters/berry/character.json')));
  assert.equal(characterFrame(manifest, { direction: 'down' }, 0).sx, 0);
  assert.equal(characterFrame(manifest, { direction: 'down' }, 500).sx, 148);
  assert.equal(characterFrame(manifest, { direction: 'up' }, 0).sy, 196);
  assert.equal(characterFrame(manifest, { direction: 'left' }, 125, 'walk').sy, 980);
  assert.equal(characterFrame(manifest, { direction: 'left' }, 125, 'walk').flipX, true);
  assert.deepEqual([0,125,250,375].map(t => characterFrame(manifest, { direction: 'down' }, t, 'walk').sx), [0,148,296,444]);
  assert.equal(characterFrame(manifest, { direction: 'up' }, 0, 'run').sy, 784);
  assert.equal(characterFrame(manifest, { direction: 'down' }, 0, 'harvest').sy, 0);
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox'] });
  try {
    const errors = [];
    async function makePage() {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://estate.test/**', async route => {
        const file = path.resolve(ROOT, '.' + new URL(route.request().url()).pathname);
        if (!file.startsWith(ROOT + path.sep)) return route.fulfill({ status: 403 });
        try {
          const body = await fs.readFile(file);
          const contentType = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.json': 'application/json', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
          await route.fulfill({ body, contentType });
        } catch { await route.fulfill({ status: 404 }); }
      });
      await page.addInitScript(() => {
        window.sent = []; window.spriteDraws = [];
        window.WebSocket = class { static OPEN = 1; constructor() { this.readyState = 1; } send(data) { sent.push(JSON.parse(data)); } addEventListener() {} close() {} };
        const original = CanvasRenderingContext2D.prototype.drawImage;
        CanvasRenderingContext2D.prototype.drawImage = function(image, ...args) {
          if (image.src?.includes('/characters/') && this.canvas.classList.contains('estate-canvas')) {
            spriteDraws.push({ src: image.src, args, smoothing: this.imageSmoothingEnabled });
            if (spriteDraws.length > 100) spriteDraws.shift();
          }
          return original.call(this, image, ...args);
        };
      });
      return page;
    }
    async function enter(page, snapshot) {
      await page.goto('http://estate.test/game.html');
      await page.evaluate(async snapshot => {
        window.core = await import('/assets/js/core.js');
        core.setSignedIn({ username: 'alice', nickname: '庄园主', coins: 10000 });
        core.state.hallPage = 'estate'; core.renderGameView();
        core.handleServerMessage({ type: 'estate_state', ...snapshot });
      }, snapshot);
      await page.waitForFunction(() => spriteDraws.length > 0);
    }
    async function respond(page, id, result = {}) {
      await page.evaluate(({ snapshot, id, result }) => core.handleServerMessage({ type: 'estate_state', ...snapshot,
        profile: { ...snapshot.profile, skin_id: id }, ...result }), { snapshot: fixture, id, result });
    }
    // Fresh-account store purchase and wardrobe navigation.
    const shop = await makePage();
    const locked = { ...fixture, coins: stevePrice - 1, skins: { owned: ['berry'], missing_skins: ['steve'], missing_collectibles: ['antique_watch', 'lost_underwear'] } };
    await enter(shop, locked);
    await shop.getByRole('button', { name: '打开角色衣橱' }).click();
    await shop.locator('[data-skin-id="collection_reward"]').click();
    assert.equal(await shop.locator('.estate-equip').isDisabled(), true);
    assert.match(await shop.locator('.estate-wardrobe-status').innerText(), /旧怀表/);
    await shop.locator('[data-skin-id="steve"]').click();
    await shop.waitForFunction(() => !document.querySelector('.estate-equip').disabled);
    assert.equal(await shop.locator('.estate-equip').innerText(), '前往商店');
    await shop.locator('.estate-equip').click();
    assert.equal(await shop.locator('.estate-sheet-title').innerText(), '商店');
    assert.equal(await shop.locator('[data-shop-skin]').count(), skinIds.length);
    assert.equal(await shop.locator('[data-shop-skin="steve"] button').isDisabled(), true);
    assert.equal(await shop.evaluate(() => sent.filter(x => x.type === 'estate_buy_skin').length), 0);
    await shop.evaluate(snapshot => core.handleServerMessage({ type: 'estate_state', ...snapshot,
      coins: snapshot.catalog.skins.steve.price }), locked);
    await shop.locator('[data-shop-skin="steve"] button').click();
    const purchase = await shop.evaluate(() => sent.filter(x => x.type === 'estate_buy_skin').at(-1));
    assert.equal(purchase.skin_id, 'steve');
    await shop.evaluate(({ snapshot, purchase }) => core.handleServerMessage({ type: 'estate_state', ...snapshot,
      coins: 0, skins: { ...snapshot.skins, owned: ['berry', 'steve'] },
      request_id: purchase.request_id, result: { action: 'buy_skin', skin_id: 'steve', charged: snapshot.catalog.skins.steve.price } }), { snapshot: locked, purchase });
    assert.equal(await shop.locator('[data-shop-skin="steve"] button').innerText(), '已解锁');
    assert.match(await shop.evaluate(() => spriteDraws.at(-1).src), /\/berry\//);
    assert.equal(await shop.locator('[data-shop-skin="collection_reward"] button').isDisabled(), true);
    await shop.close();
    const page = await makePage();
    await enter(page, fixture);
    await page.getByRole('button', { name: '打开角色衣橱' }).click();
    await page.waitForFunction(() => document.querySelector('.estate-fitting-load').textContent === '');
    assert.equal(await page.locator('.estate-skin-card').count(), skinIds.length);
    assert.equal(await page.locator('.estate-equip').isDisabled(), true);
    for (const id of skinIds.filter(id => id !== 'berry')) {
      await page.locator(`[data-skin-id="${id}"]`).click();
      await page.waitForFunction(() => !document.querySelector('.estate-equip').disabled);
      assert.match(await page.locator('.estate-fitting-stage canvas').evaluate(c => c.toDataURL()), /^data:image\/png/);
    }
    await page.locator('[data-skin-id="steve"]').click();
    await page.waitForFunction(() => !document.querySelector('.estate-equip').disabled);
    assert.equal(await page.evaluate(() => sent.filter(x => x.type === 'estate_set_skin').length), 0, 'preview must not equip');
    await page.getByRole('button', { name: '走两步' }).click();
    await page.getByRole('button', { name: '向左预览' }).click();
    const firstPreview = await page.locator('.estate-fitting-stage canvas').evaluate(c => c.toDataURL());
    await page.waitForFunction(first => document.querySelector('.estate-fitting-stage canvas').toDataURL() !== first, firstPreview);
    const before = await page.evaluate(() => spriteDraws.at(-1).args[4]);
    await page.keyboard.down('d');
    await page.evaluate(() => new Promise(resolve => { let count = 0; function tick() { if (++count === 10) resolve(); else requestAnimationFrame(tick); } requestAnimationFrame(tick); }));
    await page.keyboard.up('d');
    assert.equal(await page.evaluate(() => spriteDraws.at(-1).args[4]), before, 'wardrobe must block movement');
    await page.locator('.estate-equip').click();
    const request = await page.evaluate(() => sent.filter(x => x.type === 'estate_set_skin').at(-1));
    assert.equal(request.skin_id, 'steve');
    assert.equal(await page.locator('.estate-equip').isDisabled(), true);
    assert.match(await page.evaluate(() => spriteDraws.at(-1).src), /\/berry\//, 'map waits for server confirmation');
    await respond(page, 'steve', { request_id: request.request_id, result: { action: 'set_skin', skin_id: 'steve', replayed: false } });
    await page.waitForFunction(() => document.querySelector('.estate-wardrobe-status').textContent.includes('已保存到账号'));
    await page.waitForFunction(() => spriteDraws.at(-1).src.includes('/steve/'));
    assert.equal(await page.locator('[data-skin-id="steve"] .estate-skin-badge').innerText(), '已穿戴');
    assert.equal(await page.evaluate(() => spriteDraws.at(-1).smoothing), true);
    // Replayed action result must never override the authoritative profile.
    await respond(page, 'xiaofei', { result: { action: 'set_skin', skin_id: 'steve', replayed: true } });
    await page.waitForFunction(() => spriteDraws.at(-1).src.includes('/xiaofei/'));
    assert.equal(await page.locator('[data-skin-id="xiaofei"] .estate-skin-badge').innerText(), '已穿戴');
    // Focus trap in both directions and Escape restores the wardrobe trigger.
    await page.getByRole('button', { name: '关闭衣橱' }).focus();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('.estate-equip').evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.getByRole('button', { name: '关闭衣橱' }).evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.estate-wardrobe').isHidden(), true);
    assert.equal(await page.getByRole('button', { name: '打开角色衣橱' }).evaluate(el => el === document.activeElement), true);
    const standingX = await page.evaluate(() => spriteDraws.at(-1).args[4]);
    await page.keyboard.down('d');
    await page.waitForFunction(x => spriteDraws.at(-1).args[4] > x + 5, standingX);
    await page.keyboard.up('d');
    // Full page reload receives the same account profile, not a localStorage skin override.
    const persisted = { ...fixture, profile: { ...fixture.profile, skin_id: 'xiaofei' } };
    await enter(page, persisted);
    await page.waitForFunction(() => spriteDraws.at(-1).src.includes('/xiaofei/'));
    for (const [width, height] of [[1280,800], [768,1024], [390,844], [320,568], [844,390]]) {
      await page.setViewportSize({ width, height });
      await page.getByRole('button', { name: '打开角色衣橱' }).click();
      await page.waitForFunction(() => document.querySelector('.estate-fitting-load').textContent === '');
      const geometry = await page.evaluate(() => {
        const dialog = document.querySelector('.estate-wardrobe section').getBoundingClientRect();
        const body = document.querySelector('.estate-wardrobe-body');
        const footer = document.querySelector('.estate-wardrobe-footer').getBoundingClientRect();
        return { fits: dialog.left >= 0 && dialog.right <= innerWidth && dialog.top >= 0 && dialog.bottom <= innerHeight,
          horizontalOverflow: body.scrollWidth > body.clientWidth + 1, footerVisible: footer.bottom <= innerHeight };
      });
      assert.deepEqual(geometry, { fits: true, horizontalOverflow: false, footerVisible: true }, `viewport ${width}x${height}`);
      if (process.env.ESTATE_SCREENSHOT_DIR && [1280,390].includes(width)) {
        await fs.mkdir(process.env.ESTATE_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.ESTATE_SCREENSHOT_DIR, `wardrobe-${width}.png`) });
      }
      await page.keyboard.press('Escape');
    }
    // Deployment-defined reward metadata and assets reach the shop, wardrobe and map.
    const custom = await makePage();
    const customSnapshot = structuredClone(fixture);
    Object.assign(customSnapshot.catalog.skins.collection_reward, {
      name: '星空守望者', description: '本站独有的珍藏奖励', asset_id: 'dva',
      required_skins: ['steve'], required_collectibles: ['antique_watch'],
    });
    customSnapshot.profile.skin_id = 'collection_reward';
    await enter(custom, customSnapshot);
    await custom.waitForFunction(() => spriteDraws.at(-1).src.includes('/dva/'));
    await custom.getByRole('button', { name: '打开角色衣橱' }).click();
    assert.equal(await custom.locator('#estate-wardrobe-title').innerText(), '角色衣橱');
    assert.equal(await custom.locator('[data-skin-id="collection_reward"] b').innerText(), '星空守望者');
    assert.equal(await custom.locator('[data-skin-id="collection_reward"] small').innerText(), '本站独有的珍藏奖励');
    assert.match(await custom.locator('[data-skin-id="collection_reward"] img').getAttribute('src'), /\/dva\/portrait.png$/);
    assert.equal(await custom.locator('.estate-selected-name').innerText(), '星空守望者');
    await custom.keyboard.press('Escape');
    delete customSnapshot.catalog.skins.collection_reward;
    customSnapshot.profile.skin_id = 'berry';
    await enter(custom, customSnapshot);
    await custom.getByRole('button', { name: '打开角色衣橱' }).click();
    assert.equal(await custom.locator('[data-skin-id="collection_reward"]').count(), 0);
    const safePath = await custom.evaluate(async () => {
      const characters = await import('/assets/js/estate/characters.js');
      characters.configureCharacters({invalid: {asset_id: '../outside'}});
      return characters.characterAsset('invalid');
    });
    assert.equal(safePath, 'assets/estate/characters/berry/character.png');
    await custom.close();
    // A missing image can be retried without changing the account or allowing a broken equip.
    const failure = await makePage();
    await failure.route('**/characters/xiaofei/character.png', route => route.fulfill({ status: 503 }));
    await enter(failure, fixture);
    await failure.getByRole('button', { name: '打开角色衣橱' }).click();
    await failure.locator('[data-skin-id="xiaofei"]').click();
    await failure.getByRole('button', { name: '重试', exact: true }).waitFor();
    assert.equal(await failure.locator('.estate-equip').isDisabled(), true);
    await failure.unroute('**/characters/xiaofei/character.png');
    await failure.getByRole('button', { name: '重试', exact: true }).click();
    await failure.waitForFunction(() => !document.querySelector('.estate-equip').disabled);
    // Reduced motion freezes preview, while keeping direction and action controls available.
    await failure.emulateMedia({ reducedMotion: 'reduce' });
    await failure.getByRole('button', { name: '走两步' }).click();
    await failure.evaluate(() => new Promise(requestAnimationFrame));
    const still = await failure.locator('.estate-fitting-stage canvas').evaluate(c => c.toDataURL());
    await failure.evaluate(() => new Promise(resolve => { let frames = 0; const tick = () => ++frames > 12 ? resolve() : requestAnimationFrame(tick); requestAnimationFrame(tick); }));
    assert.equal(await failure.locator('.estate-fitting-stage canvas').evaluate(c => c.toDataURL()), still);
    // Unmounting / reentering must clean up listeners and must not leave the loading veil behind.
    await failure.keyboard.press('Escape');
    await failure.locator('.estate-back').click();
    await failure.evaluate(() => { core.state.hallPage = 'estate'; core.renderGameView(); });
    assert.equal(await failure.locator('.estate-loading').isHidden(), true);
    await failure.getByRole('button', { name: '打开角色衣橱' }).click();
    assert.equal(await failure.locator('.estate-wardrobe:not([hidden])').count(), 1);
    await failure.locator('[data-skin-id="xiaofei"]').click();
    await failure.waitForFunction(() => !document.querySelector('.estate-equip').disabled);
    await failure.locator('.estate-equip').click();
    await failure.evaluate(snapshot => {
      const request = sent.filter(x => x.type === 'estate_set_skin').at(-1);
      core.handleServerMessage({ type: 'estate_error', request_id: request.request_id, code: 'invalid_skin', message: '皮肤不存在', state: snapshot });
    }, fixture);
    await failure.locator('#liveDialog').waitFor();
    await failure.keyboard.press('Escape');
    assert.equal(await failure.locator('#liveDialog').count(), 0, 'error dialog owns Escape');
    assert.equal(await failure.locator('.estate-wardrobe').isVisible(), true);
    assert.equal(await failure.locator('.estate-wardrobe-status').innerText(), '皮肤不存在');
    assert.equal(await failure.locator('.estate-equip').isEnabled(), true);
    await failure.clock.install();
    await failure.locator('.estate-equip').click();
    await failure.clock.fastForward(15010);
    assert.match(await failure.locator('.estate-wardrobe-status').innerText(), /保存超时/);
    assert.equal(await failure.evaluate(async () => [...(await import('/assets/js/estate/state.js')).estateStore.pending.values()].filter(record => record.type === 'estate_set_skin').length), 0);
    await failure.evaluate(() => core.setSignedIn(null));
    assert.equal(await failure.locator('.estate-wardrobe').count(), 0);
    assert.equal(await failure.evaluate(async () => (await import('/assets/js/estate/state.js')).estateStore.snapshot), null);
    assert.deepEqual(errors, []);
    console.log('PASS: configurable reward metadata/assets/visibility, animation grid, mirror/fallback, preview/equip, server-authoritative replay, focus/input, reload, 5 layouts, asset retry, reduced motion, error/timeout, logout and teardown');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
