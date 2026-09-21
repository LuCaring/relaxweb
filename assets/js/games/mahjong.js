/* 国标麻将场景：四向牌河、中央局况盘、底部手牌与操作条、
   mahjim 国标牌面图（assets/tiles/mahjim/），常驻操作条
   （打出/吃/碰/杠，可胡时显示胡），手机端通过「牌河」二级菜单查看
   全部打出的牌。起和判定与番种计算以服务器为准。 */

import {
  displayNameOf, elements, formatCoins, playerAvatarNode, ratingBadge, renderGameView, requestProfile,
  selfUsername, send, startHallTicker, state,
} from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const TILE_DIR = "assets/tiles/mahjim";

let actionLock = false;
let selected = new Set();
let lastHandJson = "";
let chiOpen = false;
let gangOpen = false;
let riverOverlayOpen = false;
let lastRoomView = null;
let turnDeadline = 0;

function tileLabel(code) {
  if (code >= 34) return "春夏秋冬梅兰竹菊"[code - 34];
  if (code >= 27) return "东南西北中发白"[code - 27];
  return `${code % 9 + 1}${"万条筒"[Math.floor(code / 9)]}`;
}

function sortHand(hand) {
  const drawnIndex = state.myRoom.your_draw_index;
  return hand.map((code, index) => ({ code, index }))
    .sort((a, b) => Number(a.index === drawnIndex) - Number(b.index === drawnIndex)
      || a.code - b.code);
}

/* =========================================================
   牌面组件：mahjim 国标牌面图；side=横置（吃碰杠），back=牌背
========================================================= */

function mjTileNode(code, opts = {}) {
  const wrap = document.createElement("span");
  wrap.className = "mj-tile";
  if (opts.small) wrap.classList.add("small");
  if (opts.mini) wrap.classList.add("mini");
  if (opts.tiny) wrap.classList.add("tiny");
  if (opts.picked) wrap.classList.add("picked");
  if (opts.win) wrap.classList.add("win");
  if (code >= 34) wrap.classList.add("flower");
  const img = document.createElement("img");
  img.src = `${TILE_DIR}/${code}.png`;
  img.alt = tileLabel(code);
  img.draggable = false;
  wrap.append(img);
  return wrap;
}

function mjBackNode(opts = {}) {
  const wrap = document.createElement("span");
  wrap.className = "mj-tile back";
  if (opts.small) wrap.classList.add("small");
  if (opts.mini) wrap.classList.add("mini");
  if (opts.tiny) wrap.classList.add("tiny");
  const img = document.createElement("img");
  img.src = `${TILE_DIR}/back.png`;
  img.alt = "";
  img.draggable = false;
  wrap.append(img);
  return wrap;
}

/** 副露里横置的一张（吃碰杠来自别家的牌），占位宽高互换。 */
function mjSideTileNode(opts = {}) {
  const wrap = document.createElement("span");
  wrap.className = "mj-tile side";
  if (opts.small) wrap.classList.add("small");
  if (opts.mini) wrap.classList.add("mini");
  const img = document.createElement("img");
  img.src = `${TILE_DIR}/back.png`;
  img.alt = "";
  img.draggable = false;
  wrap.append(img);
  return wrap;
}

function mjSideFaceNode(code, opts = {}) {
  const wrap = document.createElement("span");
  wrap.className = "mj-tile side";
  if (opts.small) wrap.classList.add("small");
  if (opts.mini) wrap.classList.add("mini");
  if (opts.tiny) wrap.classList.add("tiny");
  const img = document.createElement("img");
  img.src = `${TILE_DIR}/${code}.png`;
  img.alt = tileLabel(code);
  img.draggable = false;
  wrap.append(img);
  return wrap;
}

