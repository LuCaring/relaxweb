/* Waiting room layout and stable, username-keyed seat nodes. */

import { elements, formatCoins, formatCoinsWhole, isRoomOwner, ratingBadge, send, state, stopHallTicker } from "./core.js";
import { gameMetaById } from "./game-config.js";
import { gameView } from "./registry.js";
import { chatOpenButton } from "./room-chat.js";
import { enableVoiceAudio, toggleMic, voiceMicWanted, voiceStatus } from "./room-voice.js";

const FOUR_SEAT_POSITIONS = ["bottom", "left", "top", "right"];

function seatPosition(index, capacity) {
  if (capacity === 4) return FOUR_SEAT_POSITIONS[index] || "top";
  const angle = index * Math.PI * 2 / capacity;
  return `ellipse:${(50 + 35 * Math.sin(angle)).toFixed(2)}:${(50 + 34 * Math.cos(angle)).toFixed(2)}`;
}

function playerSeat(player, index, room, capacity) {
  const seat = document.createElement("div");
  seat.className = "waiting-seat";
  seat.dataset.username = player.username;
  seat.dataset.seatIndex = String(index);
  const name = document.createElement("div");
  name.className = "waiting-seat-name";
  const rating = document.createElement("span");
  rating.className = "waiting-seat-rating";
  const badges = document.createElement("span");
  badges.className = "waiting-seat-badges";
  const stack = document.createElement("div");
  stack.className = "waiting-seat-stack";
  seat.append(name, rating, badges, stack);
  updatePlayerSeat(seat, player, index, room, capacity);
  seat.classList.add("waiting-seat-enter");
  return seat;
}

function updatePlayerSeat(seat, player, index, room, capacity) {
  seat.dataset.username = player.username;
  seat.dataset.seatIndex = String(index);
  const position = seatPosition(index, capacity);
  seat.dataset.position = position.startsWith("ellipse:") ? "ellipse" : position;
  if (position.startsWith("ellipse:")) {
    const [, x, y] = position.split(":");
    seat.style.setProperty("--seat-x", `${x}%`);
    seat.style.setProperty("--seat-y", `${y}%`);
  }
  seat.classList.toggle("is-me", player.username === state.currentUser?.username);
  seat.classList.toggle("is-owner", player.username === room.owner);
  const name = seat.querySelector(".waiting-seat-name");
  name.textContent = player.nickname || player.username;
  const ratingWrap = seat.querySelector(".waiting-seat-rating");
  ratingWrap.replaceChildren(ratingBadge(player.rating));
  const badges = seat.querySelector(".waiting-seat-badges");
  badges.replaceChildren();
  if (player.username === room.owner) {
    const badge = document.createElement("span");
    badge.className = "waiting-owner-badge";
    badge.textContent = "房主";
    badges.append(badge);
  }
  if (player.username === state.currentUser?.username) {
    const badge = document.createElement("span");
    badge.className = "waiting-me-badge";
    badge.textContent = "我";
    badges.append(badge);
  }
  if (room.game_type === "guandan") {
    const team = document.createElement("span");
    team.className = `waiting-team-badge team-${index % 2}`;
    team.textContent = index % 2 === 0 ? "A 队" : "B 队";
    badges.append(team);
  }
  seat.querySelector(".waiting-seat-stack").textContent = `${formatCoins(player.stack)} 筹码`;
}

function emptySeat(index, capacity) {
  const seat = document.createElement("div");
  seat.className = "waiting-seat is-empty";
  seat.dataset.seatIndex = String(index);
  const position = seatPosition(index, capacity);
  seat.dataset.position = position.startsWith("ellipse:") ? "ellipse" : position;
  if (position.startsWith("ellipse:")) {
    const [, x, y] = position.split(":");
    seat.style.setProperty("--seat-x", `${x}%`);
    seat.style.setProperty("--seat-y", `${y}%`);
  }
  seat.textContent = "空位";
  return seat;
}

function syncSeats(seats, room, capacity) {
  const oldNodes = [...seats.children];
  const oldByName = new Map(oldNodes.filter((seat) => seat.dataset.username)
    .map((seat) => [seat.dataset.username, seat]));
  const oldEmptyByIndex = new Map(oldNodes.filter((seat) => seat.classList.contains("is-empty"))
    .map((seat) => [seat.dataset.seatIndex, seat]));
  const keep = new Set();
  const nodes = [];
  room.players.slice(0, capacity).forEach((player, index) => {
    let seat = oldByName.get(player.username);
    if (!seat) {
      seat = playerSeat(player, index, room, capacity);
      window.setTimeout(() => seat.classList.remove("waiting-seat-enter"), 420);
    } else updatePlayerSeat(seat, player, index, room, capacity);
    keep.add(seat);
    nodes.push(seat);
  });
  for (let i = room.players.length; i < capacity; i += 1) {
    let seat = oldEmptyByIndex.get(String(i));
    if (!seat) seat = emptySeat(i, capacity);
    keep.add(seat);
    nodes.push(seat);
  }
  for (const seat of oldNodes) if (!keep.has(seat)) seat.remove();
  nodes.forEach((node, index) => {
    const at = seats.children[index];
    if (at !== node) seats.insertBefore(node, at || null);
  });
}

function startRule(room, meta) {
  if (meta.minStartPlayers === 4) {
    return { count: room.players.filter((player) => player.stack > 0).length, minimum: 4, text: "需要正好 4 名有筹码的玩家。" };
  }
  return { count: room.players.filter((player) => player.stack > 0).length, minimum: 2, text: "至少需要 2 名有筹码的玩家。" };
}

