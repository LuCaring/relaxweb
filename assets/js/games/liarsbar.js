/* 骗子酒馆牌桌：沿用德扑布局——中央桌面牌与决斗展示、四周座位、底部
   手牌与操作条。出牌选择在本地维护（最多选 max_play 张），实际以服务
   器视图为准；决斗翻牌与开枪结果用覆盖层展示。 */

import { displayNameOf, elements, formatCoins, formatCoinsWhole, ratingBadge, renderGameView, requestProfile, selfUsername, send, startHallTicker, state } from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const CARD_LABELS = { K: "K", Q: "Q", A: "A", R: "★" };
const CARD_NAMES = { K: "K 国王", Q: "Q 王后", A: "A 尖儿", R: "小丑" };
const RULES_SUMMARY = {
  champion: "冠军通吃",
  rank: "按出局顺序结算",
};

let actionLock = false;
let lastRoomView = null;
let selected = [];          // 本次出牌选中的手牌下标
let lastHandKey = "";
let lastTurnKey = "";
let turnDeadline = 0;
let titleBeforeTurn = null;
let attentionTimer = 0;

const desktopLayout = window.matchMedia("(min-width: 1024px)");
document.addEventListener("gameviewchange", () => {
  window.clearInterval(attentionTimer);
  if (titleBeforeTurn !== null) document.title = titleBeforeTurn;
  titleBeforeTurn = null;
});
desktopLayout.addEventListener("change", () => {
  if (state.myRoom?.game_type === "liarsbar") renderGameView();
});

