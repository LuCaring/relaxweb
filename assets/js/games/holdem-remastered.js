/* Alternate visual client for the same holdem room and action protocol. The Phaser
   bundle is loaded only when this table is opened. */
import { elements, formatCoins, selfUsername, send, state } from "../core.js";
import { registerGame, registeredGameView } from "../registry.js";
import { reapplySeatBubbles } from "../room-chat.js";

let table = null;
let modulePromise = null;
let loadingRoot = null;
let root = null;
let pendingRoom = null;

function dispose() {
  table?.destroy();
  table = null;
  root = null;
  pendingRoom = null;
  loadingRoot = null;
}

function showLoadError() {
  if (!root?.isConnected) return;
  const error = document.createElement("div");
  error.className = "poker-remastered-load-error";
  error.setAttribute("role", "alert");
  error.textContent = "动态牌桌加载失败，请重试。";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "重新加载";
  retry.addEventListener("click", () => { error.remove(); load(); });
  error.append(retry);
  root.append(error);
}

function load() {
  if (!root || loadingRoot === root) return;
  const target = root;
  loadingRoot = target;
  if (!modulePromise) modulePromise = import("../../build/holdem-remastered.js").catch((error) => {
    modulePromise = null;
    throw error;
  });
  modulePromise.then(({ HoldemTable }) => {
    if (loadingRoot === target) loadingRoot = null;
    if (root !== target || !target.isConnected || !pendingRoom || state.myRoom?.game_type !== "holdem"
        || localStorage.getItem("relaxweb:holdem-view") !== "remastered") return;
    target.querySelector(".poker-remastered-loading")?.remove();
    table = new HoldemTable(target, {
      send, self: selfUsername, coins: formatCoins,
      onSeats: reapplySeatBubbles,
    });
    table.update(pendingRoom);
  }).catch(() => {
    if (root === target) { loadingRoot = null; showLoadError(); }
  });
}

function renderTable() {
  const room = state.myRoom;
  if (!room) return;
  if (table && root?.isConnected && root.dataset.roomId === String(room.room_id)) {
    pendingRoom = room;
    table.update(room);
    return;
  }
  if (table) dispose();
  pendingRoom = room;
  if (!root?.isConnected) {
    elements.gameMain.replaceChildren();
    root = document.createElement("section");
    root.className = "poker-remastered";
    root.dataset.roomId = String(room.room_id);
    root.setAttribute("aria-label", "德扑重制版牌桌");
    elements.gameMain.append(root);
    const loadingNote = document.createElement("div");
    loadingNote.className = "poker-remastered-loading";
    loadingNote.textContent = "正在展开牌桌…";
    root.append(loadingNote);
  }
  load();
}

document.addEventListener("gameviewchange", () => {
  if (!state.myRoom || state.myRoom.game_type !== "holdem" || state.myRoom.status !== "playing" || state.myRoom.settlement
      || localStorage.getItem("relaxweb:holdem-view") !== "remastered") dispose();
});
document.addEventListener("gameactionerror", () => table?.unlock());
document.addEventListener("gamesocketclose", () => table?.socketClosed());
document.addEventListener("gamejoined", () => table?.socketRejoined());
window.addEventListener("beforeunload", dispose);

const classic = () => registeredGameView("holdem");
registerGame("holdem-remastered", {
  stakeLabel: "小盲注",
  blindLabel: "下一局盲注",
  waitingHint: "等待房主开局。中途退出会自动弃牌，已投入的筹码留在底池。",
  noNextHint: () => classic()?.noNextHint(),
  renderTable,
  renderHandResult: (result) => classic()?.renderHandResult(result),
  renderReview: (result) => classic()?.renderReview(result),
  handResultDelay: () => table?.handResultDelay() || 0,
  seatElement: (index) => root?.querySelector(`.poker-remastered-seat[data-seat="${index}"]`) || null,
});
