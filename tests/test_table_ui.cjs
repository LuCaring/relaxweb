// Local deploy/serve.py + Playwright. Override CHROME_PATH, NODE_PATH and PYTHON as needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');

// Compare actual Python and browser rule implementations, including wildcard hands.
const fixtures = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c', `
import asyncio, json, random
from games.base import create_room
from games.guandan import build_deck, resolve_combo, find_moves
random.seed(19)
def card(r, s=0): return {"r": r, "s": s}
cases = []
for levels in ({2}, {7, 8}):
    hands = [[card(7)], [card(7), card(7, 1)],
        [card(r, r % 4) for r in range(3, 8)],
        [card(r) for r in range(4, 9)],
        [card(8, s) for s in range(4)],
        [card(9, s) for s in range(4)] + [card(8, 1)],
        [card(16, 4)] * 2 + [card(17, 4)] * 2 + [card(8, 1)]]
    hands += [build_deck()[:27] for _ in range(6)]
    for hand in hands:
        for standing in (None, resolve_combo([card(5)], None, levels),
                         resolve_combo([card(5), card(5, 1)], None, levels)):
            cases.append(dict(hand=hand, wild=8, levels=sorted(levels), standing=standing,
                combo=resolve_combo(hand, 8, levels, standing),
                moves=find_moves(hand, 8, levels, standing)))
async def views():
    result = {}
    for game in ('mahjong', 'guandan'):
        room = create_room(game, room_id=1, name='周末好友桌', owner='p0', buy_in=200, blind=1)
        for i in range(4): room.add_member('p'+str(i), 200)
        room.player_avatar = lambda username: 'https://example.test/' + username + '.png'
        async def noop(*args, **kwargs): pass
        room.broadcast_views = room.broadcast_payload = room.on_rooms_changed = noop
        await room.start()
        g = room.game
        if game == 'guandan':
            g['hands']['p0'] = [card(7), card(7, 1), card(8), card(9)]
            g['hands']['p3'] = [card(5), card(5, 1), card(10)]
            g['to_act'] = 'p3'
            await room.perform_action('p3', 'play', {'cards': [0, 1]})
        else:
            g['hands']['p0'] = [26, 0, 1, 2, 3, 4, 5, 9, 10, 11, 18, 19, 20, 27]
            g['last_draw'] = 27
            g['discards'] = {'p'+str(i): [4, 7, 4] for i in range(4)}
            g['last_discard'] = {'by': 'p3', 'tile': 4}
        result[game] = room.view_for('p0')
        room.add_spectator('watcher', 'p0')
        result[game + '_spectator'] = room.spectator_view('watcher')
        room.remove_member('p1')
        room.note_leave('p1')
        await room.progress_game()
        result[game + '_aborted'] = room.view_for('p0')
        room.cancel_timers()
    return result
