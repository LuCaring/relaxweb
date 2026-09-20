const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async()=>{
 const browser = await chromium.launch({headless:true,
  executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome',args:['--no-sandbox']});
 try {
  const page = await browser.newPage({viewport:{width:1024,height:768},reducedMotion:'reduce'});
  await page.addInitScript(()=>{window.WebSocket=class{static OPEN=1;constructor(){this.readyState=1;}send(){}addEventListener(){}};});
  await page.goto(process.env.TEST_BASE_URL || 'http://localhost:8000/game.html');
  await page.evaluate(async()=>{window.openMiningGame=(await import('/assets/js/estate/mining.js')).openMiningGame;});

  async function geometry(revealed) {
   return page.evaluate(async revealed=>{
    document.body.classList.add('estate-active');
    const root=document.createElement('section'); root.className='estate-root';
    document.getElementById('gameMain').replaceChildren(root);
    window.openMiningGame(root,{
     run_id:'layout-test',mine_level:1,mine_name:'浅层矿脉',strikes_left:5,size:5,loot:{},
     revealed:revealed?[0]:[],revealed_cells:revealed?{'0':'stone'}:{},
    });
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const rect=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height].map(n=>Math.round(n*100)/100);};
    return {
     grid:rect(root.querySelector('.mine-grid')),
     cells:[...root.querySelectorAll('.mine-cell')].map(rect),
    };
   },revealed);
  }

  for(const [width,height] of [[390,844],[1024,768]]) {
   await page.setViewportSize({width,height});
   const before=await geometry(false);
   const after=await geometry(true);
   assert.deepEqual(after,before,`${width}px mining grid must not move or resize after revealing ore`);
  }
  console.log('PASS mining grid geometry stays fixed before and after revealing ore');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
