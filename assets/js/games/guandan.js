/* 掼蛋场景：两侧对手、上方队友、底部选牌、提示与结算回顾。
   牌型判定逻辑与 games/guandan.py 保持一致：客户端只做预校验和提示，
   服务器仍是唯一裁判。 */

import { displayNameOf, elements, formatCoins, playerAvatarNode, ratingBadge, renderGameView, requestProfile, send, startHallTicker, state } from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";

const SUIT_CHARS = ["♠", "♥", "♦", "♣"];
const RANK_CHARS = { 11: "J", 12: "Q", 13: "K", 14: "A", 16: "小王", 17: "大王" };
const COMBO_NAMES = {
  single: "单张", pair: "对子", triple: "三张", triple_pair: "三带二",
  straight: "顺子", pairs_seq: "连对", triple_seq: "钢板",
  flush_straight: "同花顺", bomb: "炸弹", king_bomb: "天王炸",
};
const ROLE_NAMES = { 1: "头游", 2: "二游", 3: "三游", 4: "末游" };
const TEAM_NAMES = ["蓝队", "红队"];
const TEAM_COLORS = ["#2f6fd6", "#d64545"];
const ACE = 14;
const LOW = 3;

let gdActionLock = false;
let selectedIndices = new Set();
let lastHandJson = "";
let hintMoves = [];
let hintCursor = 0;
let hintKey = "";
let lastRoomView = null;
let turnDeadline = 0;

/* =========================================================
   牌型判定（与后端 games/guandan.py 同一套规则）
========================================================= */

function rankKey(tierKey, point) {
  return tierKey * 20 + point;
}

function rankValue(rank, levels) {
  if (rank === 17) return rankKey(3, 2);
  if (rank === 16) return rankKey(3, 1);
  if (levels.includes(rank)) return rankKey(2, rank);
  return rankKey(1, rank);
}

function mainChar(main) {
  if (main >= 60) return main === 62 ? "大王" : "小王";
  const point = main % 20;
  return RANK_CHARS[point] || String(point);
}

function isWildCard(card, wildRank) {
  return Boolean(wildRank) && card.s === 1 && card.r === wildRank;
}

/** needs 的缺口能否全由万能牌补上（万能牌不能补王）。 */
function checkNeeds(needs, nat, wilds) {
  let used = 0;
  for (const [rank, need] of Object.entries(needs)) {
    const r = Number(rank);
    const shortage = need - (nat[r] || 0);
    if (shortage <= 0) continue;
    if (r >= 16) return false;
    used += shortage;
    if (used > wilds) return false;
  }
  return true;
}