print(json.dumps(dict(cases=cases, rooms=asyncio.run(views()))))
`], {cwd:root, encoding:'utf8'}));
const source = fs.readFileSync(path.join(root, 'assets/js/games/guandan.js'), 'utf8')
  .replace(/^import\s+[\s\S]*?from\s+"[^"]+";\s*/gm, '');
const context = {document:{addEventListener(){}}, registerGame(){}};
vm.runInNewContext(source + '\nglobalThis.rules={resolveCombo,findMoves};', context);
function normalize(combo) {
  if (!combo) return null;
  return {type:combo.type, tier:combo.tier, main:Array.isArray(combo.main) ? combo.main[0] * 20 + combo.main[1] : combo.main,
    len:combo.len, cards:JSON.stringify(combo.cards.slice().sort((a,b)=>a.s-b.s||a.r-b.r))};
}
for (const fixture of fixtures.cases) {
  const standing = fixture.standing ? {...fixture.standing, main:fixture.standing.main[0]*20+fixture.standing.main[1]} : null;
  const actual = context.rules.resolveCombo(fixture.hand, fixture.wild, fixture.levels, standing);
  assert.deepEqual(normalize(actual), normalize(fixture.combo));
  const moves = context.rules.findMoves(fixture.hand, fixture.wild, fixture.levels, standing);
  const moveKeys = list => Array.from(list, move=>JSON.stringify(normalize(move))).sort();
  assert.deepEqual(moveKeys(moves), moveKeys(fixture.moves));
}
for (const [cards, expected] of [
 [[{r:9,s:0},{r:7,s:0},{r:9,s:1},{r:7,s:1},{r:9,s:2}],[9,9,9,7,7]],
 [[{r:3,s:0},{r:4,s:0},{r:5,s:0},{r:3,s:1},{r:4,s:1},{r:5,s:1}],[3,3,4,4,5,5]],
 [[{r:3,s:0},{r:4,s:0},{r:3,s:1},{r:4,s:1},{r:3,s:2},{r:4,s:2}],[3,3,3,4,4,4]],
]) {
  assert.deepEqual(Array.from(context.rules.resolveCombo(cards,null,[2]).cards,c=>c.r),expected);
}
assert.deepEqual(Array.from(context.rules.resolveCombo(
 [{r:3,s:0},{r:3,s:1},{r:3,s:2},{r:14,s:0},{r:2,s:1}],2,[2]).cards,c=>c.r),
 [3,3,3,2,14]);
console.log(`PASS Python/JavaScript parity for ${fixtures.cases.length} hands and standing combinations`);

(async()=>{
 const browser = await chromium.launch({headless:true,
   executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome', args:['--no-sandbox']});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:900}, reducedMotion:'reduce', hasTouch:true});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.WebSocket=class{static OPEN=1;constructor(){this.readyState=1;}send(){}close(){}addEventListener(){}};});
  await page.goto(process.env.TEST_BASE_URL || 'http://localhost:8000/game.html');
  await page.evaluate(async()=>{
   window.core=await import('/assets/js/core.js');
   core.state.currentUser={username:'p0',nickname:'我'};
   core.state.socket={readyState:1,send:data=>{window.sent.push(JSON.parse(data));}};
  });
  async function setRoom(game, patch={}) {
   await page.evaluate(room=>{
    window.sent=[];core.state.myRoom=room;core.renderGameView();window.scrollTo(0,0);
   }, {...fixtures.rooms[game], ...patch});
  }
  const actions = () => page.evaluate(()=>window.sent.filter(m=>m.type==='poker_action'));
  async function assertChatReadable(label) {
   const contrast = await page.locator('#roomChatInput').evaluate(input => {
    const rgb = value => value.match(/[\d.]+/g).map(Number);
    const blend = (fg,bg) => fg.slice(0,3).map((c,i)=>c*(fg[3]??1)+bg[i]*(1-(fg[3]??1)));
    const chain=[];
    for(let el=input;el;el=el.parentElement) chain.unshift(el);
    const background=chain.reduce((bg,el)=>blend(rgb(getComputedStyle(el).backgroundColor),bg),[255,255,255]);
    const luminance = color => color.map(c=>c/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4)
     .reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
    const ratio = color => {
     const a=luminance(blend(rgb(color),background)),b=luminance(background);
     return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    };
    const style=getComputedStyle(input);
    return {text:ratio(style.color),caret:ratio(style.caretColor),placeholder:ratio(getComputedStyle(input,'::placeholder').color),
     color:style.color,background};
   });
   assert.ok(contrast.text>=4.5 && contrast.caret>=3 && contrast.placeholder>=4.5,
    `${label}: typed text, caret and placeholder must be readable: ${JSON.stringify(contrast)}`);
  }
  for(const game of ['mahjong','guandan']) {
   await setRoom(game);
   await assertChatReadable(`${game} unfocused chat`);
   await page.locator('#roomChatInput').fill('测试聊天文字 Chat 123');
   await assertChatReadable(`${game} focused chat`);
   assert.equal(await page.locator('#roomChatInput').inputValue(),'测试聊天文字 Chat 123');
   await page.locator('#roomChatInput').fill('');
   await page.locator('#roomChatInput').blur();
  }
  await setRoom('guandan');
  assert.equal(await page.locator('#desktopRoomChat.compact-room-chat').count(), 1);
  assert.equal(await page.locator('.gd-seat .casual-avatar img').count(), 4);
  assert.deepEqual(await page.evaluate(()=>{
   const avatar=document.querySelector('.gd-seat.me .casual-avatar').getBoundingClientRect();
   const status=document.querySelector('.gd-status, .gd-table > .poker-status').getBoundingClientRect();
   const arena=document.querySelector('.gd-arena').getBoundingClientRect();
   return {ownAvatarVisible:avatar.width>0&&avatar.height>0,statusAboveCards:status.bottom<=arena.top};
  }),{ownAvatarVisible:true,statusAboveCards:true},'own avatar remains visible and activity text stays above the playing area');
  await page.getByRole('button',{name:'♠7',exact:true}).click();
  await page.getByRole('button',{name:'♥7',exact:true}).click();
  assert.match(await page.locator('.gd-selection-status').innerText(), /对子 7/);
  await page.getByRole('button',{name:'出牌 · 2 张',exact:true}).click();
  assert.deepEqual(await actions(), [{type:'poker_action',action:'play',cards:[0,1]}]);
  await page.evaluate(()=>core.renderGameView());
  assert.equal(await page.locator('.gd-hand-card:enabled').count(), 0, 'local renders preserve action lock');

  await setRoom('guandan');
  await page.getByRole('button',{name:'提示',exact:true}).click();
  assert.equal(await page.locator('.gd-hand-card[aria-pressed=true]').count(), 2);
  await page.getByRole('button',{name:'重选',exact:true}).click();
  assert.equal(await page.locator('.gd-hand-card[aria-pressed=true]').count(), 0);
  // Same hand, different standing: hint cache must refresh to single 8.
  await setRoom('guandan', {standing:{by:'p3',type:'single',tier:0,main:[1,7],len:1,label:'单张 7',cards:[{r:7,s:0}]}});
  await page.getByRole('button',{name:'提示',exact:true}).click();
  assert.equal(await page.locator('.gd-hand-card[aria-pressed=true]').getAttribute('aria-label'), '♠8');
  await setRoom('guandan', {paused:true});
  assert.equal(await page.locator('.gd-hand-card:enabled,.gd-dock .action-btn:enabled').count(), 0);

  await setRoom('guandan', {your_hand:[{r:16,s:4},{r:17,s:4}]});
  await page.locator('.joker-big .gc-joker-icon').evaluate(img=>img.decode());
  assert.equal(await page.locator('.joker-small .gc-joker-icon').getAttribute('src'), 'assets/cards/joker-flat.png');
  assert.equal(await page.locator('.joker-big .gc-joker-icon').getAttribute('src'), 'assets/cards/joker-flat.png');
  assert.notEqual(await page.locator('.joker-small .gc-joker-icon').evaluate(e=>getComputedStyle(e).filter), 'none');
  assert.equal(await page.locator('.joker-big .gc-joker-icon').evaluate(e=>getComputedStyle(e).filter), 'none');
  assert.ok(await page.locator('.joker-card').evaluateAll(cards=>cards.every(card=>{
   const c=card.getBoundingClientRect(),i=card.querySelector('.gc-joker-icon').getBoundingClientRect();
   return i.left-c.left<=9&&i.top-c.top<=9&&i.right<c.right-10;
  })),'joker icons sit in the same upper-left corner as other card ranks');
  if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TABLE_SCREENSHOT_DIR,'guandan-jokers.png'),fullPage:true});

  await setRoom('guandan', {players:fixtures.rooms.guandan.players.map((p,i)=>({...p,passed:i===1}))});
  assert.equal(await page.evaluate(()=>{
   const a=document.querySelector('.gd-seat.pos-left .gs-pass')?.getBoundingClientRect();
   const b=document.querySelector('.gd-standing-cards')?.getBoundingClientRect();
   return Boolean(a&&b&&a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top);
  }),false,'player status must not overlap played cards');

  await setRoom('guandan', {
   levels:[2,2],
   your_hand:[{r:3,s:0},{r:3,s:1},{r:3,s:2},{r:14,s:0},{r:2,s:1}],
   standing:{by:'p3',type:'triple_pair',tier:0,main:[1,3],len:5,label:'三带二 3',
    cards:[{r:3,s:0},{r:3,s:1},{r:3,s:2},{r:2,s:1},{r:14,s:0}]},
  });
  assert.deepEqual(await page.locator('.gd-standing-cards .gcard').evaluateAll(cards=>
    cards.map(card=>Number(card.dataset.rank))),[3,3,3,2,14]);
  assert.equal(await page.locator('.gd-standing-cards .gcard.wild').count(),1,
    'played wild card remains marked inside its represented group');
  assert.equal(await page.locator('.gd-hand .gcard.wild').count(),1,
    'wild card in hand uses the same visual treatment');
  assert.equal(await page.locator('.gd-hand .gcard.wild').evaluate(card=>getComputedStyle(card).color),
    'rgb(232, 117, 11)');
  if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({
   path:path.join(process.env.TABLE_SCREENSHOT_DIR,'guandan-triple-pair.png'),fullPage:true,
  });

  await setRoom('guandan', {status:'waiting'});
  assert.equal(await page.locator('#desktopRoomChat:not(.compact-room-chat)').count(),1,
    'guandan waiting room restores full desktop chat');

  await setRoom('mahjong');
  assert.equal(await page.locator('#desktopRoomChat:not(.compact-room-chat)').count(), 1);
  assert.equal(await page.locator('.mj-player-head .casual-avatar img').count(), 4);
  await page.getByRole('button',{name:'9筒',exact:true}).click();
  assert.equal(await page.locator('.mj-act.primary').innerText(), '打出 9筒');
  await page.getByRole('button',{name:'1万',exact:true}).click();
  assert.equal(await page.locator('.mj-hand-card[aria-pressed=true]').count(), 1);
  assert.equal(await page.locator('.mj-act.primary').innerText(), '打出 1万');
  assert.equal(await page.locator('.just-discarded').count(), 1, 'highlight only final occurrence');
  await page.getByRole('button',{name:'打出 1万',exact:true}).click();
  assert.deepEqual(await actions(), [{type:'poker_action',action:'discard',index:1}]);
  await page.evaluate(()=>core.renderGameView());
  assert.equal(await page.locator('.mj-hand-card:enabled,.mj-act:enabled').count(), 0);

  await setRoom('mahjong');
  await page.getByRole('button',{name:'9筒',exact:true}).dblclick();
  assert.deepEqual(await actions(), [{type:'poker_action',action:'discard',index:0}]);
  await setRoom('mahjong', {phase:'claim',to_act:'p3',claim:{by:'p3',tile:4,waiting:['p1']},your_options:{submitted:true}});
  assert.match(await page.locator('.mj-dock-head .my-cards-label').innerText(), /已确认/);
  assert.equal(await page.locator('.mj-act:enabled').count(),0);
  await setRoom('mahjong', {phase:'claim',paused:true,your_options:{claim:{peng:true,hu:true}}});
  assert.equal(await page.locator('.mj-act:enabled,.mj-hand-card:enabled').count(),0);
  for(const [width,height] of [[320,568],[1024,768],[1366,768],[1440,900],[1920,1080]]) {
   await page.setViewportSize({width,height});
   await setRoom('mahjong');
   await setRoom('mahjong', {phase:'claim',to_act:null,claim:{by:'p3',tile:1,waiting:['p0']},
    your_flowers:[34,35,36,37,38,39,40,41], tenpai:{waits:[24,27,30],remaining:{24:3,27:2,30:1}},
    your_options:{claim:{chi:[[0,2],[2,3]],peng:true,gang:true,hu:true}}});
   await page.locator('.mj-self-controls').getByRole('button',{name:'吃',exact:true}).click();
   assert.equal(await page.locator('.mj-self-controls .mj-chip:not(.cancel)').count(),2);
   assert.ok(await page.evaluate(()=>{
    const avatar=document.querySelector('.mj-me').getBoundingClientRect();
    return [...document.querySelectorAll('.mj-self-controls button:not([hidden])')].every(button=>{
     const r=button.getBoundingClientRect();return r.left>=avatar.right&&r.right<=innerWidth;
    });
   }),`${width}px all claim controls and chi choices remain right of our avatar`);
   if(width>=1024) {
    const size=await page.evaluate(()=>({height:innerHeight,scroll:document.documentElement.scrollHeight}));
    if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TABLE_SCREENSHOT_DIR,`mahjong-claim-${width}.png`),fullPage:true});
    assert.ok(size.scroll<=size.height+1,`${width}px expanded chi choices fit the screen: ${JSON.stringify(size)}`);
   }
   await page.locator('.mj-self-controls .mj-chip:not(.cancel)').first().click();
   assert.deepEqual(await actions(),[{type:'poker_action',action:'claim',kind:'chi',tiles:[0,2]}]);
   assert.equal(await page.locator('.mj-self-controls button:enabled,.mj-hand-card:enabled').count(),0);
  }
  await setRoom('mahjong', {status:'waiting'});
  assert.equal(await page.locator('#desktopRoomChat:not(.compact-room-chat)').count(),1,
    'mahjong waiting room restores full desktop chat');

  for(const game of ['mahjong','guandan']) {
   for(const [width,height] of [[320,568],[390,844],[844,390],[1024,768],[1440,900]]) {
    await page.setViewportSize({width,height});
    await page.waitForTimeout(20);
    const largeHand = Array.from({length:27},(_,i)=>({r:2+i%13,s:i%4}));
    await setRoom(game, game==='guandan' ? {your_hand:largeHand} : {});
    if(game==='guandan') {
     assert.ok(await page.locator('.gd-seat.me .casual-avatar').isVisible(),`${width}px own avatar is visible`);
     await setRoom(game,{your_hand:largeHand,last_action:{nickname:'很长的玩家昵称',text:'打出了三带二，现在等待下一位玩家出牌'}});
     assert.ok(await page.evaluate(()=>{
      const status=document.querySelector('.gd-status').getBoundingClientRect();
      const arena=document.querySelector('.gd-arena').getBoundingClientRect();
      const cards=document.querySelector('.gd-standing-cards').getBoundingClientRect();
      const levels=document.querySelector('.gd-levels').getBoundingClientRect();
      return levels.bottom<=status.top&&status.bottom<=arena.top&&status.bottom<=cards.top;
     }),`${width}px long activity text stays above the cards`);
    }
    assert.equal(await page.locator('#desktopRoomChat').count(),width>=1024?1:0,
      `${game} ${width}px desktop chat visibility`);
    assert.equal(await page.locator('#desktopRoomChat.compact-room-chat').count(),width>=1024&&game==='guandan'?1:0,
      `${game} ${width}px uses the appropriate chat layout`);
    if(width>=1024) {
     const chatBox=await page.locator('#desktopRoomChat').boundingBox();
     assert.ok(chatBox&&chatBox.x>=0&&chatBox.y>=0&&chatBox.x+chatBox.width<=width
       &&chatBox.y+chatBox.height<=height,
       `${game} ${width}px compact chat stays in viewport: ${JSON.stringify(chatBox)}`);
    }
    const geom = await page.evaluate(game=>{
     const boxes=[...document.querySelectorAll(game==='mahjong'?'.mj-river':'.gd-seat')].map(e=>e.getBoundingClientRect());
     return {overflow:document.documentElement.scrollWidth>innerWidth,
      overlap:boxes.some((a,i)=>boxes.slice(i+1).some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top))};
    },game);
    assert.equal(geom.overflow,false,`${game} ${width}px overflow`);
    assert.equal(geom.overlap,false,`${game} ${width}px overlapping areas`);
    const selector = game==='mahjong' ? '.mj-hand' : '.gd-hand';
    const deadline=await page.evaluate(()=>core.state.hallDeadlineAt);
    await page.locator(selector).evaluate(e=>{e.scrollLeft=120;});
    const before=await page.locator(selector).evaluate(e=>e.scrollLeft);
    await page.locator(`${selector} button`).evaluateAll(buttons=>{
     const area=buttons[0].parentElement.getBoundingClientRect();
     buttons.find(b=>{const r=b.getBoundingClientRect();return r.left>=area.left&&r.right<=area.right;}).click();
    });
    assert.equal(await page.locator(selector).evaluate(e=>e.scrollLeft),before,`${game} selection preserves scroll`);
    assert.equal(await page.evaluate(()=>core.state.hallDeadlineAt),deadline,`${game} selection preserves countdown`);
    if(width>=1024) assert.ok(await page.locator('#desktopRoomChat').isVisible(),
      `${game} selection preserves compact chat`);
    if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TABLE_SCREENSHOT_DIR,`${game}-${width}.png`),fullPage:true});
   }
  }
  // Chat bubbles must size independently of narrow seats and stay on the table.
  for (const game of ['mahjong', 'guandan']) {
   for (const [width,height] of [[1440,900],[1024,768],[390,844],[320,568],[844,390]]) {
    await page.setViewportSize({width,height});
    await setRoom(game);
    await page.evaluate(() => {
     core.state.myRoom.players.forEach((p,i)=>core.handleServerMessage({
      type:'room_chat',room_id:core.state.myRoom.room_id,username:p.username,nickname:p.nickname,time:'12:00',
      text:i===0?'这张牌先留着，等下一轮看看大家怎么出。':i===1?'AReallyLongUnbrokenMessageThatStillNeedsToStayInsideTheBubble':'这局打得不错，我们慢慢来，下一轮再看看。',
     }));
    });
    const bubbles=await page.evaluate(()=>[...document.querySelectorAll('.seat-bubble')].map(node=>{
     const r=node.getBoundingClientRect(),style=getComputedStyle(node);
     const table=node.closest('.casual-page').getBoundingClientRect();
     return {visible:!node.hidden,left:r.left,right:r.right,top:r.top,
      minTop:Math.max(8,table.top+8),width:r.width,fontSize:parseFloat(style.fontSize),
      overflow:node.scrollWidth>node.clientWidth,breakAll:style.wordBreak==='break-all'};
    }));
    assert.equal(bubbles.length,4);
    for(const b of bubbles.filter(b=>b.visible)) {
     assert.ok(b.left>=15&&b.right<=width-15&&b.top>=b.minTop-2,`${game} ${width}px bubble stays on table: ${JSON.stringify(b)}`);
     assert.ok(b.width>=200&&b.fontSize>=14,`${game} ${width}px readable bubble: ${JSON.stringify(b)}`);
     assert.ok(!b.overflow&&!b.breakAll,`${game} ${width}px natural wrapping`);
    }
    if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TABLE_SCREENSHOT_DIR,`${game}-bubbles-${width}.png`),fullPage:true});
    await page.evaluate(()=>core.renderGameView());
    assert.equal(await page.locator('.seat-bubble').count(),4,'table redraw restores each active bubble once');
    await page.evaluate(async()=>{
     (await import('/assets/js/room-chat.js')).clearSeatBubbles();
    });
    assert.equal(await page.locator('.seat-bubble').count(),0,'clearing room chat removes floating bubbles');
   }
  }
  for (const game of ['mahjong', 'guandan']) {
   // The four rivers must surround the hub, including long late-game rivers.
   if (game === 'mahjong') {
    for (const [width, height] of [[320,568], [390,844], [844,390], [1024,768], [1366,768], [1440,900], [1920,1080]]) {
     await page.setViewportSize({width,height});
     const room = structuredClone(fixtures.rooms.mahjong);
     room.discards = Object.fromEntries(room.players.map(p =>
      [p.username, Array.from({length:24}, (_, i) => i % 34)]));
     room.last_discard = {by:'p3',tile:23};
     room.players[0].melds = [{type:'peng',tiles:[4,4,4]}, {type:'angang',tiles:[8,8,8,8]}];
     room.players[1].melds = [{type:'chi',tiles:[0,1,2]}, {type:'angang',tiles:[8,8,8,8]}];
     room.players[1].concealed = 7;
     room.players[2].melds = [{type:'peng',tiles:[13,13,13]}];
     room.players[2].concealed = 10;
     room.players[3].melds = [{type:'gang',tiles:[27,27,27,27]}];
     room.players[3].concealed = 10;
     room.your_hand = [0,1,2,9,10,11,27,27];
     room.your_flowers = [34,35,36,37,38,39,40,41];
     room.tenpai = {waits:[24,27,30],remaining:{24:3,27:2,30:1}};
     await setRoom('mahjong', room);
     const layout = await page.evaluate(() => {
      const rect = s => document.querySelector(s).getBoundingClientRect();
      const hub = rect('.mj-center');
      const rivers = [...document.querySelectorAll('.mj-river')];
      const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      const seats = [...document.querySelectorAll('.mj-player-head')].filter(e=>e.getBoundingClientRect().width);
      const chat = document.querySelector('#desktopRoomChat')?.getBoundingClientRect();
      return {
       directions: rect('.river-top').bottom < hub.top && rect('.river-bottom').top > hub.bottom
        && rect('.river-left').right < hub.left && rect('.river-right').left > hub.right,
       overlap: rivers.some(e=>overlap(e.getBoundingClientRect(),hub)
        || rivers.some(other=>other!==e&&overlap(e.getBoundingClientRect(),other.getBoundingClientRect()))
        || seats.some(s=>overlap(e.getBoundingClientRect(),s.getBoundingClientRect()))),
       latest: rivers.every(e=>Math.abs(e.scrollHeight-e.clientHeight-e.scrollTop)<2),
       chatOverlap: chat && [...seats,...document.querySelectorAll('.mj-actions,.mj-my-melds,.mj-hand')]
        .some(e=>overlap(chat,e.getBoundingClientRect())),
       clipped: rect('.mj-my-flowers').bottom > rect('.mj-page').bottom,
      };
     });
     assert.ok(layout.directions, `${width}px rivers face their seats`);
     assert.equal(layout.overlap, false, `${width}px dense rivers clear hub and player information`);
     assert.ok(layout.latest, `${width}px late-game rivers show newest tiles`);
     assert.ok(!layout.chatOverlap, `${width}px chat clears players and controls`);
     assert.equal(layout.clipped, false, `${width}px expanded dock remains inside table page`);
     assert.equal(await page.locator('.mj-my-melds .mj-meld').count(),2);
     const racks = await page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const hub = rect('.mj-center');
      const midX = r => r.left + r.width / 2, midY = r => r.top + r.height / 2;
      const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      const rackRects = [...document.querySelectorAll('.mj-player-rack')].map(e=>e.getBoundingClientRect());
      const heads = [...document.querySelectorAll('.mj-player-head')].map(e=>e.getBoundingClientRect());
      const rivers = [...document.querySelectorAll('.mj-river')].map(e=>e.getBoundingClientRect());
      const chat = document.querySelector('#desktopRoomChat')?.getBoundingClientRect();
      const size = [...document.querySelectorAll('.mj-player-rack .mj-tile,.mj-river .mj-tile,.mj-corner .mj-tile')].map(e=>{
       const r=e.getBoundingClientRect(); return [Math.min(r.width,r.height),Math.max(r.width,r.height)];
      });
      return {
       centered: Math.abs(midX(rect('.rack-top'))-midX(hub))<1 && Math.abs(midX(rect('.rack-bottom'))-midX(hub))<1
        && Math.abs(midY(rect('.rack-left'))-midY(hub))<1 && Math.abs(midY(rect('.rack-right'))-midY(hub))<1,
       uniform: size.every(s=>Math.abs(s[0]-size[0][0])<.2&&Math.abs(s[1]-size[0][1])<.2),
       overlap: rackRects.some(r=>overlap(r,hub)||rivers.some(b=>overlap(r,b))||heads.some(b=>overlap(r,b)))
        ||rackRects.some((r,i)=>rackRects.slice(i+1).some(b=>overlap(r,b)))
        ||heads.some((r,i)=>heads.slice(i+1).some(b=>overlap(r,b))),
       chatOverlap: chat&&rackRects.some(r=>overlap(r,chat)),
       orientations: ['top','left','right'].map(pos=>{
        const m=new DOMMatrix(getComputedStyle(document.querySelector(`.rack-${pos}`)).transform);
        return [Math.round(m.a),Math.round(m.b),Math.round(m.c),Math.round(m.d)].map(n=>n||0);
       }),
      };
     });
     assert.ok(racks.centered, `${width}px all four racks align with the centre`);
     assert.ok(racks.uniform, `${width}px concealed, exposed, river and own tiles share one size`);
     assert.equal(racks.overlap,false,`${width}px racks, rivers and player labels do not overlap`);
     assert.ok(!racks.chatOverlap, `${width}px chat stays outside all racks`);
     assert.deepEqual(racks.orientations,[[-1,0,0,-1],[0,1,-1,0],[0,-1,1,0]]);
     const workspace = await page.evaluate(() => {
      const page=document.querySelector('.mj-page').getBoundingClientRect();
      const stage=document.querySelector('.mj-stage').getBoundingClientRect();
      const controls=document.querySelector('.mj-dock').getBoundingClientRect();
      const footer=document.querySelector('.mj-footer').getBoundingClientRect();
      const flowers=document.querySelector('.mj-my-flowers').getBoundingClientRect();
      const tenpai=document.querySelector('.mj-tenpai').getBoundingClientRect();
      const selfRow=document.querySelector('.mj-self-row').getBoundingClientRect();
      const avatar=document.querySelector('.mj-me').getBoundingClientRect();
      const actions=document.querySelector('.mj-actions').getBoundingClientRect();
      const chat=document.querySelector('#desktopRoomChat')?.getBoundingClientRect();
      return {
       noFooter: !document.querySelector('.mj-page > .mj-dock') && page.bottom-footer.bottom<22,
       controlsByActions: controls.top>=actions.bottom && controls.left>=avatar.right,
       corners: flowers.left<selfRow.left && tenpai.right>selfRow.right,
       fitsScreen: document.documentElement.scrollHeight<=innerHeight+1,
       extent: {scroll:document.documentElement.scrollHeight, height:innerHeight, pageBottom:page.bottom, footerHeight:footer.height, top:page.top},
       compactHub: document.querySelector(".mj-center").getBoundingClientRect().width < document.querySelector(".river-bottom").getBoundingClientRect().width * .75,
       actionsBesideAvatar: actions.left>=avatar.right && actions.top<avatar.bottom && actions.bottom>avatar.top
        && actions.right<=page.right && selfRow.top>=stage.bottom,
       separateSidebar: chat&&chat.left>=page.right&&chat.height>=innerHeight-110,
       tileWidth: document.querySelector('.river-bottom .mj-tile').getBoundingClientRect().width,
      };
     });
     assert.ok(workspace.noFooter,`${width}px no obsolete hand area below the table`);
     assert.ok(workspace.controlsByActions,`${width}px turn information stays with the action buttons`);
     assert.ok(workspace.actionsBesideAvatar,`${width}px action buttons sit to the right of our avatar, below the hand`);
     if(width>=1024) {
      assert.ok(workspace.separateSidebar,`${width}px full-height chat is a separate right column`);
      assert.ok(workspace.tileWidth>=(height>=900?30:22),`${width}px desktop tiles remain readable`);
      assert.ok(workspace.corners,`${width}px flowers and waits occupy the bottom corners`);
      assert.ok(workspace.fitsScreen,`${width}px the whole table fits the desktop viewport: ${JSON.stringify(workspace.extent)}`);
      assert.ok(workspace.compactHub,`${width}px central round panel is smaller than a river`);
     }
     assert.equal(await page.locator('.mj-player-backs img:not([src$="/back.png"])').count(),0);
     assert.equal(await page.locator('.mj-opp .meld-angang .back').count(),4);
     assert.equal(await page.locator('.mj-opp .meld-chi img[src$="/0.png"],.mj-opp .meld-chi img[src$="/1.png"],.mj-opp .meld-chi img[src$="/2.png"]').count(),3);
     assert.equal(await page.locator('.mj-opp .mj-backs-pics .mj-tile').count(),27);
     if(process.env.TABLE_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TABLE_SCREENSHOT_DIR,`mahjong-racks-${width}.png`),fullPage:true});
    }
    await setRoom('mahjong');
    assert.equal(await page.locator('.mj-center-seat.active').count(),1);
    assert.ok(await page.locator('.center-bottom').evaluate(e=>e.classList.contains('active')));
    await page.emulateMedia({reducedMotion:'no-preference'});
    assert.equal(await page.locator('.mj-me .mj-player-head').evaluate(e=>getComputedStyle(e).animationName),'mjSeatPulse');
    await setRoom('mahjong',{to_act:'p1'});
    assert.equal(await page.locator('.mj-opp-right .mj-player-head').evaluate(e=>getComputedStyle(e).animationName),'mjSeatPulse');
    assert.equal(await page.locator('.mj-me .mj-player-head').evaluate(e=>getComputedStyle(e).animationName),'none');
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('.mj-opp-right .mj-player-head').evaluate(e=>getComputedStyle(e).animationName),'none');
    await page.emulateMedia({reducedMotion:'no-preference'});
    await setRoom('mahjong',{paused:true});
    assert.equal(await page.locator('.mj-center-seat.active').count(),0);
    assert.ok(await page.locator('.mj-player-head').evaluateAll(heads=>heads.every(e=>getComputedStyle(e).animationName==='none')));
    await page.emulateMedia({reducedMotion:'reduce'});
   }
   for (const [width, height] of [[320,568], [844,390], [1440,900]]) {
    await page.setViewportSize({width,height});
    await setRoom(game + '_aborted');
    if(width>=1024) {
     assert.equal(await page.locator('#desktopRoomChat:not(.compact-room-chat)').count(),1,
       `${game} settlement restores full desktop chat`);
    }
    assert.match(await page.locator('.game-card-page').first().innerText(), /作废/);
    assert.equal(await page.getByRole('button',{name:'结算并再来一局',exact:true}).isEnabled(), false);
    const dissolve = page.getByRole('button',{name:'结算并解散房间',exact:true});
    await dissolve.scrollIntoViewIfNeeded();
    const hit = await dissolve.evaluate(e=>{
     const r=e.getBoundingClientRect();
     return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
    });
    assert.ok(hit, `${game} ${width}px leave settlement must not cover its buttons`);
    await dissolve.click();
    assert.ok(await page.evaluate(()=>window.sent.some(m=>m.type==='settle_vote'&&m.choice==='dissolve')));
   }
  }
  // 观战：被看玩家第一视角、操作只读、灰色标注聊天、更换玩家入口。
  await page.setViewportSize({width:1440,height:900});
  await page.evaluate(()=>{ core.state.currentUser={username:'watcher',nickname:'小观'}; });
  await setRoom('mahjong_spectator');
  assert.equal(await page.locator('.mj-me').evaluate(e=>e.dataset.username),'p0',
    'spectated player sits at the bottom as the first-person seat');
  assert.equal(await page.locator('.rack-bottom .mj-hand-card').count(),
    fixtures.rooms.mahjong_spectator.your_hand.length);
  assert.equal(await page.locator('.rack-bottom .mj-hand-card:disabled').count(),
    fixtures.rooms.mahjong_spectator.your_hand.length, 'spectator cannot select tiles');
  assert.equal(await page.locator('.mj-actions button:enabled').count(),0,
    'no visible action buttons for a spectator');
  assert.ok(await page.locator('#spectateBadge').isVisible());
  assert.match(await page.locator('#spectateBadge').innerText(),/观战中/);
  assert.match(await page.locator('#spectateBadge').innerText(),/p0/);
  assert.equal(await page.locator('#spectateManage:not([hidden])').count(),1);
  await page.locator('#spectateButton').click();
  assert.equal(await page.locator('#spectateMenu .room-manage-item').count(),4);
  await page.locator('#spectateMenu .room-manage-item').nth(1).click();
  assert.ok(await page.evaluate(()=>window.sent.some(m=>m.type==='watch_player'&&m.username==='p1')),
    'player menu sends watch_player');
  await page.evaluate(msg=>import('/assets/js/registry.js').then(r=>r.dispatchMessage(msg)),
    {type:'room_chat', room_id:1, username:'p0', nickname:'p0', spectator:true, text:'各位继续', time:'09/21 12:00'});
  assert.match(await page.locator('.rc-name.rc-spectator').first().innerText(),/（观战）/);
  assert.equal(await page.locator('.rc-name.rc-spectator').first().evaluate(e=>getComputedStyle(e).color),
    'rgb(138, 147, 161)','spectator id renders gray');
  assert.equal(await page.locator('.seat-bubble').count(),0,'spectator message raises no seat bubble');
  await page.evaluate(msg=>import('/assets/js/registry.js').then(r=>r.dispatchMessage(msg)),
    {type:'room_chat', room_id:1, username:'p1', nickname:'p1', spectator:false, text:'好的', time:'09/21 12:01'});
  assert.equal(await page.locator('.seat-bubble').count(),1,'player message still raises a seat bubble');
  await setRoom('guandan_spectator');
  assert.equal(await page.locator('.gd-seat.me').count(),1,'guandan spectator keeps first-person seat');
  assert.equal(await page.locator('.gd-dock .action-bar').count(),0,'guandan spectator gets no action bar');
  await page.evaluate(()=>{core.state.currentUser.avatar='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>';});
  await setRoom('guandan_spectator', {players:fixtures.rooms.guandan_spectator.players.map(p=>({...p,avatar:''}))});
  assert.equal(await page.locator('.gd-seat.me .casual-avatar img').count(),0,
    'a watched player without an avatar never borrows the spectator account avatar');
  await page.setViewportSize({width:390,height:844});
  await setRoom('mahjong_spectator');
  assert.equal(await page.locator('.mj-me').evaluate(e=>e.dataset.username),'p0');
  assert.ok(await page.locator('#spectateBadge').isVisible());
  assert.equal(await page.locator('.mj-actions button:enabled').count(),0);
  await page.evaluate(()=>{ core.state.currentUser={username:'p0',nickname:'我'}; });
  await page.setViewportSize({width:320,height:568});
  await setRoom('mahjong');
  assert.equal(await page.locator('#desktopRoomChat').count(),0);
  await page.getByRole('button',{name:'打开聊天',exact:true}).click();
  await page.locator('#chatOverlay').waitFor();
  await page.locator('.chat-overlay-close').click();
  await page.getByRole('button',{name:'牌河',exact:true}).click();
  assert.equal(await page.locator('.mj-river-overlay').evaluate(e=>e.scrollWidth>e.clientWidth),false);
  await page.getByRole('button',{name:'✕ 关闭',exact:true}).click();
  assert.equal(await page.locator('.mj-river-overlay').count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS real engine views, follow/play/hints, single selection, double-click, locks, pause, scroll, timers and responsive viewports (including 7 Mahjong sizes)');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
