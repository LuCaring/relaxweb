const assert = require('node:assert/strict');
const { chromium } = require('playwright-core');

async function main() {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(process.env.BETA_URL || 'http://127.0.0.1:8765/dungeon-beta.html');
    await page.evaluate(async () => {
      const [{ Shop }, { createState }] = await Promise.all([
        import('/assets/js/dungeon/dgn-beta-shop.js'),
        import('/assets/js/dungeon/dgn-beta-state.js'),
      ]);
      const run = createState();
      run.materials = 100;
      document.getElementById('dgn-screen-shop').classList.remove('dgn-hidden');
      window.craftingShop = new Shop({ onError: (message) => { throw new Error(message); } });
      window.craftingShop.open(run);
      window.craftingRun = run;
    });
    assert.equal(await page.locator('#dgn-shop-cards .dgn-card').count(), 4);
    await page.locator('#dgn-owned-weapons .dgn-wslot').first().click();
    assert.match(await page.locator('#dgn-affix-odds').innerText(), /T3/);
    await page.locator('#dgn-affix-odds-detail summary').click();
    assert.match(await page.locator('#dgn-affix-odds-detail').innerText(), /权重 1000/);
    await page.getByRole('button', { name: /蜕变石/ }).click();
    assert.match(await page.locator('#dgn-gear-detail').innerText(), /魔法/);
    assert.match(await page.locator('#dgn-gear-detail').innerText(), /T3/);
    assert.equal(await page.evaluate(() => window.craftingRun.currencies.transmutation), 0);
    await page.evaluate(() => {
      window.craftingShop.state.shopOffers = [{ kind: 'currency', id: 'augmentation' }];
      window.craftingShop.render();
    });
    assert.doesNotMatch(await page.locator('#dgn-shop-cards .dgn-price').innerText(), /NaN/);
    await page.locator('#dgn-shop-cards button.dgn-btn-primary').click();
    assert.equal(await page.evaluate(() => window.craftingRun.currencies.augmentation), 1);
    assert.deepEqual(errors, []);
    console.log('Beta crafting shop UI OK');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
