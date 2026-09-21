/* UNO 牌桌：手牌、出牌区、行动条与牌局回顾。 */

import { displayNameOf, elements, formatCoins, ratingBadge, renderGameView, requestProfile, selfUsername, send, startHallTicker, state } from "../core.js";
import { animateUnoEvent } from "./uno-effects.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const UNO_COLORS = ["r", "y", "g", "b"];
const UNO_COLOR_NAMES = { r: "红", y: "黄", g: "绿", b: "蓝" };
const UNO_COLOR_TEXT = {
  r: "#e02020", y: "#c77800", g: "#1d9e4f", b: "#1f6fd6", w: "#3a3a3c",
};
const UNO_VALUE_GLYPHS = {
  skip: "⊘", rev: "⇄", d2: "+2", wild: "◎", wd4: "+4",
};

let pendingWildCard = null;   // 已点选、等待选色的万能牌下标
let pendingWildHand = "";
let unoActionLock = false;    // 出牌/摸牌后到下一帧视图前忽略重复点击

function unoAct(payload) {
  if (unoActionLock || state.myRoom?.spectator) return;
  unoActionLock = send({ type: "poker_action", ...payload });
  if (unoActionLock) {
    // 常驻 UNO 按钮保持可点、样式恒定（自身点击逻辑已挡住无效时机），
    // 其余行动按钮在下一帧视图到来前全部禁用，防止连点重复发送
    document.querySelectorAll(".uno-dock button:not(.uno-fab), .uno-challenge")
      .forEach((button) => { button.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "uno") return;
  unoActionLock = false;
  renderGameView();
});

function isMyTurn() {
  const me = selfUsername();
  return Boolean(me) && state.myRoom.to_act === me;
}

function unoMatches(card, active) {
  if (!active) return false;
  if (card.c === "w") return true;
  return card.c === active.c || card.v === active.v;
}

function unoCardNode(card, opts = {}) {
  const node = document.createElement("span");
  node.className = `ucard${opts.back ? " back" : ` ${card.c}`}${opts.big ? " big" : ""}`;
  if (opts.tint && UNO_COLOR_TEXT[opts.tint]) {
    node.style.boxShadow = `0 0 0 3px ${UNO_COLOR_TEXT[opts.tint]}, 0 8px 18px rgba(0,0,0,.3)`;
  }
  if (opts.back) {
    const logo = document.createElement("span");
    logo.className = "uc-back-logo";
    logo.textContent = "UNO";
    node.append(logo);
    return node;
  }
  const glyph = UNO_VALUE_GLYPHS[card.v] || card.v;
  const top = document.createElement("span");
  top.className = "uc-corner";
  top.textContent = glyph;
  const mid = document.createElement("span");
  mid.className = "uc-mid";
  mid.textContent = glyph;
  const bottom = document.createElement("span");
  bottom.className = "uc-corner uc-flip";
  bottom.textContent = glyph;
  node.append(top, mid, bottom);
  return node;
}

function unoSeatNode(p) {
  const seat = document.createElement("div");
  seat.className = "uno-seat";
  seat.dataset.username = p.username;
  if (state.myRoom.status === "playing" && state.myRoom.to_act === p.username) {
    seat.classList.add("active");
  }
  if (p.username === selfUsername()) seat.classList.add("me");
  const name = document.createElement("div");
  name.className = "us-name";
  name.textContent = p.nickname;
  const info = document.createElement("div");
  info.className = "us-info";
  const stack = document.createElement("span");
  stack.className = "us-stack";
  stack.textContent = formatCoins(p.stack);
  const count = document.createElement("span");
  count.className = "us-count";
  count.textContent = state.myRoom.status === "playing"
    ? (p.in_hand ? `🂠 ${p.cards}` : "观战")
    : `${formatCoins(p.stack)} 筹码`;
  info.append(stack, count);
  const uno = document.createElement("div");
  uno.className = "us-uno";
  uno.textContent = p.uno ? "未喊 UNO" : "UNO!";
  if (p.in_hand && p.cards === 1) uno.classList.add("show");
  seat.append(name, ratingBadge(p.rating), info, uno);
  // 自己的补喊入口固定在dock的UNO常驻按钮上，座位上只保留对他人的质疑
  if (p.uno && p.username !== selfUsername()) {
    const challenge = document.createElement("button");
    challenge.type = "button";
    challenge.className = "uno-challenge";
    const available = state.myRoom.your_options?.challenge?.includes(p.username);
    challenge.disabled = state.myRoom.paused || !available;
    challenge.textContent = available ? "质疑漏喊 +2" : "2 秒保护期";
    challenge.setAttribute("aria-label", `质疑 ${p.nickname} 漏喊 UNO，罚摸两张`);
    challenge.addEventListener("click", () => unoAct({ action: "challenge_uno", target: p.username }));
    seat.append(challenge);
  }
  return seat;
}

function unoResultNode(result) {
  const box = document.createElement("div");
  box.className = "poker-result";
  const total = Object.values(result.payouts || {}).reduce((sum, n) => sum + n, 0);
  if (result.winner) {
    requestProfile(result.winner);
    const head = document.createElement("div");
    head.className = "poker-result-row";
    const left = document.createElement("span");
    left.textContent = `🏆 ${displayNameOf(result.winner)} 先出完`;
    const right = document.createElement("span");
    right.className = "win";
    right.textContent = total ? `+${formatCoins(total)}` : "";
    head.append(left, right);
    box.append(head);
  }
  for (const [username, amount] of Object.entries(result.payouts || {})) {
    requestProfile(username);
    const row = document.createElement("div");
    row.className = "poker-result-row";
    const left = document.createElement("span");
    left.textContent = `${displayNameOf(username)} 剩 ${result.penalties?.[username] ?? "?"} 张`;
    const right = document.createElement("span");
    right.textContent = `-${formatCoins(amount)}`;
    row.append(left, right);
    box.append(row);
  }
  const reveal = document.createElement("div");
  reveal.className = "uno-reveal";
  for (const [username, cards] of Object.entries(result.cards || {})) {
    requestProfile(username);
    if (!cards.length) continue;
    const line = document.createElement("div");
    line.className = "uno-reveal-row";
    const name = document.createElement("span");
    name.className = "uno-reveal-name";
    name.textContent = displayNameOf(username);
    const cardsBox = document.createElement("span");
    cardsBox.className = "uno-reveal-cards";
    for (const card of cards) cardsBox.append(unoCardNode(card));
    line.append(name, cardsBox);
    reveal.append(line);
  }
  box.append(reveal);
  return box;
}

function unoFabNode() {
  // UNO 常驻按钮：一直显示，避免剩 1 张时突然弹出；非补喊时机点击无反应
  const fab = document.createElement("button");
  fab.type = "button";
  fab.className = "uno-fab";
  fab.textContent = "UNO";
  const pending = Boolean(state.myRoom.your_options?.uno);
  fab.classList.toggle("pending", pending);
  fab.setAttribute(
    "aria-label",
    pending ? "立即喊 UNO" : "UNO（剩 1 张未喊时点击补喊）",
  );
  fab.addEventListener("click", () => {
    if (state.myRoom.paused || !pending) return;
    unoAct({ action: "uno" });
  });
  return fab;
}

function unoActionBarNode() {
  const bar = document.createElement("div");
  bar.className = "action-bar";
  const options = state.myRoom.your_options || {};
  const act = (payload) => unoAct(payload);

  if (options.draw) {
    const draw = document.createElement("button");
    draw.className = "action-btn primary";
    draw.type = "button";
    draw.textContent = "摸一张";
    draw.addEventListener("click", () => act({ action: "draw" }));
    bar.append(draw);
  }
  if (options.pass) {
    const pass = document.createElement("button");
    pass.className = "action-btn danger";
    pass.type = "button";
    pass.textContent = "保留摸牌 · 跳过";
    pass.addEventListener("click", () => {
      pendingWildCard = null;
      act({ action: "pass" });
    });
    bar.append(pass);
  }

  // 万能牌选色条：点选万能牌后出现
  if (pendingWildCard !== null) {
    const picker = document.createElement("div");
    picker.className = "uno-color-picker";
    const label = document.createElement("span");
    label.className = "uno-color-label";
    label.textContent = "选颜色：";
    picker.append(label);
    for (const color of UNO_COLORS) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `uno-color-dot ${color}`;
      btn.title = UNO_COLOR_NAMES[color];
      btn.setAttribute("aria-label", `改为${UNO_COLOR_NAMES[color]}色`);
      btn.addEventListener("click", () => {
        const index = pendingWildCard;
        pendingWildCard = null;
        act({ action: "play", card: index, color });
      });
      picker.append(btn);
    }
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "uno-color-cancel";
    cancel.textContent = "✕";
    cancel.addEventListener("click", () => {
      pendingWildCard = null;
      renderGameView();
    });
    picker.append(cancel);
    bar.append(picker);
  }
  return bar;
}

