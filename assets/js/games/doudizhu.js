/* 斗地主场景：地主在顶部、两侧农民、底部选牌、叫分按钮与结算回顾。
   牌型判定逻辑与 games/doudizhu.py 保持一致：客户端只做预校验和提示，
   服务器仍是唯一裁判。 */

import { displayNameOf, elements, formatCoins, formatCoinsWhole, playerAvatarNode, ratingBadge, renderGameView, requestProfile, selfUsername, send, startHallTicker, state } from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const SUIT_CHARS = ["♠", "♥", "♦", "♣"];
const RANK_CHARS = { 11: "J", 12: "Q", 13: "K", 14: "A", 15: "2", 16: "小王", 17: "大王" };
const COMBO_NAMES = {
  single: "单张", pair: "对子", triple: "三张", triple_one: "三带一",
  triple_pair: "三带二", straight: "顺子", pairs_seq: "连对",
  plane: "飞机", plane_single: "飞机带单", plane_pair: "飞机带对",
  four_two: "四带二", four_two_pairs: "四带两对",
  bomb: "炸弹", rocket: "王炸",
};
const ACE = 14;
const LOW = 3;
const MIN_PLANE = 2;

let ddActionLock = false;
let selectedIndices = new Set();
let lastHandJson = "";
let hintMoves = [];
let hintCursor = 0;
let hintKey = "";
let lastRoomView = null;
let turnDeadline = 0;

/* =========================================================
   牌型判定（与后端 games/doudizhu.py 同一套规则）
========================================================= */

function rankChar(rank) {
  return RANK_CHARS[rank] || String(rank);
}

function comboLabel(shape) {
  if (shape.type === "rocket") return COMBO_NAMES.rocket;
  return `${COMBO_NAMES[shape.type]} ${rankChar(shape.main)}`;
}

function shapeSortKey(shape) {
  return [shape.tier, shape.main, shape.len];
}

function beatsShape(a, b) {
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
}

/** 点数 3~A 内所有连窗：窗内每个点数都要有 need 张，返回窗顶点数列表。 */
function windows(counts, length, need) {
  const tops = [];
  for (let top = LOW + length - 1; top <= ACE; top += 1) {
    let ok = true;
    for (let rank = top - length + 1; rank <= top; rank += 1) {
      if ((counts[rank] || 0) < need) { ok = false; break; }
    }
    if (ok) tops.push(top);
  }
  return tops;
}

/** 从 banned 之外的牌里凑 need 张的所有组合，返回 [{rank: 张数}]。 */
function kickerCombos(counts, banned, need) {
  const pool = Object.keys(counts).map(Number)
    .filter((rank) => !banned.has(rank) && counts[rank] > 0);
  const results = [];
  const chosen = {};
  const walk = (index, left) => {
    if (left === 0) {
      results.push({ ...chosen });
      return;
    }
    if (index >= pool.length) return;
    const rank = pool[index];
    const limit = Math.min(counts[rank], left);
    for (let take = limit; take >= 0; take -= 1) {
      if (take) chosen[rank] = take;
      else delete chosen[rank];
      walk(index + 1, left - take);
      delete chosen[rank];
    }
  };
  walk(0, need);
  return results;
}