function liarAct(payload) {
  if (actionLock || state.myRoom?.spectator) return;
  if (send({ type: "poker_action", ...payload })) {
    actionLock = true;
    document.querySelectorAll(".liar-dock button, .liar-card").forEach((b) => { b.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "liarsbar") return;
  actionLock = false;
  renderGameView();
});

function isMyTurn() {
  const me = selfUsername();
  return Boolean(me) && state.myRoom.to_act === me;
}

/** 手牌或行动权变化时清空选择，避免残留旧下标。 */
function syncSelection() {
  const room = state.myRoom;
  const handKey = JSON.stringify(room.your_hand || []);
  const turnKey = `${room.round_no}:${room.to_act}`;
  if (handKey !== lastHandKey || turnKey !== lastTurnKey) {
    lastHandKey = handKey;
    lastTurnKey = turnKey;
    selected = [];
  }
  const max = room.your_options?.max_play || 0;
  if (selected.length > max) selected = selected.slice(0, max);
  selected = selected.filter((i) => i < (room.your_hand || []).length);
}

function turnTitle(active) {
  const dock = document.querySelector(".liar-dock");
  if (!dock || !active || !desktopLayout.matches) return;
  window.clearInterval(attentionTimer);
  if (titleBeforeTurn === null) titleBeforeTurn = document.title;
  document.title = `轮到你了 · ${titleBeforeTurn}`;
  attentionTimer = window.setInterval(() => {
    const left = (turnDeadline - Date.now()) / 1000;
    if (left <= 0) {
      window.clearInterval(attentionTimer);
      if (titleBeforeTurn !== null) document.title = titleBeforeTurn;
      titleBeforeTurn = null;
    }
  }, 500);
}

/* =========================================================
   卡牌
========================================================= */

function cardNode(card, opts = {}) {
  const node = document.createElement("span");
  node.className = `liar-card c-${card}${opts.big ? " big" : ""}${opts.selected ? " selected" : ""}`;
  if (opts.settled) node.style.animation = "none";
  else if (opts.delay) node.style.animationDelay = `${opts.delay}ms`;
  if (opts.clickable) {
    node.classList.add("clickable");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
  }
  node.textContent = CARD_LABELS[card] || card;
  node.title = CARD_NAMES[card] || card;
  return node;
}

function cardBackNode() {
  const node = document.createElement("span");
  node.className = "liar-card back";
  node.textContent = "?";
  return node;
}

/* =========================================================
   中央区：桌面牌 + 最近暗牌 / 决斗展示
========================================================= */

function centerNode() {
  const room = state.myRoom;
  const center = document.createElement("div");
  center.className = "liar-center";

  if (room.stage === "reveal" && room.last_duel) {
    center.append(duelNode(room.last_duel));
    return center;
  }

  const claim = document.createElement("div");
  claim.className = "liar-claim";
  const label = document.createElement("div");
  label.className = "liar-claim-label";
  label.textContent = `第 ${room.round_no || "-"} 轮 · 桌面牌`;
  const card = cardNode(room.table_card, { big: true, settled: true });
  card.classList.add("claim");
  claim.append(label, card);
  center.append(claim);

  const last = document.createElement("div");
  last.className = "liar-last";
  if (room.last_play) {
    const backs = document.createElement("div");
    backs.className = "liar-backs";
    for (let i = 0; i < room.last_play.count; i += 1) backs.append(cardBackNode());
    const who = document.createElement("div");
    who.className = "liar-last-text";
    who.textContent = `${room.last_play.nickname} 刚打出 ${room.last_play.count} 张，声称是 ${CARD_LABELS[room.table_card]}`;
    last.append(backs, who);
  } else {
    const waiting = document.createElement("div");
    waiting.className = "liar-last-text";
    waiting.textContent = "本轮流先出牌，尚无暗牌";
    last.append(waiting);
  }
  center.append(last);
  return center;
}

function duelNode(duel) {
  const box = document.createElement("div");
  box.className = `liar-duel${duel.hit ? " fatal" : ""}`;

  const head = document.createElement("div");
  head.className = "liar-duel-head";
  head.textContent = `${duel.challenger_name} 质疑 ${duel.accused_name}！`;
  box.append(head);

  const reveal = document.createElement("div");
  reveal.className = "liar-duel-cards";
  const verdict = document.createElement("span");
  verdict.className = `liar-verdict ${duel.truthful ? "truth" : "lie"}`;
  verdict.textContent = duel.truthful ? "真话" : "说谎";
  reveal.append(verdict);
  for (const card of duel.cards || []) reveal.append(cardNode(card, { settled: true }));
  const note = document.createElement("span");
  note.className = "liar-duel-note";
  note.textContent = `桌面牌 ${CARD_LABELS[duel.table_card] || duel.table_card}`;
  reveal.append(note);
  box.append(reveal);

  const shot = document.createElement("div");
  shot.className = `liar-shot ${duel.hit ? "fatal" : "blank"}`;
  shot.textContent = duel.hit
    ? `🔫 砰！${duel.shooter_name} 阵亡（命中率 ${duel.odds}）`
    : `🔫 咔哒…空弹，${duel.shooter_name} 活了下来（命中率 ${duel.odds}）`;
  box.append(shot);
  return box;
}

/* =========================================================
   座位
========================================================= */

function gunNode(p) {
  const gun = document.createElement("span");
  const denom = Number((p.gun || "1/6").split("/")[1] || 6);
  gun.className = `liar-gun${denom <= 2 ? " hot" : denom <= 3 ? "warm" : ""}`;
  gun.textContent = `🔫${p.gun}`;
  gun.title = `左轮命中率 ${p.gun} · 已扛过 ${p.survived || 0} 枪`;
  return gun;
}

function seatNode(p) {
  const room = state.myRoom;
  const seat = document.createElement("div");
  seat.className = "seat liar-seat";
  if (room.to_act === p.username && p.alive) seat.classList.add("active");
  if (!p.alive && room.stage !== "reveal") seat.classList.add("folded");
  if (p.username === selfUsername()) seat.classList.add("me");

  const name = document.createElement("div");
  name.className = "seat-name";
  name.textContent = p.nickname;
  if (room.last_play && room.last_play.username === p.username) {
    const mark = document.createElement("span");
    mark.className = "liar-target";
    mark.textContent = "⬅";
    mark.title = "上家暗牌，可被质疑";
    name.append(mark);
  }

  const stack = document.createElement("div");
  stack.className = "seat-stack";
  stack.textContent = formatCoins(p.stack);

  const pile = document.createElement("div");
  pile.className = "seat-bet";
  if (p.pile > 0 && p.in_match) {
    pile.classList.add("on");
    pile.textContent = `已出 ${p.pile} 张`;
  }

  const status = document.createElement("div");
  status.className = "seat-status";
  if (!p.alive && p.in_match) {
    status.textContent = "💀 阵亡";
    status.classList.add("allin");
  } else if (!p.in_match) {
    status.textContent = "";
  } else if (room.to_act === p.username && room.stage === "play") {
    status.textContent = "思考中…";
    status.classList.add("think");
  } else if (room.to_act === p.username && room.stage === "reveal") {
    status.textContent = "决斗中…";
    status.classList.add("allin");
  }

  seat.append(name, ratingBadge(p.rating), gunNode(p), stack, pile, status);
  return seat;
}

/* =========================================================
   底部操作区：手牌 + 出牌/质疑
========================================================= */

function actionBarNode(options) {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "action-bar liar-actions";
  const me = room.players.find((p) => p.username === selfUsername());

  if (options.can_challenge) {
    const challenge = document.createElement("button");
    challenge.className = "action-btn danger";
    challenge.type = "button";
    challenge.textContent = `质疑 ${options.challenge_name}！`;
    challenge.addEventListener("click", () => liarAct({ action: "challenge" }));
    bar.append(challenge);
  }

  const play = document.createElement("button");
  play.className = "action-btn primary";
  play.type = "button";
  const count = selected.length;
  play.textContent = count
    ? `打出 ${count} 张（声称 ${CARD_LABELS[options.table_card]}）`
    : `出牌（声称 ${CARD_LABELS[options.table_card]}，选 ${options.min_play}-${options.max_play} 张）`;
  play.disabled = !count;
  play.addEventListener("click", () => {
    if (!selected.length) return;
    liarAct({ action: "play", cards: [...selected] });
  });
  bar.append(play);

  const hint = document.createElement("div");
  hint.className = "liar-play-hint";
  hint.textContent = me && me.survived
    ? `你已扛过 ${me.survived} 枪，下次开枪命中率 ${me.gun}`
    : `你的左轮命中率 ${me?.gun || "1/6"}，输掉决斗就要对自己开枪`;
  bar.append(hint);
  return bar;
}

function dockNode() {
  const room = state.myRoom;
  const dock = document.createElement("div");
  dock.className = "poker-dock liar-dock";

  const dockHead = document.createElement("div");
  dockHead.className = "dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  const myTurn = isMyTurn() && room.stage === "play" && Boolean(room.your_options);
  label.textContent = room.spectator
    ? `${displayNameOf(selfUsername())} 的手牌（观战）`
    : room.your_hand?.length ? `你的手牌 · 桌面牌 ${CARD_LABELS[room.table_card]}` : "等待下一轮发牌…";
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  dockHead.append(label, chatToggle);
  dock.append(dockHead);

  syncSelection();
  const hand = document.createElement("div");
  hand.className = "my-cards liar-hand";
  const options = room.your_options;
  if (room.your_hand?.length) {
    const canPick = myTurn && !room.paused && options?.can_play && !room.spectator;
    room.your_hand.forEach((card, index) => {
      const node = cardNode(card, {
        big: true,
        settled: true,
        selected: selected.includes(index),
        clickable: canPick,
      });
      node.setAttribute("aria-label", `${CARD_NAMES[card] || card}${canPick ? "，点击选择" : ""}`);
      if (canPick) {
        const toggle = () => {
          if (actionLock) return;
          const at = selected.indexOf(index);
          if (at >= 0) selected.splice(at, 1);
          else {
            if (selected.length >= (options.max_play || 1)) selected.shift();
            selected.push(index);
          }
          renderGameView();
        };
        node.addEventListener("click", toggle);
        node.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
          }
        });
      }
      hand.append(node);
    });
  } else if (!room.spectator && room.players.find((p) => p.username === selfUsername())?.in_match) {
    const empty = document.createElement("span");
    empty.className = "my-cards-label";
    empty.textContent = "手牌已打空，只能质疑上家";
    hand.append(empty);
  } else {
    const empty = document.createElement("span");
    empty.className = "my-cards-label";
    empty.textContent = room.stage === "reveal" ? "决斗展示中…" : "你已阵亡，观战至本局结束";
    hand.append(empty);
  }
  dock.append(hand);

  const countdown = document.createElement("div");
  countdown.className = "countdown";
  const fill = document.createElement("div");
  fill.className = "countdown-fill";
  countdown.append(fill);
  dock.append(countdown);

  if (room.paused) {
    const pausedNote = document.createElement("div");
    pausedNote.className = "last-action";
    pausedNote.textContent = "牌局已暂停";
    dock.append(pausedNote);
  } else if (myTurn && options) {
    dock.append(actionBarNode(options));
    if (room.turn_left > 0) startHallTicker(fill, room.turn_left);
  } else {
    const waiting = document.createElement("div");
    waiting.className = "last-action";
    waiting.textContent = room.stage === "reveal"
      ? "决斗展示中…"
      : room.to_act ? `等待 ${displayNameOf(room.to_act)} 行动…` : "发牌中…";
    dock.append(waiting);
  }
  turnTitle(myTurn);
  return dock;
}

