/* 段位概览、公开计分规则与逐局收益明细。分数只取服务端结果。 */
import { elements, formatClock, formatCoins, ratingBadge, renderGameView, renderIdentity, send, state } from "./core.js";
import { onMessage, registerView } from "./registry.js";

function signed(value) {
  return `${value > 0 ? "+" : ""}${value}`;
}

const LEADERBOARD_LIMIT = 100;
const REFRESH_DELAY = 150;
let leaderboardSequence = 0;
let leaderboardRefreshTimer = 0;
const expandedStats = new Set();

function rankingsVisible() {
  return Boolean(state.currentUser && !state.myRoom && state.hallPage === "rankings");
}

function cancelLeaderboardRefresh() {
  window.clearTimeout(leaderboardRefreshTimer);
  leaderboardRefreshTimer = 0;
}

function requestLeaderboard(offset = state.ratingLeaderboardOffset) {
  if (!rankingsVisible()) return;
  cancelLeaderboardRefresh();
  offset = Math.max(0, Math.floor(offset / LEADERBOARD_LIMIT) * LEADERBOARD_LIMIT);
  const request = { id: `rating-${++leaderboardSequence}`, offset, username: state.currentUser.username };
  state.ratingLeaderboardOffset = offset;
  state.ratingLeaderboardRequest = request;
  if (!send({ type: "get_rating_leaderboard", offset, request_id: request.id })) {
    state.ratingLeaderboardRequest = null;
  }
  renderGameView();
}

// 不从 core 反向导入功能模块；登录/重连统一走带关联 ID 的请求路径。
document.addEventListener("authstatechange", ({ detail }) => {
  cancelLeaderboardRefresh();
  if (!detail.user) expandedStats.clear();
  if (rankingsVisible()) requestLeaderboard();
});

document.addEventListener("gameviewchange", () => {
  if (rankingsVisible()) return;
  cancelLeaderboardRefresh();
  state.ratingLeaderboardRequest = null;
});

export function ratingResultsNode(results) {
  const box = document.createElement("section");
  box.className = "rating-results";
  const heading = document.createElement("h3");
  heading.textContent = "本局段位结算";
  box.append(heading);
  for (const [username, entry] of Object.entries(results)) {
    const row = document.createElement("div");
    row.className = "rating-result-row";
    const info = document.createElement("div");
    const label = document.createElement("strong");
    label.textContent = state.myRoom?.players.find((p) => p.username === username)?.nickname || username;
    const detail = document.createElement("div");
    detail.className = "rating-detail";
    const roi = ((entry.final / entry.initial - 1) * 100).toFixed(1);
    detail.textContent = `${formatCoins(entry.initial)} → ${formatCoins(entry.final)} · 收益率 ${Number(roi) > 0 ? "+" : ""}${roi}%`;
    info.append(label, detail);
    const outcome = document.createElement("div");
    outcome.className = "rating-outcome";
    const delta = document.createElement("strong");
    delta.className = entry.delta > 0 ? "rating-gain" : entry.delta < 0 ? "rating-loss" : "";
    delta.textContent = `${signed(entry.delta)} 分`;
    outcome.append(delta, ratingBadge(entry.rating));
    row.append(info, outcome);
    box.append(row);
  }
  return box;
}

