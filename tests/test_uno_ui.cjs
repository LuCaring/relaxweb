// Run: npm run test:browser -- tests/test_uno_ui.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH || chromium.executablePath(),args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.WebSocket=class {static OPEN=1;constructor(){this.readyState=1;}send(){}close(){}addEventListener(){}};});
 await page.goto('http://localhost:8000/game.html');
 await page.evaluate(async()=>{
  window.core=await import('/assets/js/core.js');
  core.state.currentUser={username:'p0',nickname:'我'};
  core.state.socket={readyState:1,send:data=>{window.sent=JSON.parse(data);}};
  core.state.myRoom={room_id:1,game_type:'uno',name:'UNO 好友桌',status:'playing',owner:'p0',blind:1,hand_no:1,to_act:'p0',turn_left:30,direction:1,deck_left:45,active:{c:'r',v:'3',card:{c:'r',v:'3'}},players:Array.from({length:9},(_,i)=>({username:`p${i}`,nickname:['我','月亮','小橘子','晚风','幸运星','River','周末玩家','小熊','可乐'][i],stack:100,in_hand:true,cards:7})),your_hand:[{c:'r',v:'3'},{c:'r',v:'skip'},{c:'r',v:'rev'},{c:'r',v:'d2'},{c:'w',v:'wd4'},{c:'b',v:'7'},{c:'g',v:'2'}],your_options:{draw:true,challenge:[]}};
  core.renderGameView();
 });
 for(const [width,height] of [[1024,768],[1280,800],[1440,900],[1920,1080],[390,844],[844,390]]){
  await page.setViewportSize({width,height});
  const geometry=await page.evaluate(()=>{
   const seats=[...document.querySelectorAll('.uno-seat')].map(e=>e.getBoundingClientRect());
   return {overflow:document.documentElement.scrollWidth>innerWidth,overlap:seats.some((a,i)=>seats.slice(i+1).some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top))};
  });
  if(process.env.UNO_SCREENSHOT) await page.screenshot({path:`${process.env.UNO_SCREENSHOT}.${width}.png`,fullPage:true});
  assert.equal(geometry.overflow,false,`${width}px overflow`);
  assert.equal(geometry.overlap,false,`${width}px seat overlap`);
 }
 await page.setViewportSize({width:1440,height:900});
 await page.waitForSelector('#desktopRoomChat');
 if(process.env.UNO_SCREENSHOT) await page.screenshot({path:process.env.UNO_SCREENSHOT,type:'png',fullPage:true});
 // Card buttons support keyboard and wild colour selection.
 await page.getByRole('button',{name:'万能 +4',exact:true}).click();
 await page.getByRole('button',{name:'改为蓝色',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'poker_action',action:'play',card:4,color:'b'});
 // Resident UNO button: always in the dock, inert unless a callout is pending.
 assert.ok(await page.locator('.uno-fab').count()>0);
 await page.locator('.uno-fab').click();
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'poker_action',action:'play',card:4,color:'b'});
 await page.evaluate(()=>{
  core.state.myRoom.players[0].uno=true;core.state.myRoom.players[0].cards=1;
  core.state.myRoom.your_options={draw:false,pass:false,uno:true,challenge:[]};
  core.renderGameView();
 });
 assert.equal(await page.locator('.uno-seat.me .uno-challenge').count(),0);
 assert.ok(await page.locator('.uno-fab.pending').count()>0);
 // 呼吸缩放动画会让 Playwright 的稳定性检查永不通过，真实点击不受影响，这里 force
 await page.locator('.uno-fab').click({force:true});
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'poker_action',action:'uno'});
 // Active seat signals its turn by pulsing the seat colour, not an outline.
 const pulseAnimation=await page.locator('.uno-seat.active').evaluate(e=>getComputedStyle(e).animationName);
 assert.ok(pulseAnimation.includes('unoSeatPulse'),`animationName=${pulseAnimation}`);
 // Review cards render on light surfaces: reveal names must stay readable.
 const revealColor=await page.evaluate(async()=>{
  const {gameView}=await import('/assets/js/registry.js');
  const host=document.createElement('div');
  host.className='game-card-page';
  document.getElementById('gameMain').append(host);
  host.append(gameView('uno').renderReview({winner:'p1',payouts:{p2:10},penalties:{p2:2},cards:{p1:[],p2:[{c:'r',v:'5'}]}}));
  return getComputedStyle(host.querySelector('.uno-reveal-name')).color;
 });
 assert.equal(revealColor,'rgb(110, 110, 115)',revealColor);
 await page.evaluate(()=>{
  core.state.myRoom.your_options={draw:false,uno:false,challenge:['p1']};
  core.state.myRoom.to_act='p2';
  core.state.myRoom.players[1].uno=true;core.state.myRoom.players[1].cards=1;
  core.renderGameView();
 });
 await page.getByRole('button',{name:'质疑 月亮 漏喊 UNO，罚摸两张',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.sent),{type:'poker_action',action:'challenge_uno',target:'p1'});
 // New server event animates once, local rerenders do not replay it.
 for(const [id,value] of ['rev','skip','d2','wd4','wild'].entries()){
  await page.evaluate(({id,value})=>{
   core.state.myRoom.direction=value==='rev'?-1:1;
   core.state.myRoom.action_event={id:id+1,kind:'play',username:'p1',card:{c:value==='wd4'||value==='wild'?'w':'r',v:value},target:'p2',count:value==='d2'?2:value==='wd4'?4:0};
   core.renderGameView();
  },{id,value});
  await page.locator(`.effect-${value}`).waitFor();
  assert.ok(await page.locator('.uno-flight').count()>0);
  await page.evaluate(()=>core.renderGameView());
  assert.equal(await page.locator(`.effect-${value}`).count(),0);
 }
 assert.equal(await page.locator('.uno-direction').innerText().then(t=>t.includes('顺时针')),true);
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.evaluate(()=>{core.state.myRoom.action_event={id:9,kind:'challenge',username:'p0',target:'p1',count:2};core.renderGameView();});
 await page.locator('.effect-challenge').waitFor();
 assert.equal(await page.locator('.uno-flight').count(),0);
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.evaluate(()=>{core.state.myRoom.paused=true;core.renderGameView();});
 assert.equal(await page.locator('.uno-challenge:enabled').count(),0);
 assert.equal(await page.locator('.uno-direction-arrow').evaluate(e=>getComputedStyle(e).animationPlayState),'paused');
 assert.deepEqual(errors,[]);
 console.log('PASS UNO layout, keyboard card controls, wild selection, challenge, special-card effects, event deduplication, reduced motion and pause');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
