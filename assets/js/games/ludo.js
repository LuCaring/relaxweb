/* 飞行棋场景：左侧正方形棋盘（15×15 网格，四角机场、52 格主圈、
   终点跑道），右侧竖排操作列（掷骰 / 选机 / 提示）。桌面端沿用麻将
   布局：棋局与操作在左，整高房间聊天栏在右。起飞、撞机与结算全部
   以服务器视图为准。 */

import {
  displayNameOf, elements, formatCoins, formatCoinsWhole, playerAvatarNode,
  renderGameView, requestProfile, selfUsername, send, startHallTicker, state,
} from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

/* ---- 棋盘几何（与 games/ludo.py 保持一致） ---- */

const LOOP_STEPS = 51;        // 主圈步数：journey 0..50
const GOAL = 56;              // 终点
const ENTRY = [0, 13, 26, 39];
const FLY_FROM = 16;
const FLY_TO = 28;
const COLOR_NAMES = ["红", "黄", "蓝", "绿"];
const COLOR_KEYS = ["red", "yellow", "blue", "green"];

// 15×15 网格上的 52 格主圈（row, col），顺时针。
const LOOP = [
  [6, 1], [6, 2], [6, 3], [6, 4], [6, 5],
  [5, 6], [4, 6], [3, 6], [2, 6], [1, 6], [0, 6],
  [0, 7],
  [0, 8], [1, 8], [2, 8], [3, 8], [4, 8], [5, 8],
  [6, 9], [6, 10], [6, 11], [6, 12], [6, 13], [6, 14],
  [7, 14],
  [8, 14], [8, 13], [8, 12], [8, 11], [8, 10], [8, 9],
  [9, 8], [10, 8], [11, 8], [12, 8], [13, 8], [14, 8],
  [14, 7],
  [14, 6], [13, 6], [12, 6], [11, 6], [10, 6], [9, 6],
  [8, 5], [8, 4], [8, 3], [8, 2], [8, 1], [8, 0],
  [7, 0],
  [6, 0],
];
// 各色终点跑道 5 格（journey 51..55），从外向内。
const RUNWAY = [
  [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5]],
  [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7]],
  [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9]],
  [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7]],
];
// 各色机场停机位（棋盘百分比坐标，1 号机在左上，顺时针排列）。
const HANGAR_SLOTS = [
  [[10, 10], [10, 23], [23, 10], [23, 23]],
  [[10, 77], [10, 90], [23, 77], [23, 90]],
  [[77, 77], [77, 90], [90, 77], [90, 90]],
  [[77, 10], [77, 23], [90, 10], [90, 23]],
];

let actionLock = false;
let lastRoomView = null;
let turnDeadline = 0;

function loopIndexOf(color, journey) {
  if (!(journey >= 0 && journey <= LOOP_STEPS - 1)) return null;
  return (ENTRY[color] + journey) % LOOP.length;
}

/** journey -> [row, col]；机场返回 null，终点返回中心格。 */
function tokenCellOf(color, journey) {
  const idx = loopIndexOf(color, journey);
  if (idx != null) return LOOP[idx];
  if (journey === GOAL) return [7, 7];
  if (journey >= LOOP_STEPS) return RUNWAY[color][journey - LOOP_STEPS];
  return null;
}

function positionText(journey) {
  if (journey === -1) return "机场";
  if (journey === GOAL) return "已到达";
  if (journey >= LOOP_STEPS) return `跑道第 ${journey - LOOP_STEPS + 1} 格`;
  return `主圈第 ${journey + 1} 格`;
}

function cellPct(row, col) {
  return { x: (col + 0.5) / 15 * 100, y: (row + 0.5) / 15 * 100 };
}

function rulesSummary(rules) {
  if (!rules) return "";
  return [
    rules.launch === 5 ? "掷5或6起飞" : "掷6起飞",
    rules.extra_roll ? "掷6连投" : "不连投",
    rules.jump4 ? "同色跳格" : "无跳格",
    rules.fly12 ? "飞行捷径" : "无飞行",
    rules.payout === "rank" ? "按名次结算" : "冠军通吃",
  ].join(" · ");
}

/* =========================================================
   操作发送
========================================================= */

