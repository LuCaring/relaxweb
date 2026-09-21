/* Hand and match settlement views. */

import { displayNameOf, elements, formatCoins, leaveRoom, ratingBadge, selfUsername, send, spectating, state } from "./core.js";
import { ratingResultsNode } from "./rating.js";
import { fillBlindOptions } from "./game-config.js";
import { gameView } from "./registry.js";
import { chatOpenButton } from "./room-chat.js";

/* =========================================================
   每手结算弹层（二级菜单）
   一手结束后弹出：公共牌、每人的牌型与筹码加减，底部一个「继续下一手」。
   全员点过、或 10 秒倒计时到点，服务端直接开下一手，hand_ready 消失即自动关闭。
========================================================= */

const HAND_RESULT_ID = "handResult";
let handResultTicker = 0;
let handResultDeadline = 0;
let handResultHandNo = 0;
let handResultPressed = false;

export function closeHandResultOverlay() {
  if (handResultTicker) {
    window.clearInterval(handResultTicker);
    handResultTicker = 0;
  }
  document.getElementById(HAND_RESULT_ID)?.remove();
  handResultDeadline = 0;
  handResultHandNo = 0;
  handResultPressed = false;
}

function syncHandResultOverlay() {
  const info = state.myRoom?.hand_ready;
  const overlay = document.getElementById(HAND_RESULT_ID);
  if (!overlay || !info) return;
  const ready = new Set(info.ready || []);
  const me = selfUsername();
  const total = info.total || state.myRoom.players.length;
  const waiting = state.myRoom.players
    .filter((p) => !ready.has(p.username))
    .map((p) => p.nickname);
  const progress = overlay.querySelector(".hand-result-progress");
  if (progress) {
    progress.textContent = waiting.length
      ? `已准备 ${ready.size}/${total} · 等待 ${waiting.join("、")}`
      : "全员已准备，正在开始下一手…";
  }
  const button = overlay.querySelector(".hand-continue-button");
  if (!button) return;
  if (handResultPressed || (me && ready.has(me))) {
    button.disabled = true;
    button.textContent = "已准备，等待其他人…";
    return;
  }
  const left = Math.max(0, Math.ceil((handResultDeadline - Date.now()) / 1000));
  button.disabled = false;
  button.textContent = left > 0 ? `继续下一手（${left}）` : "开始下一手…";
}

export function renderHandResultOverlay() {
  const info = state.myRoom?.hand_ready;
  const result = state.myRoom?.result;
  if (!info || !result) return;
  if (document.getElementById(HAND_RESULT_ID) && handResultHandNo === info.hand_no) {
    syncHandResultOverlay();
    return;
  }
  closeHandResultOverlay();
  handResultHandNo = info.hand_no;
  handResultDeadline = Date.now() + (info.left || 0) * 1000;

  const overlay = document.createElement("div");
  overlay.className = "hand-result";
  overlay.id = HAND_RESULT_ID;
  const card = document.createElement("div");
  card.className = "hand-result-card";

  const head = document.createElement("div");
  head.className = "hand-result-head";
  const title = document.createElement("div");
  title.className = "hand-result-title";
  title.textContent = `第 ${info.hand_no} 手结算`;
  const pot = document.createElement("div");
  pot.className = "hand-result-pot";
  pot.textContent = `底池 ${formatCoins(result.pot || 0)}`;
  head.append(title, pot);
  card.append(head);

  const bodyNode = document.createElement("div");
  bodyNode.className = "hand-result-body";
  const game = gameView(state.myRoom.game_type);
  const review = game?.renderHandResult?.(result) || game?.renderReview?.(result);
  if (review) bodyNode.append(review);
  card.append(bodyNode);

  const foot = document.createElement("div");
  foot.className = "hand-result-foot";
  const progress = document.createElement("div");
  progress.className = "hand-result-progress";
  foot.append(progress);
  // 观战者无需准备；弹层也必须提供随时退出的入口。
  if (!spectating()) {
    const button = document.createElement("button");
    button.className = "login-submit hand-continue-button";
    button.type = "button";
    button.textContent = "继续下一手";
    button.addEventListener("click", () => {
      handResultPressed = true;
      button.disabled = true;
      button.textContent = "已准备，等待其他人…";
      send({ type: "hand_continue" });
    });
    foot.append(button);
  } else {
    const exit = document.createElement("button");
    exit.className = "login-submit";
    exit.type = "button";
    exit.textContent = "退出观战";
    exit.addEventListener("click", leaveRoom);
    foot.append(exit);
  }
  card.append(foot);

  overlay.append(card);
  document.body.append(overlay);
  syncHandResultOverlay();
  handResultTicker = window.setInterval(syncHandResultOverlay, 200);
}



