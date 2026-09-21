/* 德州扑克牌桌：座位下注、动作条、公共牌与手牌回顾。 */

import { displayNameOf, elements, formatCoins, ratingBadge, renderGameView, selfUsername, send, startHallTicker, state } from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const SUIT_CHARS = ["♠", "♥", "♦", "♣"];
const RANK_CHARS = { 11: "J", 12: "Q", 13: "K", 14: "A" };
const STAGE_NAMES = {
  preflop: "翻牌前",
  flop: "翻牌",
  turn: "转牌",
  river: "河牌",
  showdown: "摊牌",
};

let hallBoardCount = 0;
let lastHoleKey = "";
const desktopPoker = window.matchMedia("(min-width: 1024px)");
let attentionTimer = 0;
let titleBeforeTurn = null;

function clearAttention() {
  window.clearInterval(attentionTimer);
  if (titleBeforeTurn !== null) document.title = titleBeforeTurn;
  titleBeforeTurn = null;
}
document.addEventListener("gameviewchange", () => {
  clearAttention();
});
desktopPoker.addEventListener("change", () => {
  clearAttention();
  if (state.myRoom?.game_type === "holdem") renderGameView();
});

function turnNotice(dock, myTurn) {
  const note = document.createElement("div");
  note.className = "desktop-turn-notice";
  note.setAttribute("role", "status");
  const text = document.createElement("span");
  note.append(text);
  dock.append(note);
  const active = myTurn && !state.myRoom.paused && Boolean(state.myRoom.your_options);
  dock.classList.toggle("is-my-turn", active);
  if (!active) {
    const me = state.myRoom.players.find((p) => p.username === selfUsername());
    if (state.myRoom.spectator) {
      text.textContent = state.myRoom.paused ? "牌局已暂停" : `观战中 · 等待 ${displayNameOf(state.myRoom.to_act) || "其他玩家"} 行动`;
    } else {
      text.textContent = state.myRoom.paused ? "牌局已暂停" : me?.folded ? "你已弃牌 · 等待本手结束" : me?.allin ? "你已全下 · 等待摊牌" : `等待 ${displayNameOf(state.myRoom.to_act) || "其他玩家"} 行动`;
    }
    return;
  }
  const deadline = Date.now() + Math.max(0, state.myRoom.turn_left || 0) * 1000;
  const tick = () => {
    const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    const message = seconds ? `轮到你了 · 剩余 ${seconds} 秒` : "等待服务器确认…";
    if (text.textContent !== message) text.textContent = message;
    dock.classList.toggle("turn-urgent", seconds > 0 && seconds <= 5);
    if (!seconds) {
      dock.querySelectorAll(".action-bar button, .action-bar input").forEach((control) => { control.disabled = true; });
      clearAttention();
    }
  };
  if (!desktopPoker.matches) return;
  tick();
  if (deadline <= Date.now()) return;
  titleBeforeTurn = document.title;
  document.title = `轮到你了 · ${titleBeforeTurn}`;
  attentionTimer = window.setInterval(tick, 250);
}

// 服务端拒绝动作时解除提交锁，玩家可修正并重试。
document.addEventListener("gameactionerror", () => {
  const bar = document.querySelector(".poker-dock .action-bar");
  if (!bar) return;
  bar.dataset.pending = "";
  bar.querySelectorAll("button, input").forEach((control) => { control.disabled = false; });
});

function seatNode(p) {
  const seat = document.createElement("div");
  seat.className = "seat";
  if (state.myRoom.to_act === p.username) seat.classList.add("active");
  if (p.folded) seat.classList.add("folded");
  if (p.username === selfUsername()) seat.classList.add("me");
  const name = document.createElement("div");
  name.className = "seat-name";
  name.textContent = p.nickname;
  if (p.dealer) {
    const chip = document.createElement("span");
    chip.className = "dchip";
    chip.textContent = "D";
    name.append(chip);
  }
  const stack = document.createElement("div");
  stack.className = "seat-stack";
  stack.textContent = formatCoins(p.stack);
  // 本轮下注：座位前压着的筹码，标明这一条街投入了多少
  const bet = document.createElement("div");
  bet.className = "seat-bet";
  if (p.bet > 0) {
    bet.classList.add("on");
    bet.textContent = p.folded ? `本轮 ${formatCoins(p.bet)}` : `本轮 +${formatCoins(p.bet)}`;
  }
  // 本手累计：跨街之后才显示，避免和「本轮」重复
  const total = document.createElement("div");
  total.className = "seat-total";
  if ((p.hand_bet || 0) > (p.bet || 0)) {
    total.textContent = `本手投入 ${formatCoins(p.hand_bet)}`;
  }
  const status = document.createElement("div");
  status.className = "seat-status";
  if (p.folded) {
    status.textContent = "弃牌";
    status.classList.add("fold");
  } else if (p.allin) {
    status.textContent = "全下";
    status.classList.add("allin");
  } else if (state.myRoom.to_act === p.username) {
    status.textContent = "思考中…";
    status.classList.add("think");
  } else {
    status.textContent = p.in_hand ? "" : "观战";
  }
  seat.append(name, ratingBadge(p.rating), stack, bet, total, status);
  return seat;
}

