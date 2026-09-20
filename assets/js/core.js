/* 游戏厅前端核心：元素引用、共享状态、WebSocket 与消息分发、通用格式化、页面骨架切换。 */

import { dispatchMessage, onMessage, renderView } from "./registry.js";
import { alertDialog, confirmDialog } from "./dialog.js";

export const $ = (id) => document.getElementById(id);

export const elements = {
  authModal: $("loginModal"), authSubmit: $("authSubmit"),
  authSwitch: $("authSwitch"), authTitle: $("authTitle"),
  coinBalance: $("coinBalance"), coinChip: $("coinChip"),
  dropdownName: $("dropdownName"),
  financeBalance: $("financeBalance"), financeButton: $("financeButton"),
  financeClose: $("financeClose"), financeDetailPanel: $("financeDetailPanel"),
  financeList: $("financeList"), financeModal: $("financeModal"),
  financeTabDetail: $("financeTabDetail"), financeTabTransfer: $("financeTabTransfer"),
  financeTransferPanel: $("financeTransferPanel"),
  gameHeader: $("gameHeader"), gameMain: $("gameMain"),
  inviteCode: $("inviteCode"),
  leaveRoomButton: $("leaveRoomButton"),
  loginButton: $("loginButton"), logoutButton: $("logoutButton"),
  manageButton: $("manageButton"), manageDrawButton: $("manageDrawButton"),
  manageMenu: $("manageMenu"), managePauseButton: $("managePauseButton"),
  manageRestartButton: $("manageRestartButton"), roomManage: $("roomManage"),
  onlineNumber: $("onlineNumber"),
  password: $("password"),
  roomTopName: $("roomTopName"), roomTopbar: $("roomTopbar"),
  transferAmount: $("transferAmount"), transferFeedback: $("transferFeedback"),
  transferSubmit: $("transferSubmit"), transferTo: $("transferTo"),
  userArea: $("userArea"), userAvatar: $("userAvatar"),
  userChip: $("userChip"), userDropdown: $("userDropdown"), userName: $("userName"),
  username: $("username"),
};

export const AUTH_TOKEN_KEY = "liveAuthToken";

/* 服务端注入的配置（见 config.py / deploy/serve.py）；直接打开文件时用默认值兜底。 */
const LIVE_CONFIG = window.LIVE_CONFIG || {};
export const CHAT_PORT = LIVE_CONFIG.chat_port || 8765;
export const SITE = LIVE_CONFIG.site || {};

const profiles = new Map();
const pendingProfiles = new Set();

/** 转账目标选择组件仍是全局 IIFE（index.html 也在用），这里统一收口引用。 */
export const transferSelect = () => window.TransferSelect;

/** 跨模块共享的可变状态：ESM 的 import 绑定只读，统一挂在 state 上改。 */
export const state = {
  authMode: "login",
  currentUser: null,
  socket: null,
  reconnectTimer: 0,
  hallRooms: [],
  myRoom: null,
  hallTimer: 0,
  hallDeadlineAt: 0,
  currentGameId: null,
  hallPage: null,
  roomChat: [],
  roomChatDraft: "",
  ratingEntries: [],
  ratingLeaderboard: null,
  ratingLeaderboardOffset: 0,
  ratingLeaderboardRequest: null,
};

export const rewardsPanel = window.DailyRewards.create({
  send,
  onCoins(coins) {
    if (!state.currentUser) return;
    state.currentUser.coins = coins;
    updateCoinChip();
    if (elements.financeModal.style.display === "flex") send({ type: "get_finance" });
  },
});

let manageMenuOpen = false;

export function formatCoins(value) {
  return Number(value || 0).toFixed(2);
}

export function ratingBadge(rating) {
  const badge = document.createElement("span");
  badge.className = "rating-badge";
  badge.hidden = !rating;
  if (rating) {
    badge.dataset.tier = rating.tier;
    badge.textContent = `${rating.tier} ${rating.score}`;
    badge.title = `段位分 ${rating.score} · 已结算 ${rating.games} 局`;
  }
  return badge;
}

