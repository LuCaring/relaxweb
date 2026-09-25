// 斗地主真实多人端到端：自带 chat/web 服务与临时库，3 个浏览器会话
// 开房→加入→开局→叫分→出牌→结算。Run: npm run test:browser -- tests/test_doudizhu_live.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.PYTHON || path.join(root, '.venv', 'bin', 'python');

const ROOM_NAME = '端到端斗地主';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const shot = name => path.join(root, 'tests', `__dd_${name}.png`);

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitForHttp(url, tries = 60) {
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch { /* 等待服务监听 */ }
    await sleep(200);
  }
  throw new Error(`等待 ${url} 超时`);
}

async function ensureUser(page, webPort, name, token) {
  await page.addInitScript((value) => {
    localStorage.setItem('liveAuthToken', value);
  }, token);
  await page.goto(`http://127.0.0.1:${webPort}/game.html`);
  await page.waitForFunction(
    () => document.querySelector('#userName')?.textContent.trim().length > 0,
    null, { timeout: 15000 });
}

async function gotoGameRooms(page) {
  await page.waitForSelector('.hall-game-card', { timeout: 15000 });
  await page.locator('.hall-game-card', { hasText: '斗地主' }).first().click();
  await page.waitForSelector('.hall-create-button', { timeout: 15000 });
}

async function createRoom(page) {
  await gotoGameRooms(page);
  await page.click('.hall-create-button');
  await page.waitForSelector('.create-room-form', { timeout: 15000 });
  await page.fill('.create-room-form input[placeholder="例如：周末好友桌"]', ROOM_NAME);
  await page.screenshot({ path: shot('create'), fullPage: true });
  await page.click('.create-submit');
  await page.waitForSelector('.waiting-room', { timeout: 15000 });
}

async function joinRoom(page) {
  await gotoGameRooms(page);
  const row = page.locator('.hall-room-row', { hasText: ROOM_NAME }).first();
  await row.waitFor({ timeout: 15000 });
  await row.locator('.hall-join').click();
  await page.waitForSelector('.waiting-room', { timeout: 15000 });
}

/** 在页面里直接点当前可用的动作按钮：提示选牌后立刻出牌，否则不出。 */
async function actOnTurn(page) {
  return page.evaluate(() => {
    const buttons = () => [...document.querySelectorAll('.dd-dock .action-bar button')];
    const hint = buttons().find(b => b.textContent.includes('提示') && !b.disabled);
    if (hint) hint.click();          // 选一组能出的牌，出牌按钮随即变为可用
    const play = buttons().find(b => b.textContent.includes('出牌') && !b.disabled);
    if (play) {
      play.click();
      return 'play';
    }
    const pass = buttons().find(b => b.textContent.includes('不出') && !b.disabled);
    if (pass) {
      pass.click();
      return 'pass';
    }
    return hint ? 'hint-only' : null;
  });
}

/** 在任一会话轮到叫分时点「叫 3 分」，其余点「不叫」；返回是否已定地主。 */
async function driveBidding(pages) {
  for (let step = 0; step < 12; step += 1) {
    for (const page of pages) {
      const decided = await page.evaluate(() => {
        const buttons = [...document.querySelectorAll('.dd-dock .action-bar button')];
        const bid3 = buttons.find(b => b.textContent.includes('叫 3 分') && !b.disabled);
        if (bid3) {
          bid3.click();
          return true;
        }
        const pass = buttons.find(b => b.textContent.includes('不叫') && !b.disabled);
        if (pass) pass.click();
        return false;
      });
      if (decided) return true;
      await sleep(300);
    }
    await sleep(300);
  }
  return false;
}

/** 谁轮到就提示选牌后出牌/不出，直到出现结算。 */
async function playToResult(pages) {
  for (let step = 0; step < 400; step += 1) {
    if (await pages[0].locator('.dd-result').count()) return true;
    let acted = 0;
    for (const page of pages) {
      if (await page.locator('.dd-result').count()) return true;
      const action = await actOnTurn(page);
      if (action) acted += 1;
      await sleep(250);
    }
    if (!acted) await sleep(250);
  }
  return false;
}