function ludoAct(payload) {
  if (actionLock || state.myRoom?.spectator) return;
  actionLock = send({ type: "poker_action", ...payload });
  if (actionLock) {
    document.querySelectorAll(".ludo-side button, .ludo-plane").forEach((b) => { b.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "ludo") return;
  actionLock = false;
  renderGameView();
});

function isMyTurn() {
  const me = selfUsername();
  return Boolean(me) && state.myRoom.to_act === me;
}

/* =========================================================
   棋盘渲染
========================================================= */

function topbarNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "ludo-topbar";
  const left = document.createElement("span");
  left.className = "ludo-topbar-info";
  left.textContent = `飞行棋 · ${rulesSummary(room.rules)} · 底注 ${formatCoinsWhole(room.blind)}`;
  left.title = `房主 ${displayNameOf(room.owner)} · 第 ${room.race_no || 1} 局竞速`;
  const right = document.createElement("div");
  right.className = "ludo-topbar-right";
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle ludo-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬";
  chatToggle.setAttribute("aria-label", "打开聊天");
  chatToggle.addEventListener("click", openChatOverlay);
  right.append(chatToggle);
  bar.append(left, statusNode(), right);
  return bar;
}

function statusNode() {
  const room = state.myRoom;
  const status = document.createElement("div");
  status.className = "ludo-status";
  const la = room.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : room.to_act ? `等待 ${displayNameOf(room.to_act)} 掷骰子…` : "准备开局…";
  status.title = status.textContent;
  return status;
}

/** 静态棋盘：主圈、起飞/飞行标记、四角机场、终点跑道与中央终点。 */
function staticBoardNode(board) {
  const frag = document.createDocumentFragment();
  LOOP.forEach(([row, col], idx) => {
    const cell = document.createElement("div");
    const color = idx % 4;
    cell.className = `ludo-cell c${color}`;
    cell.style.gridArea = `${row + 1} / ${col + 1}`;
    if (ENTRY.includes(idx)) {
      cell.classList.add("launch");
      cell.title = `${COLOR_NAMES[color]}方起飞格`;
    } else if (idx === (ENTRY[color] + FLY_FROM) % LOOP.length) {
      cell.classList.add("fly");
      cell.title = `${COLOR_NAMES[color]}方飞行格 · 直飞 12 格`;
    } else if (idx === (ENTRY[color] + FLY_TO) % LOOP.length) {
      cell.classList.add("fly-dest");
      cell.title = `${COLOR_NAMES[color]}方飞行落点`;
    }
    board.append(cell);
  });
  for (let color = 0; color < 4; color += 1) {
    for (const [row, col] of RUNWAY[color]) {
      const cell = document.createElement("div");
      cell.className = `ludo-cell runway c${color}`;
      cell.style.gridArea = `${row + 1} / ${col + 1}`;
      frag.append(cell);
    }
  }
  board.append(frag);
  for (let color = 0; color < 4; color += 1) {
    const hangar = document.createElement("div");
    hangar.className = `ludo-hangar h${color}`;
    board.append(hangar);
  }
  const center = document.createElement("div");
  center.className = "ludo-center";
  center.style.gridArea = "7 / 7 / 10 / 10";
  center.title = "终点：需掷出恰好点数到达";
  const disc = document.createElement("span");
  disc.textContent = "终";
  center.append(disc);
  board.append(center);
}

/** 玩家名牌：头像、昵称、筹码与到达进度，钉在自家机场角落。 */
function plateNode(p, index) {
  const room = state.myRoom;
  const plate = document.createElement("div");
  plate.className = `ludo-plate plate-${p.color ?? index}`;
  plate.dataset.username = p.username;
  plate.dataset.seat = String(index);
  if (!room.paused && room.to_act === p.username && room.status === "playing") {
    plate.classList.add("active");
  }
  const avatar = playerAvatarNode(p, "casual-avatar ludo-avatar");
  const info = document.createElement("span");
  info.className = "ludo-plate-name";
  const isMe = p.username === selfUsername() && !room.spectator;
  info.textContent = isMe ? "我" : (p.nickname || displayNameOf(p.username));
  const stack = document.createElement("span");
  stack.className = "ludo-plate-stack";
  stack.textContent = formatCoins(p.stack);
  const progress = document.createElement("span");
  progress.className = "ludo-plate-progress";
  progress.textContent = p.in_race ? `🛫${p.finished || 0}/4` : "";
  plate.title = `${info.textContent} · ${COLOR_NAMES[p.color ?? index]}方 · 筹码 ${formatCoins(p.stack)}`
    + (p.in_race ? ` · 到达 ${p.finished || 0}/4` : " · 未参与本局");
  plate.append(avatar, info, stack);
  if (progress.textContent) plate.append(progress);
  return plate;
}