export function formatClock(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function displayNameOf(username) {
  const profile = profiles.get(username);
  return profile?.nickname || username;
}

export function rememberProfile(data) {
  if (!data?.username) return;
  profiles.set(data.username, {
    nickname: data.nickname || "",
    avatar: data.avatar || "",
  });
  pendingProfiles.delete(data.username);
}

export function requestProfile(username) {
  if (!username || profiles.has(username) || pendingProfiles.has(username)) return;
  pendingProfiles.add(username);
  send({ type: "get_profile", username });
}

export function send(payload) {
  if (state.socket?.readyState !== WebSocket.OPEN) {
    void alertDialog("游戏厅尚未连接");
    return false;
  }
  state.socket.send(JSON.stringify(payload));
  return true;
}

export function connectGame() {
  clearTimeout(state.reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${location.hostname}:${CHAT_PORT}/?client=game`);
  state.socket.addEventListener("open", () => {
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (token) send({ type: "resume", token });
    else renderGameView();
    send({ type: "list_rooms" });
    send({ type: "get_room" });
  });
  state.socket.addEventListener("error", () => {});
  state.socket.addEventListener("close", () => {
    state.reconnectTimer = window.setTimeout(connectGame, 2000);
  });
  state.socket.addEventListener("message", ({ data }) => {
    try {
      handleServerMessage(JSON.parse(data));
    } catch (error) {
      console.error("无法解析服务器消息", error);
    }
  });
}

export function renderIdentity() {
  const user = state.currentUser;
  const name = user ? user.nickname || user.username : "";
  elements.userAvatar.replaceChildren();
  if (user?.avatar) {
    const image = document.createElement("img");
    image.src = user.avatar;
    image.alt = "";
    elements.userAvatar.append(image);
  } else {
    elements.userAvatar.textContent = user ? user.username.charAt(0).toUpperCase() : "";
  }
  elements.userName.textContent = name;
  elements.dropdownName.textContent = name;
  if (user?.rating) elements.dropdownName.append(ratingBadge(user.rating));
}

export function updateCoinChip() {
  if (state.currentUser) {
    elements.coinBalance.textContent = formatCoins(state.currentUser.coins);
  }
}

export function setUserMenu(open) {
  elements.userDropdown.hidden = !open;
  elements.userChip.setAttribute("aria-expanded", String(open));
}

export function setManageMenu(open) {
  if (elements.roomManage.hidden) open = false;
  manageMenuOpen = open;
  elements.manageMenu.hidden = !open;
  elements.manageButton.setAttribute("aria-expanded", String(open));
}

export function setSignedIn(user) {
  // 同一账号重连保留排行榜页码；切换账号/退出时清除分页及在途请求。
  if (!user || user.username !== state.currentUser?.username) state.ratingLeaderboardOffset = 0;
  state.currentUser = user;
  rewardsPanel.setUser(user);
  state.ratingEntries = [];
  state.ratingLeaderboard = null;
  state.ratingLeaderboardRequest = null;
  elements.loginButton.hidden = Boolean(user);
  elements.coinChip.style.display = user ? "flex" : "none";
  elements.userAvatar.style.display = user ? "flex" : "none";
  elements.userName.style.display = user ? "block" : "none";
  elements.userChip.style.display = user ? "flex" : "none";
  renderIdentity();
  setUserMenu(false);
  updateCoinChip();
  if (user) {
    send({ type: "get_rating_history" });
    renderGameView();
  }
  else {
    state.myRoom = null;
    state.currentGameId = null;
    state.hallPage = null;
    renderGameView();
  }
  document.dispatchEvent(new CustomEvent("authstatechange", { detail: { user } }));
}


/* =========================================================
   登录
========================================================= */

export function renderGameView() {
  stopHallTicker();
  document.dispatchEvent(new Event("gameviewchange"));
  if (!state.currentUser) {
    clearRoomMode();
    renderView("entry");
    return;
  }
  if (state.myRoom) {
    renderView("room");
    return;
  }
  clearRoomMode();
  if (state.hallPage === "create" && state.currentGameId) renderView("create");
  else if (state.hallPage === "rooms" && state.currentGameId) renderView("rooms");
  else if (state.hallPage === "rankings") renderView("rankings");
  else if (state.hallPage) renderView(state.hallPage);
  else renderView("hall");
}

export function isRoomOwner() {
  return Boolean(state.currentUser) && state.myRoom.owner === state.currentUser.username;
}

function leaveConfirm() {
  if (isRoomOwner()) {
    return state.myRoom.status === "playing"
      ? { title: "确定流局？", message: "牌局结束，所有人按当前筹码退回金币。", tone: "danger" }
      : { title: "解散房间？", message: "所有人的买入将原额退还。", tone: "danger" };
  }
  return { title: "退出房间", message: "退出后取回你当前的筹码。", tone: "default" };
}

export function setRoomMode() {
  document.body.classList.add("game-in-room");
  elements.gameHeader.classList.add("in-room");
  elements.roomTopbar.hidden = false;
  elements.roomTopName.textContent = state.myRoom.name;
  const showManage = isRoomOwner() && state.myRoom.status === "playing" && !state.myRoom.settlement;
  elements.roomManage.hidden = !showManage;
  setManageMenu(manageMenuOpen && showManage);
  elements.managePauseButton.textContent = state.myRoom.paused ? "继续" : "暂停";
}

function clearRoomMode() {
  document.body.classList.remove("game-in-room");
  document.getElementById("desktopRoomChat")?.remove();
  document.getElementById("chatOverlay")?.remove();
  elements.gameHeader.classList.remove("in-room");
  elements.roomTopbar.hidden = true;
  setManageMenu(false);
}

export async function leaveRoom() {
  const { title, message, tone } = leaveConfirm();
  if (await confirmDialog(message, { title, tone })) send({ type: "leave_room" });
}

export function startHallTicker(fill, seconds) {
  stopHallTicker();
  state.hallDeadlineAt = Date.now() + seconds * 1000;
  const update = () => {
    const left = Math.max(0, state.hallDeadlineAt - Date.now());
    fill.style.width = `${Math.max(0, Math.min(100, (left / (seconds * 1000)) * 100))}%`;
    if (left <= 0) stopHallTicker();
  };
  update();
  state.hallTimer = window.setInterval(update, 500);
}

export function stopHallTicker() {
  if (state.hallTimer) {
    window.clearInterval(state.hallTimer);
    state.hallTimer = 0;
  }
}

export function handleServerMessage(data) {
  dispatchMessage(data);
}

onMessage("online", (data) => {
  elements.onlineNumber.textContent = data.count;
});
