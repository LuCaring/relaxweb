// Real DOM/CSS + mocked WebSocket protocol. Coordinate an existing HTTP server before running.
// RATING_TEST_URL defaults to http://localhost:8000; CHROME_PATH optionally selects a browser.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { existsSync } = require('node:fs');

const baseURL = process.env.RATING_TEST_URL || 'http://localhost:8000';
const executablePath = process.env.CHROME_PATH || [
  '/usr/bin/google-chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(path => existsSync(path));

function stats(overrides = {}) {
  const raw = {hands: 200, wins: 80, folds: 50, manual_folds: 30, timeout_folds: 10, leave_folds: 10,
    vpip_hands: 70, pfr_hands: 40, flop_hands: 100, showdown_hands: 30, showdown_wins: 18,
    aggressive_actions: 90, call_actions: 30, net_profit: 1000, score_delta: 60, net_bb: 120, ...overrides};
  const ratio = (n, d) => d ? n / d : null;
  return {...raw, win_rate: ratio(raw.wins, raw.hands), fold_rate: ratio(raw.folds, raw.hands),
    score_per_hand: ratio(raw.score_delta, raw.hands), profit_per_hand: ratio(raw.net_profit, raw.hands),
    bb_per_100: raw.hands ? raw.net_bb / raw.hands * 100 : null,
    vpip: ratio(raw.vpip_hands, raw.hands), pfr: ratio(raw.pfr_hands, raw.hands),
    flop_rate: ratio(raw.flop_hands, raw.hands), wtsd: ratio(raw.showdown_hands, raw.flop_hands),
    showdown_win_rate: ratio(raw.showdown_wins, raw.showdown_hands), af: ratio(raw.aggressive_actions, raw.call_actions),
    af_no_calls: raw.aggressive_actions > 0 && raw.call_actions === 0, small_sample: raw.hands < 100};
}

(async () => {
  const browser = await chromium.launch({headless: true, executablePath, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, hasTouch: true});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error' && message.text().includes('无法解析服务器消息')) errors.push(message.text()); });
    await page.addInitScript(() => {
      window.sent = [];
      window.sockets = [];
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState = 1; this.listeners = {}; sockets.push(this); }
        send(data) { sent.push(JSON.parse(data)); }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        close() {}
      };
    });
    await page.goto(new URL('/game.html', baseURL).href);
    const deliver = data => page.evaluate(data => sockets.at(-1).listeners.message({data: JSON.stringify(data)}), data);
    const lastRequest = async offset => {
      const request = await page.evaluate(() => sent.filter(m => m.type === 'get_rating_leaderboard').at(-1));
      assert.equal(request?.offset, offset);
      assert.equal(typeof request.request_id, 'string');
      assert.ok(request.request_id.length);
      return request;
    };
    const respond = (board, request) => deliver({...board, request_id: request.request_id});
    const clearSent = () => page.evaluate(() => { sent.length = 0; });
    const requestCount = () => page.evaluate(() => sent.filter(m => m.type === 'get_rating_leaderboard').length);
    const previous = () => page.getByRole('button', {name: '← 上一页', exact: true}).first();
    const next = () => page.getByRole('button', {name: '下一页 →', exact: true}).first();
    const refresh = () => page.getByRole('button', {name: '刷新排行', exact: true}).click();
    const rows = page.locator('.rating-ranking-row');
    const metric = (row, key) => rows.nth(row).locator(`[data-metric="${key}"] .rating-metric-value`);
    const tiers = ['青铜', '白银', '黄金', '铂金', '钻石', '大师'].map((tier, i) => ({
      tier, score: i ? 400 + i * 400 : 0, floor: i ? 400 + i * 400 : 0,
      next_score: i === 5 ? null : 800 + i * 400, games: 0,
    }));
    const user = {type: 'resume_success', username: 'alice', nickname: '爱丽丝',
      coins: 900, rating: {...tiers[1], score: 1000, games: 200}};
    const entries = Array.from({length: 100}, (_, i) => ({username: `player${i}`,
      nickname: i === 2 ? '<img src=x onerror=alert(1)>' : i === 3 ? '一个很长很长很长很长很长很长的玩家昵称' : `玩家 ${i + 1}`,
      rank: i < 2 ? 1 : i + 1, rating: {...tiers[Math.max(0, 5 - i)], games: 200 + i}, holdem_stats: stats()}));
    entries[1].rating = entries[0].rating;
    entries[1].holdem_stats = stats({hands: 20, wins: 8, folds: 5, manual_folds: 3, timeout_folds: 1,
      leave_folds: 1, vpip_hands: 7, pfr_hands: 4, flop_hands: 10, showdown_hands: 3,
      showdown_wins: 2, aggressive_actions: 9, call_actions: 0});
    delete entries[2].holdem_stats; // Old persisted entries have no statistics yet.
    entries[3].holdem_stats = stats(Object.fromEntries(Object.keys(stats()).map(key => [key, 0])));
    entries[4].holdem_stats = stats({hands: 5, wins: 0, folds: 5, manual_folds: 0, timeout_folds: 2,
      leave_folds: 3, vpip_hands: 0, pfr_hands: 0, flop_hands: 0, showdown_hands: 0,
      showdown_wins: 0, aggressive_actions: 0, call_actions: 0, net_profit: -25, score_delta: -10, net_bb: -5});
    const own = {username: 'alice', nickname: '爱丽丝', rank: 106, rating: user.rating, holdem_stats: stats()};
    const board = {type: 'rating_leaderboard', entries, offset: 0, limit: 100, total: 108, tiers,
      stats_since: 1700000000, self: own};
    const tail = Array.from({length: 8}, (_, i) => ({username: `tail${i}`, nickname: `末页玩家 ${i}`,
      rank: i === 0 ? 100 : 101 + i, rating: user.rating, holdem_stats: stats()}));
    tail[5] = own;
    const lastPage = {...board, entries: tail, offset: 100};

    await deliver(user);
    await page.getByRole('button', {name: '查看段位排行 →'}).click();
    const initialRequest = await lastRequest(0);
    assert.match(await page.locator('.rating-leaderboard').innerText(), /正在读取排行/);
    await deliver(board); // An uncorrelated push must not resolve a pending request.
    assert.equal(await rows.count(), 0);
    await respond(lastPage, initialRequest); // Matching ID alone cannot authorize an unrelated page.
    assert.equal(await rows.count(), 0);
    await respond(board, initialRequest);
    assert.equal(await rows.count(), 100);
    assert.match(await page.locator('.rating-own-rank').innerText(), /第 106 名/);
    assert.equal(await page.locator('.rating-own-rank [data-metric="hands"] .rating-metric-value').innerText(), '200 手');
    assert.deepEqual(await page.locator('.rating-place').allTextContents().then(a => a.slice(0, 3)), ['1', '1', '3']);
    assert.equal(await page.locator('.rating-player img').count(), 0);
    assert.match(await page.locator('.rating-player').nth(2).innerText(), /<img src=x/);
    assert.equal(await previous().isDisabled(), true);
    assert.equal(await next().isEnabled(), true);
    assert.match(await page.locator('.rating-page-number').first().innerText(), /第 1 \/ 2 页/);
    assert.match(await page.locator('.rating-stats-scope').innerText(), /2023/);
    assert.match(await page.locator('.rating-stats-scope').innerText(), /登录用户公开.*不改变段位排序/);
    assert.equal(await page.locator('.rating-tier-item').count(), 6);
    const icons = await page.locator('.rating-tier-item .rating-badge').evaluateAll(nodes =>
      nodes.map(node => getComputedStyle(node, '::before').backgroundImage));
    assert.equal(new Set(icons).size, 6);
    for (const icon of icons) {
      const url = icon.match(/url\("(.*)"\)/)[1];
      assert.equal((await page.request.get(url)).status(), 200, url);
    }

    // Public per-player metrics, fractions, sample sizes, units and extra explanations.
    assert.equal(await rows.nth(0).locator('.rating-stats-grid').first().locator('.rating-metric').count(), 5);
    assert.equal(await rows.nth(0).locator('.rating-sample-note').count(), 0);
    for (const [key, expected] of Object.entries({hands: '200 手', win_rate: '40.0%', fold_rate: '25.0%',
      score_per_hand: '+0.30', bb_per_100: '+60.00'})) assert.equal(await metric(0, key).innerText(), expected);
    assert.match(await rows.nth(0).locator('[data-metric="fold_rate"]').innerText(), /50 \/ 200 手.*超时、离桌/);
    assert.equal(await rows.nth(0).locator('.rating-stats-more').evaluate(el => el.open), false);
    await rows.nth(0).locator('summary').click();
    for (const [key, expected] of Object.entries({net_profit: '+1000.00', profit_per_hand: '+5.00', vpip: '35.0%',
      pfr: '20.0%', flop_rate: '50.0%', wtsd: '30.0%', showdown_win_rate: '60.0%', af: '3.00'})) {
      assert.equal(await metric(0, key).innerText(), expected);
    }
    assert.match(await rows.nth(0).locator('[data-metric="wtsd"]').innerText(), /摊牌 30 \/ 看翻牌 100 手/);
    assert.match(await rows.nth(0).locator('.rating-stats-more').innerText(), /主动 30 手 · 超时 10 手 · 离桌 10 手/);
    assert.match(await rows.nth(1).innerText(), /小样本/);
    await rows.nth(1).locator('summary').click();
    assert.equal(await metric(1, 'af').innerText(), '∞（无跟注）');
    for (const index of [2, 3]) {
      assert.match(await rows.nth(index).innerText(), /暂无数据/);
      assert.equal(await metric(index, 'hands').innerText(), '0 手');
      assert.equal(await metric(index, 'win_rate').innerText(), '—');
      assert.equal(await metric(index, 'bb_per_100').innerText(), '—');
      await rows.nth(index).locator('summary').click();
      assert.equal(await metric(index, 'af').innerText(), '—');
    }
    await rows.nth(4).locator('summary').click();
    assert.equal(await metric(4, 'win_rate').innerText(), '0.0%');
    assert.equal(await metric(4, 'fold_rate').innerText(), '100.0%');
    assert.equal(await metric(4, 'score_per_hand').innerText(), '-2.00');
    assert.equal(await metric(4, 'wtsd').innerText(), '—');
    assert.equal(await metric(4, 'showdown_win_rate').innerText(), '—');
    assert.equal(await metric(4, 'af').innerText(), '—');
    assert.doesNotMatch(await page.locator('.rating-leaderboard').innerText(), /NaN|Infinity|null|undefined/);

    // Desktop columns and mobile stacked rows, including opened details and long/XSS names.
    for (const [width, height] of [[1440, 900], [320, 568], [390, 844], [844, 390]]) {
      await page.setViewportSize({width, height});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
        `${width}px fits without horizontal overflow`);
      assert.equal(await rows.evaluateAll(rows => rows.every(row => {
        const [rank, player, score, stats] = [...row.children].map(el => el.getBoundingClientRect());
        const separated = (a, b) => a.right <= b.left + 1 || b.right <= a.left + 1
          || a.bottom <= b.top + 1 || b.bottom <= a.top + 1;
        return separated(rank, player) && separated(player, score) && separated(rank, score)
          && stats.top >= Math.max(rank.bottom, player.bottom, score.bottom) - 1;
      })), true, `${width}px identity and statistics do not overlap`);
      assert.equal(await page.locator('.rating-metric:visible').evaluateAll(nodes => nodes.every(node => {
        const r = node.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth + 1;
      })), true, `${width}px metrics remain inside viewport`);
      if (width === 390 && process.env.RANKING_SCREENSHOT) {
        await page.screenshot({path: `${process.env.RANKING_SCREENSHOT}-mobile.png`});
      }
    }
    await page.setViewportSize({width: 1440, height: 900});
    if (process.env.RANKING_SCREENSHOT) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: `${process.env.RANKING_SCREENSHOT}-desktop.png`});
    }

    // Same-page refreshes can complete out of order; only the latest request may apply.
    await refresh();
    const staleRefresh = await lastRequest(0);
    await refresh();
    const newestRefresh = await lastRequest(0);
    assert.notEqual(staleRefresh.request_id, newestRefresh.request_id);
    await respond(board, newestRefresh);
    await respond({...board, self: {...own, rank: 999}}, staleRefresh);
    assert.match(await page.locator('.rating-own-rank').innerText(), /第 106 名/);
    assert.equal(await rows.nth(0).locator('.rating-stats-more').evaluate(el => el.open), true);

    // Page changes never renumber ranks; keep self even when outside the current page.
    await next().click();
    const pageTwoRequest = await lastRequest(100);
    assert.equal(await rows.count(), 0, 'do not display cached entries under a new page number');
    assert.match(await page.locator('.rating-own-rank').innerText(), /第 106 名/);
    await respond(board, newestRefresh);
    assert.equal(await rows.count(), 0);
    await respond(lastPage, pageTwoRequest);
    assert.equal(await rows.count(), 8);
    assert.equal(await page.locator('.rating-place').first().innerText(), '100');
    assert.equal(await page.locator('.rating-ranking-row.is-self').count(), 1);
    assert.equal(await next().isDisabled(), true);
    assert.equal(await previous().isEnabled(), true);
    await refresh();
    const pageTwoRefresh = await lastRequest(100);
    await respond(lastPage, pageTwoRefresh);

    // Fast previous/next clicks while a page is loading must preserve the newest navigation.
    await previous().click();
    const oldPageRequest = await lastRequest(0);
    await next().click();
    const currentPageRequest = await lastRequest(100);
    await respond(board, oldPageRequest);
    assert.match(await page.locator('.rating-page-number').first().innerText(), /第 2 \/ 2 页/);
    await respond(lastPage, currentPageRequest);
    assert.equal(await rows.count(), 8);

    // Bursts of settlements across players produce exactly one refresh on the current page.
    await clearSent();
    await page.evaluate(rating => {
      for (let i = 0; i < 30; i++) sockets.at(-1).listeners.message({data: JSON.stringify({
        type: 'rating_update', username: `player${i}`, rating,
      })});
    }, tiers[5]);
    assert.equal(await requestCount(), 0);
    await page.waitForFunction(() => sent.some(m => m.type === 'get_rating_leaderboard'));
    await page.waitForTimeout(250);
    assert.equal(await requestCount(), 1);
    await respond(lastPage, await lastRequest(100));
    await clearSent();
    const promoted = {...own, rating: {...tiers[5], score: 2500, games: 10}};
    await deliver({type: 'rating_update', username: 'alice', rating: promoted.rating});
    assert.equal(await page.evaluate(() => sent.filter(m => m.type === 'get_rating_history').length), 1);
    assert.match(await page.locator('#dropdownName .rating-badge').textContent(), /大师 2500/);
    await page.waitForFunction(() => sent.some(m => m.type === 'get_rating_leaderboard'));
    await respond({...lastPage, self: promoted, entries: tail.map(entry => entry.username === 'alice' ? promoted : entry)}, await lastRequest(100));
    assert.match(await page.locator('.rating-own-rank').innerText(), /大师 2500/);
    assert.match(await page.locator('.rating-ranking-row.is-self').innerText(), /大师 2500/);
    await clearSent();
    await deliver({type: 'rating_update', username: 'player0', rating: tiers[5]});
    await refresh(); // Explicit refresh also consumes the scheduled batch.
    await page.waitForTimeout(250);
    assert.equal(await requestCount(), 1);
    await respond(lastPage, await lastRequest(100));

    // Exercise an actual socket close/open/resume, preserving the page but changing request ID.
    await refresh();
    const beforeReconnect = await lastRequest(100);
    await clearSent();
    await page.evaluate(() => {
      localStorage.setItem('liveAuthToken', 'test-token');
      sockets.at(-1).listeners.close();
    });
    await page.waitForFunction(() => sockets.length === 2);
    await page.evaluate(() => sockets.at(-1).listeners.open());
    await deliver(user);
    const reconnectRequest = await lastRequest(100);
    assert.equal(await requestCount(), 1);
    assert.notEqual(reconnectRequest.request_id, beforeReconnect.request_id);
    await respond(lastPage, beforeReconnect);
    assert.equal(await rows.count(), 0);
    await respond(lastPage, reconnectRequest);
    assert.equal(await rows.count(), 8);

    // Server may clamp the page after account deletions; accept its offset for the matching ID.
    await refresh();
    const clampedRequest = await lastRequest(100);
    await respond({...board, entries: entries.slice(0, 90), total: 90}, clampedRequest);
    assert.equal(await rows.count(), 90);
    assert.match(await page.locator('.rating-page-number').first().innerText(), /第 1 \/ 1 页/);
    await refresh();
    const afterClamp = await lastRequest(0);
    await respond(board, afterClamp);

    // Late responses and queued settlement refreshes must not resurrect a closed view.
    await refresh();
    const leavingRequest = await lastRequest(0);
    await clearSent();
    await deliver({type: 'rating_update', username: 'player0', rating: tiers[5]});
    await page.getByRole('button', {name: '← 游戏厅'}).click();
    await respond(board, leavingRequest);
    await page.waitForTimeout(250);
    assert.equal(await requestCount(), 0);
    assert.equal(await page.locator('.rating-leaderboard').count(), 0);
    await page.getByRole('button', {name: '查看段位排行 →'}).click();
    const emptyRequest = await lastRequest(0);
    await respond({...board, entries: [], self: null, total: 0}, emptyRequest);
    assert.match(await page.locator('.rating-leaderboard').innerText(), /暂无排行数据/);
    assert.equal(await next().isDisabled(), true);
    assert.equal(await previous().isDisabled(), true);
    await refresh();
    const logoutRequest = await lastRequest(0);
    await deliver({type: 'auth_expired'});
    await respond(board, logoutRequest);
    assert.equal(await page.locator('.rating-leaderboard').count(), 0);
    assert.equal(await page.locator('.game-entry-card').count(), 1);
    await deliver(user);
    await page.getByRole('button', {name: '查看段位排行 →'}).click();
    await lastRequest(0);
    await respond(board, logoutRequest);
    assert.equal(await rows.count(), 0, 'previous login response cannot overwrite a new login');

    // The live-room account menu keeps its existing tier emblems.
    await page.goto(new URL('/index.html', baseURL).href);
    await page.evaluate(() => { window.MediaMTXWebRTCReader = class { close() {} }; });
    await deliver(user);
    await page.locator('#userChip').click();
    const badge = page.locator('#dropdownName .rating-badge');
    assert.equal(await badge.getAttribute('data-tier'), '白银');
    assert.match(await badge.evaluate(el => getComputedStyle(el, '::before').backgroundImage), /silver\.svg/);
    await deliver({type: 'rating_update', username: 'alice', rating: tiers[5]});
    assert.equal(await badge.getAttribute('data-tier'), '大师');
    assert.deepEqual(errors, []);
    console.log('PASS public holdem metrics, samples/zero denominators, details, ties/global ranks, 100-row pagination, stale responses, debounce, refresh/reconnect/logout, XSS, tier emblems and responsive layouts');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