/** 全部飞机棋子；可动的高亮可点，同格多机自动错开。 */
function planeTokensNode(room) {
  const frag = document.createDocumentFragment();
  const myPlanes = room.your_options?.plane || [];
  const groups = new Map();
  const entries = [];
  for (const p of room.players || []) {
    if (!p.in_race) continue;
    (p.planes || []).forEach((journey, idx) => {
      const slot = HANGAR_SLOTS[p.color]?.[idx];
      if (journey === -1 && slot) {
        entries.push({ p, idx, journey, x: slot[1], y: slot[0], key: `h${p.color}-${idx}` });
        return;
      }
      const cell = tokenCellOf(p.color, journey);
      if (!cell) return;
      const { x, y } = cellPct(cell[0], cell[1]);
      const key = `${cell[0]}-${cell[1]}`;
      entries.push({ p, idx, journey, x, y, key });
      groups.set(key, (groups.get(key) || 0) + 1);
    });
  }
  const seen = new Map();
  for (const item of entries) {
    const token = document.createElement("button");
    token.type = "button";
    token.className = `ludo-plane p${item.p.color}`;
    const count = groups.get(item.key) || 1;
    const nth = seen.get(item.key) || 0;
    seen.set(item.key, nth + 1);
    const spread = count > 1 ? (nth - (count - 1) / 2) * 3.4 : 0;
    token.style.left = `${item.x + spread}%`;
    token.style.top = `${item.y - (count > 1 ? Math.abs(spread) * 0.6 : 0)}%`;
    const movable = !room.paused && !room.spectator && isMyTurn()
      && room.awaiting_move && item.p.username === selfUsername()
      && myPlanes.includes(item.idx);
    token.classList.toggle("can-move", movable);
    token.disabled = !movable;
    token.setAttribute("aria-label",
      `${item.p.nickname} ${item.idx + 1}号机 ${positionText(item.journey)}${movable ? "，点击移动" : ""}`);
    token.title = `${item.p.nickname || displayNameOf(item.p.username)} · ${item.idx + 1}号机 · ${positionText(item.journey)}`;
    token.textContent = String(item.idx + 1);
    if (movable) {
      token.addEventListener("click", () => {
        ludoAct({ action: "move", plane: item.idx });
      });
    }
    frag.append(token);
  }
  return frag;
}

/* =========================================================
   操作列（竖排）：提示 / 骰子 / 掷骰 / 选机 / 战报
========================================================= */

const DICE_PIPS = {
  1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8],
  5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8],
};

function diceNode(value) {
  const dice = document.createElement("div");
  dice.className = "ludo-dice";
  if (!value) {
    dice.classList.add("idle");
    dice.textContent = "🎲";
    dice.title = "还没掷骰子";
    return dice;
  }
  dice.title = `掷出 ${value} 点`;
  for (let i = 0; i < 9; i += 1) {
    const pip = document.createElement("span");
    if (DICE_PIPS[value]?.includes(i)) pip.classList.add("on");
    dice.append(pip);
  }
  return dice;
}

function hintNode() {
  const room = state.myRoom;
  const hint = document.createElement("div");
  hint.className = "ludo-hint";
  const me = selfUsername();
  hint.textContent = room.paused ? "牌局已暂停，等待房主继续"
    : room.spectator ? "观战视角 · 等待玩家操作"
    : room.to_act === me && !room.awaiting_move ? "轮到你 · 点击「掷骰子」"
    : room.to_act === me && room.awaiting_move
      ? `掷出 ${room.dice} 点 · 点选棋盘上要移动的飞机`
    : room.awaiting_move ? `等待 ${displayNameOf(room.to_act)} 移动飞机…`
    : room.to_act ? `等待 ${displayNameOf(room.to_act)} 掷骰子…`
    : "准备开局…";
  return hint;
}

function sideNode() {
  const room = state.myRoom;
  const side = document.createElement("div");
  side.className = "ludo-side";
  side.setAttribute("aria-label", "操作区");
  side.append(hintNode());

  const diceBox = document.createElement("div");
  diceBox.className = "ludo-dice-box";
  diceBox.append(diceNode(room.awaiting_move ? room.dice : null));
  side.append(diceBox);

  const roll = document.createElement("button");
  roll.type = "button";
  roll.className = "ludo-btn primary";
  roll.textContent = "🎲 掷骰子";
  roll.disabled = actionLock || room.paused || room.spectator
    || !isMyTurn() || Boolean(room.awaiting_move);
  roll.addEventListener("click", () => ludoAct({ action: "roll" }));
  if (room.spectator || room.paused) roll.hidden = true;
  side.append(roll);

  const myPlanes = room.your_options?.plane || [];
  if (!room.paused && !room.spectator && isMyTurn() && room.awaiting_move && myPlanes.length) {
    const list = document.createElement("div");
    list.className = "ludo-plane-list";
    for (const idx of myPlanes) {
      const journey = (room.players || []).find((p) => p.username === selfUsername())
        ?.planes?.[idx];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ludo-btn";
      btn.textContent = `${idx + 1}号机 · ${positionText(journey ?? -1)}`;
      btn.disabled = actionLock || room.paused;
      btn.addEventListener("click", () => ludoAct({ action: "move", plane: idx }));
      list.append(btn);
    }
    side.append(list);
  }

  const la = room.last_action;
  const last = document.createElement("div");
  last.className = "ludo-last";
  last.textContent = la ? `${la.nickname} ${la.text}` : "";
  last.title = last.textContent;
  side.append(last);

  if (!room.paused && !room.spectator && isMyTurn() && room.turn_left > 0) {
    const countdown = document.createElement("div");
    countdown.className = "countdown";
    const fill = document.createElement("div");
    fill.className = "countdown-fill";
    countdown.append(fill);
    side.append(countdown);
    const remaining = (turnDeadline - Date.now()) / 1000;
    if (remaining > 0) startHallTicker(fill, remaining);
  }
  return side;
}

