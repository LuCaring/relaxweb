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
  assert.equal(await page.locator('#desktopRoomChat.compact-room-chat').count(), 1);
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
    assert.equal(await page.locator('#desktopRoomChat.compact-room-chat').count(),width>=1024?1:0,
      `${game} ${width}px compact chat visibility`);
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
    for (const [width, height] of [[320,568], [390,844], [844,390], [1024,768], [1440,900]]) {
     await page.setViewportSize({width,height});
     const room = structuredClone(fixtures.rooms.mahjong);
     room.discards = Object.fromEntries(room.players.map(p =>
      [p.username, Array.from({length:24}, (_, i) => i % 34)]));
     room.last_discard = {by:'p3',tile:23};
     room.players[0].melds = [{type:'peng',tiles:[4,4,4]}, {type:'angang',tiles:[8,8,8,8]}];
     room.your_hand = [0,1,2,9,10,11,27,27];
     room.tenpai = {waits:[27],remaining:{27:2}};
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
    }
    await setRoom('mahjong');
    assert.equal(await page.locator('.mj-center-seat.active').count(),1);
    assert.ok(await page.locator('.center-bottom').evaluate(e=>e.classList.contains('active')));
    await setRoom('mahjong',{paused:true});
    assert.equal(await page.locator('.mj-center-seat.active').count(),0);
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
  console.log('PASS real engine views, follow/play/hints, single selection, double-click, locks, pause, scroll, timers and 5 responsive viewports');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
