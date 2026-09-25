// 斗地主前端牌型判定与后端一致性 + 视图渲染冒烟。Run: npm run test:browser -- tests/test_doudizhu_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.venv', 'bin', 'python');

// Compare actual Python and browser rule implementations over random hands.
const fixtures = JSON.parse(execFileSync(python, ['-c', `
import asyncio, json, random
from games.base import create_room
from games.doudizhu import build_deck, resolve_combo, find_moves
random.seed(7)
def card(r, s=0): return {"r": r, "s": s}
cases = []
standings = [None,
    resolve_combo([card(5)]),
    resolve_combo([card(5), card(5, 1)]),
    resolve_combo([card(5), card(5, 1), card(5, 2)]),
    resolve_combo([card(5), card(5, 1), card(5, 2), card(6)]),
    resolve_combo([card(r) for r in range(3, 8)]),
    resolve_combo([card(9, s) for s in range(4)]),
    resolve_combo([card(16, 4), card(17, 4)])]
hands = [[card(7)], [card(7), card(7, 1)], [card(7), card(7, 1), card(7, 2), card(3)],
    [card(r, r % 4) for r in range(3, 8)],
    [card(3), card(3, 1), card(3, 2), card(4), card(4, 1), card(4, 2), card(5), card(6)],
    [card(9, s) for s in range(4)] + [card(3), card(3, 1)],
    [card(16, 4), card(17, 4)]]
hands += [build_deck()[:17] for _ in range(6)]
hands += [build_deck()[:20] for _ in range(4)]
for hand in hands:
    for standing in standings:
        cases.append(dict(hand=hand, standing=standing,
            combo=resolve_combo(hand) if standing is None else None,
            moves=find_moves(hand, standing)))
async def views():
    result = {}
    room = create_room('doudizhu', room_id=1, name='周末斗地主', owner='p0', buy_in=200, blind=1,
                       rules={'bid_mode': 'bid', 'bottom_visible': True, 'bomb_cap': 4, 'spring': True})
    for i in range(3): room.add_member('p'+str(i), 200)
    room.player_avatar = lambda username: 'https://example.test/' + username + '.png'
    async def noop(*args, **kwargs): pass
    room.broadcast_views = room.broadcast_payload = room.on_rooms_changed = noop
    await room.start()
    g = room.game
    await room.perform_action('p0', 'bid', {'score': 2})
    await room.perform_action('p1', 'pass', {})
    await room.perform_action('p2', 'pass', {})
    g['hands']['p0'] = [card(r, r % 4) for r in range(3, 15)]
    g['hands']['p1'] = [card(5), card(5, 1)]
    g['hands']['p2'] = [card(9)]
    await room.perform_action('p0', 'play', {'cards': [0, 1, 2, 3, 4]})
    result['play'] = room.view_for('p0')
    room.add_spectator('watcher', 'p1')
    result['spectator'] = room.spectator_view('watcher')
    await room.perform_action('p1', 'pass', {})
    await room.perform_action('p2', 'pass', {})
    await room.perform_action('p0', 'play', {'cards': [0, 1, 2, 3, 4, 5, 6]})
    result['result'] = room.view_for('p0')
    room.cancel_timers()
    return result
print(json.dumps(dict(cases=cases, rooms=asyncio.run(views()))))
`], {cwd:root, encoding:'utf8', env:{...process.env, PYTHONHASHSEED:'0'}}));
const source = fs.readFileSync(path.join(root, 'assets/js/games/doudizhu.js'), 'utf8')
  .replace(/^import\s+[\s\S]*?from\s+"[^"]+";\s*/gm, '');
const context = {document:{addEventListener(){}}, registerGame(){}};
vm.runInNewContext(source + '\nglobalThis.rules={resolveCombo,findMoves};', context);
function normalize(combo) {
  if (!combo) return null;
  return {type:combo.type, tier:combo.tier, main:combo.main,
    len:combo.len, cards:JSON.stringify(combo.cards.slice().sort((a,b)=>a.s-b.s||a.r-b.r))};
}
for (const fixture of fixtures.cases) {
  if (fixture.standing === null) {
    const actual = context.rules.resolveCombo(fixture.hand);
    assert.deepEqual(normalize(actual), normalize(fixture.combo));
  }
  const moves = context.rules.findMoves(fixture.hand, fixture.standing);
  const moveKeys = list => Array.from(list, move=>JSON.stringify(normalize(move))).sort();
  assert.deepEqual(moveKeys(moves), moveKeys(fixture.moves));
}
console.log(`牌型一致性 ${fixtures.cases.length} 组用例通过`);

// Render smoke: load game.html, feed a doudizhu room view into the real module and render.
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || chromium.executablePath()});
  const page = await browser.newPage({viewport: {width: 900, height: 1400}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.env.LIVE_WEB_PORT || 8000}/game.html`);
  const view = fixtures.rooms.play;
  await page.evaluate(async (data) => {
    const core = await import('/assets/js/core.js');
    const registry = await import('/assets/js/registry.js');
    core.state.currentUser = {username: 'p0', nickname: 'p0', coins: 200};
    core.state.myRoom = data;
    registry.gameView('doudizhu').renderTable();
  }, view);
  await page.waitForSelector('.dd-table', {timeout: 5000});
  assert.ok(await page.locator('.dd-seat').count() === 3, '三个座位');
  assert.ok(await page.locator('.dd-hand-card').count() === 7, '手牌 7 张');
  assert.ok(await page.locator('.dd-play-cards .dcard').count() === 5, '桌面出牌 5 张');
  assert.ok(await page.locator('.dd-bottom .dcard').count() > 0, '明底牌可见');
  // 结算视图
  await page.evaluate(async (data) => {
    const core = await import('/assets/js/core.js');
    const registry = await import('/assets/js/registry.js');
    core.state.myRoom = data;
    registry.gameView('doudizhu').renderTable();
  }, fixtures.rooms.result);
  await page.waitForSelector('.dd-result', {timeout: 5000});
  assert.ok(await page.locator('.dd-match-banner').count() === 1, '胜负横幅');
  const shot = path.join(root, 'tests', '__doudizhu_table.png');
  await page.screenshot({path: shot, fullPage: true});
  console.log(`牌桌渲染冒烟通过 · 截图 ${path.relative(root, shot)}`);
  await browser.close();
  if (errors.length) throw new Error('页面报错: ' + errors.join('; '));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
