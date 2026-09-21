/* 所有账号的钱包金币排行；复用段位榜的布局、分页和名次样式。 */
import { elements, formatCoins, renderGameView, send, state } from "./core.js";
import { onMessage, registerView } from "./registry.js";

const LEADERBOARD_LIMIT = 100;
let leaderboardSequence = 0;

function rankingsVisible() {
  return Boolean(state.currentUser && !state.myRoom && state.hallPage === "asset-rankings");
}

function requestLeaderboard(offset = state.assetLeaderboardOffset) {
  if (!rankingsVisible()) return;
  offset = Math.max(0, Math.floor(offset / LEADERBOARD_LIMIT) * LEADERBOARD_LIMIT);
  const request = { id: `asset-${++leaderboardSequence}`, offset, username: state.currentUser.username };
  state.assetLeaderboardOffset = offset;
  state.assetLeaderboardRequest = request;
  if (!send({ type: "get_asset_leaderboard", offset, request_id: request.id })) {
    state.assetLeaderboardRequest = null;
  }
  renderGameView();
}

// 不从 core 反向导入功能模块；登录/重连统一走带关联 ID 的请求路径。
document.addEventListener("authstatechange", () => {
  if (rankingsVisible()) requestLeaderboard();
});

document.addEventListener("gameviewchange", () => {
  if (rankingsVisible()) return;
  state.assetLeaderboardRequest = null;
});

export function assetCard() {
  const card = document.createElement("section");
  card.className = "game-card-page rating-card asset-card";
  const heading = document.createElement("h2");
  heading.textContent = "资产排行榜";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "online-stat rating-ranking-button";
  open.textContent = "查看资产排行 →";
  open.addEventListener("click", () => {
    state.hallPage = "asset-rankings";
    state.assetLeaderboard = null;
    requestLeaderboard();
  });
  heading.append(open);
  const description = document.createElement("p");
  description.className = "rating-detail";
  description.textContent = "查看所有玩家当前持有的金币数量与我的名次。";
  card.append(heading, description);
  return card;
}

function coinAmount(coins) {
  const amount = document.createElement("strong");
  amount.className = "asset-coin-amount";
  amount.textContent = `${formatCoins(coins)} 金币`;
  return amount;
}

function leaderboardPager(total, position) {
  const offset = state.assetLeaderboardOffset;
  const nav = document.createElement("nav");
  nav.className = "rating-pagination";
  nav.setAttribute("aria-label", `排行榜分页（${position}）`);
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "online-stat";
  previous.textContent = "← 上一页";
  previous.disabled = offset === 0;
  previous.addEventListener("click", () => requestLeaderboard(offset - LEADERBOARD_LIMIT));
  const page = document.createElement("span");
  page.className = "rating-page-number rating-detail";
  page.textContent = `第 ${offset / LEADERBOARD_LIMIT + 1} / ${Math.max(1, Math.ceil(total / LEADERBOARD_LIMIT))} 页 · 每页 ${LEADERBOARD_LIMIT} 位`;
  const next = document.createElement("button");
  next.type = "button";
  next.className = "online-stat";
  next.textContent = "下一页 →";
  next.disabled = offset + LEADERBOARD_LIMIT >= total;
  next.addEventListener("click", () => requestLeaderboard(offset + LEADERBOARD_LIMIT));
  nav.append(previous, page, next);
  return nav;
}

