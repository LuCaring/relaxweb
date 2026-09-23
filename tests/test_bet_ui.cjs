// 竞猜封盘 UI 回归：本地 deploy/serve.py + Playwright（mock WebSocket，不依赖后端）。
// 覆盖：创建表单的封盘设置、进行中的倒计时与参与表单、封盘后的禁投提示与发起者按钮。
// 运行：node tests/test_bet_ui.cjs（需先 bash live-test/restart_all.sh）
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_PATH || chromium.executablePath(), args: ['--no-sandbox']});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.sent = [];
    window.sockets = [];
    window.WebSocket = class {
      static OPEN = 1;
      constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
      send(data) { sent.push(JSON.parse(data)); }
      addEventListener(type, handler) { this.listeners[type] = handler; }
      close() {}
    };
    window.MediaMTXWebRTCReader = class { close() {} };
  });
  await page.goto('http://localhost:8000/index.html');
  const deliver = data => page.evaluate(data => sockets[0].listeners.message({data: JSON.stringify(data)}), data);
  await deliver({type: 'resume_success', username: 'alice', nickname: 'Alice', coins: 1000});

  const now = Math.floor(Date.now() / 1000);

  // 1. 无竞猜：创建视图含「自动封盘」下拉，默认不封盘
  await page.locator('#betStat').click();
  await assert.ok(await page.locator('#betModalBody select.login-input').isVisible());
  const options = await page.locator('#betModalBody select.login-input option').allTextContents();
  assert.equal(options[0], '不封盘');
  assert.ok(options.includes('5 分钟后'));
  await page.locator('#betModalBody input.login-input').first().fill('测试问题');
  await page.locator('#betModalBody .login-submit').click();
  const created = await page.evaluate(() => sent.filter(m => m.type === 'create_bet').at(-1));
  assert.equal(created.close_minutes, 0);

  // 2. 进行中的竞猜（5 分钟后封盘）：展示倒计时与参与表单
  await deliver({type: 'bet_update', bet: {id: 1, question: '能吃到火锅吗', options: ['能', '不能'],
    creator: 'alice', created_at: now - 30, entries: [], totals: [0, 0], pot: 0,
    close_delay: 5, closed_at: null, closes_at: now + 270}});
  await page.waitForTimeout(50);
  const status = await page.locator('#betCloseStatus').textContent();
  assert.match(status, /距封盘 4:/);
  assert.equal(await page.locator('.bet-closed-note').count(), 0);
  assert.ok(await page.locator('.bet-input-row .login-input').first().isVisible());

  // 3. 封盘后：倒计时变已封盘，未参与者看到禁投提示；发起者看到结账提示、无「立即封盘」
  await deliver({type: 'bet_update', bet: {id: 1, question: '能吃到火锅吗', options: ['能', '不能'],
    creator: 'alice', created_at: now - 30, entries: [{username: 'bob', option_index: 0, amount: 20}],
    totals: [20, 0], pot: 20, close_delay: 5, closed_at: now - 1, closes_at: now + 240}});
  await page.waitForTimeout(50);
  assert.match(await page.locator('#betCloseStatus').textContent(), /已封盘/);
  assert.match(await page.locator('.bet-closed-note').first().textContent(), /无法参与/);
  assert.equal(await page.getByText('立即封盘').count(), 0);
  assert.equal(await page.getByText('流局（退还全部投注）').count(), 1);
  assert.match(await page.locator('#betStatTitle').textContent(), /已封盘/);

  // 4. 未封盘时发起者有「立即封盘」按钮，点击发送 close_bet
  await deliver({type: 'bet_update', bet: {id: 2, question: '今晚下雨吗', options: ['下', '不下'],
    creator: 'alice', created_at: now, entries: [], totals: [0, 0], pot: 0,
    close_delay: 0, closed_at: null, closes_at: null}});
  await page.waitForTimeout(50);
  assert.equal(await page.locator('#betCloseStatus').count(), 0);
  assert.doesNotMatch(await page.locator('#betStatTitle').textContent(), /已封盘/);
  await page.getByText('立即封盘').click();
  await page.locator('.live-dialog-confirm').click();

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('PASS bet close UI: create select default 不封盘, countdown, closed notice, manual close button');
})().catch(error => { console.error(error); process.exit(1); });