function enumerateShapes(nat, natSuit, wilds, total, levels, wildRank) {
  const shapes = [];
  const add = (type, tier, main, needs, suit = null) => {
    shapes.push({ type, tier, main, len: total, needs, suit });
  };

  if (total >= 4 && total <= 8) {
    for (let r = 2; r <= ACE; r += 1) {
      const count = (nat[r] || 0) + (r === wildRank ? wilds : 0);
      if (count >= total) {
        add("bomb", total, rankValue(r, levels), { [r]: total });
      }
    }
  }
  if (total === 4 && (nat[16] || 0) === 2 && (nat[17] || 0) === 2) {
    add("king_bomb", 99, rankKey(4, 0), { 16: 2, 17: 2 });
  }

  if (total === 1) {
    for (const rank of Object.keys(nat)) {
      add("single", 0, rankValue(Number(rank), levels), { [rank]: 1 });
    }
    if (wilds) add("single", 0, rankValue(wildRank, levels), {});
  }

  if (total === 2) {
    for (const [rank, count] of Object.entries(nat)) {
      if (checkNeeds({ [rank]: 2 }, nat, wilds)) {
        add("pair", 0, rankValue(Number(rank), levels), { [rank]: 2 });
      }
    }
    for (let rank = 2; rank <= ACE; rank += 1) {
      const needWilds = 2 - (nat[rank] || 0);
      if (needWilds > 0 && needWilds <= wilds) {
        add("pair", 0, rankValue(rank, levels), { [rank]: 2 });
      }
    }
  }

  if (total === 3) {
    for (let rank = 2; rank <= ACE; rank += 1) {
      if (checkNeeds({ [rank]: 3 }, nat, wilds)) {
        add("triple", 0, rankValue(rank, levels), { [rank]: 3 });
      }
    }
  }

  if (total === 5) {
    for (let t = 2; t <= ACE; t += 1) {
      for (let p = 2; p <= 17; p += 1) {
        if (p === t) continue;
        const needs = { [t]: 3, [p]: 2 };
        if (checkNeeds(needs, nat, wilds)) {
          add("triple_pair", 0, rankValue(t, levels), needs);
        }
      }
    }
    for (let top = LOW + 4; top <= ACE; top += 1) {
      const needs = {};
      for (let r = top - 4; r <= top; r += 1) needs[r] = 1;
      if (checkNeeds(needs, nat, wilds)) add("straight", 0, rankKey(1, top), needs);
    }
    for (let suit = 0; suit < 4; suit += 1) {
      for (let top = LOW + 4; top <= ACE; top += 1) {
        let used = 0;
        let ok = true;
        for (let r = top - 4; r <= top; r += 1) {
          const shortage = 1 - (natSuit[`${r}:${suit}`] || 0);
          if (shortage > 0) {
            used += shortage;
            if (used > wilds) { ok = false; break; }
          }
        }
        if (ok) {
          const needs = {};
          for (let r = top - 4; r <= top; r += 1) needs[r] = 1;
          add("flush_straight", 5.5, rankKey(1, top), needs, suit);
        }
      }
    }
  }

  if (total >= 6 && total % 2 === 0) {
    const pairs = total / 2;
    if (pairs >= 3) {
      for (let top = LOW + pairs - 1; top <= ACE; top += 1) {
        const needs = {};
        for (let r = top - pairs + 1; r <= top; r += 1) needs[r] = 2;
        if (checkNeeds(needs, nat, wilds)) add("pairs_seq", 0, rankKey(1, top), needs);
      }
    }
  }
  if (total >= 6 && total % 3 === 0) {
    const triples = total / 3;
    if (triples >= 2) {
      for (let top = LOW + triples - 1; top <= ACE; top += 1) {
        const needs = {};
        for (let r = top - triples + 1; r <= top; r += 1) needs[r] = 3;
        if (checkNeeds(needs, nat, wilds)) add("triple_seq", 0, rankKey(1, top), needs);
      }
    }
  }
  return shapes;
}

function shapeSortKey(shape) {
  return [shape.type === "king_bomb" ? 1 : 0, shape.tier, shape.main, shape.len];
}

function comboLabel(shape) {
  const name = COMBO_NAMES[shape.type];
  if (shape.type === "bomb") return `炸弹 ${mainChar(shape.main)}×${shape.len}张`;
  if (shape.type === "king_bomb") return name;
  return `${name} ${mainChar(shape.main)}`;
}

function displayRanks(shape) {
  const ranks = Object.keys(shape.needs).map(Number).sort((a, b) => a - b);
  if (shape.type === "triple_pair") {
    const tripleRank = shape.main % 20;
    return [tripleRank, ...ranks.filter((rank) => rank !== tripleRank)];
  }
  return ranks;
}

function realizeShape(shape, cards, wildRank) {
  const wildCards = shape.type === "bomb" ? [] : cards.filter((c) => isWildCard(c, wildRank));
  const natural = cards.filter((c) => !isWildCard(c, wildRank));
  const nats = shape.type === "bomb"
    ? [...natural, ...cards.filter((c) => isWildCard(c, wildRank))] : natural;
  const pool = new Map();
  if (shape.suit === null) {
    for (const card of nats) {
      if (!pool.has(card.r)) pool.set(card.r, []);
      pool.get(card.r).push(card);
    }
  } else {
    for (const card of nats) {
      const key = `${card.r}:${card.s}`;
      if (!pool.has(key)) pool.set(key, []);
      pool.get(key).push(card);
    }
  }
  const picked = [];
  let wildIndex = 0;
  for (const rank of displayRanks(shape)) {
    const need = shape.needs[rank];
    const key = shape.suit === null ? rank : `${rank}:${shape.suit}`;
    const naturalCards = (pool.get(key) || []).slice(0, need)
      .sort((a, b) => a.s - b.s || a.r - b.r);
    picked.push(...naturalCards);
    const short = need - naturalCards.length;
    if (wildIndex + short > wildCards.length) return null;
    picked.push(...wildCards.slice(wildIndex, wildIndex + short));
    wildIndex += short;
  }
  const remaining = shape.len - picked.length;
  if (remaining > wildCards.length - wildIndex) return null;
  picked.push(...wildCards.slice(wildIndex, wildIndex + remaining));
  return { type: shape.type, tier: shape.tier, main: shape.main, len: shape.len,
    label: comboLabel(shape), cards: picked };
}