function actionBarNode(options) {
  const me = state.myRoom.players.find((p) => p.username === selfUsername());
  const bar = document.createElement("div");
  bar.className = "action-bar";
  const act = (payload) => {
    if (state.myRoom?.spectator || bar.dataset.pending) return;
    if (send({ type: "poker_action", ...payload })) {
      bar.dataset.pending = "true";
      bar.querySelectorAll("button, input").forEach((control) => { control.disabled = true; });
    }
  };

  const fold = document.createElement("button");
  fold.className = "action-btn danger";
  fold.type = "button";
  fold.textContent = "弃牌";
  fold.addEventListener("click", () => act({ action: "fold" }));
  bar.append(fold);

  if (options.check) {
    const check = document.createElement("button");
    check.className = "action-btn";
    check.type = "button";
    check.textContent = "看牌";
    check.addEventListener("click", () => act({ action: "check" }));
    bar.append(check);
  }
  if (options.call) {
    const call = document.createElement("button");
    call.className = "action-btn primary";
    call.type = "button";
    call.textContent = options.call_amount >= (me?.stack || 0)
      ? `全下跟注 ${formatCoins(options.call_amount)}`
      : `跟注 ${formatCoins(options.call_amount)}`;
    call.addEventListener("click", () => act({ action: "call" }));
    bar.append(call);
  }
  if (options.can_raise) {
    const input = document.createElement("input");
    input.className = "raise-input";
    input.type = "number";
    input.inputMode = "decimal";
    input.required = true;
    input.min = String(options.raise_min);
    input.max = String(options.raise_max);
    input.step = "0.01";
    input.setAttribute("aria-label", "加注到的总额");
    input.value = String(options.raise_min);
    bar.append(input);
    const raise = document.createElement("button");
    raise.className = "action-btn primary";
    raise.type = "button";
    raise.textContent = "加注到";
    raise.addEventListener("click", () => {
      let value = Number(input.value);
      if (!input.value || !Number.isFinite(value)) { input.reportValidity(); return; }
      value = Math.round(
        Math.min(Math.max(value, options.raise_min), options.raise_max) * 100,
      ) / 100;
      act({ action: "raise", raise_to: value });
    });
    bar.append(raise);
    const presets = document.createElement("div");
    presets.className = "desktop-raise-presets";
    for (const [label, amount] of [["最小加注", options.raise_min], ["½ 底池", (me?.bet || 0) + (options.call_amount || 0) + (state.myRoom.pot + (options.call_amount || 0)) / 2], ["底池", (me?.bet || 0) + (options.call_amount || 0) + state.myRoom.pot + (options.call_amount || 0)], ["全下", options.raise_max]]) {
      const preset = document.createElement("button");
      preset.type = "button";
      preset.textContent = label;
      preset.addEventListener("click", () => {
        input.value = String(Math.round(Math.min(options.raise_max, Math.max(options.raise_min, amount)) * 100) / 100);
        input.focus();
      });
      presets.append(preset);
    }
    bar.append(presets);
  }
  if (options.allin) {
    const allin = document.createElement("button");
    allin.className = "action-btn danger";
    allin.type = "button";
    allin.textContent = `全下 ${formatCoins(options.allin_to)}`;
    allin.addEventListener("click", () => act({ action: "raise", raise_to: options.allin_to }));
    bar.append(allin);
  }
  return bar;
}

/** 结算回顾：公共牌 + 每个人的手牌（含弃牌者）+ 本手输赢。 */