function renderUnoTable() {
  const body = elements.gameMain;
  unoActionLock = false;
  body.replaceChildren();

  const wrap = document.createElement("div");
  wrap.className = "poker-page uno-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "uno-table";

  const topbar = document.createElement("div");
  topbar.className = "poker-topbar";
  const left = document.createElement("span");
  left.textContent = `第 ${state.myRoom.hand_no || "-"} 局 · 每张赔付 ${state.myRoom.blind}`;
  const right = document.createElement("span");
  right.textContent = state.myRoom.paused
    ? "⏸ 已暂停"
    : `方向 ${state.myRoom.direction === -1 ? "↺" : "↻"}`;
  topbar.append(left, right);
  table.append(topbar);

  const status = document.createElement("div");
  status.className = "poker-status";
  const la = state.myRoom.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : state.myRoom.to_act
      ? `等待 ${displayNameOf(state.myRoom.to_act)} 出牌…`
      : "发牌中…";
  table.append(status);

  const seats = document.createElement("div");
  seats.className = "uno-seats";
  const players = state.myRoom.players;
  const myIndex = Math.max(0, players.findIndex((p) => p.username === selfUsername()));
  players.forEach((p, index) => {
    const seat = unoSeatNode(p);
    // direction=1 的服务端座序对应视觉顺时针；本人固定在下方。
    const angle = ((index - myIndex + players.length) % players.length) * Math.PI * 2 / players.length;
    seat.style.setProperty("--seat-x", `${50 - 41 * Math.sin(angle)}%`);
    seat.style.setProperty("--seat-y", `${50 + 39 * Math.cos(angle)}%`);
    seats.append(seat);
  });
  table.append(seats);

  const direction = document.createElement("div");
  direction.className = `uno-direction${state.myRoom.direction === -1 ? " reverse" : ""}${state.myRoom.paused ? " paused" : ""}`;
  const arrow = document.createElement("span");
  arrow.className = "uno-direction-arrow";
  arrow.textContent = state.myRoom.direction === -1 ? "↺" : "↻";
  arrow.setAttribute("aria-hidden", "true");
  const directionLabel = document.createElement("span");
  directionLabel.textContent = state.myRoom.direction === -1 ? "逆时针" : "顺时针";
  direction.append(arrow, directionLabel);
  table.append(direction);

  const center = document.createElement("div");
  center.className = "uno-center";
  const active = state.myRoom.active;
  if (active?.card) {
    const myTurn = isMyTurn();
    const drawPile = document.createElement("button");
    drawPile.type = "button";
    drawPile.setAttribute("aria-label", "从牌堆摸一张");
    drawPile.disabled = state.myRoom.paused || !myTurn || !state.myRoom.your_options?.draw;
    drawPile.className = "uno-pile";
    drawPile.append(unoCardNode(null, { big: true, back: true }));
    const deckLeft = document.createElement("span");
    deckLeft.className = "uno-deck-count";
    deckLeft.textContent = `牌堆 ${state.myRoom.deck_left ?? 0}`;
    drawPile.append(deckLeft);
    if (myTurn && state.myRoom.your_options?.draw) {
      drawPile.classList.add("clickable");
      drawPile.addEventListener("click", () => {
        unoAct({ action: "draw" });
      });
    }

    const discard = document.createElement("div");
    discard.className = "uno-discard";
    discard.append(unoCardNode(active.card, {
      big: true,
      tint: active.card.c === "w" ? active.c : null,
    }));

    const strip = document.createElement("div");
    strip.className = "uno-color-strip";
    strip.style.background = UNO_COLOR_TEXT[active.c] || "#3a3a3c";
    strip.textContent = UNO_COLOR_NAMES[active.c]
      ? `当前颜色 ${UNO_COLOR_NAMES[active.c]}`
      : "";
    center.append(drawPile, discard, strip);
  }
  table.append(center);

  if (state.myRoom.result) table.append(unoResultNode(state.myRoom.result));

  if (state.myRoom.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);

  const dock = document.createElement("div");
  dock.className = "poker-dock uno-dock";
  dock.classList.toggle("is-my-turn", !state.myRoom.spectator && isMyTurn() && !state.myRoom.paused);
  const dockHead = document.createElement("div");
  dockHead.className = "dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  label.textContent = state.myRoom.spectator ? "观战视角 · 手牌"
    : isMyTurn() ? "你的手牌 · 轮到你出牌" : "你的手牌";
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  dockHead.append(label, chatToggle);
  dock.append(dockHead);

  const handRow = document.createElement("div");
  handRow.className = "uno-hand-row";
  const myCards = document.createElement("div");
  myCards.className = "uno-hand";
  const hand = state.myRoom.your_hand || [];
  const options = state.myRoom.your_options || {};
  if (pendingWildHand !== JSON.stringify(hand) || !isMyTurn() || !hand[pendingWildCard] || hand[pendingWildCard]?.c !== "w") pendingWildCard = null;
  if (!hand.length) {
    const waiting = document.createElement("span");
    waiting.className = "my-cards-label";
    waiting.textContent = "等待下一局发牌…";
    myCards.append(waiting);
  }
  const drawnOnly = options.pass && state.myRoom.your_drawn != null;
  hand.forEach((card, index) => {
    const playable = !state.myRoom.spectator && isMyTurn() && !state.myRoom.paused
      && (!drawnOnly || index === state.myRoom.your_drawn)
      && unoMatches(card, state.myRoom.active);
    const node = document.createElement("button");
    node.type = "button";
    node.className = "uno-hand-card";
    node.disabled = !playable;
    node.setAttribute("aria-label", `${UNO_COLOR_NAMES[card.c] || "万能"} ${UNO_VALUE_GLYPHS[card.v] || card.v}`);
    node.append(unoCardNode(card, {}));
    if (drawnOnly && index === state.myRoom.your_drawn) node.firstElementChild.classList.add("drawn");
    if (pendingWildCard === index) node.firstElementChild.classList.add("picked");
    if (playable) {
      node.firstElementChild.classList.add("playable");
      node.addEventListener("click", () => {
        if (card.c === "w") {
          pendingWildCard = index;
          pendingWildHand = JSON.stringify(hand);
          renderGameView();
          return;
        }
        pendingWildCard = null;
        unoAct({ action: "play", card: index });
      });
    }
    myCards.append(node);
  });
  handRow.append(myCards, unoFabNode());
  dock.append(handRow);

  const countdown = document.createElement("div");
  countdown.className = "countdown";
  const fill = document.createElement("div");
  fill.className = "countdown-fill";
  countdown.append(fill);
  dock.append(countdown);

  if (!state.myRoom.paused) {
    // 补喊入口已固定在常驻 UNO 按钮上，行动条只在自己回合（摸/留/选色）出现
    if (!state.myRoom.spectator && (isMyTurn() || pendingWildCard !== null)) {
      dock.append(unoActionBarNode());
      if (isMyTurn() && state.myRoom.turn_left > 0) startHallTicker(fill, state.myRoom.turn_left);
    }
  } else {
    const pausedNote = document.createElement("div");
    pausedNote.className = "last-action";
    pausedNote.textContent = "牌局已暂停";
    dock.append(pausedNote);
  }
  wrap.append(dock);

  reapplySeatBubbles();
  animateUnoEvent(unoCardNode);
}


/* =========================================================
   事件绑定
========================================================= */

registerGame("uno", {
  stakeLabel: "每张赔付",
  blindLabel: "下一局底注",
  waitingHint: "等待房主开局。中途退出会自动离局，当前筹码原封退回。",
  noNextHint: () => "有玩家筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderUnoTable,
  renderReview: (result) => {
    const review = unoResultNode(result);
    animateUnoEvent(unoCardNode);
    return review;
  },
  seatElement: (index) => document.querySelector(`#gameMain .uno-seats .uno-seat:nth-child(${index + 1})`),
});
