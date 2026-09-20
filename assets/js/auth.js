/* 登录注册与财务（金币明细、转账）。 */

import { AUTH_TOKEN_KEY, elements, formatClock, formatCoins, rememberProfile, renderGameView, renderIdentity, rewardsPanel, send, setSignedIn, setUserMenu, state, transferSelect, updateCoinChip } from "./core.js";
import { alertDialog } from "./dialog.js";
import { onMessage } from "./registry.js";

const coinKinds = {
  register: "注册奖励",
  admin: "管理员调整",
  transfer_out: "转账转出",
  transfer_in: "转账转入",
  bet_stake: "竞猜投注",
  bet_win: "竞猜奖励",
  bet_refund: "竞猜退款",
  game_buyin: "游戏厅买入",
  game_settle: "游戏厅结算",
  bet_result: "竞猜结果",
  game_result: "游戏结算",
  lottery_win: "签到抽奖",
  holdem_daily_reward: "每日德扑流水奖励",
};

export function setAuthMode(mode) {
  state.authMode = mode;
  const isLogin = mode === "login";
  elements.authTitle.textContent = isLogin ? "登录游戏厅" : "注册账号";
  elements.authSubmit.textContent = isLogin ? "登录" : "注册";
  elements.password.autocomplete = isLogin ? "current-password" : "new-password";
  elements.inviteCode.hidden = isLogin;
  const toggle = document.createElement("button");
  toggle.className = "auth-link";
  toggle.type = "button";
  toggle.textContent = isLogin ? "注册" : "返回登录";
  elements.authSwitch.replaceChildren(
    document.createTextNode(isLogin ? "还没有账号？ " : "已经有账号？ "), toggle,
  );
}

export function openLogin() {
  setAuthMode("login");
  elements.authModal.style.display = "flex";
  elements.username.focus();
}

export function submitAuth() {
  const username = elements.username.value.trim();
  const password = elements.password.value;
  if (!username || !password) {
    void alertDialog("请输入用户名和密码");
    return;
  }
  send({
    type: state.authMode,
    username,
    password,
    ...(state.authMode === "register" ? { invite_code: elements.inviteCode.value.trim() } : {}),
  });
}


/* =========================================================
   财务管理
========================================================= */

export function switchFinanceTab(tab) {
  const detail = tab === "detail";
  elements.financeTabDetail.classList.toggle("active", detail);
  elements.financeTabTransfer.classList.toggle("active", !detail);
  elements.financeDetailPanel.hidden = !detail;
  elements.financeTransferPanel.hidden = detail;
  elements.transferFeedback.textContent = "";
  if (!detail) TransferSelect.requestUsers();
}

export function openFinance() {
  if (!state.currentUser) return;
  setUserMenu(false);
  switchFinanceTab("detail");
  elements.financeBalance.textContent = formatCoins(state.currentUser.coins);
  elements.financeList.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "invite-empty";
  loading.textContent = "加载中…";
  elements.financeList.append(loading);
  elements.financeModal.style.display = "flex";
  send({ type: "get_finance" });
}

function renderFinance(data) {
  if (!state.currentUser) return;
  state.currentUser.coins = data.coins;
  updateCoinChip();
  elements.financeBalance.textContent = formatCoins(data.coins);
  const list = elements.financeList;
  list.replaceChildren();
  const items = data.transactions || [];
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "invite-empty";
    empty.textContent = "暂无金币明细";
    list.append(empty);
    return;
  }
  for (const tx of items) {
    const row = document.createElement("div");
    row.className = "finance-row";
    const info = document.createElement("div");
    info.className = "finance-row-info";
    const kind = document.createElement("div");
    kind.className = "finance-row-kind";
    kind.textContent = coinKinds[tx.kind] || tx.kind;
    if (tx.kind === "bet_stake" || tx.kind === "game_buyin") {
      const pending = document.createElement("span");
      pending.className = "finance-row-pending";
      pending.textContent = "未结算";
      kind.append(pending);
    }
    const detail = document.createElement("div");
    detail.className = "finance-row-detail";
    detail.textContent = [tx.detail, formatClock(tx.created_at)].filter(Boolean).join(" · ");
    info.append(kind, detail);
    const side = document.createElement("div");
    const amount = document.createElement("div");
    amount.className = `finance-row-amount ${tx.amount >= 0 ? "plus" : "minus"}`;
    amount.textContent = `${tx.amount >= 0 ? "+" : ""}${formatCoins(tx.amount)}`;
    const balance = document.createElement("div");
    balance.className = "finance-row-balance";
    balance.textContent = `余额 ${formatCoins(tx.balance)}`;
    side.append(amount, balance);
    row.append(info, side);
    list.append(row);
  }
}

export function submitTransfer() {
  if (!state.currentUser) return;
  const to = elements.transferTo.value.trim();
  const amount = Number(elements.transferAmount.value);
  if (!to) {
    void alertDialog("请选择转账对象");
    return;
  }
  if (to === state.currentUser.username) {
    void alertDialog("不能转账给自己");
    return;
  }
  if (!Number.isFinite(amount) || amount <= 0) {
    void alertDialog("请输入正确的转账金额");
    return;
  }
  elements.transferFeedback.textContent = "";
  send({ type: "transfer_coins", to, amount: Math.round(amount * 100) / 100 });
}


/* =========================================================
   视图骨架
========================================================= */

onMessage("register_success", () => {
  void alertDialog("注册成功，请登录");
  elements.password.value = "";
  setAuthMode("login");
});

onMessage("login_success", (data) => {
  localStorage.setItem(AUTH_TOKEN_KEY, data.token);
  elements.authModal.style.display = "none";
  setSignedIn({
    username: data.username, nickname: data.nickname, avatar: data.avatar,
    coins: data.coins, rating: data.rating,
  });
});

onMessage("resume_success", (data) => {
  setSignedIn({
    username: data.username, nickname: data.nickname, avatar: data.avatar,
    coins: data.coins, rating: data.rating,
  });
});

onMessage("profile", (data) => {
  rememberProfile(data);
  if (state.currentUser && data.username === state.currentUser.username) {
    state.currentUser.nickname = data.nickname || "";
    state.currentUser.avatar = data.avatar || "";
    state.currentUser.rating = data.rating;
    renderIdentity();
  }
  if (state.myRoom?.result) renderGameView();
});

onMessage("profile_error", (data) => { void alertDialog(data.message); });

onMessage("auth_expired", () => {
  localStorage.removeItem(AUTH_TOKEN_KEY);
  setSignedIn(null);
});

onMessage("auth_error", (data) => { void alertDialog(data.message); });

onMessage("finance", (data) => { renderFinance(data); });
for (const type of ["daily_rewards", "checkin_result", "lottery_result", "holdem_reward_result", "rewards_error"]) {
  onMessage(type, (data) => rewardsPanel.handle(data));
}

onMessage("transfer_success", (data) => {
  if (state.currentUser) state.currentUser.coins = data.coins;
  updateCoinChip();
  elements.transferAmount.value = "";
  transferSelect()?.reset();
  elements.transferFeedback.textContent = "转账成功";
  send({ type: "get_finance" });
});

onMessage("coins_error", (data) => { void alertDialog(data.message); });

onMessage("coins", (data) => {
  if (state.currentUser && data.username === state.currentUser.username) {
    state.currentUser.coins = data.coins;
    updateCoinChip();
  }
});

onMessage("user_list", (data) => { transferSelect()?.setUsers(data.users || []); });