/* =========================================================
   主渲染
========================================================= */

function rulesSummary(rules) {
  if (!rules) return "";
  return [
    `${rules.cards} 张手牌`,
    `单次至多 ${rules.max_play} 张`,
    `${rules.chambers} 弹巢`,
    rules.respin ? "每次重转" : "概率递增",
    rules.jokers === "wild" ? "小丑百搭" : "无小丑",
    RULES_SUMMARY[rules.payout] || "",
  ].join(" · ");
}

function renderLiarsBarTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    // 每个新视图（含本人行动被确认后的回显）解除提交锁
    actionLock = false;
    lastRoomView = room;
  }
  if (room.turn_left > 0) turnDeadline = Date.now() + room.turn_left * 1000;
  else if (room.stage !== "play") turnDeadline = 0;
  body.replaceChildren();

  const wrap = document.createElement("div");
  wrap.className = "poker-page liar-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "poker-table liar-table";

  const topbar = document.createElement("div");
  topbar.className = "poker-topbar";
  const left = document.createElement("span");
  left.textContent = `第 ${room.round_no || "-"} 轮 · 底注 ${formatCoinsWhole(room.blind)}`;
  left.title = rulesSummary(room.rules);
  const right = document.createElement("span");
  right.textContent = room.paused ? "⏸ 已暂停"
    : room.stage === "reveal" ? "决斗展示"
    : room.table_card ? `桌面牌 ${CARD_LABELS[room.table_card]} · ${rulesSummary(room.rules)}` : "";
  topbar.append(left, right);
  table.append(topbar);

  const status = document.createElement("div");
  status.className = "poker-status";
  const la = room.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : room.to_act ? `等待 ${displayNameOf(room.to_act)} 行动…` : "准备开局…";
  status.title = status.textContent;
  table.append(status);

  table.append(centerNode());

  const seats = document.createElement("div");
  seats.className = "poker-seats";
  const players = room.players;
  const myIndex = Math.max(0, players.findIndex((p) => p.username === selfUsername()));
  players.forEach((p, index) => {
    const seat = seatNode(p);
    const angle = ((index - myIndex + players.length) % players.length) * Math.PI * 2 / players.length;
    seat.style.setProperty("--seat-x", `${50 + 41 * Math.sin(angle)}%`);
    seat.style.setProperty("--seat-y", `${50 + 39 * Math.cos(angle)}%`);
    seats.append(seat);
  });
  table.append(seats);

  if (room.result) table.append(resultNode(room.result));

  if (room.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);
  wrap.append(dockNode());

  reapplySeatBubbles();
}