function enumerateShapes(counts, total) {
  const shapes = [];
  const add = (type, tier, main, picks) => {
    shapes.push({ type, tier, main, len: total, picks });
  };

  if (total === 4) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] === 4) add("bomb", 1, Number(rank), { [rank]: 4 });
    }
  }
  if (total === 2 && counts[16] && counts[17]) {
    add("rocket", 2, 17, { 16: 1, 17: 1 });
  }

  if (total === 1) {
    for (const rank of Object.keys(counts)) add("single", 0, Number(rank), { [rank]: 1 });
  }
  if (total === 2) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] >= 2) add("pair", 0, Number(rank), { [rank]: 2 });
    }
  }
  if (total === 3) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] >= 3) add("triple", 0, Number(rank), { [rank]: 3 });
    }
  }

  if (total === 4) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] < 3) continue;
      for (const kickers of kickerCombos(counts, new Set([Number(rank)]), 1)) {
        add("triple_one", 0, Number(rank), { [rank]: 3, ...kickers });
      }
    }
  }
  if (total === 5) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] < 3) continue;
      for (const other of Object.keys(counts)) {
        if (other !== rank && counts[other] >= 2) {
          add("triple_pair", 0, Number(rank), { [rank]: 3, [other]: 2 });
        }
      }
    }
  }

  if (total >= 5 && total <= 12) {
    for (const top of windows(counts, total, 1)) {
      const picks = {};
      for (let rank = top - total + 1; rank <= top; rank += 1) picks[rank] = 1;
      add("straight", 0, top, picks);
    }
  }
  if (total >= 6 && total % 2 === 0 && total <= 24) {
    const pairs = total / 2;
    if (pairs >= 3) {
      for (const top of windows(counts, pairs, 2)) {
        const picks = {};
        for (let rank = top - pairs + 1; rank <= top; rank += 1) picks[rank] = 2;
        add("pairs_seq", 0, top, picks);
      }
    }
  }

  if (total >= 3 * MIN_PLANE && total % 3 === 0) {
    const triples = total / 3;
    for (const top of windows(counts, triples, 3)) {
      const picks = {};
      for (let rank = top - triples + 1; rank <= top; rank += 1) picks[rank] = 3;
      add("plane", 0, top, picks);
    }
  }
  if (total >= 4 * MIN_PLANE && total % 4 === 0) {
    const triples = total / 4;
    if (triples >= MIN_PLANE && triples <= 5) {
      for (const top of windows(counts, triples, 3)) {
        const banned = new Set();
        for (let rank = top - triples + 1; rank <= top; rank += 1) banned.add(rank);
        const body = {};
        for (let rank = top - triples + 1; rank <= top; rank += 1) body[rank] = 3;
        for (const kickers of kickerCombos(counts, banned, triples)) {
          add("plane_single", 0, top, { ...body, ...kickers });
        }
      }
    }
  }
  if (total >= 5 * MIN_PLANE && total % 5 === 0) {
    const triples = total / 5;
    if (triples >= MIN_PLANE && triples <= 4) {
      for (const top of windows(counts, triples, 3)) {
        const body = {};
        const bodySet = new Set();
        for (let rank = top - triples + 1; rank <= top; rank += 1) {
          body[rank] = 3;
          bodySet.add(rank);
        }
        const pairRanks = Object.keys(counts).map(Number)
          .filter((rank) => !bodySet.has(rank) && counts[rank] >= 2);
        for (let i = 0; i < pairRanks.length; i += 1) {
          for (let j = i + 1; j < pairRanks.length; j += 1) {
            add("plane_pair", 0, top,
              { ...body, [pairRanks[i]]: 2, [pairRanks[j]]: 2 });
          }
        }
      }
    }
  }

  if (total === 6) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] < 4) continue;
      for (const kickers of kickerCombos(counts, new Set([Number(rank)]), 2)) {
        add("four_two", 0, Number(rank), { [rank]: 4, ...kickers });
      }
    }
  }
  if (total === 8) {
    for (const rank of Object.keys(counts)) {
      if (counts[rank] < 4) continue;
      const pairRanks = Object.keys(counts).map(Number)
        .filter((other) => other !== rank && counts[other] >= 2);
      for (let i = 0; i < pairRanks.length; i += 1) {
        for (let j = i + 1; j < pairRanks.length; j += 1) {
          add("four_two_pairs", 0, Number(rank),
            { [rank]: 4, [pairRanks[i]]: 2, [pairRanks[j]]: 2 });
        }
      }
    }
  }
  return shapes;
}