function resolveCombo(cards, wildRank, levels, standing = null) {
  const nats = cards.filter((c) => !isWildCard(c, wildRank));
  const nat = {};
  const natSuit = {};
  for (const card of nats) {
    nat[card.r] = (nat[card.r] || 0) + 1;
    natSuit[`${card.r}:${card.s}`] = (natSuit[`${card.r}:${card.s}`] || 0) + 1;
  }
  const wilds = cards.length - nats.length;
  const shapes = enumerateShapes(nat, natSuit, wilds, cards.length, levels, wildRank)
    .filter((shape) => !standing || beats(shape, standing));
  if (!shapes.length) return null;
  shapes.sort((a, b) => {
    const ka = shapeSortKey(a);
    const kb = shapeSortKey(b);
    return kb[0] - ka[0] || kb[1] - ka[1] || kb[2] - ka[2] || kb[3] - ka[3];
  });
  return realizeShape(shapes[0], cards, wildRank);
}

function beats(candidate, standing) {
  if (standing.type === "king_bomb") return false;
  if (candidate.type === "king_bomb") return true;
  if (candidate.tier && standing.tier) {
    if (candidate.tier !== standing.tier) return candidate.tier > standing.tier;
    return candidate.type === standing.type && candidate.main > standing.main;
  }
  if (candidate.tier) return true;
  if (standing.tier) return false;
  return candidate.type === standing.type
    && candidate.len === standing.len
    && candidate.main > standing.main;
}

function findMoves(hand, wildRank, levels, standing) {
  const nats = hand.filter((c) => !isWildCard(c, wildRank));
  const nat = {};
  const natSuit = {};
  for (const card of nats) {
    nat[card.r] = (nat[card.r] || 0) + 1;
    natSuit[`${card.r}:${card.s}`] = (natSuit[`${card.r}:${card.s}`] || 0) + 1;
  }
  const wilds = hand.length - nats.length;
  const moves = [];
  const seen = new Set();
  for (let count = 1; count <= hand.length; count += 1) {
    for (const shape of enumerateShapes(nat, natSuit, wilds, count, levels, wildRank)) {
      const picked = realizeShape(shape, hand, wildRank);
      if (!picked) continue;
      const combo = resolveCombo(picked.cards, wildRank, levels, standing);
      if (!combo) continue;
      const key = `${combo.type}:${combo.main}:${combo.len}`;
      if (seen.has(key)) continue;
      seen.add(key);
      moves.push(combo);
    }
  }
  moves.sort((a, b) => {
    const ka = shapeSortKey(a);
    const kb = shapeSortKey(b);
    return ka[0] - kb[0] || ka[1] - kb[1] || ka[2] - kb[2] || ka[3] - kb[3];
  });
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
  return Boolean(state.currentUser) && state.myRoom.to_act === state.currentUser.username;
}

function levelSet() {
  const levels = state.myRoom.levels || [2, 2];
  return [...new Set(levels)];
}

function wildRankForMe() {
  const room = state.myRoom;
  if (!room.rules?.wild) return null;
  const team = room.my_team ?? 0;
  return (room.levels || [2, 2])[team];
}

function wildRankForPlayer(username) {
  const room = state.myRoom;
  if (!room.rules?.wild) return null;
  const player = room.players?.find((item) => item.username === username);
  return (room.levels || [2, 2])[player?.team ?? 0];
}

function gcardNode(card, opts = {}) {
  const node = document.createElement("span");
  node.dataset.rank = card.r;
  node.dataset.suit = card.s;
  const red = card.s === 1 || card.s === 2 || card.r === 17;
  node.className = `gcard${red ? " red" : ""}${opts.small ? " small" : ""}${opts.picked ? " picked" : ""}`;
  if (opts.wild) {
    node.classList.add("wild");
    node.title = "逢人配（红桃级牌，可代任意牌）";
  }
  const rank = document.createElement("span");
  rank.className = "gc-rank";
  if (card.s === 4) {
    const big = card.r === 17;
    node.classList.add("joker-card", big ? "joker-big" : "joker-small");
    rank.classList.add("gc-joker-icon");
    rank.textContent = "🃏";
    node.title = big ? "大王" : "小王";
  } else {
    rank.textContent = RANK_CHARS[card.r] || String(card.r);
  }
  const suit = document.createElement("span");
  suit.className = "gc-suit";
  if (card.s === 4) suit.classList.add("gc-joker-label");
  suit.textContent = card.s === 4 ? (card.r === 17 ? "大王" : "小王") : SUIT_CHARS[card.s];
  node.append(rank, suit);
  return node;
}