function renderRankings() {
  const toolbar = document.createElement("div");
  toolbar.className = "rooms-toolbar rating-toolbar";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "online-stat hall-back";
  back.textContent = "← 游戏厅";
  back.addEventListener("click", () => {
    state.hallPage = null;
    renderGameView();
  });
  const heading = document.createElement("h1");
  heading.className = "hall-page-title";
  heading.textContent = "资产排行榜";
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "online-stat";
  refresh.textContent = "刷新排行";
  refresh.addEventListener("click", () => requestLeaderboard());
  toolbar.append(back, heading, refresh);

  const board = document.createElement("section");
  board.className = "game-card-page asset-leaderboard";
  board.setAttribute("aria-label", "资产排行");
  const data = state.assetLeaderboard;
  board.setAttribute("aria-busy", String(Boolean(state.assetLeaderboardRequest)));
  if (!data) {
    const loading = document.createElement("p");
    loading.className = "rating-detail";
    loading.setAttribute("role", "status");
    loading.textContent = "正在读取排行，未显示时可点击刷新。";
    board.append(loading);
    elements.gameMain.replaceChildren(toolbar, board);
    return;
  }

  if (data.self) {
    const own = document.createElement("div");
    own.className = "rating-own-rank";
    const rank = document.createElement("strong");
    rank.textContent = `我的名次 · 第 ${data.self.rank} 名`;
    own.append(rank, coinAmount(data.self.coins));
    board.append(own);
  }
  const note = document.createElement("p");
  note.className = "rating-detail";
  const offset = state.assetLeaderboardOffset;
  const pageReady = data.offset === offset;
  const entries = pageReady ? data.entries : [];
  const first = data.total ? offset + 1 : 0;
  const last = Math.min(offset + LEADERBOARD_LIMIT, data.total);
  note.textContent = `共 ${data.total} 位玩家 · 第 ${first}–${last} 位 · 按当前金币余额从高到低排序，同额并列（如 1、1、3）。所有账号均参与，跨页保持全站名次。仅统计钱包余额，不含牌局筹码及未结算竞猜；点击刷新查看最新排行。`;
  board.append(note, leaderboardPager(data.total, "顶部"));
  if (state.assetLeaderboardRequest || !pageReady) {
    const loading = document.createElement("p");
    loading.className = "rating-detail";
    loading.setAttribute("role", "status");
    loading.textContent = pageReady ? "正在刷新当前页…" : "正在读取当前页，未显示时可点击刷新。";
    board.append(loading);
  }

  const list = document.createElement("ol");
  list.className = "rating-ranking-list";
  list.start = offset + 1;
  for (const entry of entries) {
    const row = document.createElement("li");
    row.className = "rating-ranking-row";
    row.value = entry.rank;
    if (entry.username === state.currentUser.username) row.classList.add("is-self");
    const place = document.createElement("strong");
    place.className = "rating-place";
    place.textContent = String(entry.rank);
    place.setAttribute("aria-label", `第 ${entry.rank} 名`);
    if (entry.rank <= 3) place.dataset.podium = String(entry.rank);
    const player = document.createElement("div");
    player.className = "rating-player";
    const name = document.createElement("strong");
    name.textContent = entry.nickname || entry.username;
    const username = document.createElement("div");
    username.className = "rating-detail";
    username.textContent = `@${entry.username}${entry.username === state.currentUser.username ? " · 我" : ""}`;
    player.append(name, username);
    const outcome = document.createElement("div");
    outcome.className = "rating-ranking-score";
    outcome.append(coinAmount(entry.coins));
    row.append(place, player, outcome);
    list.append(row);
  }
  board.append(list);
  if (pageReady && !entries.length) {
    const empty = document.createElement("p");
    empty.className = "rating-detail";
    empty.textContent = "暂无排行数据。";
    board.append(empty);
  }

  if (entries.length) board.append(leaderboardPager(data.total, "底部"));

  elements.gameMain.replaceChildren(toolbar, board);
}

onMessage("asset_leaderboard", (data) => {
  const request = state.assetLeaderboardRequest;
  // 服务端可能将越界 offset 截到末页；关联 ID 决定响应归属，不能仅比页码。
  if (!rankingsVisible() || !request || request.username !== state.currentUser.username
      || data.request_id !== request.id) return;
  if (!Number.isInteger(data.total) || data.total < 0 || !Array.isArray(data.entries)) return;
  const lastOffset = Math.floor(Math.max(0, data.total - 1) / LEADERBOARD_LIMIT) * LEADERBOARD_LIMIT;
  if (data.offset !== Math.min(request.offset, lastOffset)) return;
  state.assetLeaderboard = data;
  state.assetLeaderboardOffset = data.offset;
  state.assetLeaderboardRequest = null;
  renderGameView();
});

registerView("asset-rankings", renderRankings);