function reviewNode(result, showBoard = true) {
  const box = document.createElement("div");
  box.className = "poker-review";

  if (showBoard) {
    const head = document.createElement("div");
    head.className = "review-head";
    const label = document.createElement("span");
    label.textContent = "公共牌";
    const potChip = document.createElement("span");
    potChip.className = "review-pot";
    potChip.textContent = `底池 ${formatCoins(result.pot || 0)}`;
    head.append(label, potChip);
    box.append(head);

    const board = document.createElement("div");
    board.className = "review-board";
    const boardCards = result.board || [];
    for (let i = 0; i < 5; i += 1) {
      if (i < boardCards.length) {
        board.append(cardNode(boardCards[i], { settled: true }));
      } else {
        const slot = document.createElement("span");
        slot.className = "pcard-slot";
        board.append(slot);
      }
    }
    box.append(board);
  }

  const rows = document.createElement("div");
  rows.className = "review-rows";
  for (const item of reviewRows(result)) {
    const paid = Number(result.payouts?.[item.username] || 0);
    const stake = Number(item.committed || 0);
    const net = Math.round((paid - stake) * 100) / 100;
    const row = document.createElement("div");
    row.className = "review-row";
    if (item.folded) row.classList.add("folded");
    else if (net > 0) row.classList.add("winner");

    const who = document.createElement("div");
    who.className = "review-who";
    who.textContent = item.nickname || displayNameOf(item.username);

    const cards = document.createElement("div");
    cards.className = "review-cards";
    for (const c of item.cards || []) cards.append(cardNode(c, { settled: true }));

    const handName = document.createElement("div");
    handName.className = "review-hand";
    handName.textContent = item.folded ? "已弃牌" : (item.hand_name || "未摊牌");

    const delta = document.createElement("div");
    delta.className = `review-delta ${net > 0 ? "win" : net < 0 ? "lose" : "flat"}`;
    delta.textContent = `${net > 0 ? "+" : ""}${formatCoins(net)}`;

    row.append(who, cards, handName, delta);
    rows.append(row);
  }
  box.append(rows);
  return box;
}

/** 优先用服务端的 hands 字段；老payload 退回到 reveal + payouts。 */

function reviewRows(result) {
  if (Array.isArray(result.hands) && result.hands.length) return result.hands;
  const stakes = result.committed || {};
  return (result.reveal || []).map((item) => ({
    username: item.username,
    nickname: displayNameOf(item.username),
    cards: item.cards,
    hand_name: item.hand_name,
    folded: false,
    committed: stakes[item.username] || 0,
  }));
}

function cardNode(card, opts = {}) {
  const node = document.createElement("span");
  node.className = `pcard${card.s === 1 || card.s === 2 ? " red" : ""}${opts.big ? " big" : ""}`;
  if (opts.settled) node.style.animation = "none";
  else if (opts.delay) node.style.animationDelay = `${opts.delay}ms`;
  const rank = document.createElement("span");
  rank.className = "pc-rank";
  rank.textContent = RANK_CHARS[card.r] || String(card.r);
  const suit = document.createElement("span");
  suit.className = "pc-suit";
  suit.textContent = SUIT_CHARS[card.s];
  node.append(rank, suit);
  return node;
}