/** 副露组：碰/杠横置来自别家的那张，吃横置末张（来自上家），暗杠全背面。 */
function meldNode(meld, opts = {}) {
  const group = document.createElement("span");
  group.className = `mj-meld meld-${meld.type}`;
  const tiles = meld.tiles || [];
  if (meld.type === "angang") {
    for (let i = 0; i < 4; i += 1) group.append(mjBackNode(opts));
    group.title = "暗杠";
    return group;
  }
  tiles.forEach((code, i) => {
    if (meld.type === "chi") {
      group.append(i === tiles.length - 1
        ? mjSideFaceNode(code, opts) : mjTileNode(code, opts));
      return;
    }
    if (meld.type === "peng") {
      group.append(i === 0 ? mjSideFaceNode(code, opts) : mjTileNode(code, opts));
      return;
    }
    // 明杠（含补杠）：首张横置 + 三张正置
    group.append(i === 0 ? mjSideFaceNode(code, opts) : mjTileNode(code, opts));
  });
  return group;
}

/* =========================================================
   操作发送
========================================================= */

function mjAct(payload) {
  if (actionLock || state.myRoom?.spectator) return;
  actionLock = send({ type: "poker_action", ...payload });
  if (actionLock) {
    document.querySelectorAll(".mj-dock button, .mj-self-controls button, .mj-hand button").forEach((b) => { b.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "mahjong") return;
  actionLock = false;
  renderGameView();
});

function isMyTurn() {
  const me = selfUsername();
  return Boolean(me) && state.myRoom.to_act === me;
}

function mySeatIndex() {
  const players = state.myRoom.players || [];
  return Math.max(0, players.findIndex((p) => p.username === selfUsername()));
}

function claimTile() {
  return state.myRoom.claim?.tile;
}

function chiOptions() {
  return state.myRoom.your_options?.claim?.chi || [];
}

/** 杠的全部来源：声明窗明杠 / 手牌暗杠 / 补杠。 */
function gangActions() {
  const room = state.myRoom;
  const list = [];
  if (room.your_options?.claim?.gang && claimTile() != null) {
    list.push({
      label: `杠 ${tileLabel(claimTile())}`,
      run: () => mjAct({ action: "claim", kind: "gang" }),
    });
  }
  for (const code of room.your_options?.angang || []) {
    list.push({
      label: `暗杠 ${tileLabel(code)}`,
      run: () => mjAct({ action: "angang", index: (room.your_hand || []).indexOf(code) }),
    });
  }
  for (const code of room.your_options?.bugang || []) {
    list.push({
      label: `补杠 ${tileLabel(code)}`,
      run: () => mjAct({ action: "bugang", index: (room.your_hand || []).indexOf(code) }),
    });
  }
  return list;
}

/* =========================================================
   牌桌渲染
========================================================= */

function topbarNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "mj-topbar";
  const left = document.createElement("span");
  left.className = "mj-topbar-info";
  const rules = room.rules || {};
  left.textContent = `国标麻将 · ${rules.min_fan ?? 8}番起和 · 底注 ${room.blind}`;
  left.title = `庄家 ${displayNameOf(room.dealer)}`
    + (rules.flowers ? " · 花牌开" : " · 花牌关")
    + (rules.chow ? " · 吃开" : " · 吃关");
  const right = document.createElement("div");
  right.className = "mj-topbar-right";
  const riverBtn = document.createElement("button");
  riverBtn.type = "button";
  riverBtn.className = "mj-river-toggle";
  riverBtn.textContent = "牌河";
  riverBtn.title = "查看全部打出的牌";
  riverBtn.addEventListener("click", () => {
    riverOverlayOpen = true;
    renderGameView();
  });
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle mj-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬";
  chatToggle.setAttribute("aria-label", "打开聊天");
  chatToggle.addEventListener("click", openChatOverlay);
  right.append(riverBtn, chatToggle);
  bar.append(left, statusNode(), right);
  return bar;
}

