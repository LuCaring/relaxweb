// Run: npm run test:browser -- tests/test_desktop.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || chromium.executablePath(), args:['--no-sandbox']});
 const page = await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{ window.WebSocket=class { static OPEN=1; constructor(){this.readyState=1;} send(){} close(){} addEventListener(){} }; });
 await page.goto('http://localhost:8000/game.html');
 await page.evaluate(async()=>{
  const core=await import('/assets/js/core.js'); window.core=core;
  core.state.currentUser={username:'玩家1',nickname:'玩家1'};
  core.state.socket={readyState:1, send: data => {window.sent=JSON.parse(data); window.sendCount=(window.sendCount||0)+1;}};
  core.state.myRoom={room_id:123,name:'周末好友桌',game_type:'holdem',status:'playing',owner:'玩家1',owner_name:'玩家1',blind:5,hand_no:8,stage:'flop',pot:360,to_act:'玩家1',turn_left:30,board:[{r:14,s:0},{r:12,s:1},{r:10,s:2}],your_hole:[{r:14,s:1},{r:13,s:1}],your_options:{check:true,can_raise:true,raise_min:20,raise_max:880},players:Array.from({length:9},(_,i)=>({username:`玩家${i+1}`,nickname:['玩家1','小熊同学','River King','周末来一手','小橘子','All in','幸运星','月亮','最后一张'][i],rating:{score:1000+i*40,tier:'白银',games:10},stack:880-i*20,bet:20,hand_bet:40,in_hand:true,dealer:i===3}))};
  core.renderGameView();
 });

 // Full tables fit without seat overlap on compact and wide desktops.
 for (const [width,height] of [[1024,768],[1280,800],[1440,900],[1920,1080]]) {
  await page.setViewportSize({width,height});
  const geometry = await page.evaluate(()=>{
   const seats=[...document.querySelectorAll('.poker-seats .seat')].map(e=>e.getBoundingClientRect());
   const overlaps=seats.some((a,i)=>seats.slice(i+1).some(b=>a.left<b.right && a.right>b.left && a.top<b.bottom && a.bottom>b.top));
   return {overlaps,overflow:document.documentElement.scrollWidth>innerWidth,dock:document.querySelector('.poker-dock').getBoundingClientRect().bottom<=innerHeight};
  });
  assert.deepEqual(geometry,{overlaps:false,overflow:false,dock:true},`${width}px layout`);
 }
 await page.locator('#roomChatInput').fill('保留这条草稿');
 await page.evaluate(()=>core.renderGameView());
 assert.equal(await page.locator('#roomChatInput').inputValue(),'保留这条草稿');
 assert.equal(await page.locator('#roomChatInput').evaluate(e=>document.activeElement===e),true);
 await page.locator('#roomChatInput').press('Enter');
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'room_chat',text:'保留这条草稿'});
 assert.equal(await page.locator('#roomChatInput').inputValue(),'');
 await page.evaluate(()=>core.handleServerMessage({type:'room_chat_history',room_id:123,messages:Array.from({length:40},(_,i)=>({username:'同桌',text:`消息 ${i}`}))}));
 await page.locator('.room-chat-list').evaluate(e=>e.scrollTop=0);
 await page.evaluate(()=>core.handleServerMessage({type:'room_chat',room_id:123,username:'玩家2',text:'新消息'}));
 assert.equal(await page.locator('.room-chat-list').evaluate(e=>e.scrollTop),0);
 await page.getByRole('button',{name:'½ 底池',exact:true}).click();
 assert.equal(await page.locator('.raise-input').inputValue(),'200');
 await page.locator('.raise-input').fill('999999');
 await page.getByRole('button',{name:'加注到',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'poker_action',action:'raise',raise_to:880});
 assert.equal(await page.locator('.action-bar button:enabled').count(),0);
 // Rejected actions unlock controls; next turn, pause and room exit clear attention.
 await page.evaluate(()=>core.handleServerMessage({type:'game_error',message:'测试拒绝'}));
 await page.getByRole('button',{name:'确定',exact:true}).click();
 assert.ok(await page.locator('.action-bar button:enabled').count()>0);
 await page.evaluate(()=>{core.state.myRoom.paused=true;core.renderGameView();});
 assert.equal(await page.locator('.action-bar').count(),0);
 assert.ok(!(await page.title()).startsWith('轮到你了'));
 await page.evaluate(()=>{core.state.myRoom.paused=false;core.renderGameView();});
 await page.locator('#roomAudioSettingsButton').click();
 const soundEnabled=page.getByRole('checkbox',{name:'音效',exact:true});
 await soundEnabled.uncheck();
 assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('gameAudioSettings')).enabled),false);
 await soundEnabled.check();
 assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('gameAudioSettings')).enabled),true);
 await page.keyboard.press('Escape');
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>!document.getElementById('desktopRoomChat'));
 assert.equal(await page.locator('.desktop-room-chat').count(),0);
 assert.equal(await page.locator('.desktop-turn-notice').isVisible(),false);
 await page.getByRole('button',{name:'💬 聊天',exact:true}).click();
 await page.locator('#roomChatInput').fill('手机草稿');
 await page.setViewportSize({width:1440,height:900});
 await page.waitForSelector('#desktopRoomChat');
 assert.equal(await page.locator('#chatOverlay').count(),0);
 assert.equal(await page.locator('#roomChatInput').count(),1);
 assert.equal(await page.locator('#roomChatInput').inputValue(),'手机草稿');
 await page.evaluate(()=>{core.state.myRoom.turn_left=4;core.renderGameView();});
 assert.equal(await page.locator('.turn-urgent').count(),1);
 await page.evaluate(()=>{core.state.myRoom.turn_left=0;core.renderGameView();});
 assert.equal(await page.locator('.action-bar button:enabled').count(),0);
 assert.ok(!(await page.title()).startsWith('轮到你了'));
 await page.evaluate(()=>{core.state.myRoom.status='waiting';core.renderGameView();});
 assert.equal(await page.locator('.desktop-room-chat').count(),1);
 await page.evaluate(()=>{core.state.myRoom.status='playing';core.state.myRoom.settlement={can_next:true,votes:{},total:9,blind:5};core.renderGameView();});
 assert.equal(await page.locator('.desktop-room-chat').count(),1);
 await page.evaluate(()=>{core.state.myRoom=null;core.renderGameView();});
 assert.equal(await page.locator('.desktop-room-chat').count(),0);
 assert.ok(!(await page.title()).startsWith('轮到你了'));
 assert.deepEqual(errors,[]);
 console.log('PASS desktop geometry, chat persistence/scroll/send, raise bounds/lock/recovery, turn state, sound toggle, mobile resize, lobby/settlement/exit');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