export function ratingCard() {
  const card = document.createElement("section");
  card.className = "game-card-page rating-card";
  const heading = document.createElement("h2");
  heading.textContent = "我的段位";
  const rating = state.currentUser?.rating;
  heading.append(ratingBadge(rating));
  const rankingButton = document.createElement("button");
  rankingButton.type = "button";
  rankingButton.className = "online-stat rating-ranking-button";
  rankingButton.textContent = "查看段位排行 →";
  rankingButton.addEventListener("click", () => {
    state.hallPage = "rankings";
    state.ratingLeaderboard = null;
    requestLeaderboard();
  });
  heading.append(rankingButton);
  const progress = document.createElement("p");
  progress.className = "rating-detail";
  progress.textContent = rating
    ? `已结算 ${rating.games} 局 · ${rating.next_score == null ? "已达最高段位" : `距${rating.next_tier}还差 ${rating.next_score - rating.score} 分`}`
    : "正在读取段位…";
  const rules = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "积分怎么算？";
  const copy = document.createElement("p");
  copy.textContent = "从 1000 分起步，所有游戏共用段位。每局收益率 =（结余 − 开局筹码）÷ 开局筹码；盈利 × 40，亏损 × 20，加扣系数 2:1，更容易上分。四舍五入到整数，最多 +40 / −20，最低 0 分。例：100 → 120 加 8 分，100 → 80 扣 4 分，保本不变。";
  const tiers = document.createElement("p");
  tiers.textContent = "青铜 0–799 · 白银 800–1199 · 黄金 1200–1599 · 铂金 1600–1999 · 钻石 2000–2399 · 大师 2400+";
  const scope = document.createElement("p");
  scope.textContent = "再来一局以新的开局筹码计分。中途离桌按实际取回筹码立即结算（UNO 原规则原额退出，收益为 0）；未完成的流局、重开及重启退款不计分，已完成的结算保留。转账、竞猜和金币调整不影响段位。";
  rules.append(summary, copy, tiers, scope);
  const history = document.createElement("details");
  const historyTitle = document.createElement("summary");
  historyTitle.textContent = "最近 20 局积分明细";
  history.append(historyTitle);
  if (!state.ratingEntries.length) {
    const empty = document.createElement("p");
    empty.textContent = "暂无积分结算，完成一局后会记录在这里。";
    history.append(empty);
  }
  for (const entry of state.ratingEntries) {
    const row = document.createElement("div");
    row.className = "rating-history-row";
    const title = document.createElement("strong");
    title.textContent = `${entry.game_type === "uno" ? "UNO" : "德州"} · ${entry.room_name} · 第 ${entry.hand_no} 局 · ${signed(entry.delta)} 分`;
    const detail = document.createElement("div");
    detail.className = "rating-detail";
    detail.textContent = `${formatClock(entry.created_at)} · ${formatCoins(entry.initial)} → ${formatCoins(entry.final)} · ${entry.rating.tier} ${entry.rating.score}`;
    row.append(title, detail);
    history.append(row);
  }
  card.append(heading, progress, rules, history);
  return card;
}

onMessage("rating_update", (data) => {
  if (rankingsVisible()) {
    cancelLeaderboardRefresh();
    leaderboardRefreshTimer = window.setTimeout(() => requestLeaderboard(), REFRESH_DELAY);
  }
  if (data.username !== state.currentUser?.username) return;
  state.currentUser.rating = data.rating;
  renderIdentity();
  send({ type: "get_rating_history" });
});

