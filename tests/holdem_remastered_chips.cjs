const assert = require('node:assert/strict');

module.exports = async function checkChips(page, update, playingRoom) {
  function fixture(pot = 0, hand = 70) {
    const weights = [.6, .25, .15];
    return { ...playingRoom(), hand_no: hand, pot, to_act: null, your_options: null,
      players: playingRoom().players.map((player, i) => ({ ...player,
        stack: (1000 - pot) * weights[i], bet: pot * weights[i], hand_bet: pot * weights[i] })) };
  }
  const pile = page.locator('.poker-remastered-pot-chips');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const art = new Map();
  for (const [pot, tier] of [[0, 0], [1, 1], [199, 1], [200, 2], [399, 2], [400, 3], [599, 3], [600, 4], [799, 4], [800, 5], [1000, 5]]) {
    await update(fixture(pot));
    assert.equal(await pile.getAttribute('data-tier'), String(tier), `pot ${pot}/1000 uses tier ${tier}`);
    assert.equal(Math.round(Number(await pile.getAttribute('data-total')) * 100), 100000, 'pot is counted once in table bankroll');
    assert.equal(Number(await pile.getAttribute('data-amount')), pot);
    assert.match(await page.locator('.poker-remastered-pot').innerText(), /本手入池/);
    if (tier) {
      assert.equal(await pile.locator('svg').count(), 1);
      assert.equal(await pile.locator('img, image, canvas').count(), 0);
      art.set(tier, await pile.locator('svg').evaluate(svg => svg.outerHTML));
    }
  }
  assert.equal(new Set(art.values()).size, 5, 'five chip quantities have distinct vector drawings');
  if (process.env.REMASTER_SCREENSHOTS) {
    const gallery = await page.context().browser().newPage();
    await gallery.setViewportSize({ width: 1080, height: 280 });
    await gallery.setContent(`<body style="margin:0;padding:24px;background:#0d3430;color:#f6dfa5;font:18px sans-serif">
      <div>桌面筹码量 · 占全桌筹码比例</div><div style="display:flex;justify-content:space-between;margin-top:25px">
      ${[...art].map(([tier, svg]) => `<section style="width:190px;text-align:center"><div style="height:135px">${svg.replace(/width="[^"]*"/, 'width="100%"').replace(/height="[^"]*"/, 'height="100%"')}</div><p style="margin:8px">第 ${tier} 档 · ${tier === 1 ? '0–20%' : `${(tier - 1) * 20}–${tier * 20}%`}</p></section>`).join('')}
      </div></body>`);
    await gallery.screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-chip-tiers.png` });
    await gallery.close();
  }
  await page.emulateMedia({ reducedMotion: 'no-preference' });

  async function transaction(amount, hand) {
    const base = fixture(0, hand);
    await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), base);
    const next = { ...base, pot: amount, players: base.players.map((p, i) =>
      i === 0 ? { ...p, stack: p.stack - amount, bet: amount, hand_bet: amount } : p) };
    await update(next);
    await page.waitForTimeout(70);
    const nodes = page.locator('[data-direction="bet"][data-player="alice"]');
    const clusters = await nodes.count();
    const count = await nodes.locator('svg ellipse').count();
    assert.ok(clusters > 0 && count > 0, 'new money flies from the contributing seat');
    assert.ok((await nodes.evaluateAll(nodes => nodes.map(node => Number(node.dataset.amount)))).includes(amount));
    await update({ ...next, turn_left: 29 });
    assert.equal(await nodes.count(), clusters, 'a repeated snapshot does not replay the transfer');
    await page.waitForFunction(() => !document.querySelector('[data-direction="bet"]'));
    return { count, next };
  }
  const small = await transaction(5, 71);
  const large = await transaction(450, 72);
  assert.ok(large.count > small.count, 'larger transfers use visibly more chips');
  // Bet counters reset per street; cumulative contributions do not.
  const nextStreet = { ...large.next, stage: 'turn', pot: 470,
    players: large.next.players.map((p, i) => i === 0
      ? { ...p, stack: p.stack - 20, bet: 20, hand_bet: 470 } : { ...p, bet: 0 }) };
  await update(nextStreet);
  await page.waitForTimeout(70);
  assert.ok(await page.locator('[data-direction="bet"][data-player="alice"][data-amount="20"]').count() > 0,
    'a new street animates the true hand contribution delta');
  await page.waitForFunction(() => !document.querySelector('[data-direction="bet"]'));

  const final = { ...nextStreet, stage: 'showdown', players: nextStreet.players.map((p, i) => ({ ...p,
    stack: p.stack + (i === 0 ? 350 : i === 1 ? 120 : 0), bet: 0, hand_bet: 0 })),
    result: { pot: 470, payouts: { alice: 350, p1: 120 }, hands: [] },
    hand_ready: { hand_no: 72, ready: [], total: 3, left: 10 } };
  await update(final);
  await page.waitForTimeout(70);
  assert.equal(Number(await pile.getAttribute('data-total')), 1000,
    'paid-out stacks already include the historical pot; do not double-count it');
  for (const [player, amount] of [['alice', 350], ['p1', 120]]) {
    assert.ok(await page.locator(`[data-direction="payout"][data-player="${player}"][data-amount="${amount}"]`).count() > 0,
      'each split-pot recipient receives their exact payout animation');
  }
  assert.equal(await page.locator('#handResult').isVisible(), false, 'the result overlay lets collection finish visibly');
  await update(final);
  await page.waitForFunction(() => !document.querySelector('[data-direction="payout"]'));
  await page.locator('#handResult').waitFor({ state: 'visible' });
  assert.equal(await pile.getAttribute('data-tier'), '0', 'paid-out table chips are cleared');
  assert.match(await page.locator('.poker-remastered-pot').innerText(), /470/, 'historical hand contribution remains visible');
  await update(final);
  assert.equal(await page.locator('[data-direction="payout"]').count(), 0, 'settlement updates do not replay payouts');

  // Rejoining a settled hand skips the collection and never leaves a hidden overlay.
  await page.evaluate(room => registry.dispatchMessage({ type: 'game_joined', room }), final);
  assert.equal(await page.locator('[data-direction="payout"]').count(), 0);
  assert.equal(await page.locator('#handResult').isVisible(), true);
  await update(fixture(0, 73));
  assert.equal(await page.locator('#handResult').count(), 0);
};
