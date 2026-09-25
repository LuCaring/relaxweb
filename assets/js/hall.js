/* Game hall: game navigation, room discovery and room creation. */

import { openLogin } from "./auth.js";
import { confirmDialog } from "./dialog.js";
import { elements, formatCoins, renderGameView, send, state } from "./core.js";
import { onMessage, registerView } from "./registry.js";
import { ratingCard } from "./rating.js";
import { assetCard } from "./asset-ranking.js";
import { DEFAULT_GAME, GAME_TYPES, ROOM_GAME_TYPES, gameMetaById } from "./game-config.js";
import "./create-room.js";

export { fillBlindOptions, gameMetaById } from "./game-config.js";

const GAME_SHORT_NAMES = GAME_TYPES.map((game) => game.name.split(" · ")[0]).join("、");
let roomSearch = "";
let roomStatusFilter = "all";
function button(label, className, onClick) {
  const node = document.createElement("button");
  node.className = className;
  node.type = "button";
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

async function joinRoom(room) {
  // 开局后的房间只能观战进入；等待中的房间照常作为玩家加入。
  if (room.status === "playing" || room.status === "settled") {
    const ok = await confirmDialog("牌局进行中，将以观战身份进入，可随时退出。", {
      title: "观战进入",
    });
    if (ok) send({ type: "join_room", room_id: room.id, spectate: true });
    return;
  }
  send({ type: "join_room", room_id: room.id });
}

function renderEntry() {
  const body = elements.gameMain;
  body.replaceChildren();
  const card = document.createElement("div");
  card.className = "game-entry-card";
  const icon = document.createElement("div");
  icon.className = "hall-game-icon";
  icon.textContent = "🎮";
  const title = document.createElement("div");
  title.className = "game-entry-title";
  title.textContent = "欢迎来到游戏厅";
  const desc = document.createElement("div");
  desc.className = "game-entry-desc";
  desc.textContent = `登录后与直播间的朋友们玩${GAME_SHORT_NAMES}，金币通用。`;
  const login = button("登录 / 注册", "login-button", openLogin);
  card.append(icon, title, desc, login);
  body.append(card);
}

function selectGame(gameId) {
  const game = gameMetaById(gameId);
  state.currentGameId = game.id;
  state.hallPage = game.mode === "solo" ? game.view : "rooms";
  renderGameView();
}

function enterVoiceHall() {
  state.hallPage = "voicehall";
  renderGameView();
}

function voiceChannelName(channelId) {
  return state.voiceHub?.channels?.find((channel) => channel.id === channelId)?.name || channelId;
}

function voiceCard() {
  const card = button("", "hall-game-card hall-feature-card hall-voice-card", enterVoiceHall);
  const icon = document.createElement("div");
  icon.className = "hall-game-icon";
  icon.textContent = "🎙️";
  const info = document.createElement("div");
  const name = document.createElement("div");
  name.className = "hall-game-name";
  name.textContent = "语音聊天室";
  const desc = document.createElement("div");
  desc.className = "hall-game-desc";
  desc.textContent = "随时开麦的语音频道，测试与畅聊两相宜。";
  const meta = document.createElement("div");
  meta.className = "hall-voice-meta";
  const members = state.voiceHub?.channels?.reduce((count, channel) =>
    count + channel.members.length, 0) || 0;
  meta.textContent = `6 个频道 · ${members} 人在线`;
  info.append(name, desc, meta);
  const go = document.createElement("div");
  go.className = "hall-game-go";
  go.textContent = state.voiceHub?.myChannel ? "返回频道 →" : "进入频道 →";
  card.append(icon, info, go);
  return card;
}

function gameCard(game, extraClass = "") {
  const card = button("", `hall-game-card${extraClass ? ` ${extraClass}` : ""}`,
    () => selectGame(game.id));
  const icon = document.createElement("div");
  icon.className = "hall-game-icon";
  icon.textContent = game.icon;
  const info = document.createElement("div");
  const name = document.createElement("div");
  name.className = "hall-game-name";
  name.textContent = game.name;
  const desc = document.createElement("div");
  desc.className = "hall-game-desc";
  desc.textContent = game.desc;
  info.append(name, desc);
  const go = document.createElement("div");
  go.className = "hall-game-go";
  go.textContent = game.mode === "solo" ? "进入庄园 →" : "查看房间 →";
  card.append(icon, info, go);
  return card;
}

function renderHall() {
  const body = elements.gameMain;
  body.replaceChildren();
  const titleRow = document.createElement("div");
  titleRow.className = "hall-title-row";
  const heading = document.createElement("div");
  heading.className = "hall-page-title";
  heading.textContent = "一起玩";
  titleRow.append(heading);
  // 已在语音频道时，标题行右侧给一枚随时返回频道的胶囊
  if (state.voiceHub?.myChannel) {
    titleRow.append(button(
      `🎙️ ${voiceChannelName(state.voiceHub.myChannel)} · 返回`,
      "hall-voice-pill",
      enterVoiceHall,
    ));
  }
  body.append(titleRow);

  const features = document.createElement("section");
  features.className = "hall-feature-section";
  const featureHeading = document.createElement("h2");
  featureHeading.className = "hall-section-heading";
  featureHeading.textContent = "庄园与聊天";
  const featureGrid = document.createElement("div");
  featureGrid.className = "hall-feature-grid";
  featureGrid.append(gameCard(gameMetaById("estate"), "hall-feature-card hall-estate-card"),
    voiceCard());
  features.append(featureHeading, featureGrid);

  const games = document.createElement("section");
  games.className = "hall-games-section";
  const gamesHeading = document.createElement("h2");
  gamesHeading.className = "hall-section-heading";
  gamesHeading.textContent = "小游戏";
  const grid = document.createElement("div");
  grid.className = "hall-grid";
  for (const game of ROOM_GAME_TYPES) grid.append(gameCard(game));
  games.append(gamesHeading, grid);

  const rankings = document.createElement("section");
  rankings.className = "hall-rankings-section";
  const rankingsHeading = document.createElement("h2");
  rankingsHeading.className = "hall-section-heading";
  rankingsHeading.textContent = "排行榜";
  const rankingsGrid = document.createElement("div");
  rankingsGrid.className = "hall-rankings-grid";
  rankingsGrid.append(ratingCard(), assetCard());
  rankings.append(rankingsHeading, rankingsGrid);
  body.append(features, games, rankings);
}

function roomKey(room) {
  return String(room.id ?? room.room_id);
}

function roomVisible(room, gameId) {
  if (room.game !== gameId) return false;
  if (roomStatusFilter === "waiting" && room.status === "playing") return false;
  if (roomStatusFilter === "playing" && room.status !== "playing") return false;
  const query = roomSearch.trim().toLocaleLowerCase();
  return !query || `${room.name || ""} ${room.owner_name || ""}`.toLocaleLowerCase().includes(query);
}

function roomRow(room) {
  const row = document.createElement("div");
  row.className = "hall-room-row";
  row.dataset.roomId = roomKey(room);
  row.setAttribute("role", "row");
  const values = {};
  for (const column of ["room", "owner", "players", "buyin", "status"]) {
    values[column] = document.createElement("div");
    values[column].className = `hall-room-cell hall-room-${column}`;
    values[column].setAttribute("role", "cell");
    row.append(values[column]);
  }
  const action = document.createElement("div");
  action.className = "hall-room-cell hall-room-action";
  action.setAttribute("role", "cell");
  const join = button("进入", "hall-join", () => joinRoom(row._room || room));
  action.append(join);
  row.append(action);
  row._cells = values;
  row._join = join;
  updateRoomRow(row, room);
  return row;
}

function updateRoomRow(row, room) {
  row._room = room;
  const cells = row._cells;
  cells.room.textContent = room.name || "好友房";
  cells.owner.textContent = room.owner_name || "—";
  cells.players.textContent = `${room.players.length} / ${gameMetaById(room.game).seats}`;
  cells.buyin.textContent = formatCoins(room.buy_in);
  const status = room.status === "playing" ? "游戏中" : "等待中";
  cells.status.textContent = status;
  cells.status.dataset.status = room.status === "playing" ? "playing" : "waiting";
  if (room.status === "playing" || room.status === "settled") {
    // 开局后进入一律观战，满员与否不再影响入口。
    row._join.disabled = false;
    row._join.textContent = "观战";
    row._join.title = "观战这场对局";
    return;
  }
  const full = room.players.length >= gameMetaById(room.game).seats;
  row._join.disabled = full;
  row._join.textContent = full ? "已满" : "进入";
  row._join.title = full ? "房间已满" : "进入房间";
}

function synchronizeRoomRows(box, gameId) {
  if (!box) return;
  const rooms = state.hallRooms.filter((room) => roomVisible(room, gameId));
  const previous = new Map([...box.querySelectorAll(".hall-room-row")].map((row) => [row.dataset.roomId, row]));
  const visibleKeys = new Set(rooms.map(roomKey));
  for (const [key, row] of previous) if (!visibleKeys.has(key)) row.remove();

  if (!rooms.length) {
    box.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "hall-empty-state";
    empty.textContent = roomSearch || roomStatusFilter !== "all"
      ? "没有符合条件的房间，试试调整搜索或筛选。"
      : "这里还没有房间，创建一间，等朋友加入。";
    box.append(empty);
    return;
  }

  box.querySelector(".hall-empty-state")?.remove();
  rooms.forEach((room, index) => {
    const key = roomKey(room);
    let row = previous.get(key);
    if (!row) {
      row = roomRow(room);
      row.classList.add("room-entering");
      window.setTimeout(() => row.classList.remove("room-entering"), 450);
    } else {
      const oldPlayers = Number(row.dataset.playerCount || 0);
      const oldStatus = row._cells.status.dataset.status;
      updateRoomRow(row, room);
      if (room.players.length > oldPlayers || (room.status === "playing" && oldStatus !== "playing")) {
        row.classList.remove("room-updated");
        void row.offsetWidth;
        row.classList.add("room-updated");
        window.setTimeout(() => row.classList.remove("room-updated"), 600);
      }
    }
    row.dataset.playerCount = String(room.players.length);
    const at = box.querySelectorAll(".hall-room-row")[index];
    if (at !== row) box.insertBefore(row, at || null);
  });
}

function setStatusFilter(value, buttons) {
  roomStatusFilter = value;
  for (const [filter, control] of buttons) {
    const selected = filter === value;
    control.setAttribute("aria-pressed", String(selected));
  }
  const box = document.getElementById("hallRoomsBox");
  if (box) synchronizeRoomRows(box, state.currentGameId || DEFAULT_GAME.id);
}

function renderGameRooms() {
  const activeGame = gameMetaById(state.currentGameId);
  const body = elements.gameMain;
  body.replaceChildren();
  const toolbar = document.createElement("div");
  toolbar.className = "hall-toolbar";
  const back = button("← 游戏厅", "online-stat hall-back", () => {
    state.hallPage = null;
    state.currentGameId = null;
    renderGameView();
  });
  const heading = document.createElement("div");
  heading.className = "hall-page-title";
  heading.textContent = `${activeGame.name} 房间`;
  const searchWrap = document.createElement("label");
  searchWrap.className = "hall-search-wrap";
  const search = document.createElement("input");
  search.type = "search";
  search.className = "hall-search";
  search.placeholder = "搜索房间或房主";
  search.setAttribute("aria-label", "搜索房间或房主");
  search.value = roomSearch;
  search.addEventListener("input", () => {
    roomSearch = search.value;
    synchronizeRoomRows(document.getElementById("hallRoomsBox"), state.currentGameId);
  });
  searchWrap.append(search);
  const create = button("＋ 创建房间", "hall-create-button", () => {
    state.hallPage = "create";
    renderGameView();
  });
  toolbar.append(back, heading, searchWrap, create);
  body.append(toolbar);

  const layout = document.createElement("div");
  layout.className = "rooms-layout";
  const nav = document.createElement("nav");
  nav.className = "rooms-game-nav";
  nav.setAttribute("aria-label", "游戏类型");
  const navTitle = document.createElement("div");
  navTitle.className = "rooms-nav-title";
  navTitle.textContent = "游戏";
  nav.append(navTitle);
  for (const game of ROOM_GAME_TYPES) {
    const item = button(`${game.icon} ${game.name.split(" · ")[0]}`, `rooms-game-link${game.id === activeGame.id ? " active" : ""}`, () => {
      state.currentGameId = game.id;
      renderGameView();
    });
    item.setAttribute("aria-current", String(game.id === activeGame.id));
    nav.append(item);
  }
  layout.append(nav);

  const content = document.createElement("section");
  content.className = "rooms-content";
  const filters = document.createElement("div");
  filters.className = "hall-room-filters";
  const filterButtons = new Map();
  for (const [value, label] of [["all", "全部"], ["waiting", "等待中"], ["playing", "游戏中"]]) {
    const item = button(label, "hall-filter-button", () => setStatusFilter(value, filterButtons));
    item.setAttribute("aria-pressed", String(value === roomStatusFilter));
    filterButtons.set(value, item);
    filters.append(item);
  }
  const roomTable = document.createElement("div");
  roomTable.className = "hall-room-table";
  roomTable.setAttribute("role", "table");
  roomTable.setAttribute("aria-label", "房间列表");
  const header = document.createElement("div");
  header.className = "hall-room-header";
  header.setAttribute("role", "row");
  for (const [key, label] of [["room", "房间"], ["owner", "房主"], ["players", "人数"], ["buyin", "买入"], ["status", "状态"], ["action", ""]]) {
    const cell = document.createElement("div");
    cell.className = `hall-room-cell hall-room-${key}`;
    cell.setAttribute("role", "columnheader");
    cell.textContent = label;
    header.append(cell);
  }
  const list = document.createElement("div");
  list.id = "hallRoomsBox";
  list.className = "hall-room-list";
  roomTable.append(header, list);
  content.append(filters, roomTable);
  layout.append(content);
  body.append(layout);
  synchronizeRoomRows(list, activeGame.id);
}


function refreshRoomList() {
  const box = document.getElementById("hallRoomsBox");
  if (box) synchronizeRoomRows(box, state.currentGameId || DEFAULT_GAME.id);
}

onMessage("room_list", (data) => {
  state.hallRooms = data.rooms || [];
  if (!state.myRoom && state.hallPage === "rooms") refreshRoomList();
});


registerView("entry", renderEntry);
registerView("hall", renderHall);
registerView("rooms", renderGameRooms);