/* 对局结束（有人筹码不足盲注）：展示每人的资产与段位分变化，投票再来一局或解散。 */
function renderMatchSettlement(body) {
  const room = state.myRoom;
  const data = room.match_result;
  const game = gameView(room.game_type);

  const heading = document.createElement("div");
  heading.className = "hall-page-title";
  heading.textContent = `${room.name} · 本局结束`;
  body.append(heading);

  const info = document.createElement("div");
  info.className = "game-hint";
  info.style.marginTop = "0";
  info.textContent = `${data.reason} · 共打了 ${data.hand_no} 手 · 盲注 ${formatCoins(data.blind)} · 买入 ${formatCoins(data.buy_in)}`;
  body.append(info);

  const card = document.createElement("div");
  card.className = "game-card-page";
  const title = document.createElement("div");
  title.className = "hall-section-title";
  title.style.marginTop = "0";
  title.textContent = `资产与段位分（第 ${data.match_no} 局）`;
  card.append(title);

  const rows = document.createElement("div");
  rows.className = "match-rows";
  for (const item of data.players || []) {
    const landed = Math.round(Number(item.net || 0) * 100) / 100;
    const row = document.createElement("div");
    row.className = "match-row";
    if (landed > 0) row.classList.add("win");
    else if (landed < 0) row.classList.add("lose");
    if (state.currentUser && item.username === selfUsername()) row.classList.add("me");

    const who = document.createElement("div");
    who.className = "match-who";
    const nameText = document.createElement("span");
    nameText.textContent = item.nickname || displayNameOf(item.username);
    who.append(nameText, ratingBadge(item.rating));

    const lastHand = document.createElement("div");
    lastHand.className = "match-hand";
    lastHand.textContent = item.folded
      ? "最后一手已弃牌"
      : (item.hand_name ? `最后一手：${item.hand_name}` : "最后一手未摊牌");

    const assets = document.createElement("div");
    assets.className = "match-assets";
    const stack = document.createElement("span");
    stack.className = "match-stack";
    stack.textContent = `${formatCoins(item.stack)} 资产`;
    assets.append(stack);
    if (Number(item.paid) > Number(data.buy_in)) {
      const paid = document.createElement("span");
      paid.className = "match-paid";
      paid.textContent = `累计买入 ${formatCoins(item.paid)}`;
      assets.append(paid);
    }

    const net = document.createElement("div");
    net.className = `match-net ${landed > 0 ? "win" : landed < 0 ? "lose" : "flat"}`;
    net.textContent = `${landed > 0 ? "+" : ""}${formatCoins(landed)}`;

    const delta = document.createElement("div");
    const ratingDelta = Number(item.rating_delta || 0);
    delta.className = `match-rating ${ratingDelta > 0 ? "up" : ratingDelta < 0 ? "down" : "flat"}`;
    delta.textContent = `段位 ${ratingDelta > 0 ? "+" : ""}${ratingDelta}`;

    row.append(who, lastHand, assets, net, delta);
    rows.append(row);
  }
  card.append(rows);
  body.append(card);

  const votes = data.votes || {};
  const total = data.total || room.players.length;
  const nextCount = Object.values(votes).filter((v) => v.choice === "next").length;
  const dissolveCount = Object.values(votes).filter((v) => v.choice === "dissolve").length;
  const myChoice = votes[selfUsername()]?.choice;
  const canNext = Boolean(data.can_next);

  const voteCard = document.createElement("div");
  voteCard.className = "game-card-page";
  const voteTitle = document.createElement("div");
  voteTitle.className = "hall-section-title";
  voteTitle.style.marginTop = "0";
  voteTitle.textContent = "接下来做什么？（过半数生效）";
  voteCard.append(voteTitle);

  const progress = document.createElement("div");
  progress.className = "settle-progress";
  progress.textContent = `已投 ${nextCount + dissolveCount} / ${total} 票 · 再来一局 ${nextCount} 票 · 解散 ${dissolveCount} 票`;
  voteCard.append(progress);

  if (!spectating()) {
    const blindRow = document.createElement("div");
    blindRow.className = "settle-blind-row";
    const blindLabel = document.createElement("span");
    blindLabel.className = "settle-blind-label";
    blindLabel.textContent = game?.blindLabel || "下一局盲注";
    const blind = document.createElement("select");
    blind.className = "login-input";
    blind.id = "settleBlindSelect";
    fillBlindOptions(blind, room.game_type, data.blind || room.blind);
    blind.disabled = !canNext;
    blindRow.append(blindLabel, blind);
    voteCard.append(blindRow);

    const btnRow = document.createElement("div");
    btnRow.className = "game-btn-row";
    const again = document.createElement("button");
    again.className = "login-submit";
    again.type = "button";
    again.textContent = myChoice === "next" ? "已投：再来一局" : "结算并再来一局";
    again.disabled = !canNext;
    if (!canNext) again.title = "人数不足两人，无法再来一局";
    again.addEventListener("click", () => {
      send({ type: "settle_vote", choice: "next", blind: Number(blind.value) });
    });
    btnRow.append(again);

    const dissolve = document.createElement("button");
    dissolve.className = "login-submit danger";
    dissolve.type = "button";
    dissolve.textContent = myChoice === "dissolve" ? "已投：结算并解散房间" : "结算并解散房间";
    dissolve.addEventListener("click", () => {
      send({ type: "settle_vote", choice: "dissolve" });
    });
    btnRow.append(dissolve);
    voteCard.append(btnRow);
  }

  const hint = document.createElement("div");
  hint.className = "game-hint";
  hint.textContent = spectating()
    ? "观战者无需投票，等待玩家决定房间去向。"
    : canNext
      ? (game?.noNextHint?.() || "过半数投「再来一局」即按买入额重新买入开新的一局。")
      : "人数不足，只能结算并解散房间。";
  voteCard.append(hint);
  body.append(voteCard);

  body.append(chatOpenButton());
}