function gdAct(payload) {
  if (gdActionLock) return;
  gdActionLock = send({ type: "poker_action", ...payload });
  if (gdActionLock) {
    document.querySelectorAll(".gd-dock button").forEach((button) => { button.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "guandan") return;
  gdActionLock = false;
  renderGameView();
});

function myHandSorted() {
  const hand = state.myRoom.your_hand || [];
  const levels = levelSet();
  return hand.map((card, index) => ({ card, index }))
    .sort((a, b) => rankValue(b.card.r, levels) - rankValue(a.card.r, levels)
      || b.card.s - a.card.s);
}

function resolvedSelection() {
  const hand = state.myRoom.your_hand || [];
  if (!selectedIndices.size) return null;
  const cards = [...selectedIndices].map((i) => hand[i]).filter(Boolean);
  if (cards.length !== selectedIndices.size) return null;
  return resolveCombo(cards, wildRankForMe(), levelSet(), standingCombo());
}

function standingCombo() {
  const room = state.myRoom;
  if (room.free_lead || !room.standing) return null;
  const standing = room.standing;
  return { ...standing, main: Array.isArray(standing.main)
    ? rankKey(...standing.main) : standing.main };
}

function availableMoves() {
  const room = state.myRoom;
  const key = JSON.stringify([room.room_id, room.hand_no, room.your_hand,
    room.standing, room.free_lead, room.levels, room.my_team, room.rules?.wild]);
  if (hintKey !== key) {
    hintMoves = findMoves(room.your_hand || [], wildRankForMe(), levelSet(), standingCombo());
    hintCursor = 0;
    hintKey = key;
  }
  return hintMoves;
}

function updateSelection() {
  document.querySelectorAll(".gd-hand-card").forEach((button) => {
    const picked = selectedIndices.has(Number(button.dataset.index));
    button.setAttribute("aria-pressed", String(picked));
    button.querySelector(".gcard").classList.toggle("picked", picked);
  });
  document.querySelector(".gd-selection-status")?.replaceWith(selectionStatusNode());
  document.querySelector(".gd-dock .action-bar")?.replaceWith(gdActionBarNode(resolvedSelection()));
}

function selectionStatusNode() {
  const note = document.createElement("div");
  note.className = "gd-selection-status";
  note.setAttribute("role", "status");
  const count = selectedIndices.size;
  const combo = resolvedSelection();
  note.textContent = count ? `已选 ${count} 张 · ${combo?.label || "当前选择无法出牌，请重新选牌"}`
    : "点击选牌，可多选；再次点击取消";
  return note;
}

function seatNode(p) {
  const room = state.myRoom;
  const seat = document.createElement("div");
  seat.className = "gd-seat";
  seat.dataset.username = p.username;
  const team = p.team ?? 0;
  seat.style.setProperty("--team-color", TEAM_COLORS[team]);
  if (room.status === "playing" && room.to_act === p.username) seat.classList.add("active");
  if (state.currentUser && p.username === state.currentUser.username) seat.classList.add("me");
  const name = document.createElement("div");
  name.className = "gs-name";
  const dot = document.createElement("span");
  dot.className = "gs-team-dot";
  dot.title = TEAM_NAMES[team];
  name.append(dot, document.createTextNode(p.nickname));
  const avatar = playerAvatarNode(p);
  const relation = document.createElement("span");
  relation.className = "gs-relation";
  relation.textContent = p.username === state.currentUser?.username ? "我"
    : team === room.my_team ? "队友" : "对手";
  const info = document.createElement("div");
  info.className = "gs-info";
  const stack = document.createElement("span");
  stack.className = "gs-stack";
  stack.textContent = formatCoins(p.stack);
  const count = document.createElement("span");
  count.className = "gs-count";
  count.textContent = room.status === "playing"
    ? (p.in_hand ? `🂠 ${p.cards}` : "已出完") : `${formatCoins(p.stack)} 筹码`;
  info.append(stack, count);
  seat.append(avatar, relation, name, ratingBadge(p.rating), info);
  if (p.finished) {
    const badge = document.createElement("span");
    badge.className = "gs-role";
    badge.textContent = ROLE_NAMES[p.finished] || "";
    seat.append(badge);
  }
  if (p.passed && room.status === "playing" && !p.finished) {
    const pass = document.createElement("span");
    pass.className = "gs-pass";
    pass.textContent = "不出";
    seat.append(pass);
  }
  return seat;
}

function levelsBarNode() {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "gd-levels";
  const levels = room.levels || [2, 2];
  const myTeam = room.my_team ?? 0;
  for (const team of [0, 1]) {
    const chip = document.createElement("span");
    chip.className = "gd-level-chip";
    const label = team === myTeam ? `我方 · ${TEAM_NAMES[team]}` : `对方 · ${TEAM_NAMES[team]}`;
    chip.innerHTML = `<i class="gd-dot" style="background:${TEAM_COLORS[team]}"></i>${label} 级 <b>${RANK_CHARS[levels[team]] || levels[team]}</b>`;
    if (team === myTeam) chip.classList.add("mine");
    bar.append(chip);
  }
  const mid = document.createElement("span");
  mid.className = "gd-mult";
  const bombs = room.bombs || 0;
  mid.textContent = room.multiplier > 1
    ? `💣×${bombs} · 倍率 ×${room.multiplier}` : "倍率 ×1";
  bar.append(mid);
  return bar;
}

function centerNode() {
  const room = state.myRoom;
  const center = document.createElement("div");
  center.className = "gd-center";
  const standing = room.standing;
  if (standing) {
    const cards = document.createElement("div");
    cards.className = "gd-standing-cards";
    cards.classList.add(`combo-${standing.type}`);
    const standingWildRank = wildRankForPlayer(standing.by);
    for (const card of standing.cards || []) {
      cards.append(gcardNode(card, {
        small: (standing.cards || []).length > 8,
        wild: isWildCard(card, standingWildRank),
      }));
    }
    const by = document.createElement("div");
    by.className = "gd-standing-by";
    by.textContent = `${displayNameOf(standing.by)} · ${standing.label}`;
    center.append(cards, by);
  } else if (room.status === "playing" && room.to_act) {
    const free = document.createElement("div");
    free.className = "gd-free-lead";
    free.textContent = room.free_lead
      ? `${displayNameOf(room.to_act)} 自由出牌`
      : "等待出牌…";
    center.append(free);
  }
  return center;
}

function gdResultNode(result) {
  const box = document.createElement("div");
  box.className = "poker-result gd-result";
  if (result.aborted) {
    const note = document.createElement("div");
    note.className = "poker-result-row";
    note.textContent = "有人离桌，本局作废，筹码原封不动。";
    box.append(note);
    return box;
  }
  const myTeam = state.myRoom.my_team ?? null;
  const winnerTeam = result.teams?.[result.finish?.[0]];
  if (result.match_win != null) {
    const banner = document.createElement("div");
    banner.className = "gd-match-banner";
    banner.textContent = `🏆 ${TEAM_NAMES[result.match_win]} 打过 A，取得比赛胜利！`;
    box.append(banner);
  }
  const head = document.createElement("div");
  head.className = "poker-result-row";
  const left = document.createElement("span");
  left.textContent = `🏆 ${TEAM_NAMES[winnerTeam]} 获胜${result.gain ? ` · 升 ${result.gain} 级` : ""}`
    + (result.multiplier > 1 ? ` · 倍率 ×${result.multiplier}` : "");
  const right = document.createElement("span");
  right.className = "win";
  const total = Object.values(result.gains || {}).reduce((sum, n) => sum + n, 0);
  right.textContent = total ? `+${formatCoins(total)}` : "";
  head.append(left, right);
  box.append(head);

  const levelLine = document.createElement("div");
  levelLine.className = "poker-result-row gd-level-line";
  const before = result.levels_before || {};
  const after = result.levels_after || {};
  levelLine.textContent = `级数：${TEAM_NAMES[0]} ${before[0]}→${after[0]} · ${TEAM_NAMES[1]} ${before[1]}→${after[1]}`
    + (myTeam != null ? `（我方 ${TEAM_NAMES[myTeam]}）` : "");
  box.append(levelLine);

  for (const name of result.finish || []) {
    requestProfile(name);
    const row = document.createElement("div");
    row.className = "poker-result-row";
    const who = document.createElement("span");
    const role = result.roles?.[name] || "";
    const team = result.teams?.[name];
    const dot = document.createElement("i");
    dot.className = "gd-dot";
    dot.style.background = TEAM_COLORS[team];
    who.append(dot, document.createTextNode(`${displayNameOf(name)} · ${role}`));
    const amount = document.createElement("span");
    const paid = result.payouts?.[name] || 0;
    const gained = result.gains?.[name] || 0;
    const net = gained - paid;
    amount.textContent = `${net > 0 ? "+" : net < 0 ? "-" : ""}${formatCoins(Math.abs(net))}`;
    amount.className = net > 0 ? "win" : net < 0 ? "lose" : "";
    row.append(who, amount);
    box.append(row);
  }

  const reveal = document.createElement("div");
  reveal.className = "uno-reveal";
  for (const [name, cards] of Object.entries(result.hands || {})) {
    requestProfile(name);
    if (!cards.length) continue;
    const line = document.createElement("div");
    line.className = "uno-reveal-row";
    const label = document.createElement("span");
    label.className = "uno-reveal-name";
    label.textContent = displayNameOf(name);
    const cardsBox = document.createElement("span");
    cardsBox.className = "uno-reveal-cards";
    for (const card of cards) cardsBox.append(gcardNode(card, { small: true }));
    line.append(label, cardsBox);
    reveal.append(line);
  }
  if (reveal.children.length) box.append(reveal);
  return box;
}

function gdActionBarNode(resolved) {
  const room = state.myRoom;
  const bar = document.createElement("div");
  bar.className = "action-bar";
  const options = room.your_options || {};
  const myTurn = isMyTurn() && !room.paused;

  if (myTurn) {
    const legal = Boolean(resolved) && (room.free_lead || beats(resolved, standingCombo()));
    const hint = document.createElement("button");
    hint.type = "button";
    hint.className = "action-btn";
    hint.textContent = "提示";
    hint.disabled = gdActionLock || !availableMoves().length;
    hint.textContent = availableMoves().length ? "提示" : "无牌可接";
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
    clear.className = "action-btn gd-clear";
    clear.textContent = "重选";
    clear.disabled = gdActionLock || !selectedIndices.size;
    clear.addEventListener("click", () => {
      selectedIndices.clear();
      updateSelection();
    });
    bar.append(clear);

    const play = document.createElement("button");
    play.type = "button";
    play.className = "action-btn primary";
    play.textContent = legal ? `出牌 · ${selectedIndices.size} 张` : "出牌";
    play.disabled = gdActionLock || !legal;
    play.addEventListener("click", () => {
      if (!resolved || !legal) return;
      gdAct({ action: "play", cards: [...selectedIndices].sort((a, b) => a - b) });
    });
    bar.append(play);

    if (options.pass) {
      const pass = document.createElement("button");
      pass.type = "button";
      pass.className = "action-btn danger";
      pass.textContent = "不出";
      pass.disabled = gdActionLock;
      pass.addEventListener("click", () => {
        selectedIndices = new Set();
        gdAct({ action: "pass" });
      });
      bar.append(pass);
    }
  }
  return bar;
}

function renderGuandanTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    gdActionLock = false;
    lastRoomView = room;
    turnDeadline = Date.now() + (room.turn_left || 0) * 1000;
  }
  body.replaceChildren();

  const handJson = JSON.stringify([room.room_id, room.hand_no, room.your_hand || []]);
  if (handJson !== lastHandJson) {
    selectedIndices = new Set();
    lastHandJson = handJson;
    hintKey = "";
  }

  const wrap = document.createElement("div");
  wrap.className = "casual-page gd-page";
  body.append(wrap);

  const table = document.createElement("div");
  table.className = "gd-table";

  const topbar = document.createElement("div");
  topbar.className = "poker-topbar";
  const levels = room.levels || [2, 2];
  const myTeam = room.my_team ?? 0;
  const levelText = [myTeam, 1 - myTeam].map((team) => RANK_CHARS[levels[team]] || levels[team]).join("对");
  const left = document.createElement("span");
  left.textContent = `第 ${room.hand_no || "-"} 局 · 底注 ${room.blind} · 级 ${levelText}`
    + (room.rules?.wild ? " · 逢人配开" : " · 逢人配关")
    + (room.rules?.ace_strict ? " · 严格过A" : " · 宽松过A");
  const right = document.createElement("span");
  right.textContent = room.paused ? "⏸ 已暂停" : `倍率 ×${room.multiplier || 1}`;
  topbar.append(left, right);
  table.append(topbar);

  table.append(levelsBarNode());

  const status = document.createElement("div");
  status.className = "poker-status";
  const la = room.last_action;
  status.textContent = la
    ? `${la.nickname} ${la.text}`
    : room.to_act ? `等待 ${displayNameOf(room.to_act)} 出牌…` : "发牌中…";
  table.append(status);

  const seats = document.createElement("div");
  seats.className = "gd-seats";
  const players = room.players;
  const myIndex = Math.max(0, players.findIndex((p) => p.username === state.currentUser?.username));
  const positions = ["pos-bottom", "pos-left", "pos-top", "pos-right"];
  players.forEach((p, index) => {
    const seat = seatNode(p);
    const relative = (index - myIndex + players.length) % players.length;
    seat.classList.add(positions[relative] || "pos-top");
    seats.append(seat);
  });
  const arena = document.createElement("div");
  arena.className = "gd-arena";
  arena.append(seats, centerNode());
  table.append(arena);

  if (room.result) table.append(gdResultNode(room.result));

  if (room.match_winner != null && !room.result) {
    const banner = document.createElement("div");
    banner.className = "gd-match-banner";
    banner.textContent = `🏆 ${TEAM_NAMES[room.match_winner]} 打过 A，下一局重新从 2 开始`;
    table.append(banner);
  }

  if (room.paused) {
    const overlay = document.createElement("div");
    overlay.className = "paused-overlay";
    overlay.textContent = "⏸ 牌局已暂停，等待房主继续";
    table.append(overlay);
  }
  wrap.append(table);

  const dock = document.createElement("div");
  dock.className = "casual-dock gd-dock";
  dock.classList.toggle("is-my-turn", isMyTurn() && !room.paused);
  const dockHead = document.createElement("div");
  dockHead.className = "dock-head";
  const label = document.createElement("div");
  label.className = "my-cards-label";
  const hand = room.your_hand || [];
  label.textContent = room.paused ? "牌局已暂停"
    : isMyTurn()
    ? `轮到你出牌 · 剩 ${hand.length} 张`
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
    action.className = "gd-dock-action";
    action.textContent = `${la2.nickname} ${la2.text}`;
    dockHead.append(action);
  }
  dockHead.append(chatToggle);
  dock.append(dockHead);

  const myCards = document.createElement("div");
  myCards.className = "gd-hand";
  myCards.classList.toggle("many-cards", hand.length > 14);
  if (!hand.length) {
    const waiting = document.createElement("span");
    waiting.className = "my-cards-label";
    waiting.textContent = room.status === "playing" ? "你已出完，等待本局结束…" : "等待下一局发牌…";
    myCards.append(waiting);
  }
  const wildRank = wildRankForMe();
  for (const { card, index } of myHandSorted()) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "gd-hand-card";
    node.dataset.index = index;
    node.setAttribute("aria-pressed", String(selectedIndices.has(index)));
    node.disabled = gdActionLock || room.paused;
    const cardNode = gcardNode(card, {
      wild: isWildCard(card, wildRank),
      picked: selectedIndices.has(index),
    });
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

  const resolved = resolvedSelection();
  if (!room.paused) {
    if (isMyTurn()) {
      dock.append(gdActionBarNode(resolved));
      const remaining = (turnDeadline - Date.now()) / 1000;
      if (remaining > 0) startHallTicker(fill, remaining);
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

registerGame("guandan", {
  compactDesktopChat: true,
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "掼蛋需要正好 4 名玩家：座位间隔的两人自动一队。等待房主开局，中途退出本局作废、筹码原封退回。",
  noNextHint: () => "人数不足 4 人或有人筹码已输光，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderGuandanTable,
  renderReview: (result) => {
    const review = document.createElement("div");
    const title = document.createElement("div");
    title.className = "hall-section-title";
    title.style.marginTop = "0";
    title.textContent = "本局回顾";
    review.append(title, gdResultNode(result));
    return review;
  },
  seatElement: (index) => document.querySelector(`#gameMain .gd-seats .gd-seat:nth-child(${index + 1})`),
});
