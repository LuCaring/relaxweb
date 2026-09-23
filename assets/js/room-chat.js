/* Room chat overlay, message stream and seat bubbles. */

import { send, state } from "./core.js";
import { gameView, onMessage } from "./registry.js";

const seatBubbles = new Map();
const desktopRoom = window.matchMedia("(min-width: 1024px)");
export function usesCompactDesktopChat() {
  const room = state.myRoom;
  return desktopRoom.matches
    && Boolean(gameView(room?.game_type)?.compactDesktopChat)
    && room?.status === "playing"
    && !room.hand_ready
    && !room.settlement;
}
export function usesDesktopChat() {
  return desktopRoom.matches
    && !usesCompactDesktopChat()
    && !gameView(state.myRoom?.game_type)?.overlayChat;
}
desktopRoom.addEventListener("change", () => {
  closeChatOverlay();
  document.getElementById("desktopRoomChat")?.remove();
  if (state.myRoom && (usesDesktopChat() || usesCompactDesktopChat())) openChatOverlay();
});

function roomChatRowNode(m) {
  const row = document.createElement("div");
  row.className = "rc-message";
  const name = document.createElement("span");
  name.className = "rc-name";
  // 观战者发言：灰色 id 并以（观战）注明身份。
  name.textContent = `${m.nickname || m.username}${m.spectator ? "（观战）" : ""}`;
  if (m.spectator) name.classList.add("rc-spectator");
  const time = document.createElement("span");
  time.className = "rc-time";
  time.textContent = m.time || "";
  const text = document.createElement("div");
  text.className = "rc-text";
  text.textContent = m.text;
  row.append(name, time, text);
  return row;
}

function refillRoomChatList() {
  const lists = document.querySelectorAll(".room-chat-list");
  if (!lists.length) return;
  const rows = state.roomChat.map((m) => roomChatRowNode(m));
  for (const list of lists) {
    list.replaceChildren(...rows.map((r) => r.cloneNode(true)));
    list.scrollTop = list.scrollHeight;
  }
}

function appendRoomChatRow(m) {
  const row = roomChatRowNode(m);
  for (const list of document.querySelectorAll(".room-chat-list")) {
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
    list.append(row.cloneNode(true));
    while (list.children.length > 60) list.firstElementChild.remove();
    if (atBottom) list.scrollTop = list.scrollHeight;
  }
}

function sendRoomChat() {
  const input = document.getElementById("roomChatInput");
  const text = (input?.value || state.roomChatDraft).trim();
  if (!text) return;
  if (send({ type: "room_chat", text })) {
    state.roomChatDraft = "";
    if (input) input.value = "";
  }
}

export function chatOpenButton() {
  const wrap = document.createElement("div");
  wrap.className = "chat-open-row";
  const btn = document.createElement("button");
  btn.className = "dock-chat-toggle";
  btn.type = "button";
  btn.textContent = `💬 房间聊天（${state.roomChat.length}）`;
  btn.addEventListener("click", openChatOverlay);
  wrap.append(btn);
  return wrap;
}

function showSeatBubble(username, text) {
  if (!state.myRoom) return;
  removeSeatBubble(username);
  seatBubbles.set(username, { text: text.length > 60 ? `${text.slice(0, 60)}…` : text, until: Date.now() + 3000 });
  applySeatBubble(username);
  window.setTimeout(() => {
    const bubble = seatBubbles.get(username);
    if (bubble && bubble.until <= Date.now()) {
      removeSeatBubble(username);
      seatBubbles.delete(username);
    }
  }, 3100);
}

function applySeatBubble(username) {
  if (!state.myRoom) return;
  const index = state.myRoom.players.findIndex((p) => p.username === username);
  if (index < 0) return;
  const seat = seatNodeByIndex(index);
  if (!seat) return;
  removeSeatBubble(username);
  const bubble = seatBubbles.get(username);
  if (!bubble || bubble.until <= Date.now()) return;
  const node = document.createElement("div");
  node.className = "seat-bubble";
  node.textContent = bubble.text;
  node.dataset.username = username;
  // Seat transforms create stacking contexts. Render above those contexts so
  // neighboring avatars cannot paint over a wider message.
  const table = seat.closest(".casual-page, .uno-table, .poker-table, .liar-table, .waiting-table") || seat;
  table.append(node);
  bubble.node = node;
  positionSeatBubble(node, seat, table);
}

