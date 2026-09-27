const assert = require('node:assert/strict');

module.exports = async function checkResponsiveTable(page, update, playingRoom) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const room = { ...playingRoom(9), hand_no: 39, turn_left: 120 };
  async function inspect() {
    return page.locator('.poker-remastered').evaluate(root => {
      const rect = root.getBoundingClientRect();
      const stage = root.querySelector('.poker-remastered-stage').getBoundingClientRect();
      const seats = [...root.querySelectorAll('.poker-remastered-seat')].map(node => node.getBoundingClientRect());
      const cards = [...root.querySelectorAll('.poker-remastered-card')].map(node => node.getBoundingClientRect());
      const controls = [...root.querySelectorAll('.poker-remastered-actions button, .poker-remastered-actions input')]
        .map(node => node.getBoundingClientRect());
      const sceneLayers = [...root.querySelectorAll('.poker-remastered-stage canvas, .poker-remastered-table-art')]
        .map(node => node.getBoundingClientRect());
      const publicCards = [...root.querySelectorAll('[data-role="board"]')].map(node => node.getBoundingClientRect());
      const overlap = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1
        && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
      return {
        width: stage.width, height: stage.height,
        sceneFits: sceneLayers.every(box => Math.abs(box.x - stage.x) < 2 && Math.abs(box.y - stage.y) < 2
          && Math.abs(box.width - stage.width) < 2 && Math.abs(box.height - stage.height) < 2),
        boardAligned: publicCards.length === 5 && publicCards.every(box =>
          Math.abs(box.y + box.height / 2 - stage.y - stage.height * .49) < 3),
        fitsScreen: rect.top >= -1 && rect.bottom <= innerHeight + 1
          && rect.left >= -1 && rect.right <= innerWidth + 1,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
        clipped: [...seats, ...cards, ...controls].some(box => box.left < rect.left - 1
          || box.right > rect.right + 1 || box.top < rect.top - 1 || box.bottom > rect.bottom + 1),
        seatOverlap: seats.some((box, index) => seats.slice(index + 1).some(other => overlap(box, other))),
        readableCards: cards.length === 7 && cards.every(box => box.width >= 30 && box.height >= 40),
        usableActions: controls.length > 0 && controls.every(box => box.width >= 24 && box.height >= 26),
      };
    });
  }
  for (const [width, height] of [[1024, 768], [1280, 720], [1920, 720]]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await update(room);
    await page.waitForTimeout(750);
    const result = await inspect();
    if (process.env.REMASTER_SCREENSHOTS) {
      await page.screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-responsive-${width}x${height}.png`, fullPage: true });
    }
    assert.equal(result.fitsScreen, true, `${width}×${height}: table and dock fit the available screen: ${JSON.stringify(result)}`);
    assert.equal(result.horizontalOverflow, false);
    assert.equal(result.sceneFits, true, `canvas and SVG must match the stage at ${width}×${height}: ${JSON.stringify(result)}`);
    assert.equal(result.boardAligned, true, 'public cards track the current stage height');
    assert.equal(result.clipped, false, 'cards, seats and all action controls stay inside the layout');
    assert.equal(result.seatOverlap, false, 'nine seats must stay separate on compact desktops');
    assert.equal(result.readableCards, true);
    assert.equal(result.usableActions, true);
    await page.waitForTimeout(600);
    const stable = await inspect();
    assert.ok(Math.abs(stable.width - result.width) < 1 && Math.abs(stable.height - result.height) < 1,
      'resize observation settles without an oscillating layout');
  }

  // Layout changes without a viewport resize must also update the Phaser surface.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(650);
  const before = await inspect();
  await page.evaluate(() => { document.querySelector('#gameMain').style.width = '80%'; });
  await page.waitForTimeout(750);
  const narrowed = await inspect();
  assert.ok(narrowed.width < before.width - 50, 'changing the host column resizes the table');
  assert.equal(narrowed.clipped, false);
  const canvasFits = await page.locator('.poker-remastered-stage').evaluate(stage => {
    const box = stage.getBoundingClientRect();
    const canvas = stage.querySelector('canvas').getBoundingClientRect();
    const art = stage.querySelector('.poker-remastered-table-art').getBoundingClientRect();
    return [canvas, art].every(item => Math.abs(item.width - box.width) < 2 && Math.abs(item.height - box.height) < 2);
  });
  assert.equal(canvasFits, true, 'both canvas and live SVG adapt to the new host size');
  await page.evaluate(() => { document.querySelector('#gameMain').style.width = ''; document.body.style.minHeight = '1800px'; });
  await page.waitForTimeout(750);
  const scrollBase = await inspect();
  await page.evaluate(() => {
    window.responsiveBoardNodes = [...document.querySelectorAll('[data-role="board"]')];
    window.scrollTo(0, 160);
  });
  await page.waitForTimeout(700);
  const scrolled = await inspect();
  assert.ok(Math.abs(scrolled.width - scrollBase.width) < 1 && Math.abs(scrolled.height - scrollBase.height) < 1,
    'scrolling does not change the fitted stage size');
  assert.equal(await page.evaluate(() => responsiveBoardNodes.every((node, i) =>
    document.querySelectorAll('[data-role="board"]')[i] === node)), true,
  'scrolling must not rebuild the public cards');
  await page.evaluate(() => { document.body.style.minHeight = ''; window.scrollTo(0, 0); });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
};