function realizeShape(shape, pool) {
  const picked = [];
  for (const [rank, need] of Object.entries(shape.picks)) {
    const cards = pool.get(Number(rank)) || [];
    if (cards.length < need) return null;
    picked.push(...cards.slice(0, need));
  }
  if (picked.length !== shape.len) return null;
  picked.sort((a, b) => b.r - a.r || b.s - a.s);
  return { type: shape.type, tier: shape.tier, main: shape.main, len: shape.len,
    label: comboLabel(shape), cards: picked };
}

function resolveCombo(cards) {
  const counts = {};
  const pool = new Map();
  for (const card of cards) {
    counts[card.r] = (counts[card.r] || 0) + 1;
    if (!pool.has(card.r)) pool.set(card.r, []);
    pool.get(card.r).push(card);
  }
  const shapes = enumerateShapes(counts, cards.length);
  if (!shapes.length) return null;
  const best = shapes.reduce((a, b) => (beatsShape(shapeSortKey(a), shapeSortKey(b)) >= 0 ? a : b));
  return realizeShape(best, pool);
}

function beats(candidate, standing) {
  if (standing.type === "rocket") return false;
  if (candidate.type === "rocket") return true;
  if (candidate.tier && standing.tier) return candidate.main > standing.main;
  if (candidate.tier) return true;
  if (standing.tier) return false;
  return candidate.type === standing.type
    && candidate.len === standing.len
    && candidate.main > standing.main;
}

function findMoves(hand, standing) {
  const counts = {};
  const pool = new Map();
  for (const card of hand) {
    counts[card.r] = (counts[card.r] || 0) + 1;
    if (!pool.has(card.r)) pool.set(card.r, []);
    pool.get(card.r).push(card);
  }
  const moves = [];
  const seen = new Set();
  for (let count = 1; count <= hand.length; count += 1) {
    for (const shape of enumerateShapes(counts, count)) {
      if (standing && !beats(shape, standing)) continue;
      const key = `${shape.type}:${shape.main}:${shape.len}`;
      if (seen.has(key)) continue;
      const combo = realizeShape(shape, pool);
      if (!combo) continue;
      seen.add(key);
      moves.push(combo);
    }
  }
  moves.sort((a, b) => beatsShape(shapeSortKey(a), shapeSortKey(b)));
  return moves;
}

function indicesOf(hand, cards) {
  const used = new Set();
  const indices = [];
  for (const card of cards) {
    const index = hand.findIndex((own, i) => !used.has(i)
      && own.r === card.r && own.s === card.s);
    if (index < 0) return null;
    used.add(index);
    indices.push(index);
  }
  return indices;
}

/* =========================================================
   渲染
========================================================= */

function isMyTurn() {
  const me = selfUsername();
  return Boolean(me) && state.myRoom.to_act === me;
}

function standingCombo() {
  const room = state.myRoom;
  if (room.free_lead || !room.standing) return null;
  return room.standing;
}

function resolvedSelection() {
  const hand = state.myRoom.your_hand || [];
  if (!selectedIndices.size) return null;
  const cards = [...selectedIndices].map((i) => hand[i]).filter(Boolean);
  if (cards.length !== selectedIndices.size) return null;
  return resolveCombo(cards);
}

function dcardNode(card, opts = {}) {
  const node = document.createElement("span");
  node.dataset.rank = card.r;
  node.dataset.suit = card.s;
  const red = card.s === 1 || card.s === 2 || card.r === 17;
  node.className = `dcard${red ? " red" : ""}${opts.small ? " small" : ""}${opts.picked ? " picked" : ""}`;
  const rank = document.createElement("span");
  rank.className = "dc-rank";
  if (card.s === 4) {
    const big = card.r === 17;
    node.classList.add("joker-card", big ? "joker-big" : "joker-small");
    rank.classList.add("dc-joker-rank");
    const icon = document.createElement("img");
    icon.className = "dc-joker-icon";
    icon.src = "assets/cards/joker-flat.png";
    icon.alt = "";
    icon.draggable = false;
    rank.append(icon);
    node.title = big ? "大王" : "小王";
  } else {
    rank.textContent = RANK_CHARS[card.r] || String(card.r);
  }
  const suit = document.createElement("span");
  suit.className = "dc-suit";
  if (card.s === 4) suit.classList.add("dc-joker-label");
  suit.textContent = card.s === 4 ? (card.r === 17 ? "大王" : "小王") : SUIT_CHARS[card.s];
  node.append(rank, suit);
  return node;
}