/* =========================================================
   结算回顾
========================================================= */

function resultNode(result) {
  const box = document.createElement("div");
  box.className = "liar-result";
  if (!result.winner) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "无人幸存，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  const head = document.createElement("div");
  head.className = "poker-result-row liar-win-row";
  const left = document.createElement("span");
  left.textContent = `🏆 ${result.winner_name || displayNameOf(result.winner)} 笑到了最后（共 ${result.rounds || 0} 轮）！`;
  const right = document.createElement("span");
  right.className = "win";
  right.textContent = `+${formatCoins(result.gains?.[result.winner] || 0)}`;
  head.append(left, right);
  box.append(head);

  const pays = Object.entries(result.payouts || {});
  if (pays.length) {
    const note = document.createElement("div");
    note.className = "liar-payout-note";
    note.textContent = result.payout === "rank"
      ? "按出局顺序结算：越早出局付得越多（第 2/3/4…名付 1/2/3… 份底注）"
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
    name.className = "ludo-ranking-name";
    name.textContent = `${row.username === result.winner ? "👑 " : ""}${row.nickname || displayNameOf(row.username)}`;
    const detail = document.createElement("span");
    detail.className = "ludo-ranking-detail";
    detail.textContent = row.username === result.winner
      ? `独存 ${row.rounds} 轮`
      : `扛过 ${row.survived || 0} 枪`;
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

registerGame("liarsbar", {
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "骗子酒馆需要 2–6 名玩家：暗打出牌声称是桌面牌，质疑翻牌定生死，输掉决斗对自己开枪。最后独存者收走赔付，等待房主开局。",
  noNextHint: () => "人数不足 2 人或有人筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderLiarsBarTable,
  renderReview: (result) => {
    const review = document.createElement("div");
    const title = document.createElement("div");
    title.className = "hall-section-title";
    title.style.marginTop = "0";
    title.textContent = "本局回顾";
    review.append(title, resultNode(result));
    return review;
  },
  seatElement: (index) => document.querySelector(`#gameMain .poker-seats .seat:nth-child(${index + 1})`),
});
