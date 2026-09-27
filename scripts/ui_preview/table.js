// Reuse exactly the entry module referenced by game.html, including its cache key.
await import(document.querySelector('script[type="module"][src*="assets/js/main.js"]').src);
const core = await import("/assets/js/core.js");
const params = new URLSearchParams(location.search);
const [fixtures, spectators, waitingViews] = await Promise.all([
  fetch("/__preview/fixtures").then(response => response.json()),
  fetch("/__preview/spectators").then(response => response.json()),
  fetch("/__preview/waiting").then(response => response.json()),
]);
const game = Object.hasOwn(fixtures, params.get("game")) ? params.get("game") : "guandan";
const spectating = params.get("perspective") === "spectator";
const scenes = spectating ? spectators[game] : fixtures[game];
const scene = Object.hasOwn(scenes, params.get("scene")) ? params.get("scene") : "normal";
const players = Object.hasOwn(waitingViews[game], params.get("players")) ? params.get("players") : "1";
let watched = spectating && Object.hasOwn(scenes[scene], params.get("watch")) ? params.get("watch") : "p0";
const snapshot = () => spectating ? scenes[scene][watched]
  : scene === "waiting" ? waitingViews[game][players] : scenes[scene];
const me = spectating ? {username: "preview-watcher", nickname: "本地观众"}
  : snapshot().players.find(player => player.username === "p0");
core.state.currentUser = {...me, coins: 10000};
core.renderIdentity();

