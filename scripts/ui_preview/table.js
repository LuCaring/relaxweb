// Reuse exactly the entry module referenced by game.html, including its cache key.
await import(document.querySelector('script[type="module"][src*="assets/js/main.js"]').src);
const core = await import("/assets/js/core.js");
const params = new URLSearchParams(location.search);
const fixtures = await (await fetch("/__preview/fixtures")).json();
const game = Object.hasOwn(fixtures, params.get("game")) ? params.get("game") : "guandan";
const scene = Object.hasOwn(fixtures[game], params.get("scene")) ? params.get("scene") : "normal";
const snapshot = fixtures[game][scene];
const me = snapshot.players.find(player => player.username === "p0");
core.state.currentUser = {...me, coins: 10000};
core.renderIdentity();

function feedback(text) {
  window.parent.postMessage({type: "preview-feedback", text}, location.origin);
  console.info("[UI preview]", text);
}
function render(room = structuredClone(snapshot)) {
  core.handleServerMessage({...room, type: "game_update"});
}
function chat(player, text) {
  core.handleServerMessage({type: "room_chat", room_id: snapshot.room_id,
    username: player.username, nickname: player.nickname, text, time: "12:30"});
}
window.addEventListener("preview-send", ({detail: message}) => {
  if (message.type === "room_chat") {
    chat(me, message.text);
  } else if (message.type === "pause_game") {
    render({...core.state.myRoom, paused: message.paused});
  } else if (["start_game", "restart_game", "leave_room"].includes(message.type)) {
    render(structuredClone(fixtures[game]["normal"]));
  } else if (message.type === "poker_action") {
    feedback(`已捕获操作（不推进对局）：${JSON.stringify(message)}`);
    // A fresh update releases production submission locks for the next UI test.
    render(structuredClone(core.state.myRoom));
  }
});
window.addEventListener("message", event => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data.type === "preview-bubbles") {
    core.state.myRoom.players.forEach((player, index) => chat(player, [
      "这张牌先留着，等下一轮看看大家怎么出。", "短消息",
      "AReallyLongUnbrokenMessageToCheckWrapping1234567890", "这局打得不错，我们慢慢来，下一轮再看看。",
    ][index % 4]));
  }
});
render();
feedback("本地布局样例已加载，可选牌和聊天；出牌等操作仅记录，不推进完整对局。");
document.documentElement.dataset.previewReady = game;