function positionSeatBubble(node, seat, table) {
  const anchor = seat.getBoundingClientRect();
  node.hidden = !anchor.width || !anchor.height;
  if (node.hidden) return;
  const bounds = table.getBoundingClientRect();
  const left = Math.max(16, bounds.left + 8);
  const right = Math.min(document.documentElement.clientWidth - 16,
    bounds.right - 8);
  node.style.setProperty("--bubble-available-width", `${Math.max(0, right - left)}px`);
  const width = node.offsetWidth;
  const naturalLeft = anchor.left + anchor.width / 2 - width / 2;
  const shift = Math.max(left, Math.min(naturalLeft, right - width)) - naturalLeft;
  node.style.left = `${anchor.left + anchor.width / 2 - bounds.left - table.clientLeft}px`;
  node.style.setProperty("--bubble-shift", `${shift}px`);
  node.style.setProperty("--bubble-tail-x", `${Math.max(16, Math.min(width - 16, width / 2 - shift))}px`);
  // Top seats have little space above them; place their message inside the table.
  let top = anchor.top - node.offsetHeight - 10;
  if (top < Math.max(8, bounds.top + 8)) {
    node.dataset.placement = "below";
    top = anchor.bottom + 10;
  }
  node.style.top = `${top - bounds.top - table.clientTop}px`;
}

function removeSeatBubble(username) {
  seatBubbles.get(username)?.node?.remove();
}

export function openChatOverlay() {
  const compact = usesCompactDesktopChat();
  const desktop = usesDesktopChat() || compact;
  const existing = document.getElementById("desktopRoomChat");
  if (desktop && existing && existing.classList.contains("compact-room-chat") === compact) return;
  closeChatOverlay();
  const overlay = document.createElement("div");
  overlay.className = desktop
    ? `desktop-room-chat${compact ? " compact-room-chat" : ""}`
    : "chat-overlay";
  overlay.id = desktop ? "desktopRoomChat" : "chatOverlay";
  overlay.setAttribute("aria-label", "房间聊天室");
  const head = document.createElement("div");
  head.className = "chat-overlay-head";
  const title = document.createElement("div");
  title.textContent = compact ? "桌边聊天" : "房间聊天";
  const close = document.createElement("button");
  close.className = "chat-overlay-close";
  close.type = "button";
  close.textContent = "✕";
  close.addEventListener("click", closeChatOverlay);
  head.append(title);
  if (!desktop) head.append(close);
  const list = document.createElement("div");
  list.className = "room-chat-list chat-overlay-list";
  list.setAttribute("role", "log");
  list.setAttribute("aria-label", "聊天消息");
  list.replaceChildren(...state.roomChat.map((m) => roomChatRowNode(m)));
  // 两种布局复用同一输入框，牌桌重绘不会重建桌面聊天。
  const composer = document.createElement("div");
  composer.className = "chat-overlay-composer";
  const input = document.createElement("input");
  input.className = "chat-input";
  input.id = "roomChatInput";
  input.setAttribute("aria-label", "发送房间消息");
  input.maxLength = 200;
  input.placeholder = "说点什么…（对全桌可见）";
  input.value = state.roomChatDraft;
  input.autocomplete = "off";
  input.addEventListener("input", () => { state.roomChatDraft = input.value; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) sendRoomChat();
  });
  const sendBtn = document.createElement("button");
  sendBtn.className = "send-button";
  sendBtn.type = "button";
  sendBtn.textContent = "发送";
  sendBtn.addEventListener("click", sendRoomChat);
  composer.append(input, sendBtn);
  overlay.append(head, list, composer);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeChatOverlay();
  });
  (desktop ? document.querySelector(".game-workspace") : document.body).append(overlay);
  list.scrollTop = list.scrollHeight;
  if (!desktop) input.focus({ preventScroll: true });
}

export function closeChatOverlay() {
  document.getElementById("chatOverlay")?.remove();
  document.getElementById("desktopRoomChat")?.remove();
}



function seatNodeByIndex(index) {
  const player = state.myRoom?.players[index];
  if (player) {
    const waitingSeat = [...document.querySelectorAll(".waiting-seat[data-username]")]
      .find((seat) => seat.dataset.username === player.username);
    if (waitingSeat) return waitingSeat;
  }
  return gameView(state.myRoom?.game_type)?.seatElement(index) || null;
}

export function reapplySeatBubbles() {
  for (const username of seatBubbles.keys()) applySeatBubble(username);
}

window.addEventListener("resize", reapplySeatBubbles);

export function clearSeatBubbles() {
  for (const username of seatBubbles.keys()) removeSeatBubble(username);
  seatBubbles.clear();
}

export function resetRoomChat() {
  state.roomChat = [];
  state.roomChatDraft = "";
  clearSeatBubbles();
}

onMessage("room_chat_history", (data) => {
  if (state.myRoom && data.room_id === state.myRoom.room_id) {
    state.roomChat = data.messages || [];
    refillRoomChatList();
  }
});

onMessage("room_chat", (data) => {
  if (!state.myRoom || data.room_id !== state.myRoom.room_id) return;
  state.roomChat.push(data);
  if (state.roomChat.length > 60) state.roomChat.shift();
  for (const button of document.querySelectorAll(".dock-chat-toggle")) {
    button.textContent = `💬 房间聊天（${state.roomChat.length}）`;
  }
  appendRoomChatRow(data);
  // 观战者不坐席位：发言不弹座位气泡。
  if (!data.spectator) showSeatBubble(data.username, data.text);
});
