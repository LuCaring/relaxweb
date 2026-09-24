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
    // A fresh update releases production submission locks for the next UI test.
    render(structuredClone(core.state.myRoom));
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
});
render();
syncPerspective();
feedback(spectating ? "本地观战样例已加载，可更换玩家、查看手牌并测试观战聊天；对局操作只读。"
  : scene === "waiting" ? `等待界面已加载：${players} 人入座。可在工具栏切换人数和屏宽。`
    : "本地布局样例已加载，可选牌和聊天；出牌等操作仅记录，不推进完整对局。");
document.documentElement.dataset.previewReady = game;
document.documentElement.dataset.previewScene = scene;
document.documentElement.dataset.previewPerspective = spectating ? "spectator" : "player";
document.documentElement.dataset.previewPlayers = players;