function ddAct(payload) {
  if (ddActionLock || state.myRoom?.spectator) return;
  ddActionLock = send({ type: "poker_action", ...payload });
  if (ddActionLock) {
    document.querySelectorAll(".dd-dock button").forEach((button) => { button.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "doudizhu") return;
  ddActionLock = false;
  renderGameView();
});

function myHandSorted() {
  const hand = state.myRoom.your_hand || [];
  return hand.map((card, index) => ({ card, index }))
    .sort((a, b) => b.card.r - a.card.r || b.card.s - a.card.s);
}

function updateSelection() {
  document.querySelectorAll(".dd-hand-card").forEach((button) => {
    const picked = selectedIndices.has(Number(button.dataset.index));
    button.setAttribute("aria-pressed", String(picked));
    button.querySelector(".dcard").classList.toggle("picked", picked);
  });
  document.querySelector(".dd-selection-status")?.replaceWith(selectionStatusNode());
  document.querySelector(".dd-dock .action-bar")?.replaceWith(ddActionBarNode(resolvedSelection()));
}

function selectionStatusNode() {
  const note = document.createElement("div");
  note.className = "dd-selection-status";
  note.setAttribute("role", "status");
  const count = selectedIndices.size;
  const combo = resolvedSelection();
  const standing = standingCombo();
  const legal = Boolean(combo) && (!standing || beats(combo, standing));
  note.textContent = count
    ? `${combo?.label || "当前选择无法出牌，请重新选牌"}${combo && !legal ? "（压不过上家）" : ""}`
    : "点击选牌，可多选；再次点击取消";
  return note;
}

function seatNode(p) {
  const room = state.myRoom;
  const seat = document.createElement("div");
  seat.className = "dd-seat";
  seat.dataset.username = p.username;
  if (room.status === "playing" && room.to_act === p.username) seat.classList.add("active");
  if (p.username === selfUsername()) seat.classList.add("me");
  const name = document.createElement("div");
  name.className = "ds-name";
  const avatar = playerAvatarNode(!room.spectator && p.username === selfUsername()
    ? { ...p, avatar: p.avatar || state.currentUser?.avatar || "" } : p);
  const role = document.createElement("span");
  role.className = "ds-role-tag";
  role.textContent = p.landlord ? "地主" : "农民";
  if (p.landlord) role.classList.add("is-landlord");
  const info = document.createElement("div");
  info.className = "ds-info";
  const stack = document.createElement("span");
  stack.className = "ds-stack";
  stack.textContent = formatCoins(p.stack);
  const count = document.createElement("span");
  count.className = "ds-count";
  count.textContent = room.status === "playing"
    ? (p.in_hand ? `🂠 ${p.cards}` : "已出完") : `${formatCoins(p.stack)} 筹码`;
  info.append(stack, count);
  seat.append(avatar, role, name, ratingBadge(p.rating), info);
  if (p.landlord && room.status === "playing") {
    const crown = document.createElement("span");
    crown.className = "ds-crown";
    crown.textContent = "👑";
    crown.title = "地主";
    seat.append(crown);
  }
  if (p.passed && room.status === "playing" && p.in_hand && p.cards > 0) {
    const pass = document.createElement("span");
    pass.className = "ds-pass";
    pass.textContent = "不出";
    seat.append(pass);
  }
  return seat;
}

function multiplierBarNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "dd-mults";
  const bid = document.createElement("span");
  bid.className = "dd-mult-chip";
  bid.textContent = room.rules?.bid_mode === "random"
    ? "随机地主 · 底分 ×1" : `叫分 ${room.bid_score || "-"} 分`;
  bar.append(bid);
  const bombs = document.createElement("span");
  bombs.className = "dd-mult-chip";
  bombs.textContent = room.bombs ? `💣×${room.bombs}` : "暂无炸弹";
  bar.append(bombs);
  const mult = document.createElement("span");
  mult.className = "dd-mult-chip total";
  mult.textContent = `倍率 ×${room.multiplier || 1}`;
  bar.append(mult);
  return bar;
}