/** 局况集中在桌心；方位跟随本家座位，显示真实筹码和行动状态。 */
function centerNode(posMap) {
  const room = state.myRoom;
  const center = document.createElement("div");
  center.className = "mj-center";
  center.setAttribute("aria-label", "本局局况");
  const info = document.createElement("div");
  info.className = "mj-center-info";
  const round = document.createElement("strong");
  round.className = "mj-round";
  round.textContent = `${room.round_wind || "东"}风圈`;
  const hand = document.createElement("span");
  hand.className = "mj-round-hand";
  hand.textContent = `第 ${room.hand_no || 1} 局`;
  const wall = document.createElement("span");
  wall.className = "mj-center-wall";
  wall.append("余 ");
  const count = document.createElement("b");
  count.textContent = room.wall_count ?? 0;
  wall.append(count, " 张");
  info.append(round, hand, wall);
  center.append(info);
  for (const [position, p] of posMap) {
    const marker = document.createElement("div");
    marker.className = `mj-center-seat center-${position}`;
    const active = !room.paused && ((room.phase === "discard" && room.to_act === p.username)
      || room.claim?.waiting?.includes(p.username));
    marker.classList.toggle("active", Boolean(active));
    const wind = document.createElement("span");
    wind.className = "mj-center-wind";
    wind.classList.toggle("dealer", Boolean(p.is_dealer));
    wind.textContent = p.seat_wind;
    const stack = document.createElement("span");
    stack.textContent = formatCoins(p.stack);
    marker.append(wind, stack);
    marker.title = `${p.nickname || p.username} · ${p.seat_wind}家 · 筹码 ${formatCoins(p.stack)}${active ? " · 等待操作" : ""}`;
    center.append(marker);
  }
  return center;
}

function statusNode() {
  const room = state.myRoom;
  const status = document.createElement("div");
  status.className = "mj-status";
  const la = room.last_action;
  status.textContent = la ? `${la.nickname} ${la.text}`
    : room.claim ? `${displayNameOf(room.claim.by)} 打出 ${tileLabel(room.claim.tile)}，等待响应…`
      : room.to_act ? `等待 ${displayNameOf(room.to_act)} 出牌…` : "发牌中…";
  status.title = status.textContent;
  return status;
}

function playerHeadNode(p, isMe) {
  const isSpectator = Boolean(state.myRoom?.spectator);
  const head = document.createElement("div");
  head.className = "mj-player-head";
  // 观战时"我"是被看玩家：展示其真实昵称与头像，不混入本地账号信息。
  const player = isMe && !isSpectator
    ? { ...p, ...state.currentUser, avatar: p?.avatar || state.currentUser?.avatar || "" }
    : p;
  const avatar = playerAvatarNode(player);
  const wind = document.createElement("span");
  wind.className = "mj-wind";
  wind.textContent = isMe
    ? (state.myRoom.players?.[mySeatIndex()]?.seat_wind || p?.seat_wind || "东")
    : p?.seat_wind;
  if (isMe ? state.myRoom.players?.[mySeatIndex()]?.is_dealer : p?.is_dealer) {
    wind.classList.add("dealer");
    wind.title = "庄家";
  }
  const name = document.createElement("span");
  name.className = "mj-player-name";
  name.textContent = isMe && !isSpectator
    ? (state.currentUser?.nickname || state.currentUser?.username || "我")
    : (p?.nickname || displayNameOf(p?.username) || "我");
  head.append(avatar, wind, name);
  if (!isMe) head.append(ratingBadge(p?.rating));
  const flowers = document.createElement("span");
  flowers.className = "mj-player-flowers";
  const flowerCount = isMe ? (state.myRoom.your_flowers || []).length : p?.flowers || 0;
  flowers.textContent = flowerCount ? `🌸${flowerCount}` : "";
  flowers.title = `花牌 ${flowerCount} 张`;
  head.append(flowers);
  if (p) {
    const stack = document.createElement("span");
    stack.className = "mj-player-stack";
    stack.textContent = formatCoins(p.stack);
    head.append(stack);
  }
  return head;
}