function renderPokerTable() {
  const body = elements.gameMain;
  body.replaceChildren();

  const wrap = document.createElement("div");
  wrap.className = "poker-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "poker-table";

  const topbar = document.createElement("div");
  topbar.className = "poker-topbar";
  const left = document.createElement("span");
  left.textContent = `第 ${state.myRoom.hand_no || "-"} 手 · 盲注 ${state.myRoom.blind}/${state.myRoom.blind * 2}`;
  const right = document.createElement("span");
  right.textContent = state.myRoom.paused ? "⏸ 已暂停" : (STAGE_NAMES[state.myRoom.stage] || "");
  topbar.append(left, right);
  table.append(topbar);

  // 状态栏：最新状态变化一目了然
  const status = document.createElement("div");
  status.className = "poker-status";
  const la = state.myRoom.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : state.myRoom.to_act
      ? `等待 ${displayNameOf(state.myRoom.to_act)} 行动…`
      : "发牌中…";
  table.append(status);

  const pot = document.createElement("div");
  pot.className = "poker-pot";
  pot.textContent = state.myRoom.pot ? `底池 ${formatCoins(state.myRoom.pot)}` : "";
  table.append(pot);

  const board = document.createElement("div");
  board.className = "poker-board";
  const cards = state.myRoom.board || [];
  if (!cards.length) hallBoardCount = 0;
  for (let i = 0; i < 5; i += 1) {
    if (i < cards.length) {
      const opts = {};
      if (i >= hallBoardCount) opts.delay = (i - hallBoardCount) * 140;
      else opts.settled = true;
      board.append(cardNode(cards[i], opts));
    } else {
      const slot = document.createElement("div");
      slot.className = "pcard-slot";
      board.append(slot);
    }
  }
  hallBoardCount = cards.length;
  table.append(board);

  const seats = document.createElement("div");
  seats.className = "poker-seats";
  const players = state.myRoom.players;
  const myIndex = Math.max(0, players.findIndex((p) => p.username === selfUsername()));
  players.forEach((p, index) => {
    const seat = seatNode(p);
    const angle = ((index - myIndex + players.length) % players.length) * Math.PI * 2 / players.length;
    seat.style.setProperty("--seat-x", `${50 + 41 * Math.sin(angle)}%`);
    seat.style.setProperty("--seat-y", `${50 + 39 * Math.cos(angle)}%`);
    seats.append(seat);
  });
  table.append(seats);

  if (state.myRoom.result) table.append(reviewNode(state.myRoom.result, false));

  if (state.myRoom.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);

  const dock = document.createElement("div");
  dock.className = "poker-dock";
  const dockHead = document.createElement("div");
  dockHead.className = "dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  label.textContent = "你的手牌";
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  dockHead.append(label, chatToggle);
  dock.append(dockHead);
  const myCards = document.createElement("div");
  myCards.className = "my-cards";
  const holeKey = state.myRoom.your_hole ? JSON.stringify(state.myRoom.your_hole) : "";
  if (state.myRoom.your_hole) {
    const fresh = holeKey !== lastHoleKey;
    myCards.append(cardNode(state.myRoom.your_hole[0], { big: true, settled: !fresh }));
    myCards.append(cardNode(state.myRoom.your_hole[1], { big: true, settled: !fresh, delay: fresh ? 120 : 0 }));
  } else {
    lastHoleKey = "";
    const waiting = document.createElement("span");
    waiting.className = "my-cards-label";
    waiting.textContent = "等待下一手发牌…";
    myCards.append(waiting);
  }
  lastHoleKey = holeKey;
  dock.append(myCards);

  const myTurn = Boolean(selfUsername()) && state.myRoom.to_act === selfUsername();
  const countdown = document.createElement("div");
  countdown.className = "countdown";
  const fill = document.createElement("div");
  fill.className = "countdown-fill";
  countdown.append(fill);
  dock.append(countdown);

  if (!state.myRoom.paused) {
    if (myTurn && state.myRoom.your_options) {
      dock.append(actionBarNode(state.myRoom.your_options));
      if (state.myRoom.turn_left > 0) startHallTicker(fill, state.myRoom.turn_left);
    }
  } else {
    const pausedNote = document.createElement("div");
    pausedNote.className = "last-action";
    pausedNote.textContent = "牌局已暂停";
    dock.append(pausedNote);
  }
  turnNotice(dock, myTurn);
  wrap.append(dock);

  reapplySeatBubbles();
}

registerGame("holdem", {
  stakeLabel: "小盲注",
  blindLabel: "下一局盲注",
  waitingHint: "等待房主开局。中途退出会自动弃牌，已投入的筹码留在底池。",
  noNextHint: () =>
    `再来一局需每人再买入 ${formatCoins(state.myRoom.buy_in)}，余额不足者将离桌；`
    + "过半数投「解散」则按当前筹码退还所有人并关闭房间。",
  renderTable: renderPokerTable,
  renderHandResult: (result) => reviewNode(result),
  renderReview: (result) => {
    const wrap = document.createElement("div");
    const title = document.createElement("div");
    title.className = "hall-section-title";
    title.style.marginTop = "0";
    title.textContent = "本手回顾";
    wrap.append(title, reviewNode(result));
    return wrap;
  },
  seatElement: (index) => document.querySelector(`#gameMain .poker-seats .seat:nth-child(${index + 1})`),
});
