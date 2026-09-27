const assert = require('node:assert/strict');

module.exports = async function checkFixedSeating(page, update, playingRoom) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const base = { ...playingRoom(9), hand_no: 41 };
  async function positions() {
    return page.locator('.poker-remastered-seat').evaluateAll(nodes => nodes.map(node => ({
      username: node.dataset.username, x: node.style.left, y: node.style.top,
    })));
  }
  await update(base);
  await page.evaluate(() => { window.initialHandNodes = [...document.querySelectorAll('[data-role="hole"]')]; });
  await update({ ...base, turn_left: 29 });
  assert.equal(await page.evaluate(() => initialHandNodes.every((node, i) =>
    document.querySelectorAll('[data-role="hole"]')[i] === node)), true,
  'ordinary room snapshots preserve the hand nodes and do not restart their animation');
  const expected = await positions();
  assert.ok(parseFloat(expected[0].x) < 40 && parseFloat(expected[0].y) < 40,
    'the first entrant starts to the left of the dealer opening');
  assert.ok(parseFloat(expected.at(-1).x) > 60 && parseFloat(expected.at(-1).y) < 40,
    'the last entrant ends to the right of the dealer opening');
  for (const [username, watching] of [['alice', null], ['p3', null], ['p8', null],
    ['observer', 'p2'], ['observer', 'p7']]) {
    await page.evaluate(username => { core.state.currentUser.username = username; }, username);
    const view = { ...base, spectator: Boolean(watching), watching,
      to_act: watching || username, your_options: watching ? null : base.your_options };
    await update(view);
    assert.deepEqual(await positions(), expected, `${username}/${watching} must see the same fixed seats`);
    const perspective = watching || username;
    assert.equal(await page.locator('.poker-remastered-seat.is-me').getAttribute('data-username'), perspective);
    assert.deepEqual(await page.locator('.poker-remastered-card[data-role="hole"]').evaluateAll(nodes =>
      nodes.map(node => node.dataset.player)), [perspective, perspective],
    'private cards belong to the viewing player, including after switching spectator target');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const ringOffset = await page.locator('.poker-remastered-turn-ring').evaluate(node => {
      const ring = node.getBoundingClientRect();
      const seat = document.querySelector('.poker-remastered-seat.is-active').getBoundingClientRect();
      return Math.hypot(ring.x + ring.width / 2 - seat.x - seat.width / 2,
        ring.y + ring.height / 2 - seat.y - seat.height / 2);
    });
    assert.ok(ringOffset < 3, 'the action ring follows the acting seat, regardless of viewing player');
  }

  const layoutFailures = [];
  for (let count = 2; count <= 9; count++) {
    const room = { ...playingRoom(count), hand_no: 41 };
    for (let index = 0; index < count; index++) {
      await page.evaluate(username => { core.state.currentUser.username = username; }, room.players[index].username);
      await update(room);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      if (index === 0) {
        const seats = await positions();
        for (let n = 0; n < seats.length; n++) {
          const opposite = seats.at(-1 - n);
          assert.ok(Math.abs(parseFloat(seats[n].x) + parseFloat(opposite.x) - 100) < .002
            && Math.abs(parseFloat(seats[n].y) - parseFloat(opposite.y)) < .002,
          `${count} players: seat ${n + 1} mirrors seat ${count - n}`);
        }
      }
      const geometry = await page.locator('.poker-remastered-stage').evaluate(stage => {
        const root = stage.closest('.poker-remastered');
        const box = root.getBoundingClientRect();
        const actions = root.querySelector('.poker-remastered-actions').getBoundingClientRect();
        const holes = [...root.querySelectorAll('[data-role="hole"]')];
        return {
          tableCards: stage.querySelectorAll('.poker-remastered-card').length,
          holeCount: holes.length,
          misplaced: holes.some(node => {
            const card = node.getBoundingClientRect();
            return stage.contains(node) || card.right > actions.left
              || Math.min(card.bottom, actions.bottom) <= Math.max(card.top, actions.top) || card.left < box.left
              || card.right > box.right || card.bottom > box.bottom;
          }),
        };
      });
      const failed = geometry.holeCount !== 2 || geometry.tableCards !== 5 || geometry.misplaced;
      if (failed) layoutFailures.push({ count, seat: index + 1, ...geometry });
      if (failed && process.env.REMASTER_SCREENSHOTS) {
        await page.screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-failure-${count}-${index + 1}.png`, fullPage: true });
      }
      if (process.env.REMASTER_SCREENSHOTS && count === 9 && [0, 3, 4, 8].includes(index)) {
        await page.screenshot({ path: `${process.env.REMASTER_SCREENSHOTS}-seat-${index + 1}.png`, fullPage: true });
      }
    }
  }
  assert.deepEqual(layoutFailures, [], 'private cards stay left of the actions for every seat and player count');

  // Observe a late entrant's chips in actual rendered frames, rather than only checking seat styles.
  await page.evaluate(() => { core.state.currentUser.username = 'p3'; });
  const initial = { ...base, pot: 0, to_act: null, your_options: null,
    players: base.players.map(p => ({ ...p, bet: 0, hand_bet: 0 })) };
  await update(initial);
  const chipSeat = await page.locator('.poker-remastered-seat[data-username="p3"]').evaluate(node => ({
    x: .5 + (parseFloat(node.style.left) / 100 - .5) * .31 / .392,
    y: .5 + (parseFloat(node.style.top) / 100 - .5) * .31 / .39,
  }));
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  async function trace(next, direction) {
    return page.evaluate(async ({ next, direction }) => {
      const stage = document.querySelector('.poker-remastered-stage').getBoundingClientRect();
      const frames = [];
      registry.dispatchMessage({ type: 'game_update', ...next });
      const started = performance.now();
      await new Promise(resolve => {
        function capture() {
          const node = document.querySelector(`[data-direction="${direction}"][data-player="p3"]`);
          if (node) {
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              frames.push({ x: (rect.x + rect.width / 2 - stage.x) / stage.width,
                y: (rect.y + rect.height / 2 - stage.y) / stage.height });
            }
          }
          if (performance.now() - started < 1300) requestAnimationFrame(capture);
          else resolve();
        }
        requestAnimationFrame(capture);
      });
      return frames;
    }, { next, direction });
  }
  const paid = { ...initial, pot: 100, players: initial.players.map(p => p.username === 'p3'
    ? { ...p, stack: p.stack - 100, bet: 100, hand_bet: 100 } : p) };
  const bet = await trace(paid, 'bet');
  assert.ok(bet.length > 5, 'a non-bottom player produces a visible chip transfer');
  assert.ok(Math.hypot(bet[0].x - chipSeat.x, bet[0].y - chipSeat.y) < .03,
    'chips start at the contributing player’s fixed seat');
  assert.ok(Math.hypot(bet.at(-1).x - .5, bet.at(-1).y - .302) < .03, 'bet chips arrive at the table pile');
  const settled = { ...paid, players: initial.players,
    result: { pot: 100, payouts: { p3: 100 }, hands: [] } };
  const payout = await trace(settled, 'payout');
  assert.ok(payout.length > 5, 'the winner receives a visible chip transfer');
  assert.ok(Math.hypot(payout[0].x - .5, payout[0].y - .302) < .03, 'payout starts at the table pile');
  assert.ok(Math.hypot(payout.at(-1).x - chipSeat.x, payout.at(-1).y - chipSeat.y) < .03,
    'payout returns to the same fixed seat');
  await page.evaluate(() => { core.state.currentUser.username = 'alice'; });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await update({ ...base, hand_no: 42 });
};