/** 对手座位：top（对家）/ left（左家）/ right（右家）。 */
function opponentNode(p, position) {
  const room = state.myRoom;
  const seat = document.createElement("div");
  seat.className = `mj-opp mj-opp-${position}`;
  seat.dataset.username = p.username;
  seat.dataset.seat = room.players.indexOf(p);
  if (!room.paused && ((room.phase === "discard" && room.to_act === p.username)
    || room.claim?.waiting?.includes(p.username))) seat.classList.add("active");
  seat.append(playerHeadNode(p, false));
  const rack = document.createElement("div");
  rack.className = `mj-player-rack rack-${position}`;
  rack.setAttribute("aria-label", `${p.nickname}的手牌和副露`);
  if ((p.melds || []).length) {
    const melds = document.createElement("div");
    melds.className = "mj-player-melds";
    melds.setAttribute("aria-label", "公开副露");
    for (const meld of p.melds) melds.append(meldNode(meld));
    rack.append(melds);
  }
  if (room.status === "playing" && p.in_hand && p.concealed) {
    const backs = document.createElement("div");
    backs.className = "mj-player-backs";
    const count = document.createElement("span");
    count.className = "mj-back-count";
    count.textContent = `手牌 ${p.concealed} 张`;
    count.title = `手牌 ${p.concealed} 张`;
    backs.append(count);
    const pics = document.createElement("span");
    pics.className = "mj-backs-pics";
    pics.setAttribute("aria-hidden", "true");
    for (let i = 0; i < p.concealed; i += 1) pics.append(mjBackNode());
    backs.append(pics);
    rack.append(backs);
  }
  seat.append(rack);
  return seat;
}

function myAreaNode() {
  const room = state.myRoom;
  const me = mySeatIndex();
  const area = document.createElement("div");
  area.className = "mj-me";
  if (!room.paused && ((room.phase === "discard" && isMyTurn())
    || room.claim?.waiting?.includes(selfUsername()))) {
    area.classList.add("active");
  }
  area.dataset.username = selfUsername();
  area.dataset.seat = me;
  area.append(playerHeadNode(room.players?.[me] || null, true));
  return area;
}

/** 牌河按座位分区，先出的在前；最新一张高亮。 */
function riverNode(username, position) {
  const room = state.myRoom;
  const river = document.createElement("div");
  river.className = `mj-river river-${position}`;
  const tiles = room.discards?.[username] || [];
  const positions = { top: "对家", right: "右家", left: "左家", bottom: "本家" };
  river.dataset.label = `${positions[position]} · ${tiles.length} 张`;
  river.setAttribute("aria-label", river.dataset.label);
  river.title = river.dataset.label;
  tiles.forEach((code, index) => {
    const node = mjTileNode(code, { mini: true });
    const isLast = room.last_discard && room.last_discard.by === username
      && room.last_discard.tile === code && index === tiles.length - 1;
    if (isLast) node.classList.add("just-discarded");
    river.append(node);
  });
  return river;
}

function tenpaiNode() {
  const tenpai = state.myRoom.tenpai;
  if (!tenpai || !tenpai.waits?.length) return null;
  const node = document.createElement("div");
  node.className = "mj-tenpai";
  const label = document.createElement("span");
  label.className = "mj-tenpai-label";
  label.textContent = `听 ${tenpai.waits.length} 种`;
  const tiles = document.createElement("div");
  tiles.className = "mj-corner-tiles";
  tiles.tabIndex = 0;
  tiles.setAttribute("aria-label", "听牌列表，可左右滚动");
  node.append(label, tiles);
  for (const code of tenpai.waits) {
    const chip = document.createElement("span");
    chip.className = "mj-tenpai-chip";
    chip.append(mjTileNode(code, { mini: true }));
    const count = document.createElement("span");
    const remaining = tenpai.remaining?.[code];
    count.textContent = remaining != null ? `×${remaining}` : "";
    chip.append(count);
    tiles.append(chip);
  }
  return node;
}

