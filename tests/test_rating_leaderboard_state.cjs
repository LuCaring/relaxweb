// Dependency-free request-state tests: node --experimental-vm-modules tests/test_rating_leaderboard_state.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

(async () => {
  const handlers = new Map();
  const events = new Map();
  const timers = new Map();
  const sent = [];
  const state = {currentUser: {username: 'alice'}, myRoom: null, hallPage: 'rankings',
    ratingLeaderboard: null, ratingLeaderboardOffset: 100, ratingLeaderboardRequest: null};
  let timerSequence = 0;
  let renders = 0;
  let connected = true;
  const context = vm.createContext({document: {addEventListener: (name, fn) => events.set(name, fn)},
    window: {setTimeout: (fn, delay) => { assert.equal(delay, 150); timers.set(++timerSequence, fn); return timerSequence; },
      clearTimeout: id => timers.delete(id)}});
  const core = {state, elements: {}, formatClock() {}, formatCoins() {}, ratingBadge() {}, renderIdentity() {},
    renderGameView: () => renders++, send: payload => { if (!connected) return false; sent.push(payload); return true; }};
  const registry = {onMessage: (name, fn) => handlers.set(name, fn), registerView() {}};
  const modules = new Map();
  for (const [name, exports] of [['./core.js', core], ['./registry.js', registry]]) {
    modules.set(name, new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, {context}));
  }
  const rating = new vm.SourceTextModule(readFileSync(join(__dirname, '../assets/js/rating.js'), 'utf8'), {context});
  await rating.link(name => modules.get(name));
  await rating.evaluate();
  const request = () => { events.get('authstatechange')({detail: {user: state.currentUser}}); return sent.at(-1); };
  const response = (req, extra = {}) => handlers.get('rating_leaderboard')({type: 'rating_leaderboard',
    request_id: req.request_id, offset: req.offset, entries: [], self: null, total: 205, ...extra});
  const update = () => handlers.get('rating_update')({username: 'bob', rating: {score: 1200}});
  const flushTimers = () => { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); };

  const first = request();
  assert.equal(first.type, 'get_rating_leaderboard');
  assert.equal(first.offset, 100);
  assert.equal(typeof first.request_id, 'string');
  response(first, {request_id: undefined});
  assert.equal(state.ratingLeaderboard, null);
  response(first, {offset: 0}); // A non-clamped page cannot match a request for page two.
  assert.equal(state.ratingLeaderboard, null);
  const second = request();
  assert.notEqual(second.request_id, first.request_id);
  response(first);
  assert.equal(state.ratingLeaderboard, null);
  response(second);
  assert.equal(state.ratingLeaderboard.offset, 100);
  assert.equal(state.ratingLeaderboardRequest, null);
  const acceptedRenders = renders;
  response(second, {self: {rank: 999}}); // Duplicate responses cannot change accepted data either.
  assert.equal(renders, acceptedRenders);
  assert.equal(state.ratingLeaderboard.self, null);

  const beforeBatch = sent.length;
  for (let i = 0; i < 50; i++) update();
  assert.equal(timers.size, 1);
  assert.equal(sent.length, beforeBatch);
  flushTimers();
  assert.equal(sent.length, beforeBatch + 1);
  assert.equal(sent.at(-1).offset, 100);
  response(sent.at(-1));
  update();
  const current = request(); // Manual/auth refresh consumes a pending batch.
  assert.equal(timers.size, 0);
  response(current, {total: 90, offset: 0});
  assert.equal(state.ratingLeaderboardOffset, 0);

  const leaving = request();
  update();
  state.hallPage = null;
  events.get('gameviewchange')();
  assert.equal(timers.size, 0);
  assert.equal(state.ratingLeaderboardRequest, null);
  const prior = state.ratingLeaderboard;
  response(leaving, {self: {rank: 999}});
  assert.equal(state.ratingLeaderboard, prior);
  state.hallPage = 'rankings';
  const reopened = request();
  response(leaving);
  assert.equal(state.ratingLeaderboardRequest.id, reopened.request_id);
  state.currentUser = null;
  response(reopened);
  assert.equal(state.ratingLeaderboard, prior);
  state.currentUser = {username: 'charlie'};
  response(reopened);
  assert.equal(state.ratingLeaderboard, prior);
  connected = false;
  request();
  assert.equal(state.ratingLeaderboardRequest, null);
  console.log('PASS request correlation, page validation/clamping, debounce, navigation/auth guards and send failure');
})().catch(error => { console.error(error); process.exit(1); });