function bottomCardsNode() {
  const room = state.myRoom;
  const bottom = room.bottom || [];
  const area = document.createElement("div");
  area.className = "dd-bottom";
  const label = document.createElement("span");
  label.className = "dd-bottom-label";
  label.textContent = "底牌";
  area.append(label);
  const cards = document.createElement("div");
  cards.className = "dd-bottom-cards";
  if (!bottom.length) {
    const hidden = document.createElement("span");
    hidden.className = "dd-bottom-hidden";
    hidden.textContent = "🂠 🂠 🂠";
    cards.append(hidden);
  } else {
    for (const card of bottom) cards.append(dcardNode(card, { small: true }));
  }
  area.append(cards);
  return area;
}

function tablePlayNode(player, position) {
  const room = state.myRoom;
  const play = room.table_plays?.[player.username]
    || (room.standing?.by === player.username ? room.standing : null);
  if (!play?.cards?.length) return null;
  const area = document.createElement("div");
  area.className = `dd-table-play ${position}`;
  if (room.standing?.by === player.username) area.classList.add("is-standing");
  area.dataset.username = player.username;
  area.setAttribute("aria-label", `${player.nickname} 上轮出牌：${play.label}`);
  const cards = document.createElement("div");
  cards.className = `dd-play-cards combo-${play.type}`;
  for (const card of play.cards) cards.append(dcardNode(card));
  const label = document.createElement("div");
  label.className = "dd-play-label";
  label.textContent = play.label;
  area.append(cards, label);
  return area;
}

function centerNode() {
  const room = state.myRoom;
  const center = document.createElement("div");
  center.className = "dd-center";
  if (room.status !== "playing") return center;
  if (room.stage === "bid" && room.to_act) {
    const bid = document.createElement("div");
    bid.className = "dd-bid-status";
    bid.textContent = room.bid_highest
      ? `${displayNameOf(room.to_act)} 叫分（当前最高 ${room.bid_highest} 分）`
      : `${displayNameOf(room.to_act)} 叫分`;
    center.append(bid);
  } else if (room.free_lead && room.to_act) {
    const free = document.createElement("div");
    free.className = "dd-free-lead";
    free.textContent = `${displayNameOf(room.to_act)} 自由出牌`;
    center.append(free);
  }
  return center;
}