(async () => {
  const chatPort = await freePort();
  const webPort = await freePort();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-e2e-'));
  const dbFile = path.join(tmp, 'doudizhu-e2e.db');
  const env = {
    ...process.env,
    LIVE_DB_FILE: dbFile,
    LIVE_CHAT_HOST: '127.0.0.1',
    LIVE_CHAT_PORT: String(chatPort),
    LIVE_WEB_PORT: String(webPort),
  };
  const procs = [];
  const chat = spawn(python, ['chat_server.py'], { cwd: root, env, stdio: ['ignore', 'ignore', 'pipe'] });
  chat.stderr.on('data', chunk => process.stderr.write(chunk));
  procs.push(chat);
  const web = spawn(python, ['deploy/serve.py'], { cwd: root, env, stdio: ['ignore', 'ignore', 'pipe'] });
  web.stderr.on('data', chunk => process.stderr.write(chunk));
  procs.push(web);

  let browser = null;
  const errors = [];
  try {
    await waitForHttp(`http://127.0.0.1:${webPort}/game.html`);
    await sleep(400);

    // 造 3 个账号并签发会话 token（浏览器经 localStorage 恢复会话）
    const bootstrap = `
import sys, json
sys.path.insert(0, ${JSON.stringify(root)})
from server.schema import init_db
import server.database as storage
from server.accounts import Accounts
init_db(storage.database)
accounts = Accounts(storage.database)
names = ["dd1", "dd2", "dd3"]
for name in names:
    with storage.database() as conn, conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?, '', '', 0, 1000)",
            (name,))
print(json.dumps({n: accounts.create_session(n) for n in names}))
`;
    const { stdout } = spawnSync(python, ['-c', bootstrap], { cwd: root, env, encoding: 'utf8' });
    const tokens = JSON.parse(stdout.trim().split('\n').pop());

    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.CHROME_PATH || chromium.executablePath(),
    });
    const pages = [];
    for (const name of ['dd1', 'dd2', 'dd3']) {
      const context = await browser.newContext({ viewport: { width: 1000, height: 1400 } });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => {
        const where = String(error.stack || '').split('\n').slice(0, 3).join(' | ');
        errors.push(`${name}: ${error.message} @ ${where}`);
      });
      await ensureUser(page, webPort, name, tokens[name]);
      pages.push(page);
    }
    const [p1, p2, p3] = pages;

    await createRoom(p1);
    console.log('房间已创建');
    await joinRoom(p2);
    await joinRoom(p3);
    console.log('dd2 / dd3 已加入');

    await p1.waitForFunction(
      () => document.querySelector('.waiting-start') && !document.querySelector('.waiting-start').disabled,
      null, { timeout: 15000 });
    await p1.screenshot({ path: shot('waiting'), fullPage: true });
    await p1.click('.waiting-start');
    await p1.waitForSelector('.dd-table', { timeout: 15000 });
    console.log('开局进入叫分');

    const decided = await driveBidding(pages);
    assert.ok(decided, '叫分未决出地主');
    await sleep(500);
    await p1.waitForSelector('.dd-hand-card', { timeout: 15000 });
    await p1.screenshot({ path: shot('table'), fullPage: true });
    console.log('地主已定，开始出牌');

    const finished = await playToResult(pages);
    await sleep(600);
    await p1.screenshot({ path: shot('result'), fullPage: true });
    console.log(finished ? '对局完成，出现结算' : '对局超时未完成');
    assert.ok(finished, '对局未完成');
    const resultShown = await p1.evaluate(() => {
      const banner = document.querySelector('.dd-match-banner');
      return banner ? banner.textContent : '';
    });
    assert.ok(resultShown.includes('获胜'), `结算横幅异常：${resultShown}`);
    console.log(`结算横幅：${resultShown}`);
  } finally {
    if (errors.length) console.error('页面错误:', [...new Set(errors)].slice(0, 5).join('\n'));
    for (const proc of procs) proc.kill('SIGTERM');
    if (browser) await browser.close().catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
