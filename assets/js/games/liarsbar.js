/* 骗子酒馆：桌面宣称、最近暗牌、玩家存活状态和两种决策分区展示。
   手牌选择只在本地维护，行动与决斗结果均以服务器视图为准。 */

import { displayNameOf, elements, formatCoins, formatCoinsWhole, playerAvatarNode, renderGameView, requestProfile, selfUsername, send, startHallTicker, state } from "../core.js";
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
    document.querySelectorAll(".liar-dock button").forEach((b) => { b.disabled = true; });
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
  window.clearInterval(attentionTimer);
  if (!active || !desktopLayout.matches) {
    if (titleBeforeTurn !== null) document.title = titleBeforeTurn;
    titleBeforeTurn = null;
    return;
  }
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
    node.setAttribute("aria-pressed", String(Boolean(opts.selected)));
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
  const center = document.createElement("section");
  center.className = "liar-center";

  if (room.stage === "reveal" && room.last_duel) {
    center.append(duelNode(room.last_duel));
    return center;
  }

  const claim = document.createElement("section");
  claim.className = "liar-claim";
  const label = document.createElement("div");
  label.className = "liar-claim-label";
  label.textContent = "本轮宣称";
  const card = cardNode(room.table_card, { big: true, settled: true });
  card.classList.add("claim");
  const claimName = document.createElement("strong");
  claimName.textContent = CARD_NAMES[room.table_card] || "等待发牌";
  const rule = document.createElement("div");
  rule.className = "liar-claim-rule";
  rule.textContent = room.rules?.jokers === "wild"
    ? `翻牌时，只有 ${CARD_LABELS[room.table_card] || "桌面牌"} 和小丑算真话`
    : `翻牌时，只有 ${CARD_LABELS[room.table_card] || "桌面牌"} 算真话`;
  claim.append(label, card, claimName, rule);
  center.append(claim);

  const last = document.createElement("section");
  last.className = "liar-last";
  const lastLabel = document.createElement("div");
  lastLabel.className = "liar-last-label";
  lastLabel.textContent = "最近一手 · 暗牌";
  last.append(lastLabel);
  if (room.last_play) {
    const backs = document.createElement("div");
    backs.className = "liar-backs";
    for (let i = 0; i < room.last_play.count; i += 1) backs.append(cardBackNode());
    const who = document.createElement("div");
    who.className = "liar-last-text";
    who.textContent = `${room.last_play.nickname} 打出 ${room.last_play.count} 张，声称是 ${CARD_LABELS[room.table_card]}`;
    const note = document.createElement("div");
    note.className = "liar-last-note";
    note.textContent = "只有下一位玩家可以质疑这手牌";
    last.append(backs, who, note);
  } else {
    const waiting = document.createElement("div");
    waiting.className = "liar-last-text";
    waiting.textContent = "本轮尚无暗牌，先手玩家需要出牌";
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
  head.textContent = `${duel.challenger_name} 质疑 ${duel.accused_name}`;
  box.append(head);

  const table = document.createElement("div");
  table.className = "liar-duel-table";
  table.textContent = `本轮宣称：${CARD_NAMES[duel.table_card] || duel.table_card}`;
  box.append(table);

  const reveal = document.createElement("div");
  reveal.className = "liar-duel-cards";
  const verdict = document.createElement("span");
  verdict.className = `liar-verdict ${duel.truthful ? "truth" : "lie"}`;
  verdict.textContent = duel.truthful ? "真话" : "说谎";
  reveal.append(verdict);
  for (const card of duel.cards || []) reveal.append(cardNode(card, { settled: true }));
  box.append(reveal);

  const shot = document.createElement("div");
  shot.className = `liar-shot ${duel.hit ? "fatal" : "blank"}`;
  shot.textContent = duel.hit
    ? `🔫 ${duel.shooter_name} 开枪命中 · 已出局`
    : `🔫 ${duel.shooter_name} 开出空弹 · 继续存活`;
  const odds = document.createElement("div");
  odds.className = "liar-duel-odds";
  odds.textContent = `本次命中率 ${duel.odds} · ${duel.truthful ? "质疑者" : "出牌者"}承担开枪风险`;
  box.append(shot, odds);
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

function seatNode(p, index) {
  const room = state.myRoom;
  const seat = document.createElement("div");
  seat.className = "liar-seat";
  seat.dataset.seat = String(index);
  seat.dataset.username = p.username;
  if (room.to_act === p.username && p.alive && room.stage === "play") seat.classList.add("active");
  if (!p.alive && p.in_match) seat.classList.add("eliminated");
  if (p.username === selfUsername() && !room.spectator) seat.classList.add("me");
  if (room.last_play?.username === p.username && room.stage === "play") seat.classList.add("target");

  const avatar = playerAvatarNode(p, "liar-avatar");
  const identity = document.createElement("div");
  identity.className = "liar-seat-identity";

  const name = document.createElement("div");
  name.className = "liar-seat-name";
  name.textContent = p.username === selfUsername() && !room.spectator
    ? "我" : (p.nickname || displayNameOf(p.username));
  name.title = p.nickname || displayNameOf(p.username);

  const status = document.createElement("div");
  status.className = "liar-seat-status";
  status.textContent = !p.in_match ? "本局未参与"
    : !p.alive ? "已出局"
    : room.to_act === p.username && room.stage === "play" ? "正在行动"
    : room.last_play?.username === p.username && room.stage === "play" ? "最近出牌"
    : "存活";
  identity.append(name, status);

  const detail = document.createElement("div");
  detail.className = "liar-seat-detail";
  if (p.in_match) {
    const pile = document.createElement("span");
    pile.textContent = `本轮出牌 ${p.pile || 0} 张`;
    detail.append(pile, gunNode(p));
  }
  const money = document.createElement("span");
  money.className = "liar-seat-stack";
  money.textContent = formatCoins(p.stack);
  money.title = `当前筹码 ${formatCoins(p.stack)}`;
  detail.append(money);
  seat.append(avatar, identity, detail);
  return seat;
}

/* =========================================================
   底部操作区：手牌 + 出牌/质疑
========================================================= */

function actionBarNode(options) {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "liar-actions";

  if (options.can_challenge) {
    const choice = document.createElement("div");
    choice.className = "liar-choice challenge";
    const eyebrow = document.createElement("div");
    eyebrow.className = "liar-choice-label";
    eyebrow.textContent = "揭开最近一手";
    const challenge = document.createElement("button");
    challenge.className = "liar-action danger";
    challenge.type = "button";
    challenge.textContent = `质疑 ${options.challenge_name}`;
    challenge.addEventListener("click", () => liarAct({ action: "challenge" }));
    const note = document.createElement("div");
    note.className = "liar-choice-note";
    note.textContent = "若对方为真，你需对自己开枪";
    choice.append(eyebrow, challenge, note);
    bar.append(choice);
  }

  if (options.can_play) {
    const choice = document.createElement("div");
    choice.className = "liar-choice play";
    const eyebrow = document.createElement("div");
    eyebrow.className = "liar-choice-label";
    eyebrow.textContent = "暗扣手牌";
    const play = document.createElement("button");
    play.className = "liar-action primary";
    play.type = "button";
    const count = selected.length;
    play.textContent = count
      ? `打出 ${count} 张 · 声称 ${CARD_LABELS[options.table_card]}`
      : `选择 ${options.min_play}–${options.max_play} 张后出牌`;
    play.disabled = actionLock || count < options.min_play;
    play.addEventListener("click", () => {
      if (selected.length < options.min_play) return;
      liarAct({ action: "play", cards: [...selected] });
    });
    const note = document.createElement("div");
    note.className = "liar-choice-note";
    note.textContent = `所选的牌会暗扣，统一声称 ${CARD_LABELS[options.table_card]}`;
    choice.append(eyebrow, play, note);
    bar.append(choice);
  }
  return bar;
}

function phaseText(room) {
  if (room.paused) return "牌局已暂停，等待房主继续";
  if (room.settlement || room.stage === "showdown") return "本局已结束，等待下一局投票";
  if (room.stage === "reveal") {
    return (room.players || []).filter((p) => p.alive).length <= 1
      ? "质疑已揭晓，等待本局结算" : "质疑已揭晓，准备下一轮";
  }
  if (!room.to_act) return "正在发牌…";
  if (isMyTurn() && !room.spectator) {
    return room.last_play ? "轮到你 · 出牌或质疑最近一手" : "轮到你 · 请先出牌";
  }
  return `等待 ${displayNameOf(room.to_act)} 出牌或质疑`;
}

function dockNode() {
  const room = state.myRoom;
  const dock = document.createElement("section");
  dock.className = "liar-dock";
  if (room.stage === "reveal") dock.classList.add("is-reveal");
  const myTurn = isMyTurn() && room.stage === "play" && !room.paused
    && !room.spectator && Boolean(room.your_options);
  if (myTurn) dock.classList.add("is-my-turn");
  const me = room.players.find((p) => p.username === selfUsername());

  const dockHead = document.createElement("div");
  dockHead.className = "liar-dock-head";
  const label = document.createElement("div");
  label.className = "liar-dock-title";
  label.textContent = room.stage === "reveal" ? "翻牌与开枪结果"
    : room.spectator
    ? `${displayNameOf(selfUsername())} 的手牌（观战）`
    : "你的手牌与决策";
  const gun = document.createElement("span");
  gun.className = "liar-my-gun";
  gun.textContent = !me?.in_match ? "本局未参与"
    : !me.alive ? "已出局"
    : `🔫 下次开枪 ${me.gun}`;
  gun.title = me?.alive ? `已开出 ${me.survived || 0} 次空弹` : gun.textContent;
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  dockHead.append(label, gun, chatToggle);
  dock.append(dockHead);

  syncSelection();
  const handArea = document.createElement("div");
  handArea.className = "liar-hand-area";
  const handInfo = document.createElement("div");
  handInfo.className = "liar-hand-info";
  handInfo.textContent = room.settlement || room.stage === "showdown" ? "本局已结束"
    : room.stage === "reveal" ? "本轮翻牌中"
    : myTurn
    ? `点选要暗扣的牌 · 已选 ${selected.length}/${room.your_options.max_play}`
    : room.spectator ? "观战手牌 · 不可操作"
    : room.your_hand?.length ? `剩余 ${room.your_hand.length} 张` : "当前无手牌";
  handArea.append(handInfo);
  const hand = document.createElement("div");
  hand.className = "liar-hand";
  const options = room.your_options;
  if (room.settlement || room.stage === "showdown") {
    const empty = document.createElement("span");
    empty.className = "liar-hand-empty";
    empty.textContent = "本局已结束，等待结算投票";
    hand.append(empty);
  } else if (room.stage === "reveal") {
    const empty = document.createElement("span");
    empty.className = "liar-hand-empty";
    empty.textContent = "翻牌决斗中，下一轮将重新发牌";
    hand.append(empty);
  } else if (room.your_hand?.length) {
    const canPick = myTurn && options?.can_play;
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
  } else if (!room.spectator && me?.alive) {
    const empty = document.createElement("span");
    empty.className = "liar-hand-empty";
    empty.textContent = "手牌已打空，可质疑上家";
    hand.append(empty);
  } else {
    const empty = document.createElement("span");
    empty.className = "liar-hand-empty";
    empty.textContent = "已出局，观战至本局结束";
    hand.append(empty);
  }
  handArea.append(hand);
  if (room.stage !== "reveal") dock.append(handArea);

  const decision = document.createElement("div");
  decision.className = "liar-decision";
  const decisionTitle = document.createElement("div");
  decisionTitle.className = "liar-decision-title";
  decisionTitle.textContent = phaseText(room);
  decision.append(decisionTitle);
  if (myTurn && options) {
    if (room.turn_left > 0) {
      const countdown = document.createElement("div");
      countdown.className = "countdown";
      const fill = document.createElement("div");
      fill.className = "countdown-fill";
      countdown.append(fill);
      decision.append(countdown);
      startHallTicker(fill, Math.max(0, (turnDeadline - Date.now()) / 1000));
    }
    decision.append(actionBarNode(options));
  } else {
    const waiting = document.createElement("div");
    waiting.className = "liar-decision-waiting";
    waiting.textContent = room.stage === "reveal"
      ? "牌已翻开，开枪结果在桌面显示。"
      : room.settlement ? "投票操作将在结算弹层中显示。"
      : room.paused ? "暂停期间无法出牌或质疑。"
      : "当前无需操作，留意下一位出牌者。";
    decision.append(waiting);
  }
  dock.append(decision);
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
    turnDeadline = room.turn_left > 0 ? Date.now() + room.turn_left * 1000 : 0;
  }
  body.replaceChildren();

  const wrap = document.createElement("div");
  wrap.className = "liar-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "liar-table";

  const topbar = document.createElement("div");
  topbar.className = "liar-topbar";
  const left = document.createElement("div");
  left.className = "liar-topbar-title";
  left.textContent = `骗子酒馆 · 第 ${room.match_no || 1} 局 / 第 ${room.round_no || "-"} 轮`;
  const right = document.createElement("div");
  right.className = "liar-topbar-rules";
  right.textContent = `底注 ${formatCoinsWhole(room.blind)} · ${rulesSummary(room.rules)}`;
  right.title = right.textContent;
  topbar.append(left, right);
  table.append(topbar);

  const status = document.createElement("div");
  status.className = "liar-phase";
  status.textContent = phaseText(room);
  status.title = status.textContent;
  table.append(status);

  const arena = document.createElement("div");
  arena.className = "liar-arena";
  arena.append(centerNode());

  const seats = document.createElement("section");
  seats.className = "liar-seats";
  const seatsHead = document.createElement("div");
  seatsHead.className = "liar-seats-head";
  seatsHead.textContent = `玩家状态 · ${(room.players || []).filter((p) => p.alive).length}/${(room.players || []).filter((p) => p.in_match).length} 存活`;
  seats.append(seatsHead);
  const players = room.players || [];
  players.forEach((p, index) => {
    seats.append(seatNode(p, index));
  });
  arena.append(seats);
  table.append(arena);

  const history = document.createElement("div");
  history.className = "liar-history";
  const historyLabel = document.createElement("span");
  historyLabel.textContent = "最新动态";
  const historyText = document.createElement("span");
  const la = room.last_action;
  historyText.textContent = la ? `${la.nickname} ${la.text}` : "等待本局第一手";
  historyText.title = historyText.textContent;
  history.append(historyLabel, historyText);
  table.append(history);

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
    note.className = "liar-result-summary";
    note.textContent = "无人幸存，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  const head = document.createElement("div");
  head.className = "liar-result-summary";
  const left = document.createElement("span");
  left.textContent = `🏆 ${result.winner_name || displayNameOf(result.winner)} 笑到了最后（共 ${result.rounds || 0} 轮）！`;
  const right = document.createElement("span");
  right.className = "liar-result-gain";
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
  ranking.className = "liar-ranking";
  (result.ranking || []).forEach((row) => {
    requestProfile(row.username);
    const line = document.createElement("div");
    line.className = "liar-ranking-row";
    if (row.username === result.winner) line.classList.add("champion");
    const name = document.createElement("span");
    name.className = "liar-ranking-name";
    name.textContent = `${row.username === result.winner ? "👑 " : ""}${row.nickname || displayNameOf(row.username)}`;
    const detail = document.createElement("span");
    detail.className = "liar-ranking-detail";
    detail.textContent = row.username === result.winner
      ? `独存 ${row.rounds} 轮`
      : `扛过 ${row.survived || 0} 枪`;
    const net = document.createElement("span");
    const amount = Math.round(Number(row.net || 0) * 100) / 100;
    net.className = `liar-ranking-net ${amount > 0 ? "win" : amount < 0 ? "lose" : "flat"}`;
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
  seatElement: (index) => document.querySelector(`#gameMain .liar-seat[data-seat="${index}"]`),
});
