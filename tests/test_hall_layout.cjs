// Run with deploy/serve.py on :8000 and Playwright on NODE_PATH.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const sizes = [[320, 568], [390, 844], [768, 1024], [1024, 768], [1280, 800], [1440, 900], [1920, 1080]];
const players = count => Array.from({length: count}, (_, i) => ({
  username: `p${i}`, nickname: i === 1 ? '这是一个很长很长很长的玩家昵称' : `玩家 ${i + 1}`,
  stack: 100, rating: {tier: '白银', score: 1000, games: 2},
}));
const room = (game, count) => ({
  room_id: 17, name: '周末一起玩 · 长房间名称布局验证', game_type: game,
  status: 'waiting', owner: 'p0', owner_name: '玩家 1', buy_in: 100, blind: 5,
  players: players(count),
});
const summaries = [
  {id: 1, name: '周末好友桌', game: 'holdem', owner_name: '玩家 1', players: players(2), buy_in: 100, blind: 5, status: 'waiting'},
  {id: 2, name: '正在对局', game: 'holdem', owner_name: '玩家 2', players: players(4), buy_in: 200, blind: 10, status: 'playing'},
  {id: 3, name: '满员房间名称很长很长需要正确换行或者截断', game: 'holdem', owner_name: '玩家 3', players: players(9), buy_in: 100, blind: 5, status: 'waiting'},
  {id: 4, name: '<img src=x onerror=alert(1)>', game: 'uno', owner_name: '安全文本', players: players(2), buy_in: 100, blind: 5, status: 'waiting'},
];

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.sent = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; }
        send(data) { window.sent.push(JSON.parse(data)); }
        addEventListener() {}
        close() {}
      };
    });
    await page.goto('http://localhost:8000/game.html');
    await page.evaluate(async () => {
      window.core = await import('/assets/js/core.js');
      core.setSignedIn({username: 'p0', nickname: '玩家 1', coins: 1000});
    });
    const showHall = async (game = 'holdem', section = 'rooms') => {
      await page.evaluate(({game, section, summaries}) => {
        core.state.myRoom = null;
        core.state.currentGameId = game;
        core.state.hallPage = section;
        core.state.hallRooms = summaries;
        core.renderGameView();
      }, {game, section, summaries});
    };
    const showRoom = data => page.evaluate(data => {
      core.state.myRoom = data;
      core.renderGameView();
    }, data);
    const updateRoom = data => page.evaluate(data => core.handleServerMessage({type: 'game_update', ...data}), data);
    async function fit(label, selector) {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const geometry = await page.evaluate(selector => {
        const nodes = selector ? [...document.querySelectorAll(selector)] : [];
        const rects = nodes.map(node => node.getBoundingClientRect());
        return {
          overflow: document.documentElement.scrollWidth > innerWidth,
          overlap: rects.some((a, i) => rects.slice(i + 1).some(b =>
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)),
          clipped: rects.some((a, i) => {
            const row = nodes[i].closest('.hall-room-row');
            const box = row?.getBoundingClientRect();
            return a.left < -1 || a.right > innerWidth + 1 || (box && (a.left < box.left - 1 || a.right > box.right + 1));
          }),
        };
      }, selector);
      assert.deepEqual(geometry, {overflow: false, overlap: false, clipped: false}, label);
    }
    async function screenshot(name) {
      if (!process.env.HALL_SCREENSHOT_DIR) return;
      fs.mkdirSync(process.env.HALL_SCREENSHOT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.HALL_SCREENSHOT_DIR, `${name}.png`), fullPage: true, animations: 'disabled'});
    }

    // Responsive waiting rooms must preserve every seat and the game-specific start gate.
    for (const game of ['holdem', 'uno', 'guandan', 'mahjong']) {
      const capacity = ['guandan', 'mahjong'].includes(game) ? 4 : 9;
      for (const [width, height] of sizes) {
        await page.setViewportSize({width, height});
        await showRoom(room(game, capacity));
        await fit(`${game} waiting ${width}`, '.waiting-seat, .waiting-center');
        assert.equal(await page.locator('#gameMain [data-username]').count(), capacity);
        if (width === 1440 || width === 390) await screenshot(`waiting-${game}-${width}`);
      }
      await showRoom(room(game, 1));
      assert.equal(await page.getByRole('button', {name: '开始游戏', exact: true}).isDisabled(), true);
      await updateRoom(room(game, 2));
      assert.equal(await page.getByRole('button', {name: '开始游戏', exact: true}).isDisabled(), capacity === 4);
      await updateRoom(room(game, capacity));
      assert.equal(await page.getByRole('button', {name: '开始游戏', exact: true}).isEnabled(), true);
      const noChips = room(game, capacity === 4 ? 4 : 2);
      noChips.players[1].stack = 0;
      await updateRoom(noChips);
      assert.equal(await page.getByRole('button', {name: '开始游戏', exact: true}).isDisabled(), true);
    }

    await page.setViewportSize({width: 1440, height: 900});
    await showRoom(room('guandan', 3));
    await page.evaluate(async () => (await import('/assets/js/room-chat.js')).openChatOverlay());
    await page.evaluate(() => {
      window.existingSeat = document.querySelector('#gameMain [data-username="p0"]');
      window.chatInput = document.getElementById('roomChatInput');
    });
    await page.locator('#roomChatInput').fill('保留聊天草稿');
    await page.evaluate(() => {
      window.seatRemovals = 0;
      window.seatObserver = new MutationObserver(records => {
        seatRemovals += records.reduce((total, record) => total + [...record.removedNodes].filter(node => node === existingSeat).length, 0);
      });
      seatObserver.observe(existingSeat.parentElement, {childList: true});
    });
    await updateRoom(room('guandan', 3));
    assert.equal(await page.evaluate(() => seatRemovals), 0, 'unchanged snapshots must not detach/reanimate players');
    await page.evaluate(() => seatObserver.disconnect());
    await updateRoom(room('guandan', 4));
    assert.equal(await page.evaluate(() => existingSeat === document.querySelector('#gameMain [data-username="p0"]')), true);
    assert.equal(await page.evaluate(() => chatInput === document.getElementById('roomChatInput')), true);
    assert.equal(await page.locator('#roomChatInput').inputValue(), '保留聊天草稿');
    await page.evaluate(() => core.handleServerMessage({type: 'room_chat', room_id: 17, username: 'p1', nickname: '玩家 2', text: '大家好'}));
    // Bubbles live on the table to escape each seat's transform stacking context.
    const playerBubble = page.locator('.waiting-table > .seat-bubble[data-username="p1"]');
    assert.equal(await playerBubble.innerText(), '大家好');
    await updateRoom(room('guandan', 4));
    assert.equal(await playerBubble.innerText(), '大家好');
    assert.equal(await playerBubble.count(), 1, 'snapshot updates must not duplicate the player bubble');
    await page.evaluate(async () => (await import('/assets/js/room-chat.js')).closeChatOverlay());
    await page.getByRole('button', {name: '开始游戏', exact: true}).click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), {type: 'start_game'});
    await showRoom({...room('guandan', 4), owner: 'p1'});
    assert.equal(await page.getByRole('button', {name: '开始游戏', exact: true}).count(), 0);

    for (const section of ['rooms', 'create']) {
      for (const game of ['holdem', 'uno', 'guandan', 'mahjong']) {
        await showHall(game, section);
        for (const [width, height] of sizes) {
          await page.setViewportSize({width, height});
          await fit(`${game} ${section} ${width}`, section === 'rooms' ? '.hall-room-row .hall-room-cell' : undefined);
          if (game === 'holdem' && [1440, 390].includes(width)) await screenshot(`${section}-${width}`);
        }
      }
    }
    // Search/filter updates must preserve focus, existing row identity and join semantics.
    await page.setViewportSize({width: 1440, height: 900});
    await showHall();
    const search = page.getByRole('searchbox');
    await search.fill('周末');
    assert.equal(await page.locator('.hall-room-row').count(), 1);
    await page.evaluate(() => { window.existingRow = document.querySelector('.hall-room-row'); });
    await page.evaluate(summaries => core.handleServerMessage({type: 'room_list', rooms: summaries}), summaries);
    assert.equal(await page.evaluate(() => existingRow === document.querySelector('.hall-room-row')), true);
    assert.equal(await search.evaluate(node => node === document.activeElement), true);
    assert.equal(await search.inputValue(), '周末');
    await search.fill('');
    await page.getByRole('button', {name: '游戏中', exact: true}).click();
    assert.equal(await page.locator('.hall-room-row').count(), 1);
    const joinsBefore = await page.evaluate(() => sent.filter(message => message.type === 'join_room').length);
    await page.locator('.hall-room-row[data-room-id="2"] button').click();
    assert.match(await page.locator('#liveDialog').innerText(), /观战进入/);
    assert.equal(await page.evaluate(() => sent.filter(message => message.type === 'join_room').length), joinsBefore);
    await page.locator('.live-dialog-cancel').click();
    assert.equal(await page.evaluate(() => sent.filter(message => message.type === 'join_room').length), joinsBefore);
    await page.locator('.hall-room-row[data-room-id="2"] button').click();
    await page.locator('.live-dialog-confirm').click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), {type: 'join_room', room_id: 2, spectate: true});
    await page.getByRole('button', {name: '全部', exact: true}).click();
    assert.equal(await page.locator('.hall-room-row[data-room-id="3"] button').isDisabled(), true);
    await page.locator('.hall-room-row[data-room-id="1"] button').click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), {type: 'join_room', room_id: 1});
    await page.getByRole('button', {name: /创建房间/}).click();
    const roomName = page.getByLabel('房间名称', {exact: false});
    const amount = page.getByLabel('买入金币', {exact: true});
    const stake = page.getByLabel('小盲注', {exact: true});
    const create = page.getByRole('button', {name: '创建并进入房间', exact: true});
    await roomName.fill('回归验证桌');
    await stake.selectOption('10');
    await amount.fill('100');
    assert.equal(await create.isDisabled(), true);
    assert.match(await page.locator('.create-room-form').innerText(), /200/);
    await amount.fill('1200');
    assert.equal(await create.isDisabled(), true);
    assert.match(await page.locator('.create-room-form').innerText(), /不足|余额/);
    await amount.fill('200');
    assert.equal(await create.isEnabled(), true);
    assert.match(await page.locator('.create-summary-card').innerText(), /回归验证桌/);
    await create.click();
    assert.deepEqual(await page.evaluate(() => sent.at(-1)), {
      type: 'create_room', game: 'holdem', name: '回归验证桌', buy_in: 200, blind: 10,
    });
    assert.equal(await page.locator('.create-submit').isDisabled(), true);
    await page.evaluate(() => core.handleServerMessage({type: 'game_error', message: '测试拒绝：请重试'}));
    if (await page.locator('#liveDialog').count()) await page.locator('.live-dialog-confirm').click();
    assert.equal(await create.isEnabled(), true);
    assert.equal(await roomName.inputValue(), '回归验证桌');
    await amount.fill('200.25');
    await roomName.press('Enter');
    assert.equal(await page.evaluate(() => sent.at(-1).buy_in), 200.25);
    await page.evaluate(data => core.handleServerMessage({type: 'game_joined', room: data}), room('holdem', 1));
    assert.equal(await page.locator('.waiting-room').count(), 1);
    await page.evaluate(() => core.handleServerMessage({type: 'room_closed'}));
    await showHall('holdem', 'create');
    assert.equal(await create.isEnabled(), true, 'successful creation must reset pending state');
    await showHall('uno', 'create');
    assert.notEqual(await page.getByLabel('房间名称', {exact: false}).inputValue(), '回归验证桌');
    await showHall('holdem', 'create');
    assert.equal(await roomName.inputValue(), '回归验证桌');
    await showHall('uno');
    assert.equal(await page.locator('.hall-room-row img').count(), 0);
    assert.match(await page.locator('.hall-room-row').innerText(), /<img src=x/);

    // Respect reduced motion while retaining the complete waiting state.
    await page.emulateMedia({reducedMotion: 'reduce'});
    await showRoom(room('holdem', 2));
    await updateRoom(room('holdem', 3));
    const animations = await page.locator('.waiting-seat').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).animationName));
    assert.ok(animations.every(name => name === 'none'));
    await showHall();
    assert.equal(await page.locator('#desktopRoomChat').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS shared hall/create/waiting responsive layouts, four game start gates, stable seats and chat, owner actions');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