onMessage("rating_history", (data) => {
  if (!state.currentUser) return;
  state.currentUser.rating = data.rating;
  state.ratingEntries = data.entries || [];
  renderIdentity();
  if (!state.myRoom && !state.hallPage) renderGameView();
});

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function statNumber(value, signedValue = false) {
  if (!finiteNumber(value)) return "—";
  return `${signedValue && value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function holdemStatsNode(rawStats, username) {
  const stats = rawStats || {};
  const count = (key) => finiteNumber(stats[key]) ? stats[key] : 0;
  const hands = count("hands");
  const fraction = (key, denominator = hands) => hands && denominator && finiteNumber(stats[key])
    ? `${(stats[key] * 100).toFixed(1)}%` : "—";
  const average = (key) => hands ? statNumber(stats[key], true) : "—";
  const box = document.createElement("section");
  box.className = "rating-holdem-stats";
  box.setAttribute("aria-label", "德扑统计");
  const heading = document.createElement("div");
  heading.className = "rating-stats-heading";
  const title = document.createElement("strong");
  title.textContent = "德扑统计";
  heading.append(title);
  if (hands < 100) {
    const sample = document.createElement("span");
    sample.className = "rating-sample-note";
    sample.textContent = hands ? "小样本 · 不足 100 手" : "暂无数据 · 0 手";
    heading.append(sample);
  }
  const metric = (grid, key, label, value, sample) => {
    const item = document.createElement("div");
    item.className = "rating-metric";
    item.dataset.metric = key;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    const number = document.createElement("strong");
    number.className = "rating-metric-value";
    number.textContent = value;
    const explanation = document.createElement("small");
    explanation.className = "rating-detail";
    explanation.textContent = sample;
    detail.append(number, explanation);
    item.append(term, detail);
    grid.append(item);
  };
  const primary = document.createElement("dl");
  primary.className = "rating-stats-grid";
  metric(primary, "hands", "德扑手数", `${hands} 手`, "已结算的德扑手数（含提前离桌结算）");
  metric(primary, "win_rate", "盈利胜率", fraction("win_rate"), `净盈利 ${count("wins")} / ${hands} 手`);
  metric(primary, "fold_rate", "弃牌率", fraction("fold_rate"), `弃牌 ${count("folds")} / ${hands} 手（含超时、离桌）`);
  metric(primary, "score_per_hand", "得分期望（分/手）", average("score_per_hand"), `累计 ${statNumber(stats.score_delta)} 分 ÷ ${hands} 手，非理论 EV`);
  metric(primary, "bb_per_100", "BB/100", average("bb_per_100"), `净收益 ${statNumber(stats.net_bb)} BB ÷ ${hands} 手 × 100`);
  const more = document.createElement("details");
  more.className = "rating-stats-more";
  more.open = expandedStats.has(username);
  more.addEventListener("toggle", () => {
    if (more.open) expandedStats.add(username);
    else expandedStats.delete(username);
  });
  const summary = document.createElement("summary");
  summary.textContent = "更多德扑指标与口径";
  const extra = document.createElement("dl");
  extra.className = "rating-stats-grid rating-stats-extra";
  metric(extra, "net_profit", "净收益（筹码）", average("net_profit"), `累计 ${hands} 手实际筹码净收益`);
  metric(extra, "profit_per_hand", "手均净收益（筹码/手）", average("profit_per_hand"), `净收益 ${statNumber(stats.net_profit)} ÷ ${hands} 手`);
  metric(extra, "vpip", "VPIP · 主动入池率", fraction("vpip"), `翻前主动入池 ${count("vpip_hands")} / ${hands} 手，不含仅付盲注`);
  metric(extra, "pfr", "PFR · 翻前加注率", fraction("pfr"), `翻前加注 ${count("pfr_hands")} / ${hands} 手`);
  metric(extra, "flop_rate", "看翻牌率", fraction("flop_rate"), `实际看到翻牌 ${count("flop_hands")} / ${hands} 手`);
  metric(extra, "wtsd", "WTSD · 摊牌率", fraction("wtsd", count("flop_hands")), `摊牌 ${count("showdown_hands")} / 看翻牌 ${count("flop_hands")} 手`);
  metric(extra, "showdown_win_rate", "摊牌盈利率", fraction("showdown_win_rate", count("showdown_hands")), `摊牌净盈利 ${count("showdown_wins")} / 摊牌 ${count("showdown_hands")} 手`);
  const af = hands && count("call_actions") > 0 ? statNumber(stats.af)
    : hands && count("aggressive_actions") > 0 && stats.af_no_calls ? "∞（无跟注）" : "—";
  metric(extra, "af", "AF · 翻牌后激进因子", af, `翻牌后下注/加注 ${count("aggressive_actions")} 次 ÷ 跟注 ${count("call_actions")} 次，不含过牌、弃牌`);
  const foldNote = document.createElement("p");
  foldNote.className = "rating-detail";
  foldNote.textContent = `弃牌明细：主动 ${count("manual_folds")} 手 · 超时 ${count("timeout_folds")} 手 · 离桌 ${count("leave_folds")} 手。盈利以本手净收益 > 0 计，不等同于赢得底池；BB 按各手大盲归一化。分母为 0 时显示 —；有进攻但无跟注时 AF 显示 ∞。`;
  more.append(summary, extra, foldNote);
  box.append(heading, primary, more);
  return box;
}

function leaderboardPager(total, position) {
  const offset = state.ratingLeaderboardOffset;
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
  heading.textContent = "段位排行榜";
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "online-stat";
  refresh.textContent = "刷新排行";
  refresh.addEventListener("click", () => requestLeaderboard());
  toolbar.append(back, heading, refresh);

  const board = document.createElement("section");
  board.className = "game-card-page rating-leaderboard";
  board.setAttribute("aria-label", "段位排行");
  const data = state.ratingLeaderboard;
  board.setAttribute("aria-busy", String(Boolean(state.ratingLeaderboardRequest)));
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
    own.append(rank, ratingBadge(data.self.rating), holdemStatsNode(data.self.holdem_stats, data.self.username));
    board.append(own);
  }
  const note = document.createElement("p");
  note.className = "rating-detail";
  const offset = state.ratingLeaderboardOffset;
  const pageReady = data.offset === offset;
  const entries = pageReady ? data.entries : [];
  const first = data.total ? offset + 1 : 0;
  const last = Math.min(offset + LEADERBOARD_LIMIT, data.total);
  note.textContent = `共 ${data.total} 位玩家 · 第 ${first}–${last} 位 · 按段位分排序，同分并列（如 1、1、3），跨页名次为全站名次。所有账号均参与，所有游戏共用积分。`;
  const scope = document.createElement("p");
  scope.className = "rating-detail rating-stats-scope";
  const since = finiteNumber(data.stats_since) && data.stats_since > 0
    ? new Date(data.stats_since * 1000).toLocaleString("zh-CN", { hour12: false }) : "上线启用时";
  scope.textContent = `德扑统计自 ${since} 起记录，不追溯历史数据，仅含德扑，对登录用户公开，不改变段位排序。得分期望是实际段位得分的历史均值，非理论 EV。不足 100 手为小样本，请谨慎解读。`;
  board.append(note, scope, leaderboardPager(data.total, "顶部"));
  if (state.ratingLeaderboardRequest || !pageReady) {
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
    const games = document.createElement("div");
    games.className = "rating-detail";
    games.textContent = `已结算 ${entry.rating.games} 局`;
    outcome.append(ratingBadge(entry.rating), games);
    row.append(place, player, outcome, holdemStatsNode(entry.holdem_stats, entry.username));
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

  const legend = document.createElement("section");
  legend.className = "game-card-page rating-tier-guide";
  const title = document.createElement("h2");
  title.textContent = "段位标志";
  const tiers = document.createElement("div");
  tiers.className = "rating-tier-grid";
  for (const tier of data.tiers || []) {
    const item = document.createElement("div");
    item.className = "rating-tier-item";
    const badge = ratingBadge(tier);
    badge.textContent = tier.tier;
    badge.title = `${tier.tier} · ${tier.floor} 分起`;
    const range = document.createElement("div");
    range.className = "rating-detail";
    range.textContent = tier.next_score == null ? `${tier.floor}+` : `${tier.floor}–${tier.next_score - 1}`;
    item.append(badge, range);
    tiers.append(item);
  }
  legend.append(title, tiers);
  elements.gameMain.replaceChildren(toolbar, legend, board);
}

onMessage("rating_leaderboard", (data) => {
  const request = state.ratingLeaderboardRequest;
  // 服务端可能将越界 offset 截到末页；关联 ID 决定响应归属，不能仅比页码。
  if (!rankingsVisible() || !request || request.username !== state.currentUser.username
      || data.request_id !== request.id) return;
  if (!Number.isInteger(data.total) || data.total < 0 || !Array.isArray(data.entries)) return;
  const lastOffset = Math.floor(Math.max(0, data.total - 1) / LEADERBOARD_LIMIT) * LEADERBOARD_LIMIT;
  if (data.offset !== Math.min(request.offset, lastOffset)) return;
  state.ratingLeaderboard = data;
  state.ratingLeaderboardOffset = data.offset;
  state.ratingLeaderboardRequest = null;
  renderGameView();
});

registerView("rankings", renderRankings);