function refreshWaitingVoice(root, room) {
  const panel = root.querySelector(".waiting-voice");
  panel.hidden = room.game_type !== "werewolf" || !window.LIVE_CONFIG?.voice?.enabled;
  if (panel.hidden) return;
  const status = voiceStatus();
  const on = status.connected && status.canPublish && voiceMicWanted();
  const button = panel.querySelector(".waiting-voice-mic");
  button.disabled = !status.connected || !status.canPublish;
  button.classList.toggle("on", on);
  button.textContent = on ? "🎙 关闭麦克风" : "🎙 开启麦克风";
  button.setAttribute("aria-pressed", String(on));
  panel.querySelector(".waiting-voice-status").textContent = !status.connected
    ? "语音连接中，连接后可测试麦克风"
    : status.micError || (on ? "麦克风已开启，可以和房内玩家交谈" : "已连接 · 麦克风关闭");
  const audio = panel.querySelector(".waiting-voice-audio");
  audio.hidden = !status.audioBlocked;
}

function refreshWaitingRoom(root) {
  const room = state.myRoom;
  if (!room) return;
  const meta = gameMetaById(room.game_type);
  const game = gameView(room.game_type);
  const capacity = meta.seats;
  root.dataset.game = room.game_type;
  root.querySelector(".waiting-room-title").textContent = `等待玩家加入（${room.players.length}/${capacity}）`;
  root.querySelector(".waiting-room-info").textContent = `${meta.icon} ${meta.name}  ·  ${game?.stakeLabel || "底注"} ${formatCoinsWhole(room.blind)}  ·  买入 ${formatCoins(room.buy_in)}  ·  房主 ${room.owner_name}`;
  const rule = startRule(room, meta);
  const missing = Math.max(0, rule.minimum - rule.count);
  const centerText = missing
    ? `等待玩家加入  ${rule.count} / ${rule.minimum} · 还差 ${missing} 人`
    : isRoomOwner() ? "人数已满足 · 房主可以开始" : "人数已满足 · 等待房主开始";
  root.querySelector(".waiting-center").textContent = centerText;
  const seats = root.querySelector(".waiting-seats");
  seats.dataset.capacity = String(capacity);
  syncSeats(seats, room, capacity);
  const start = root.querySelector(".waiting-start");
  
  if (isRoomOwner()) {
    start.hidden = false;
    const canStart = rule.count >= rule.minimum && (meta.minStartPlayers !== 4 || rule.count === 4);
    start.disabled = !canStart;
    start.title = canStart ? "开始游戏" : rule.text;
  } else {
    start.hidden = true;
  }
  const waiting = room.players.length < capacity ? capacity - room.players.length : 0;
  const hint = root.querySelector(".waiting-room-hint");
  hint.textContent = isRoomOwner()
    ? (missing ? `${rule.text}还需 ${missing} 名有筹码的玩家，当前空位 ${waiting} 个。` : "人数已满足，可以开始游戏。")
    : (game?.waitingHint || "等待房主开局。");
  refreshWaitingVoice(root, room);
}

export function renderRoomLobby() {
  stopHallTicker();
  const body = elements.gameMain;
  let root = body.querySelector(".waiting-room");
  if (!root) {
    body.replaceChildren();
    root = document.createElement("section");
    root.className = "waiting-room";
    const info = document.createElement("div");
    info.className = "waiting-room-info";
    const title = document.createElement("h1");
    title.className = "waiting-room-title";
    const table = document.createElement("div");
    table.className = "waiting-table";
    table.setAttribute("role", "group");
    table.setAttribute("aria-label", "等待座位");
    const seats = document.createElement("div");
    seats.className = "waiting-seats";
    const center = document.createElement("div");
    center.className = "waiting-center";
    table.append(seats, center);
    const controls = document.createElement("div");
    controls.className = "waiting-room-controls";
    const voice = document.createElement("div");
    voice.className = "waiting-voice";
    const voiceInfo = document.createElement("div");
    voiceInfo.className = "waiting-voice-info";
    const voiceTitle = document.createElement("strong");
    voiceTitle.textContent = "开局前语音调试";
    const voiceHint = document.createElement("span");
    voiceHint.textContent = "等待区可以自由聊天；开局后会自动切换到游戏语音频道。";
    voiceInfo.append(voiceTitle, voiceHint);
    const voiceActions = document.createElement("div");
    voiceActions.className = "waiting-voice-actions";
    const voiceStatusText = document.createElement("span");
    voiceStatusText.className = "waiting-voice-status";
    voiceStatusText.setAttribute("aria-live", "polite");
    const voiceAudio = document.createElement("button");
    voiceAudio.className = "waiting-voice-audio";
    voiceAudio.type = "button";
    voiceAudio.textContent = "🔊 开启声音";
    voiceAudio.addEventListener("click", () => { void enableVoiceAudio(); });
    const voiceMic = document.createElement("button");
    voiceMic.className = "waiting-voice-mic";
    voiceMic.type = "button";
    voiceMic.addEventListener("click", () => { void toggleMic(); });
    voiceActions.append(voiceStatusText, voiceAudio, voiceMic);
    voice.append(voiceInfo, voiceActions);
    const start = document.createElement("button");
    start.className = "login-submit waiting-start";
    start.type = "button";
    start.textContent = "开始游戏";
    start.addEventListener("click", () => send({ type: "start_game" }));
    const hint = document.createElement("div");
    hint.className = "game-hint waiting-room-hint";
    controls.append(voice, start, hint);
    root.append(info, title, table, controls);
    body.append(root);
    body.append(chatOpenButton());
  }
  refreshWaitingRoom(root);
}