function feedback(text) {
  window.parent.postMessage({type: "preview-feedback", text}, location.origin);
  console.info("[UI preview]", text);
}
function render(room = structuredClone(snapshot())) {
  core.handleServerMessage({...room, type: "game_update"});
}
function syncPerspective() {
  params.set("game", game);
  params.set("scene", scene);
  params.set("players", players);
  params.set("perspective", spectating ? "spectator" : "player");
  params.set("watch", watched);
  history.replaceState(null, "", `?${params}`);
  document.documentElement.dataset.previewWatch = watched;
  if (spectating) window.parent.postMessage({type: "preview-watch", watch: watched}, location.origin);
}
function chat(player, text, spectator = false) {
  core.handleServerMessage({type: "room_chat", room_id: snapshot().room_id,
    username: player.username, nickname: player.nickname, text, time: "12:30", spectator});
}
window.addEventListener("preview-send", ({detail: message}) => {
  if (message.type === "room_chat") {
    chat(me, message.text, spectating);
  } else if (message.type === "watch_player" && spectating && Object.hasOwn(scenes[scene], message.username)) {
    watched = message.username;
    render();
    syncPerspective();
    feedback(`本地观战 · 正在观看 ${snapshot().players.find(player => player.username === watched).nickname}`);
  } else if (message.type === "leave_room" && spectating) {
    core.handleServerMessage({type: "room_closed", reason: "已退出本地观战；点击预览工具栏「重置场景」可重新进入。"});
    feedback("已退出本地观战，点击「重置场景」可重新进入。");
  } else if (spectating) {
    // Production UI and server both enforce this boundary; the local stub does too.
    if (["poker_action", "hand_continue", "settle_vote", "pause_game", "start_game", "restart_game"].includes(message.type)) {
      feedback("观战为只读视角，未执行对局操作。");
    }
  } else if (message.type === "pause_game") {
    render({...core.state.myRoom, paused: message.paused});
  } else if (["start_game", "restart_game", "leave_room"].includes(message.type)) {
    render(structuredClone(fixtures[game].normal));
  } else if (message.type === "poker_action") {
    feedback(`已捕获操作（不推进对局）：${JSON.stringify(message)}`);
    // This preview does not advance the server turn; release only the local demo lock.
    render(structuredClone(core.state.myRoom));
    if (game === "holdem" && params.get("holdemView") === "remastered") {
      document.dispatchEvent(new Event("gameactionerror"));
    }
  }
});
window.addEventListener("message", event => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data.type === "preview-bubbles") {
    core.state.myRoom?.players.forEach((player, index) => chat(player, [
      "这张牌先留着，等下一轮看看大家怎么出。", "短消息",
      "AReallyLongUnbrokenMessageToCheckWrapping1234567890", "这局打得不错，我们慢慢来，下一轮再看看。",
    ][index % 4]));
  }
  if (event.data.type === "preview-demo-hand" && game === "holdem" && scene !== "waiting" && !spectating) {
    void demoHand();
  }
});
let demoGeneration = 0;
async function demoHand() {
  const generation = ++demoGeneration;
  const demoHandNo = (fixtures.holdem.normal.hand_no || 1) + generation;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const base = structuredClone(fixtures.holdem.normal);
  const board = [{r:10,s:0},{r:11,s:1},{r:12,s:2},{r:13,s:3},{r:14,s:0}];
  const commitments = [
    [0, 1, 2, 0],
    [40, 40, 40, 40],
    [90, 90, 90, 90],
    [130, 130, 130, 130],
    [170, 170, 170, 170],
  ];
  const steps = [
    {stage: "preflop", board: [], pot: 3, last_action: {nickname: "荷官", text: "正在发牌"}},
    {stage: "preflop", board: [], pot: 160, last_action: {nickname: "清风", text: "四人本手各入池 40"}},
    {stage: "flop", board: board.slice(0,3), pot: 360, last_action: {nickname: "荷官", text: "翻开三张公共牌；四人各再入池 50"}},
    {stage: "turn", board: board.slice(0,4), pot: 520, last_action: {nickname: "荷官", text: "翻开转牌；四人各再入池 40"}},
    {stage: "river", board, pot: 680, last_action: {nickname: "荷官", text: "翻开河牌；四人各再入池 40"}},
  ];
  for (const [index, step] of steps.entries()) {
    if (generation !== demoGeneration) return;
    const room = structuredClone(base);
    Object.assign(room, step);
    room.hand_no = demoHandNo;
    room.to_act = index === 0 ? "p0" : "p1";
    room.your_options = index === 0 ? base.your_options : null;
    room.players = room.players.map((player, i) => ({...player,
      stack: 200 - commitments[index][i], hand_bet: commitments[index][i],
      bet: index < 2 ? commitments[index][i] : index === 2 ? 50 : 40,
    }));
    render(room);
    await pause(index === 0 ? 1100 : 1350);
  }
  if (generation !== demoGeneration) return;
  const result = structuredClone(base);
  Object.assign(result, {stage: "showdown", board, pot: 680, to_act: null, your_options: null,
    hand_no: demoHandNo, last_action: {nickname: "荷官", text: "本手结算"}});
  result.players = result.players.map((player, i) => ({...player,
    stack: i === 0 ? 710 : 30, hand_bet: 0, bet: 0, folded: i !== 0,
  }));
  result.result = {pot: 680, board, payouts: {p0: 680}, hands: result.players.map((player, i) => ({
    username: player.username, nickname: player.nickname, cards: i === 0 ? result.your_hole : [],
    hand_name: i === 0 ? "顺子" : "未摊牌", folded: i !== 0, committed: 170,
  }))};
  render(result);
  feedback("本手演示完成。可再次点击「演示一手」。");
}
render();
syncPerspective();
feedback(spectating ? "本地观战样例已加载，可更换玩家、查看手牌并测试观战聊天；对局操作只读。"
  : scene === "waiting" ? `等待界面已加载：${players} 人入座。可在工具栏切换人数和屏宽。`
    : "本地布局样例已加载，可选牌和聊天；出牌等操作仅记录，不推进完整对局。");
document.documentElement.dataset.previewReady = game;
document.documentElement.dataset.previewScene = scene;
document.documentElement.dataset.previewPerspective = spectating ? "spectator" : "player";
document.documentElement.dataset.previewPlayers = players;