export function renderSettlementView() {
  const body = elements.gameMain;
  body.replaceChildren();
  if (state.myRoom.match_result) {
    renderMatchSettlement(body);
    return;
  }
  const game = gameView(state.myRoom.game_type);

  const heading = document.createElement("div");
  heading.className = "hall-page-title";
  heading.textContent = `${state.myRoom.name} · 本局结算`;
  body.append(heading);

  const resultCard = document.createElement("div");
  resultCard.className = "game-card-page";
  if (state.myRoom.result && game) {
    resultCard.append(game.renderReview(state.myRoom.result));
    if (state.myRoom.result.ratings) resultCard.append(ratingResultsNode(state.myRoom.result.ratings));
  } else {
    const note = document.createElement("div");
    note.className = "game-hint";
    note.style.marginTop = "0";
    note.textContent = "牌局已结束。";
    resultCard.append(note);
  }
  body.append(resultCard);

  const settlement = state.myRoom.settlement;
  const votes = settlement?.votes || {};
  const total = settlement?.total || state.myRoom.players.length;
  const nextCount = Object.values(votes).filter((v) => v.choice === "next").length;
  const dissolveCount = Object.values(votes).filter((v) => v.choice === "dissolve").length;
  const myChoice = votes[selfUsername()]?.choice;

  const voteCard = document.createElement("div");
  voteCard.className = "game-card-page";
  const voteTitle = document.createElement("div");
  voteTitle.className = "hall-section-title";
  voteTitle.style.marginTop = "0";
  voteTitle.textContent = "接下来做什么？（过半数生效）";
  voteCard.append(voteTitle);

  const progress = document.createElement("div");
  progress.className = "settle-progress";
  progress.textContent = `已投 ${nextCount + dissolveCount} / ${total} 票 · 再来一局 ${nextCount} 票 · 解散 ${dissolveCount} 票`;
  voteCard.append(progress);

  const canNext = Boolean(settlement?.can_next);
  if (!spectating()) {
    const blindRow = document.createElement("div");
    blindRow.className = "settle-blind-row";
    const blindLabel = document.createElement("span");
    blindLabel.className = "settle-blind-label";
    blindLabel.textContent = game?.blindLabel || "下一局盲注";
    const blind = document.createElement("select");
    blind.className = "login-input";
    blind.id = "settleBlindSelect";
    fillBlindOptions(blind, state.myRoom.game_type, settlement?.blind || state.myRoom.blind);
    blind.disabled = !canNext;
    blindRow.append(blindLabel, blind);
    voteCard.append(blindRow);

    const btnRow = document.createElement("div");
    btnRow.className = "game-btn-row";
    const again = document.createElement("button");
    again.className = "login-submit";
    again.type = "button";
    again.textContent = myChoice === "next" ? "已投：再来一局" : "结算并再来一局";
    again.disabled = !canNext;
    if (!canNext) again.title = "有人筹码不足下一局盲注";
    again.addEventListener("click", () => {
      send({ type: "settle_vote", choice: "next", blind: Number(blind.value) });
    });
    btnRow.append(again);

    const dissolve = document.createElement("button");
    dissolve.className = "login-submit danger";
    dissolve.type = "button";
    dissolve.textContent = myChoice === "dissolve" ? "已投：结算并解散房间" : "结算并解散房间";
    dissolve.addEventListener("click", () => {
      send({ type: "settle_vote", choice: "dissolve" });
    });
    btnRow.append(dissolve);
    voteCard.append(btnRow);
  }

  const hint = document.createElement("div");
  hint.className = "game-hint";
  hint.textContent = spectating()
    ? "观战者无需投票，等待玩家决定房间去向。"
    : canNext
      ? "过半数玩家投「再来一局」即开下一局；过半数投「解散」则按当前筹码退还所有人并关闭房间。"
      : (game?.noNextHint?.() || "过半数投「解散」后房间将按当前筹码退还所有人。");
  voteCard.append(hint);
  body.append(voteCard);

  body.append(chatOpenButton());
}