/** 牌河全览：四家完整牌河，网格随屏幕宽度排列。 */
function riverOverlayNode() {
  const room = state.myRoom;
  const overlay = document.createElement("div");
  overlay.className = "mj-river-overlay";
  const head = document.createElement("div");
  head.className = "mj-river-overlay-head";
  const title = document.createElement("div");
  title.textContent = "牌河 · 全部打出的牌";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "mj-river-overlay-close";
  close.textContent = "✕ 关闭";
  close.addEventListener("click", () => {
    riverOverlayOpen = false;
    renderGameView();
  });
  head.append(title, close);
  overlay.append(head);
  const me = mySeatIndex();
  const labels = ["本家（下）", "右家", "对家（上）", "左家"];
  for (const rel of [0, 1, 2, 3]) {
    const p = room.players[(me + rel) % room.players.length];
    const block = document.createElement("div");
    block.className = "mj-river-block";
    const name = document.createElement("div");
    name.className = "mj-river-block-name";
    name.textContent = `${labels[rel]} · ${p.nickname}`;
    block.append(name);
    const grid = document.createElement("div");
    grid.className = "mj-river-full";
    const tiles = room.discards?.[p.username] || [];
    for (const code of tiles) grid.append(mjTileNode(code, { small: true }));
    if (!tiles.length) {
      const empty = document.createElement("span");
      empty.className = "mj-river-empty";
      empty.textContent = "尚未出牌";
      grid.append(empty);
    }
    block.append(grid);
    overlay.append(block);
  }
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      riverOverlayOpen = false;
      renderGameView();
    }
  });
  return overlay;
}

/* =========================================================
   结算回顾
========================================================= */

function resultNode(result) {
  const box = document.createElement("div");
  box.className = "poker-result mj-result";
  if (result.aborted) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "有人离桌，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  if (result.draw_game) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "牌墙摸空，荒庄流局：不计分，庄家连庄。";
    box.append(note);
  } else if (result.winner) {
    const head = document.createElement("div");
    head.className = "poker-result-row mj-win-row";
    const left = document.createElement("span");
    left.textContent = `🏆 ${result.winner_name || displayNameOf(result.winner)} `
      + `${result.zimo ? "自摸" : `胡 ${displayNameOf(result.loser)} 打出的 ${tileLabel(result.win_tile)}`}`
      + ` · ${result.fan_total} 番`;
    const right = document.createElement("span");
    right.className = "win";
    right.textContent = `+${formatCoins(result.gains?.[result.winner] || 0)}`;
    head.append(left, right);
    box.append(head);
    const fans = document.createElement("div");
    fans.className = "mj-fans";
    for (const fan of result.fans || []) {
      const chip = document.createElement("span");
      chip.className = "mj-fan-chip";
      chip.textContent = `${fan.name} ${fan.value}`;
      fans.append(chip);
    }
    if (fans.children.length) box.append(fans);
  }
  const paying = Object.entries(result.payouts || {});
  if (paying.length && !result.draw_game) {
    for (const [name, amount] of paying) {
      requestProfile(name);
      const row = document.createElement("div");
      row.className = "poker-result-row";
      const who = document.createElement("span");
      who.textContent = `${displayNameOf(name)} 支付`;
      const amt = document.createElement("span");
      amt.className = "lose";
      amt.textContent = `-${formatCoins(amount)}`;
      row.append(who, amt);
      box.append(row);
    }
  }
  const reveal = document.createElement("div");
  reveal.className = "uno-reveal";
  for (const [name, tiles] of Object.entries(result.hands || {})) {
    requestProfile(name);
    const line = document.createElement("div");
    line.className = "uno-reveal-row";
    const label = document.createElement("span");
    label.className = "uno-reveal-name";
    const won = name === result.winner;
    label.textContent = `${won ? "👑 " : ""}${displayNameOf(name)}`;
    const cardsBox = document.createElement("span");
    cardsBox.className = "uno-reveal-cards";
    for (const meld of result.melds?.[name] || []) {
      cardsBox.append(meldNode(meld, { small: true }));
    }
    for (const code of tiles || []) {
      cardsBox.append(mjTileNode(code, { small: true, win: won && code === result.win_tile }));
    }
    for (const code of result.flowers?.[name] || []) {
      cardsBox.append(mjTileNode(code, { small: true }));
    }
    line.append(label, cardsBox);
    reveal.append(line);
  }
  if (reveal.children.length) box.append(reveal);
  return box;
}