function ddResultNode(result) {
  const box = document.createElement("div");
  box.className = "poker-result dd-result";
  if (result.aborted) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "有人离桌，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  const me = selfUsername();
  const banner = document.createElement("div");
  banner.className = "dd-match-banner";
  banner.textContent = result.peasants_win
    ? `🌱 农民获胜！${displayNameOf(result.landlord)} 被击败`
    : `👑 地主 ${displayNameOf(result.landlord)} 获胜！`;
  box.append(banner);

  const head = document.createElement("div");
  head.className = "poker-result-row";
  const left = document.createElement("span");
  const parts = [`叫分 ${result.bid_score}`, `炸弹 ×${2 ** (result.bombs || 0)}`];
  if (result.spring) parts.push("春天 ×2");
  left.textContent = `倍率 ×${result.multiplier}（${parts.join(" · ")}）`;
  const right = document.createElement("span");
  const myPay = result.payouts?.[me] || 0;
  const myGain = result.gains?.[me] || 0;
  const net = myGain - myPay;
  right.className = net > 0 ? "win" : net < 0 ? "lose" : "";
  right.textContent = net ? `${net > 0 ? "+" : "-"}${formatCoins(Math.abs(net))}` : "±0";
  head.append(left, right);
  box.append(head);

  const order = [...(result.winners || []),
    ...Object.keys({ ...result.payouts, ...result.gains })
      .filter((name) => !(result.winners || []).includes(name))];
  for (const name of order) {
    requestProfile(name);
    const row = document.createElement("div");
    row.className = "poker-result-row";
    const who = document.createElement("span");
    who.textContent = `${name === result.landlord ? "👑 " : "🌱 "}${displayNameOf(name)}`
      + (name === me ? "（我）" : "");
    const amount = document.createElement("span");
    const paid = result.payouts?.[name] || 0;
    const gained = result.gains?.[name] || 0;
    const net2 = gained - paid;
    amount.textContent = `${net2 > 0 ? "+" : net2 < 0 ? "-" : ""}${formatCoins(Math.abs(net2))}`;
    amount.className = net2 > 0 ? "win" : net2 < 0 ? "lose" : "";
    row.append(who, amount);
    box.append(row);
  }

  const reveal = document.createElement("div");
  reveal.className = "uno-reveal";
  const handEntries = Object.entries(result.hands || {});
  if (result.bottom?.length) {
    const line = document.createElement("div");
    line.className = "uno-reveal-row";
    const label = document.createElement("span");
    label.className = "uno-reveal-name";
    label.textContent = "底牌";
    const cardsBox = document.createElement("span");
    cardsBox.className = "uno-reveal-cards";
    for (const card of result.bottom) cardsBox.append(dcardNode(card, { small: true }));
    line.append(label, cardsBox);
    reveal.append(line);
  }
  for (const [name, cards] of handEntries) {
    requestProfile(name);
    if (!cards.length) continue;
    const line = document.createElement("div");
    line.className = "uno-reveal-row";
    const label = document.createElement("span");
    label.className = "uno-reveal-name";
    label.textContent = `${name === result.landlord ? "👑" : "🌱"} ${displayNameOf(name)}`;
    const cardsBox = document.createElement("span");
    cardsBox.className = "uno-reveal-cards";
    for (const card of cards) cardsBox.append(dcardNode(card, { small: true }));
    line.append(label, cardsBox);
    reveal.append(line);
  }
  if (reveal.children.length) box.append(reveal);
  return box;
}

function ddBidBarNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "action-bar";
  const options = room.your_options || {};
  const scores = options.bid || [];
  const pass = document.createElement("button");
  pass.type = "button";
  pass.className = "action-btn danger";
  pass.textContent = "不叫";
  pass.disabled = ddActionLock || !scores.length;
  pass.addEventListener("click", () => ddAct({ action: "pass" }));
  bar.append(pass);
  for (const score of [1, 2, 3]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `action-btn${score === 3 ? " primary" : ""}`;
    button.textContent = `叫 ${score} 分`;
    button.disabled = ddActionLock || !scores.includes(score);
    button.addEventListener("click", () => ddAct({ action: "bid", score }));
    bar.append(button);
  }
  return bar;
}

function ddActionBarNode(resolved) {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "action-bar";
  const options = room.your_options || {};
  const myTurn = isMyTurn() && !room.paused;

  if (myTurn) {
    const standing = standingCombo();
    const legal = Boolean(resolved) && (!standing || beats(resolved, standing));
    const hint = document.createElement("button");
    hint.type = "button";
    hint.className = "action-btn";
    hint.textContent = availableMoves().length ? "提示" : "无牌可接";
    hint.disabled = ddActionLock || !availableMoves().length;
    hint.addEventListener("click", () => {
      availableMoves();
      if (!hintMoves.length) return;
      const move = hintMoves[hintCursor % hintMoves.length];
      hintCursor += 1;
      const indices = indicesOf(room.your_hand || [], move.cards);
      if (indices) {
        selectedIndices = new Set(indices);
        updateSelection();
      }
    });
    bar.append(hint);

    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "action-btn dd-clear";
    clear.textContent = "重选";
    clear.disabled = ddActionLock || !selectedIndices.size;
    clear.addEventListener("click", () => {
      selectedIndices.clear();
      updateSelection();
    });
    bar.append(clear);

    const play = document.createElement("button");
    play.type = "button";
    play.className = "action-btn primary";
    play.textContent = legal ? `出牌 · ${selectedIndices.size} 张` : "出牌";
    play.disabled = ddActionLock || !legal;
    play.addEventListener("click", () => {
      if (!resolved || !legal) return;
      ddAct({ action: "play", cards: [...selectedIndices].sort((a, b) => a - b) });
    });
    bar.append(play);

    if (options.pass) {
      const pass = document.createElement("button");
      pass.type = "button";
      pass.className = "action-btn danger";
      pass.textContent = "不出";
      pass.disabled = ddActionLock;
      pass.addEventListener("click", () => {
        selectedIndices = new Set();
        ddAct({ action: "pass" });
      });
      bar.append(pass);
    }
  }
  return bar;
}