/* =========================================================
   主渲染
========================================================= */

function renderLudoTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    actionLock = false;
    lastRoomView = room;
    turnDeadline = Date.now() + (room.turn_left || 0) * 1000;
  }
  body.replaceChildren();

  const wrap = document.createElement("div");
  wrap.className = "casual-page ludo-page";
  const table = document.createElement("div");
  table.className = "ludo-table";
  table.append(topbarNode());

  const main = document.createElement("div");
  main.className = "ludo-main";
  const boardWrap = document.createElement("div");
  boardWrap.className = "ludo-board-wrap";
  const board = document.createElement("div");
  board.className = "ludo-board";
  board.setAttribute("role", "group");
  board.setAttribute("aria-label", "飞行棋棋盘");
  staticBoardNode(board);
  for (const [index, p] of (room.players || []).entries()) {
    board.append(plateNode(p, index));
  }
  board.append(planeTokensNode(room));
  boardWrap.append(board);
  main.append(boardWrap, sideNode());
  table.append(main);

  if (room.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);
  body.append(wrap);
  reapplySeatBubbles();
}

/* =========================================================
   结算回顾
========================================================= */

function resultNode(result) {
  const box = document.createElement("div");
  box.className = "ludo-result";
  if (!result.winner) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "有人离桌，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  const head = document.createElement("div");
  head.className = "poker-result-row ludo-win-row";
  const left = document.createElement("span");
  left.textContent = `🏆 ${result.winner_name || displayNameOf(result.winner)} 率先完成 4 架飞机！`;
  const right = document.createElement("span");
  right.className = "win";
  right.textContent = `+${formatCoins(result.gains?.[result.winner] || 0)}`;
  head.append(left, right);
  box.append(head);

  const pays = Object.entries(result.payouts || {});
  if (pays.length) {
    const note = document.createElement("div");
    note.className = "ludo-payout-note";
    note.textContent = result.payout === "rank"
      ? "按名次递增结算：第 2/3/4 名分别付 1/2/3 份底注"
      : "冠军通吃：每家支付一份底注";
    box.append(note);
  }
  const ranking = document.createElement("div");
  ranking.className = "ludo-ranking";
  (result.ranking || []).forEach((row) => {
    requestProfile(row.username);
    const line = document.createElement("div");
    line.className = "ludo-ranking-row";
    if (row.username === result.winner) line.classList.add("champion");
    const name = document.createElement("span");
    name.className = `ludo-ranking-name p${row.color}`;
    name.textContent = `${row.username === result.winner ? "👑 " : ""}${row.nickname || displayNameOf(row.username)}`;
    const detail = document.createElement("span");
    detail.className = "ludo-ranking-detail";
    detail.textContent = `到达 ${row.finished}/4 · 进度 ${row.progress}`;
    const net = document.createElement("span");
    const amount = Math.round(Number(row.net || 0) * 100) / 100;
    net.className = `ludo-ranking-net ${amount > 0 ? "win" : amount < 0 ? "lose" : "flat"}`;
    net.textContent = `${amount > 0 ? "+" : ""}${formatCoins(amount)}`;
    line.append(name, detail, net);
    ranking.append(line);
  });
  box.append(ranking);
  return box;
}

/* =========================================================
   事件绑定
========================================================= */

registerGame("ludo", {
  compactDesktopChat: false,
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "飞行棋需要至少 2 名玩家（最多 4 人）：掷点起飞、跳格飞行、撞机回机场，先送 4 架飞机到家者获胜。等待房主开局。",
  noNextHint: () => "人数不足 2 人或有人筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderLudoTable,
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
    `#gameMain .ludo-board .ludo-plate[data-seat="${index}"]`),
});