/* =========================================================
   常驻操作条：打出 / 吃 / 碰 / 杠 常驻，可胡时显示胡
========================================================= */

function actionButton(label, cls, enabled, onClick, title = "") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `mj-act ${cls}`;
  btn.textContent = label;
  btn.disabled = !enabled || actionLock || state.myRoom.paused;
  if (title) btn.title = title;
  btn.addEventListener("click", onClick);
  return btn;
}

function chiPairLabel(pair) {
  const tile = claimTile();
  const tiles = [...pair, tile].sort((a, b) => a - b);
  return tiles.map(tileLabel).join("·");
}

function actionsNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "mj-actions";
  const options = room.your_options || {};
  const claim = options.claim;
  const myDiscardTurn = isMyTurn() && room.phase === "discard" && !room.paused;
  const canDiscard = myDiscardTurn && selected.size === 1;

  const discard = actionButton(
    canDiscard ? `打出 ${tileLabel(room.your_hand[[...selected][0]])}` : "打出",
    "primary", canDiscard,
    () => mjAct({ action: "discard", index: [...selected][0] }),
    "先点选一张手牌，再点打出");
  discard.hidden = room.spectator || room.phase === "claim";
  bar.append(discard);

  const chis = chiOptions();
  bar.append(actionButton(
    chis.length === 1 ? `吃 ${chiPairLabel(chis[0])}` : "吃",
    "eat", chis.length > 0,
    () => {
      if (chis.length === 1) {
        mjAct({ action: "claim", kind: "chi", tiles: chis[0] });
        return;
      }
      chiOpen = !chiOpen;
      gangOpen = false;
      renderGameView();
    },
    chis.length > 1 ? "有多种吃法，点击展开选择" : ""));

  bar.append(actionButton(
    claim?.peng ? `碰 ${tileLabel(claimTile())}` : "碰",
    "peng", Boolean(claim?.peng),
    () => mjAct({ action: "claim", kind: "peng" })));

  const gangs = gangActions();
  bar.append(actionButton(
    gangs.length === 1 ? gangs[0].label : "杠",
    "gang", gangs.length > 0,
    () => {
      if (gangs.length === 1) {
        gangs[0].run();
        return;
      }
      gangOpen = !gangOpen;
      chiOpen = false;
      renderGameView();
    }));

  const canHu = Boolean(options.zimo) || Boolean(claim?.hu);
  const hu = actionButton(
    options.zimo ? "自摸胡" : "胡",
    "hu", canHu,
    () => mjAct(claim?.hu && !options.zimo
      ? { action: "claim", kind: "hu" }
      : { action: "hu" }));
  hu.hidden = !canHu;                       // 可胡时才显示
  bar.append(hu);

  if (room.phase === "claim" && claim && !options.passed) {
    bar.append(actionButton("过", "pass", true, () => mjAct({ action: "pass" })));
  }
  return bar;
}

/** 吃/杠多选项时的二级选项行。 */
function optionChipsNode() {
  const box = document.createElement("div");
  box.className = "mj-chips";
  let items = [];
  if (chiOpen && chiOptions().length > 1) {
    items = chiOptions().map((pair) => ({
      label: `吃 ${chiPairLabel(pair)}`,
      run: () => mjAct({ action: "claim", kind: "chi", tiles: pair }),
    }));
  } else if (gangOpen && gangActions().length > 1) {
    items = gangActions().map((g) => ({ label: g.label, run: g.run }));
  } else {
    return box;
  }
  for (const item of items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "mj-chip";
    chip.textContent = item.label;
    chip.disabled = actionLock || state.myRoom.paused;
    chip.addEventListener("click", item.run);
    box.append(chip);
  }
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "mj-chip cancel";
  cancel.textContent = "取消";
  cancel.addEventListener("click", () => {
    chiOpen = false;
    gangOpen = false;
    renderGameView();
  });
  box.append(cancel);
  return box;
}

/* =========================================================
   主渲染
========================================================= */

function renderMahjongTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    if (lastRoomView?.room_id !== room.room_id) riverOverlayOpen = false;
    actionLock = false;
    lastRoomView = room;
    turnDeadline = Date.now() + (room.turn_left || 0) * 1000;
  }
  body.replaceChildren();

  const handJson = JSON.stringify([room.room_id, room.hand_no, room.your_hand || []]);
  if (handJson !== lastHandJson) {
    selected = new Set();
    lastHandJson = handJson;
  }
  if (!chiOptions().length) chiOpen = false;
  if (!gangActions().length) gangOpen = false;

  const wrap = document.createElement("div");
  wrap.className = "casual-page mj-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "mj-table";
  table.append(topbarNode());
  const stage = document.createElement("div");
  stage.className = "mj-stage";
  table.append(stage);

  const players = room.players;
  const me = mySeatIndex();
  const positionOf = ["bottom", "right", "top", "left"];
  const posMap = new Map(players.map((p) =>
    [positionOf[(players.indexOf(p) - me + players.length) % players.length], p]));

  const seatRow = document.createElement("div");
  seatRow.className = "mj-seat-row";
  for (const pos of ["left", "top", "right"]) {
    const p = posMap.get(pos);
    if (p) seatRow.append(opponentNode(p, pos));
  }
  stage.append(seatRow);

  const rivers = document.createElement("div");
  rivers.className = "mj-rivers";
  for (const pos of ["top", "left", "right", "bottom"]) {
    const p = posMap.get(pos);
    if (p) rivers.append(riverNode(p.username, pos));
  }
  rivers.append(centerNode(posMap));
  stage.append(rivers);

  const selfRow = document.createElement("div");
  selfRow.className = "mj-self-row";
  selfRow.append(myAreaNode());
  const selfControls = document.createElement("div");
  selfControls.className = "casual-dock mj-self-controls";
  selfControls.setAttribute("aria-label", "我的操作");
  selfRow.append(selfControls);

  if (room.result) table.append(resultNode(room.result));
  if (room.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  if (riverOverlayOpen) table.append(riverOverlayNode());
  wrap.append(table);

  const dock = document.createElement("div");
  dock.className = "casual-dock mj-dock";
  dock.classList.toggle("is-my-turn", !room.spectator && !room.paused
    && ((isMyTurn() && room.phase === "discard") || Boolean(room.your_options?.claim)));

  const dockHead = document.createElement("div");
  dockHead.className = "mj-dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  const hand = room.your_hand || [];
  const claimMine = room.your_options?.claim;
  label.textContent = room.paused ? "牌局已暂停，等待继续"
    : room.spectator ? `观战视角 · ${hand.length} 张`
    : room.your_options?.submitted ? "已确认操作，等待其他玩家响应"
    : room.your_options?.passed ? "已过，等待其他玩家响应"
    : room.claim && claimMine
    ? "可响应：点吃 / 碰 / 杠 / 胡，或点过"
    : isMyTurn() && room.phase === "discard"
      ? "轮到你 · 点选一张牌后点「打出」"
      : `你的手牌 · ${hand.length} 张`;
  dockHead.append(label);
  const countdown = document.createElement("div");
  countdown.className = "countdown";
  const fill = document.createElement("div");
  fill.className = "countdown-fill";
  countdown.append(fill);
  dockHead.append(countdown);
  dock.append(dockHead);

  const footer = document.createElement("div");
  footer.className = "mj-footer";
  const flowers = document.createElement("div");
  flowers.className = "mj-my-flowers mj-corner";
  flowers.setAttribute("aria-label", "我的花牌");
  const tenpai = tenpaiNode() || document.createElement("div");
  tenpai.classList.add("mj-tenpai", "mj-corner");
  tenpai.setAttribute("aria-label", "听牌及剩余张数");
  footer.append(flowers, selfRow, tenpai);

  const myRack = document.createElement("div");
  myRack.className = "mj-player-rack rack-bottom";
  myRack.setAttribute("aria-label", "我的手牌和副露");
  const myMelds = room.players?.[me]?.melds || [];
  if (myMelds.length) {
    const melds = document.createElement("div");
    melds.className = "mj-my-melds";
    melds.setAttribute("aria-label", "我的副露");
    for (const meld of myMelds) melds.append(meldNode(meld, { small: true }));
    myRack.append(melds);
  }

  if ((room.your_flowers || []).length) {
    const flabel = document.createElement("span");
    flabel.className = "my-cards-label";
    flabel.textContent = `花牌 · ${room.your_flowers.length} 张`;
    const flowerTiles = document.createElement("div");
    flowerTiles.className = "mj-corner-tiles";
    flowerTiles.tabIndex = 0;
    flowerTiles.setAttribute("aria-label", "花牌列表，可左右滚动");
    flowers.append(flabel, flowerTiles);
    for (const code of room.your_flowers) flowerTiles.append(mjTileNode(code, { small: true }));
  }

  const chips = optionChipsNode();
  if (chips.children.length) selfControls.append(chips);

  const myCards = document.createElement("div");
  myCards.className = "mj-hand";
  if (!hand.length) {
    const waiting = document.createElement("span");
    waiting.className = "my-cards-label";
    waiting.textContent = room.status === "playing" ? "等待本局结束…" : "等待发牌…";
    myCards.append(waiting);
  }
  for (const { code, index } of sortHand(hand)) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "mj-hand-card";
    node.dataset.index = index;
    node.setAttribute("aria-pressed", String(selected.has(index)));
    node.disabled = actionLock || room.paused || Boolean(room.spectator);
    if (index === room.your_draw_index) {
      node.classList.add("drawn");
      node.title = "刚摸到的牌";
    }
    node.append(mjTileNode(code, { picked: selected.has(index) }));
    node.setAttribute("aria-label", tileLabel(code));
    node.addEventListener("click", () => {
      if (selected.has(index)) selected.clear();
      else selected = new Set([index]);
      document.querySelectorAll(".mj-hand-card").forEach((button) => {
        const picked = selected.has(Number(button.dataset.index));
        button.setAttribute("aria-pressed", String(picked));
        button.querySelector(".mj-tile").classList.toggle("picked", picked);
      });
      document.querySelector(".mj-actions")?.replaceWith(actionsNode());
    });
    node.addEventListener("dblclick", () => {
      if (!(isMyTurn() && room.phase === "discard" && !room.paused)) return;
      selected.clear();
      selected.add(index);
      mjAct({ action: "discard", index });
    });
    myCards.append(node);
  }
  myRack.append(myCards);
  stage.append(myRack);
  selfControls.prepend(actionsNode());

  if (!room.paused && room.turn_left > 0
      && ((isMyTurn() && room.phase === "discard") || claimMine)) {
    const remaining = (turnDeadline - Date.now()) / 1000;
    if (remaining > 0) startHallTicker(fill, remaining);
  }
  // Keep the hint with the actions; reserve the two bottom corners for tile information.
  selfControls.append(dock);
  table.append(footer);

  // Long rivers retain all tiles; keep the latest rows visible on each update.
  for (const river of rivers.querySelectorAll(".mj-river")) {
    river.scrollTop = river.scrollHeight;
  }

  reapplySeatBubbles();
}

/* =========================================================
   事件绑定
========================================================= */

registerGame("mahjong", {
  compactDesktopChat: false,
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "国标麻将需要正好 4 名玩家：吃碰杠胡、八番起和，花牌每张 1 分。等待房主开局，中途退出本局作废、筹码原封退回。",
  noNextHint: () => "人数不足 4 人或有人筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderMahjongTable,
  renderReview: (result) => {
    const review = document.createElement("div");
    const title = document.createElement("div");
    title.className = "hall-section-title";
    title.style.marginTop = "0";
    title.textContent = "本局回顾";
    review.append(title, resultNode(result));
    return review;
  },
  seatElement: (index) => document.querySelector(
    `#gameMain .mj-table [data-seat="${index}"] .mj-player-head`),
});