function availableMoves() {
  const room = state.myRoom;
  const key = JSON.stringify([room.room_id, room.hand_no, room.your_hand,
    room.standing, room.free_lead]);
  if (hintKey !== key) {
    hintMoves = findMoves(room.your_hand || [], standingCombo());
    hintCursor = 0;
    hintKey = key;
  }
  return hintMoves;
}

function renderDoudizhuTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    ddActionLock = false;
    lastRoomView = room;
    turnDeadline = Date.now() + (room.turn_left || 0) * 1000;
  }
  body.replaceChildren();

  const handJson = JSON.stringify([room.room_id, room.hand_no, room.stage, room.your_hand || []]);
  if (handJson !== lastHandJson) {
    selectedIndices = new Set();
    lastHandJson = handJson;
    hintKey = "";
  }

  const wrap = document.createElement("div");
  wrap.className = "casual-page dd-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "dd-table";

  const topbar = document.createElement("div");
  topbar.className = "dd-topbar";
  const left = document.createElement("span");
  left.textContent = `第 ${room.hand_no || "-"} 局 · 底注 ${formatCoinsWhole(room.blind)}`
    + (room.rules?.bid_mode === "random" ? " · 随机地主" : " · 叫分竞叫")
    + (room.rules?.bottom_visible ? " · 明底牌" : " · 暗底牌")
    + (room.rules?.spring === false ? " · 春天关" : " · 春天开");
  const right = document.createElement("span");
  right.textContent = room.paused ? "⏸ 已暂停" : `倍率 ×${room.multiplier || 1}`;
  topbar.append(left, right);
  table.append(topbar);

  table.append(multiplierBarNode());
  table.append(bottomCardsNode());

  const status = document.createElement("div");
  status.className = "dd-status";
  const la = room.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : room.to_act ? `等待 ${displayNameOf(room.to_act)} …` : "发牌中…";
  table.append(status);

  const seats = document.createElement("div");
  seats.className = "dd-seats";
  const plays = document.createElement("div");
  plays.className = "dd-table-plays";
  const players = room.players;
  const myIndex = Math.max(0, players.findIndex((p) => p.username === selfUsername()));
  const positions = ["pos-bottom", "pos-left", "pos-right"];
  players.forEach((p, index) => {
    const seat = seatNode(p);
    const relative = (index - myIndex + players.length) % players.length;
    const position = positions[relative] || "pos-top";
    seat.classList.add(position);
    seats.append(seat);
    const play = tablePlayNode(p, position);
    if (play) plays.append(play);
  });
  const arena = document.createElement("div");
  arena.className = "dd-arena";
  arena.append(seats, plays, centerNode());
  table.append(arena);

  if (room.result) table.append(ddResultNode(room.result));

  if (room.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);

  const dock = document.createElement("div");
  dock.className = "casual-dock dd-dock";
  dock.classList.toggle("is-my-turn", !room.spectator && isMyTurn() && !room.paused);
  const dockHead = document.createElement("div");
  dockHead.className = "dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  const hand = room.your_hand || [];
  label.textContent = room.paused ? "牌局已暂停"
    : room.spectator ? `观战视角 · 剩 ${hand.length} 张`
    : isMyTurn()
    ? `轮到你 · 剩 ${hand.length} 张`
    : `你的手牌 · 剩 ${hand.length} 张`;
  const chatToggle = document.createElement("button");
  chatToggle.className = "dock-chat-toggle";
  chatToggle.type = "button";
  chatToggle.textContent = "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  dockHead.append(label);
  const la2 = room.last_action;
  if (la2 && room.status === "playing") {
    const action = document.createElement("div");
    action.className = "dd-dock-action";
    action.textContent = `${la2.nickname} ${la2.text}`;
    dockHead.append(action);
  }
  dockHead.append(chatToggle);
  dock.append(dockHead);

  const myCards = document.createElement("div");
  myCards.className = "dd-hand";
  myCards.classList.toggle("many-cards", hand.length > 17);
  if (!hand.length) {
    const waiting = document.createElement("span");
    waiting.className = "my-cards-label";
    waiting.textContent = room.spectator || room.status !== "playing"
      ? "等待发牌…" : "你已出完，等待本局结束…";
    myCards.append(waiting);
  }
  for (const { card, index } of myHandSorted()) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "dd-hand-card";
    node.dataset.index = index;
    node.setAttribute("aria-pressed", String(selectedIndices.has(index)));
    node.disabled = ddActionLock || room.paused || Boolean(room.spectator);
    const cardNode = dcardNode(card, { picked: selectedIndices.has(index) });
    node.append(cardNode);
    node.setAttribute("aria-label", `${card.s === 4 ? RANK_CHARS[card.r] : `${SUIT_CHARS[card.s]}${RANK_CHARS[card.r] || card.r}`}`);
    node.addEventListener("click", () => {
      if (selectedIndices.has(index)) selectedIndices.delete(index);
      else selectedIndices.add(index);
      updateSelection();
    });
    myCards.append(node);
  }
  dock.append(myCards);
  dock.append(selectionStatusNode());

  const countdown = document.createElement("div");
  countdown.className = "countdown";
  const fill = document.createElement("div");
  fill.className = "countdown-fill";
  countdown.append(fill);
  dock.append(countdown);

  const bidding = room.stage === "bid";
  if (!room.paused) {
    if (!room.spectator && isMyTurn()) {
      dock.append(bidding ? ddBidBarNode() : ddActionBarNode(resolvedSelection()));
      const remaining = (turnDeadline - Date.now()) / 1000;
      if (remaining > 0) startHallTicker(fill, remaining);
    } else if (bidding) {
      const waitingBid = document.createElement("div");
      waitingBid.className = "last-action";
      waitingBid.textContent = room.bid_highest
        ? `等待叫分 · 当前最高 ${room.bid_highest} 分`
        : "等待叫分…";
      dock.append(waitingBid);
    }
  } else {
    const pausedNote = document.createElement("div");
    pausedNote.className = "last-action";
    pausedNote.textContent = "牌局已暂停";
    dock.append(pausedNote);
  }
  wrap.append(dock);

  reapplySeatBubbles();
}

/* =========================================================
   事件绑定
========================================================= */

registerGame("doudizhu", {
  compactDesktopChat: true,
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "斗地主需要正好 3 名玩家：叫分最高的玩家成为地主，拿 3 张底牌先出牌。先出完者获胜，中途退出本局作废、筹码原封退回。",
  noNextHint: () => "人数不足 3 人或有人筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderDoudizhuTable,
  renderReview: (result) => {
    const review = document.createElement("div");
    const title = document.createElement("div");
    title.className = "hall-section-title";
    title.style.marginTop = "0";
    title.textContent = "本局回顾";
    review.append(title, ddResultNode(result));
    return review;
  },
  seatElement: (index) => document.querySelector(`#gameMain .dd-seats .dd-seat:nth-child(${index + 1})`),
});
