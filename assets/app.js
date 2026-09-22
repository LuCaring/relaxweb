"use strict";

const $ = (id) => document.getElementById(id);
const elements = {
  authModal: $("loginModal"), authSubmit: $("authSubmit"), authSwitch: $("authSwitch"),
  avatarButton: $("avatarButton"), avatarFallback: $("avatarFallback"),
  avatarFile: $("avatarFile"), avatarPreview: $("avatarPreview"), avatarReset: $("avatarReset"),
  authTitle: $("authTitle"), betClose: $("betClose"), betModal: $("betModal"),
  betModalBody: $("betModalBody"), betModalTitle: $("betModalTitle"),
  betStat: $("betStat"), betStatTitle: $("betStatTitle"),
  chatCard: $("chatCard"), chatInput: $("chatInput"),
  chatStatus: $("chatStatus"), danmakuComposer: $("danmakuComposer"),
  danmakuLayer: $("danmakuLayer"), danmakuToggle: $("danmakuToggle"),
  deleteAccountButton: $("deleteAccountButton"), deleteCancel: $("deleteCancel"),
  deleteModal: $("deleteModal"), deletePassword: $("deletePassword"),
  deleteSubmit: $("deleteSubmit"), dropdownName: $("dropdownName"),
  financeBalance: $("financeBalance"), financeButton: $("financeButton"),
  financeClose: $("financeClose"), financeDetailPanel: $("financeDetailPanel"),
  financeList: $("financeList"), financeModal: $("financeModal"),
  financeTabDetail: $("financeTabDetail"), financeTabTransfer: $("financeTabTransfer"),
  financeTransferPanel: $("financeTransferPanel"),
  fullscreenButton: $("fullscreenButton"),
  headerMenu: $("headerMenu"), headerMenuButton: $("headerMenuButton"),
  inviteButton: $("inviteButton"),
  inviteClose: $("inviteClose"), inviteCreate: $("inviteCreate"),
  inviteCode: $("inviteCode"), inviteList: $("inviteList"),
  inviteModal: $("inviteModal"),
  leftHeader: $("leftHeader"), loginButton: $("loginButton"),
  logoutButton: $("logoutButton"), messages: $("messages"),
  muteToggle: $("muteToggle"),
  menuBetButton: $("menuBetButton"), menuOnlineButton: $("menuOnlineButton"),
  menuOnlineCount: $("menuOnlineCount"),
  nicknameInput: $("nicknameInput"), onlineNumber: $("onlineNumber"),
  onlinePopover: $("onlinePopover"), onlinePopoverCount: $("onlinePopoverCount"),
  onlinePopoverList: $("onlinePopoverList"), onlineStat: $("onlineStat"),
  password: $("password"), player: $("player"),
  playerMessage: $("playerMessage"), playerTools: $("playerTools"),
  playToggle: $("playToggle"), profileButton: $("profileButton"),
  profileModal: $("profileModal"), profileSubmit: $("profileSubmit"),
  sendButton: $("sendButton"),
  streamVideo: $("streamVideo"), transferAmount: $("transferAmount"),
  transferFeedback: $("transferFeedback"), transferSubmit: $("transferSubmit"),
  transferTo: $("transferTo"),
  userArea: $("userArea"), userAvatar: $("userAvatar"),
  userChip: $("userChip"), userDropdown: $("userDropdown"), userName: $("userName"),
  username: $("username"), volumeSlider: $("volumeSlider"),
};

/* 服务端注入的配置（见 config.py / deploy/serve.py）：站点文案、拉流地址与端口。
   直接打开本地文件时没有 window.LIVE_CONFIG，用这里的默认值兜底。 */
const LIVE_CONFIG = window.LIVE_CONFIG || {};
const SITE = LIVE_CONFIG.site || {};
const CHAT_PORT = LIVE_CONFIG.chat_port || 8765;
const STREAM = {
  whepPort: (LIVE_CONFIG.stream && LIVE_CONFIG.stream.whep_port) || 8889,
  path: (LIVE_CONFIG.stream && LIVE_CONFIG.stream.path) || "live",
};

function applySiteConfig() {
  if (SITE.title) document.title = SITE.title;
  const logo = document.querySelector(".logo");
  if (logo && SITE.brand) logo.textContent = SITE.brand;
}

const roles = {
  streamer: { icon: "👑 ", className: "streamer" },
  admin: { icon: "🛡 ", className: "admin" },
  system: { icon: "📢 ", className: "system" },
  user: { icon: "", className: "" },
};
const danmaku = {
  enabled: localStorage.getItem("danmakuEnabled") !== "false",
  queue: [], busyUntil: [], timer: 0,
  trackHeight: 34, topReserved: 58, bottomReserved: 72, speed: 125, gap: 45,
};

const AUTH_TOKEN_KEY = "liveAuthToken";
const CONTROLS_HIDE_DELAY = 2500;
const BET_MIN_STAKE = 10;
const BET_MAX_OPTIONS = 6;
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
  estate_purchase: "庄园支出",
  estate_sale: "庄园出售收入",
  estate_pet_defense: "宠物防守结算",
};
const profiles = new Map();
const pendingProfiles = new Set();
let authMode = "login";
let currentUser = null;
let socket = null;
let reconnectTimer = 0;
let streamReader = null;
let streamReconnectTimer = 0;
let controlsHideTimer = 0;
let pendingAvatar;
let chatHeightObserver = null;
let betCache = null;
let lastSettled = null;
let joinDraft = { optionIndex: 0, amount: "" };
let betCountdownTimer = 0;

const rewardsPanel = window.DailyRewards.create({
  send,
  onCoins(coins) {
    if (!currentUser) return;
    currentUser.coins = coins;
    if (elements.financeModal.style.display === "flex") send({ type: "get_finance" });
  },
});

function formatCoins(value) {
  return Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

function formatClock(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function displayNameOf(username) {
  const profile = profiles.get(username);
  return profile?.nickname || username;
}

function roleInfo(role) {
  return roles[role] || roles.user;
}

function setPlayerMessage(message) {
  elements.playerMessage.textContent = message;
  elements.playerMessage.hidden = !message;
}

function describeButton(button, label) {
  button.title = label;
  button.setAttribute("aria-label", label);
}

function updatePlaybackButton() {
  const paused = elements.streamVideo.paused;
  elements.playToggle.textContent = paused ? "▶" : "⏸";
  describeButton(elements.playToggle, paused ? "播放" : "暂停");
}

function updateMuteButton() {
  const muted = elements.streamVideo.muted;
  elements.muteToggle.textContent = muted ? "🔇" : "🔊";
  elements.muteToggle.classList.toggle("active", !muted);
  elements.volumeSlider.value = String(elements.streamVideo.volume);
  describeButton(elements.muteToggle, muted ? "开启声音" : "静音");
}

function updateFullscreenButton() {
  const fullscreen = document.fullscreenElement === elements.player;
  elements.fullscreenButton.textContent = fullscreen ? "⤡" : "⤢";
  describeButton(elements.fullscreenButton, fullscreen ? "退出全屏" : "进入全屏");
}

function setVolume() {
  elements.streamVideo.volume = Number(elements.volumeSlider.value);
  elements.streamVideo.muted = elements.streamVideo.volume === 0;
  updateMuteButton();
}

function connectStream() {
  clearTimeout(streamReconnectTimer);
  streamReader?.close();
  streamReader = null;
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (!currentUser || !token) {
    setPlayerMessage("登录后观看直播");
    return;
  }
  const protocol = location.protocol === "https:" ? "https:" : "http:";
  streamReader = new MediaMTXWebRTCReader({
    url: `${protocol}//${location.hostname}:${STREAM.whepPort}/${STREAM.path}/whep`,
    user: currentUser.username,
    pass: token,
    onError: () => {
      setPlayerMessage("等待直播信号…");
      streamReconnectTimer = window.setTimeout(connectStream, 3000);
    },
    onTrack: (event) => {
      setPlayerMessage("");
      elements.streamVideo.srcObject = event.streams[0];
      elements.streamVideo.play().catch(() => setPlayerMessage("点击播放以开始观看"));
    },
    onDataChannel: () => {},
  });
}

async function togglePlayback() {
  if (elements.streamVideo.paused) {
    try {
      await elements.streamVideo.play();
      setPlayerMessage("");
    } catch {
      setPlayerMessage("暂时无法播放直播");
    }
  } else {
    elements.streamVideo.pause();
  }
}

function toggleMute() {
  if (elements.streamVideo.muted && elements.streamVideo.volume === 0) {
    elements.streamVideo.volume = 0.5;
  }
  elements.streamVideo.muted = !elements.streamVideo.muted;
  updateMuteButton();
}

function showPlayerControls() {
  clearTimeout(controlsHideTimer);
  elements.player.classList.remove("controls-hidden");
  if (document.fullscreenElement === elements.player) {
    controlsHideTimer = window.setTimeout(() => {
      if (document.activeElement !== elements.chatInput) {
        elements.player.classList.add("controls-hidden");
      }
    }, CONTROLS_HIDE_DELAY);
  }
}

function syncComposerPosition() {
  const fullscreen = document.fullscreenElement === elements.player;
  elements.danmakuComposer.classList.toggle("in-player-controls", fullscreen);

  // 移动端固定视口布局下输入条固定在聊天卡片底部（DOM 默认位置），无需搬动
  const composer = elements.danmakuComposer;
  const targetParent = fullscreen ? elements.playerTools : elements.chatCard;
  const targetBefore = fullscreen
    ? elements.playerTools.querySelector(".player-tools-right")
    : null;
  if (composer.parentElement === targetParent
    && composer.nextElementSibling === targetBefore) {
    return;
  }

  const refocus = document.activeElement === elements.chatInput;
  if (targetBefore) targetParent.insertBefore(composer, targetBefore);
  else targetParent.append(composer);
  if (refocus) elements.chatInput.focus({ preventScroll: true });
}

function handleFullscreenChange() {
  clearTimeout(controlsHideTimer);
  elements.player.classList.remove("controls-hidden");
  syncComposerPosition();
  updateFullscreenButton();
  showPlayerControls();
}

function syncChatHeight() {
  elements.chatCard.style.height = "";
}

function setConnectionStatus(text) {
  elements.chatStatus.textContent = `● ${text}`;
}

function setSignedIn(user) {
  currentUser = user;
  rewardsPanel.setUser(user);
  elements.loginButton.hidden = Boolean(user);
  elements.userAvatar.style.display = user ? "flex" : "none";
  elements.userName.style.display = user ? "block" : "none";
  elements.userChip.style.display = user ? "flex" : "none";
  renderIdentity();
  setUserMenu(false);
  if (user) {
    if (!streamReader) connectStream();
  } else {
    clearTimeout(streamReconnectTimer);
    streamReader?.close();
    streamReader = null;
    elements.streamVideo.srcObject = null;
    setPlayerMessage("登录后观看直播");
  }
  elements.chatInput.placeholder = user ? "发送弹幕..." : "登录后发送弹幕...";
}

function renderIdentity() {
  const user = currentUser;
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
  if (user?.rating) {
    const badge = document.createElement("span");
    badge.className = "rating-badge";
    badge.dataset.tier = user.rating.tier;
    badge.title = `段位分 ${user.rating.score} · 已结算 ${user.rating.games} 局`;
    badge.textContent = `${user.rating.tier} ${user.rating.score}`;
    elements.dropdownName.append(badge);
  }
}

function rememberProfile(data) {
  if (!data?.username) return;
  profiles.set(data.username, {
    nickname: data.nickname || "",
    avatar: data.avatar || "",
  });
  pendingProfiles.delete(data.username);
}

function requestProfile(username) {
  if (!username || profiles.has(username) || pendingProfiles.has(username)) return;
  pendingProfiles.add(username);
  send({ type: "get_profile", username });
}

function refreshMessageIdentity(username) {
  const profile = profiles.get(username);
  for (const row of elements.messages.children) {
    if (row.dataset.username !== username) continue;
    row.querySelector(".display-name").textContent =
      profile?.nickname || row.dataset.nickname || username;
    const avatar = row.querySelector(".message-avatar");
    avatar.replaceChildren();
    if (profile?.avatar) {
      const image = document.createElement("img");
      image.src = profile.avatar;
      image.alt = "";
      avatar.append(image);
    } else {
      avatar.textContent = username.charAt(0).toUpperCase();
    }
  }
}

function setUserMenu(open) {
  elements.userDropdown.hidden = !open;
  elements.userChip.setAttribute("aria-expanded", String(open));
}

/* 页面内提示/确认弹层（assets/js/dialog.js 由 dialog-global.js 挂到 window.LiveDialog）。
   不用原生 alert/confirm：原生弹窗会阻塞浏览器主线程，自动化测试会一直卡在对话框上。 */
const uiAlert = (message) => window.LiveDialog.alert(message);
const uiConfirm = (message, options) => window.LiveDialog.confirm(message, options);

function send(payload) {
  if (socket?.readyState !== WebSocket.OPEN) {
    uiAlert("聊天室尚未连接");
    return false;
  }
  socket.send(JSON.stringify(payload));
  return true;
}

function connectChat() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.hostname}:${CHAT_PORT}`);
  socket.addEventListener("open", () => {
    setConnectionStatus("在线");
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (token) send({ type: "resume", token });
    send({ type: "get_bet" });
  });
  socket.addEventListener("error", () => setConnectionStatus("连接异常"));
  socket.addEventListener("close", () => {
    setConnectionStatus("重连中");
    reconnectTimer = window.setTimeout(connectChat, 2000);
  });
  socket.addEventListener("message", ({ data }) => {
    try {
      handleServerMessage(JSON.parse(data));
    } catch (error) {
      console.error("无法解析服务器消息", error);
    }
  });
}

const messageHandlers = {
  history(data) {
    elements.messages.replaceChildren();
    data.messages.forEach((message) => {
      if (message.type === "system") addSystemMessage(message.text, message.time);
      else addMessage(message);
    });
  },
  chat(data) {
    addMessage(data);
    addDanmaku(data);
  },
  system(data) {
    addSystemMessage(data.text, data.time);
    if (data.danmaku) {
      addDanmaku({ username: "系统", nickname: "", role: "system", text: data.text });
    }
  },
  online(data) {
    elements.onlineNumber.textContent = data.count;
    elements.menuOnlineCount.textContent = data.count;
  },
  online_users(data) {
    renderOnlineUsers(data.users || []);
  },
  register_success() {
    uiAlert("注册成功，请登录");
    elements.password.value = "";
    setAuthMode("login");
  },
  login_success(data) {
    localStorage.setItem("liveAuthToken", data.token);
    elements.authModal.style.display = "none";
    setSignedIn({
      username: data.username, nickname: data.nickname, avatar: data.avatar,
      coins: data.coins, rating: data.rating,
    });
  },
  resume_success(data) {
    setSignedIn({
      username: data.username, nickname: data.nickname, avatar: data.avatar,
      coins: data.coins, rating: data.rating,
    });
  },
  profile(data) {
    rememberProfile(data);
    if (currentUser && data.username === currentUser.username) {
      currentUser.nickname = data.nickname || "";
      currentUser.avatar = data.avatar || "";
      currentUser.rating = data.rating;
      renderIdentity();
    }
    refreshMessageIdentity(data.username);
  },
  profile_updated() {
    elements.profileModal.style.display = "none";
  },
  rating_update(data) {
    if (currentUser && data.username === currentUser.username) {
      currentUser.rating = data.rating;
      renderIdentity();
    }
  },
  profile_error(data) { uiAlert(data.message); },
  invite_list(data) {
    renderInviteList(data.codes || []);
  },
  invite_created() {
    send({ type: "list_invites" });
  },
  invite_error(data) { uiAlert(data.message); },
  account_deleted() {
    elements.deleteModal.style.display = "none";
    localStorage.removeItem(AUTH_TOKEN_KEY);
    setSignedIn(null);
    uiAlert("账号已注销");
  },
  account_error(data) { uiAlert(data.message); },
  auth_expired() {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    setSignedIn(null);
  },
  auth_error(data) { uiAlert(data.message); },
  error(data) { uiAlert(data.message); },
  user_list(data) { TransferSelect.setUsers(data.users || []); },
  finance(data) { renderFinance(data); },
  daily_rewards(data) { rewardsPanel.handle(data); },
  checkin_result(data) { rewardsPanel.handle(data); },
  lottery_result(data) { rewardsPanel.handle(data); },
  holdem_reward_result(data) { rewardsPanel.handle(data); },
  rewards_error(data) { rewardsPanel.handle(data); },
  transfer_success(data) {
    if (currentUser) currentUser.coins = data.coins;
    elements.transferAmount.value = "";
    TransferSelect.reset();
    elements.transferFeedback.textContent = "转账成功";
    send({ type: "get_finance" });
  },
  coins_error(data) { uiAlert(data.message); },
  coins(data) {
    if (currentUser && data.username === currentUser.username) {
      currentUser.coins = data.coins;
    }
    if (elements.financeModal.style.display === "flex"
      && currentUser && data.username === currentUser.username) {
      send({ type: "get_finance" });
    }
  },
  bet_state(data) { setBetState(data.bet); },
  bet_update(data) { setBetState(data.bet); },
  bet_created() {},
  bet_placed(data) {
    if (currentUser) currentUser.coins = data.coins;
    joinDraft = { optionIndex: 0, amount: "" };
    if (elements.betModal.style.display === "flex") renderBetModal();
  },
  bet_settled(data) {
    if (currentUser) {
      const mine = (data.results || []).find(
        (item) => item.username === currentUser.username,
      );
      if (mine) currentUser.coins = mine.coins;
    }
    lastSettled = data;
    betCache = null;
    renderBetChip();
    if (elements.betModal.style.display === "flex") renderBetModal();
  },
  bet_cancelled(data) {
    if (currentUser) send({ type: "get_finance" });
    lastSettled = {
      question: data.question,
      cancelled: true,
      reason: data.reason || "发起者流局",
      results: [],
    };
    betCache = null;
    renderBetChip();
    if (elements.betModal.style.display === "flex") renderBetModal();
  },
  bet_error(data) { uiAlert(data.message); },
  admin_coins_done(data) {
    uiAlert(`已将 ${data.username} 的金币设为 ${formatCoins(data.coins)}`);
  },
};

function handleServerMessage(data) {
  messageHandlers[data.type]?.(data);
}

function addMessage({ username, nickname, text, time, role }) {
  const info = roleInfo(role);
  const row = document.createElement("div");
  row.className = "message";
  row.dataset.username = username;
  row.dataset.nickname = nickname || "";
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  const body = document.createElement("div");
  const user = document.createElement("div");
  user.className = "message-user";
  if (info.className) user.classList.add(`${info.className}-name-chat`);
  const nameSpan = document.createElement("span");
  nameSpan.className = "display-name";
  user.append(document.createTextNode(info.icon), nameSpan);
  if (time) {
    const clock = document.createElement("span");
    clock.className = "message-time";
    clock.textContent = time;
    user.append(clock);
  }
  const content = document.createElement("div");
  content.className = "message-text";
  content.textContent = text;
  body.append(user, content);
  row.append(avatar, body);
  elements.messages.append(row);
  refreshMessageIdentity(username);
  requestProfile(username);
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function addSystemMessage(text, time) {
  const row = document.createElement("div");
  row.className = "message system-message";
  const avatar = document.createElement("div");
  avatar.className = "message-avatar system-avatar";
  avatar.textContent = "📢";
  const body = document.createElement("div");
  const user = document.createElement("div");
  user.className = "message-user system-name-chat";
  user.textContent = "系统";
  if (time) {
    const clock = document.createElement("span");
    clock.className = "message-time";
    clock.textContent = time;
    user.append(clock);
  }
  const content = document.createElement("div");
  content.className = "message-text system-text";
  content.textContent = text;
  body.append(user, content);
  row.append(avatar, body);
  elements.messages.append(row);
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function setAuthMode(mode) {
  authMode = mode;
  const isLogin = mode === "login";
  elements.authTitle.textContent = isLogin ? "登录直播间" : "注册账号";
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

function openLogin() {
  setAuthMode("login");
  elements.authModal.style.display = "flex";
  elements.username.focus();
}

function submitAuth() {
  const username = elements.username.value.trim();
  const password = elements.password.value;
  if (!username || !password) {
    uiAlert("请输入用户名和密码");
    return;
  }
  send({
    type: authMode,
    username,
    password,
    ...(authMode === "register" ? { invite_code: elements.inviteCode.value.trim() } : {}),
  });
}

function openProfile() {
  if (!currentUser) return;
  elements.nicknameInput.value = currentUser.nickname || "";
  pendingAvatar = undefined;
  renderAvatarPreview(currentUser.avatar);
  setUserMenu(false);
  elements.profileModal.style.display = "flex";
}

function renderAvatarPreview(src) {
  elements.avatarPreview.hidden = !src;
  elements.avatarFallback.hidden = Boolean(src);
  if (src) {
    elements.avatarPreview.src = src;
  } else {
    elements.avatarFallback.textContent = currentUser
      ? currentUser.username.charAt(0).toUpperCase() : "";
  }
}

async function pickAvatar(file) {
  if (!file || !file.type.startsWith("image/")) return;
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) {
    uiAlert("图片读取失败");
    return;
  }
  const edge = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  canvas.getContext("2d").drawImage(
    bitmap,
    (bitmap.width - edge) / 2, (bitmap.height - edge) / 2, edge, edge,
    0, 0, 128, 128,
  );
  const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
  if (dataUrl.length > 180000) {
    uiAlert("图片太大，请换一张试试");
    return;
  }
  pendingAvatar = dataUrl;
  renderAvatarPreview(dataUrl);
}

function submitProfile() {
  if (!currentUser) return;
  send({
    type: "update_profile",
    nickname: elements.nicknameInput.value.trim(),
    ...(pendingAvatar !== undefined ? { avatar: pendingAvatar } : {}),
  });
}

function openInviteModal() {
  if (!currentUser) return;
  setUserMenu(false);
  elements.inviteList.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "invite-empty";
  loading.textContent = "加载中…";
  elements.inviteList.append(loading);
  elements.inviteModal.style.display = "flex";
  send({ type: "list_invites" });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {}
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.append(helper);
  helper.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {}
  helper.remove();
  return copied;
}

function renderInviteList(codes) {
  const list = elements.inviteList;
  list.replaceChildren();
  if (!codes.length) {
    const empty = document.createElement("div");
    empty.className = "invite-empty";
    empty.textContent = "还没有可用的邀请码，点击下方生成";
    list.append(empty);
    return;
  }
  for (const item of codes) {
    const row = document.createElement("div");
    row.className = "invite-row";
    const code = document.createElement("span");
    code.className = "invite-code";
    code.textContent = item.code;
    const copy = document.createElement("button");
    copy.className = "invite-copy";
    copy.type = "button";
    copy.textContent = "复制";
    copy.addEventListener("click", async () => {
      copy.textContent = (await copyText(item.code)) ? "已复制" : "请手动复制";
      window.setTimeout(() => { copy.textContent = "复制"; }, 1500);
    });
    row.append(code, copy);
    list.append(row);
  }
}

function createInvite() {
  if (!currentUser) return;
  send({ type: "create_invite" });
}

function openDeleteModal() {
  if (!currentUser) return;
  elements.deletePassword.value = "";
  setUserMenu(false);
  elements.deleteModal.style.display = "flex";
}

function submitDeleteAccount() {
  if (!elements.deletePassword.value) {
    uiAlert("请输入密码");
    return;
  }
  send({ type: "delete_account", password: elements.deletePassword.value });
}


/* =========================================================
   财务管理
========================================================= */

function switchFinanceTab(tab) {
  const detail = tab === "detail";
  elements.financeTabDetail.classList.toggle("active", detail);
  elements.financeTabTransfer.classList.toggle("active", !detail);
  elements.financeDetailPanel.hidden = !detail;
  elements.financeTransferPanel.hidden = detail;
  elements.transferFeedback.textContent = "";
  if (!detail) TransferSelect.requestUsers();
}

function openFinance() {
  if (!currentUser) return;
  setUserMenu(false);
  switchFinanceTab("detail");
  elements.financeBalance.textContent = formatCoins(currentUser.coins);
  elements.financeList.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "invite-empty";
  loading.textContent = "加载中…";
  elements.financeList.append(loading);
  elements.financeModal.style.display = "flex";
  send({ type: "get_finance" });
}

function renderFinance(data) {
  if (!currentUser) return;
  currentUser.coins = data.coins;
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

function submitTransfer() {
  if (!currentUser) return;
  const to = elements.transferTo.value.trim();
  const amount = Number(elements.transferAmount.value);
  if (!to) {
    uiAlert("请选择转账对象");
    return;
  }
  if (to === currentUser.username) {
    uiAlert("不能转账给自己");
    return;
  }
  if (!Number.isFinite(amount) || amount <= 0) {
    uiAlert("请输入正确的转账金额");
    return;
  }
  elements.transferFeedback.textContent = "";
  send({ type: "transfer_coins", to, amount: Math.round(amount * 100) / 100 });
}


/* =========================================================
   竞猜
========================================================= */

function betClosed(bet) {
  if (!bet) return false;
  if (bet.closed_at) return true;
  return Boolean(bet.closes_at && Date.now() / 1000 >= bet.closes_at);
}

function betCloseText(bet) {
  if (!bet || !bet.closes_at) return "";
  if (betClosed(bet)) return "已封盘";
  const remain = Math.max(0, Math.ceil(bet.closes_at - Date.now() / 1000));
  const minutes = Math.floor(remain / 60);
  const seconds = remain % 60;
  return `距封盘 ${minutes}:${String(seconds).padStart(2, "0")}`;
}

function stopBetCountdown() {
  if (betCountdownTimer) {
    clearInterval(betCountdownTimer);
    betCountdownTimer = 0;
  }
}

function syncBetCountdown() {
  stopBetCountdown();
  if (!betCache || !betCache.closes_at || betCache.closed_at) return;
  betCountdownTimer = setInterval(() => {
    const node = document.getElementById("betCloseStatus");
    if (!node) return;
    const text = betCloseText(betCache);
    node.textContent = text;
    if (text === "已封盘") stopBetCountdown();
  }, 1000);
}

function renderBetChip() {
  elements.betStat.classList.toggle("active", Boolean(betCache));
  const closedTag = betClosed(betCache) ? "（已封盘）" : "";
  elements.betStatTitle.textContent = betCache ? betCache.question + closedTag : "竞猜";
  elements.betStat.title = betCache ? `竞猜：${betCache.question}${closedTag}` : "竞猜";
  elements.menuBetButton.classList.toggle("active-option", Boolean(betCache));
}

function setHeaderMenu(open) {
  elements.headerMenu.classList.toggle("open", open);
  elements.headerMenuButton.setAttribute("aria-expanded", String(open));
}

function setBetState(bet) {
  betCache = bet;
  if (betCache) lastSettled = null;
  renderBetChip();
  if (elements.betModal.style.display === "flex") renderBetModal();
  syncBetCountdown();
}

function openBetModal() {
  if (!currentUser) {
    openLogin();
    return;
  }
  elements.betModal.style.display = "flex";
  renderBetModal();
  send({ type: "get_bet" });
  syncBetCountdown();
}

function betEntryLine(entry) {
  return `${displayNameOf(entry.username)} ${formatCoins(entry.amount)} → ${betCache.options[entry.option_index] ?? "?"}`;
}

function buildBetInfo(bet) {
  const box = document.createElement("div");
  const title = document.createElement("div");
  title.className = "bet-question";
  title.textContent = bet.question;
  const meta = document.createElement("div");
  meta.className = "bet-meta";
  meta.textContent = `发起者 ${displayNameOf(bet.creator)} · 奖池 ${formatCoins(bet.pot)} 金币 · ${bet.entries.length} 人参与`;
  box.append(title, meta);
  if (bet.closes_at || bet.closed_at) {
    const closeStatus = document.createElement("div");
    closeStatus.id = "betCloseStatus";
    closeStatus.className = `bet-close-status${betClosed(bet) ? " closed" : ""}`;
    closeStatus.textContent = betClosed(bet)
      ? "🔒 已封盘，等待发起者结账"
      : `🔒 ${betCloseText(bet)}`;
    box.append(closeStatus);
  }
  const optionList = document.createElement("div");
  optionList.className = "bet-option-list";
  bet.options.forEach((text, index) => {
    const item = document.createElement("div");
    item.className = "bet-option";
    item.style.cursor = "default";
    const name = document.createElement("span");
    name.textContent = text;
    const pot = document.createElement("span");
    pot.className = "bet-option-pot";
    pot.textContent = `${formatCoins(bet.totals[index])} 金币`;
    item.append(name, pot);
    optionList.append(item);
  });
  box.append(optionList);
  if (bet.entries.length) {
    const entries = document.createElement("div");
    entries.className = "bet-entries";
    for (const entry of bet.entries) {
      const line = document.createElement("div");
      line.textContent = betEntryLine(entry);
      entries.append(line);
    }
    box.append(entries);
  }
  return box;
}

function buildJoinForm(bet) {
  const box = document.createElement("div");
  const label = document.createElement("div");
  label.className = "bet-section-title";
  if (betClosed(bet)) {
    label.textContent = "参与竞猜";
    const note = document.createElement("div");
    note.className = "bet-closed-note";
    note.textContent = "🔒 本期竞猜已封盘，无法参与，等待发起者结账";
    box.append(label, note);
    return box;
  }
  label.textContent = "参与竞猜";
  box.append(label);

  const selected = Math.min(joinDraft.optionIndex, bet.options.length - 1);
  const optionList = document.createElement("div");
  optionList.className = "bet-option-list";
  bet.options.forEach((text, index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `bet-option${index === selected ? " selected" : ""}`;
    const name = document.createElement("span");
    name.textContent = text;
    const pot = document.createElement("span");
    pot.className = "bet-option-pot";
    pot.textContent = `${formatCoins(bet.totals[index])} 金币`;
    item.append(name, pot);
    item.addEventListener("click", () => {
      joinDraft.optionIndex = index;
      optionList.querySelectorAll(".bet-option").forEach((node, i) => {
        node.classList.toggle("selected", i === index);
      });
    });
    optionList.append(item);
  });
  box.append(optionList);

  const balance = Number(currentUser?.coins || 0);
  const allIn = balance > 0 && balance < BET_MIN_STAKE;
  const row = document.createElement("div");
  row.className = "bet-input-row";
  const amount = document.createElement("input");
  amount.className = "login-input";
  amount.type = "number";
  amount.min = "0.01";
  amount.step = "0.01";
  amount.placeholder = "投注金币数量";
  if (allIn) {
    amount.value = String(balance);
    amount.readOnly = true;
    joinDraft.amount = String(balance);
  } else {
    amount.value = joinDraft.amount;
    amount.addEventListener("input", () => { joinDraft.amount = amount.value; });
  }
  row.append(amount);
  box.append(row);

  const hint = document.createElement("div");
  hint.className = "bet-hint";
  hint.textContent = balance <= 0
    ? "我的金币 0.00：金币不足，无法参与"
    : allIn
      ? `我的金币 ${formatCoins(balance)}：不足 ${BET_MIN_STAKE}，只能全部投上`
      : `我的金币 ${formatCoins(balance)} · 单独投注最低 ${BET_MIN_STAKE} 金币`;
  box.append(hint);

  const submit = document.createElement("button");
  submit.className = "login-submit";
  submit.type = "button";
  submit.textContent = "投注并参与";
  if (balance <= 0) submit.disabled = true;
  submit.addEventListener("click", () => {
    if (!currentUser) return;
    const balanceNow = Number(currentUser.coins || 0);
    let value = Number(joinDraft.amount);
    if (!Number.isFinite(value) || value <= 0) {
      uiAlert("请输入投注金币数量");
      return;
    }
    value = Math.round(value * 100) / 100;
    if (balanceNow < BET_MIN_STAKE) value = balanceNow;
    if (value < BET_MIN_STAKE) {
      uiAlert(`最低投注 ${BET_MIN_STAKE} 金币`);
      return;
    }
    if (value > balanceNow) {
      uiAlert("金币不足");
      return;
    }
    send({ type: "place_bet", option_index: joinDraft.optionIndex, amount: value });
  });
  box.append(submit);
  return box;
}

function buildMyEntry(mine) {
  const box = document.createElement("div");
  const label = document.createElement("div");
  label.className = "bet-section-title";
  label.textContent = "我的参与";
  const row = document.createElement("div");
  row.className = "bet-settle-row";
  row.textContent = `你选了「${betCache.options[mine.option_index]}」，投注 ${formatCoins(mine.amount)} 金币，等待发起者结账`;
  box.append(label, row);
  return box;
}

function buildSettlePanel(bet) {
  const box = document.createElement("div");
  const label = document.createElement("div");
  label.className = "bet-section-title";
  label.textContent = "结账（仅发起者）";
  const hint = document.createElement("div");
  hint.className = "bet-hint";
  hint.textContent = "选择正确选项并结账，猜错者的投注将按投注比例分给猜中者。";
  box.append(label, hint);
  bet.options.forEach((text, index) => {
    const row = document.createElement("div");
    row.className = "bet-settle-row";
    const name = document.createElement("span");
    name.textContent = `${text}（${formatCoins(bet.totals[index])} 金币）`;
    const button = document.createElement("button");
    button.className = "bet-settle-button";
    button.type = "button";
    button.textContent = "设为答案";
    button.addEventListener("click", async () => {
      if (await uiConfirm(`正确答案是「${text}」，确认后立即按此结账。`, { title: "确定答案？" })) {
        send({ type: "settle_bet", correct_index: index });
      }
    });
    row.append(name, button);
    box.append(row);
  });
  if (betClosed(bet)) {
    const closed = document.createElement("div");
    closed.className = "bet-closed-note";
    closed.textContent = "🔒 已封盘：不再接受新投注，可直接选答案结账。";
    box.append(closed);
  } else {
    const closeNow = document.createElement("button");
    closeNow.className = "bet-secondary";
    closeNow.type = "button";
    closeNow.textContent = "立即封盘（不再接受新投注）";
    closeNow.addEventListener("click", async () => {
      if (await uiConfirm("封盘后未参与的人无法再投注，已投注的不受影响。", { title: "确定封盘？" })) {
        send({ type: "close_bet" });
      }
    });
    box.append(closeNow);
  }
  const draw = document.createElement("button");
  draw.className = "bet-secondary";
  draw.type = "button";
  draw.textContent = "流局（退还全部投注）";
  draw.addEventListener("click", async () => {
    if (await uiConfirm("本轮竞猜作废，所有投注将原额退还。", { title: "确定流局？", tone: "danger" })) {
      send({ type: "cancel_bet" });
    }
  });
  box.append(draw);
  return box;
}

function renderCreateView(body) {
  const intro = document.createElement("p");
  intro.className = "bet-intro";
  intro.textContent = "当前没有进行中的竞猜。发起一局：全局同时只有一个竞猜，由你添加问题与选项，并由你结账。";
  body.append(intro);

  const question = document.createElement("input");
  question.className = "login-input";
  question.maxLength = 60;
  question.placeholder = "竞猜问题（例如：今晚能吃到火锅吗）";
  body.append(question);

  const optionsBox = document.createElement("div");
  const addOption = (value) => {
    if (optionsBox.children.length >= BET_MAX_OPTIONS) return;
    const row = document.createElement("div");
    row.className = "bet-option-row";
    const input = document.createElement("input");
    input.className = "login-input";
    input.maxLength = 20;
    input.value = value || "";
    input.placeholder = `选项 ${optionsBox.children.length + 1}`;
    const remove = document.createElement("button");
    remove.className = "bet-remove-option";
    remove.type = "button";
    remove.textContent = "✕";
    remove.addEventListener("click", () => row.remove());
    row.append(input, remove);
    optionsBox.append(row);
  };
  addOption("能");
  addOption("不能");
  body.append(optionsBox);

  const addButton = document.createElement("button");
  addButton.className = "bet-secondary";
  addButton.type = "button";
  addButton.textContent = "＋ 添加选项";
  addButton.addEventListener("click", () => addOption(""));
  body.append(addButton);

  const closeRow = document.createElement("div");
  closeRow.className = "bet-close-row";
  const closeLabel = document.createElement("span");
  closeLabel.className = "bet-close-label";
  closeLabel.textContent = "自动封盘";
  const closeSelect = document.createElement("select");
  closeSelect.className = "login-input";
  for (const minutes of [0, 1, 2, 3, 5, 10, 15, 30, 60]) {
    const option = document.createElement("option");
    option.value = String(minutes);
    option.textContent = minutes ? `${minutes} 分钟后` : "不封盘";
    closeSelect.append(option);
  }
  closeRow.append(closeLabel, closeSelect);
  body.append(closeRow);

  const submit = document.createElement("button");
  submit.className = "login-submit";
  submit.type = "button";
  submit.textContent = "发起竞猜";
  submit.addEventListener("click", () => {
    const options = [...optionsBox.querySelectorAll("input")]
      .map((input) => input.value.trim())
      .filter(Boolean);
    const text = question.value.trim();
    if (!text) {
      uiAlert("请输入竞猜问题");
      return;
    }
    if (options.length < 2) {
      uiAlert("至少需要两个选项");
      return;
    }
    send({
      type: "create_bet",
      question: text,
      options,
      close_minutes: Number(closeSelect.value) || 0,
    });
  });
  body.append(submit);

  const hint = document.createElement("div");
  hint.className = "bet-hint";
  hint.textContent = "默认选项为「能 / 不能」，可继续添加更多选项（单选）。设置自动封盘后，到点不再接受新投注，已投注的不受影响。";
  body.append(hint);
  question.focus();
}

function renderSettledView(body) {
  const question = document.createElement("div");
  question.className = "bet-question";
  question.textContent = lastSettled.question;
  const meta = document.createElement("div");
  meta.className = "bet-meta";
  meta.textContent = lastSettled.cancelled
    ? `${lastSettled.reason}，投注已退还`
    : lastSettled.refunded
      ? "无人猜对，投注已退还"
      : `答案：${lastSettled.answer}`;
  body.append(question, meta);
  const list = document.createElement("div");
  list.className = "bet-result-list";
  const results = lastSettled.results || [];
  if (!results.length && !lastSettled.cancelled) {
    const row = document.createElement("div");
    row.className = "bet-result-row";
    row.textContent = "本局没有玩家参与";
    list.append(row);
  }
  for (const item of results) {
    const row = document.createElement("div");
    row.className = `bet-result-row${currentUser && item.username === currentUser.username ? " mine" : ""}`;
    const name = document.createElement("span");
    name.textContent = displayNameOf(item.username);
    const amount = document.createElement("span");
    amount.className = `bet-result-amount ${item.change >= 0 ? "plus" : "minus"}`;
    amount.textContent = `${item.change >= 0 ? "+" : ""}${formatCoins(item.change)}（余额 ${formatCoins(item.coins)}）`;
    row.append(name, amount);
    list.append(row);
  }
  body.append(list);
  const again = document.createElement("button");
  again.className = "login-submit";
  again.type = "button";
  again.textContent = "发起新竞猜";
  again.addEventListener("click", () => {
    lastSettled = null;
    elements.betModalTitle.textContent = "竞猜";
    renderBetModal();
  });
  body.append(again);
}

function renderBetModal() {
  const body = elements.betModalBody;
  body.replaceChildren();
  if (lastSettled) {
    elements.betModalTitle.textContent = lastSettled.cancelled ? "竞猜流局" : "竞猜结果";
    renderSettledView(body);
    return;
  }
  if (!betCache) {
    elements.betModalTitle.textContent = "发起竞猜";
    renderCreateView(body);
    return;
  }
  elements.betModalTitle.textContent = "竞猜进行中";
  const mine = betCache.entries.find(
    (entry) => entry.username === currentUser?.username,
  );
  const isCreator = betCache.creator === currentUser?.username;
  body.append(buildBetInfo(betCache));
  if (mine) body.append(buildMyEntry(mine));
  else body.append(buildJoinForm(betCache));
  if (isCreator) body.append(buildSettlePanel(betCache));
}


function toggleOnlinePopover() {
  const willOpen = elements.onlinePopover.hidden;
  elements.onlinePopover.hidden = !willOpen;
  if (!willOpen) return;
  setUserMenu(false);
  if (socket?.readyState === WebSocket.OPEN) {
    send({ type: "get_online" });
  } else {
    renderOnlineUsers([]);
  }
}

function renderOnlineUsers(users) {
  elements.onlinePopoverCount.textContent = users.length;
  const list = elements.onlinePopoverList;
  list.replaceChildren();
  if (!users.length) {
    const empty = document.createElement("div");
    empty.className = "online-user-empty";
    empty.textContent = "当前没有登录的用户在线";
    list.append(empty);
    return;
  }
  for (const info of users) {
    const item = document.createElement("div");
    item.className = "online-user";
    const avatar = document.createElement("div");
    avatar.className = "online-user-avatar";
    if (info.avatar) {
      const image = document.createElement("img");
      image.src = info.avatar;
      image.alt = "";
      avatar.append(image);
    } else {
      avatar.textContent = (info.nickname || info.username).charAt(0).toUpperCase();
    }
    const body = document.createElement("div");
    const name = document.createElement("div");
    name.className = "online-user-name";
    name.textContent = `${roleInfo(info.role).icon}${info.nickname || info.username}`;
    body.append(name);
    if (info.nickname && info.nickname !== info.username) {
      const account = document.createElement("div");
      account.className = "online-user-account";
      account.textContent = info.username;
      body.append(account);
    }
    item.append(avatar, body);
    list.append(item);
  }
}

function sendMessage() {
  const text = elements.chatInput.value.trim();
  if (!text) return;
  if (!currentUser) {
    openLogin();
    return;
  }
  if (send({ type: "chat", text })) elements.chatInput.value = "";
}

function updateDanmakuButton() {
  elements.danmakuToggle.textContent = "💬";
  elements.danmakuToggle.classList.toggle("active", danmaku.enabled);
  elements.danmakuToggle.setAttribute("aria-pressed", String(danmaku.enabled));
  describeButton(elements.danmakuToggle, danmaku.enabled ? "关闭弹幕" : "开启弹幕");
}

function toggleDanmaku() {
  danmaku.enabled = !danmaku.enabled;
  localStorage.setItem("danmakuEnabled", String(danmaku.enabled));
  updateDanmakuButton();
  if (!danmaku.enabled) {
    danmaku.queue.length = 0;
    clearTimeout(danmaku.timer);
    danmaku.timer = 0;
    elements.danmakuLayer.replaceChildren();
  }
}

function resizeDanmakuTracks() {
  const usableHeight = elements.danmakuLayer.clientHeight
    - danmaku.topReserved - danmaku.bottomReserved;
  const count = Math.max(1, Math.floor(usableHeight / danmaku.trackHeight));
  danmaku.busyUntil = Array.from(
    { length: count }, (_, index) => danmaku.busyUntil[index] || 0,
  );
}

function addDanmaku({ username, text, role }) {
  if (!danmaku.enabled) return;
  danmaku.queue.push({
    username, role,
    text: text.length > 80 ? `${text.slice(0, 80)}…` : text,
  });
  pumpDanmakuQueue();
}

function scheduleDanmakuPump(delay) {
  if (danmaku.timer) return;
  danmaku.timer = window.setTimeout(() => {
    danmaku.timer = 0;
    pumpDanmakuQueue();
  }, Math.max(16, delay));
}

function pumpDanmakuQueue() {
  if (!danmaku.enabled || !danmaku.queue.length) return;
  resizeDanmakuTracks();
  const now = performance.now();
  const track = danmaku.busyUntil.findIndex((until) => until <= now);
  if (track < 0) {
    scheduleDanmakuPump(Math.min(...danmaku.busyUntil) - now);
    return;
  }
  launchDanmaku(danmaku.queue.shift(), track);
  if (danmaku.queue.length) pumpDanmakuQueue();
}

function launchDanmaku(data, track) {
  const info = roleInfo(data.role);
  const item = document.createElement("div");
  item.className = `danmaku-item${info.className ? ` ${info.className}` : ""}`;
  const name = profiles.get(data.username)?.nickname || data.nickname || data.username;
  item.textContent = `${info.icon}${name}：${data.text}`;
  item.style.top = `${danmaku.topReserved + track * danmaku.trackHeight}px`;
  elements.danmakuLayer.append(item);
  const itemWidth = item.offsetWidth;
  const totalDistance = elements.danmakuLayer.clientWidth + itemWidth + 30;
  const duration = totalDistance / danmaku.speed * 1000;
  const safeDelay = (itemWidth + danmaku.gap) / danmaku.speed * 1000;
  danmaku.busyUntil[track] = performance.now() + safeDelay;
  const animation = item.animate(
    [{ transform: "translate3d(0,0,0)" }, { transform: `translate3d(-${totalDistance}px,0,0)` }],
    { duration, easing: "linear" },
  );
  animation.addEventListener("finish", () => item.remove(), { once: true });
  scheduleDanmakuPump(safeDelay + 16);
}

function toggleFullscreen() {
  if (!document.fullscreenElement) elements.player.requestFullscreen?.();
  else document.exitFullscreen?.();
}

elements.loginButton.addEventListener("click", openLogin);
elements.playToggle.addEventListener("click", togglePlayback);
elements.muteToggle.addEventListener("click", toggleMute);
elements.volumeSlider.addEventListener("input", setVolume);
elements.streamVideo.addEventListener("play", updatePlaybackButton);
elements.streamVideo.addEventListener("playing", () => setPlayerMessage(""));
elements.streamVideo.addEventListener("pause", updatePlaybackButton);
elements.player.addEventListener("pointermove", showPlayerControls, { passive: true });
elements.player.addEventListener("pointerdown", showPlayerControls, { passive: true });
elements.player.addEventListener("keydown", showPlayerControls);
elements.player.addEventListener("focusin", showPlayerControls);
elements.player.addEventListener("focusout", showPlayerControls);
document.addEventListener("fullscreenchange", handleFullscreenChange);
elements.sendButton.addEventListener("click", sendMessage);
elements.danmakuToggle.addEventListener("click", toggleDanmaku);
elements.fullscreenButton.addEventListener("click", toggleFullscreen);
elements.authSubmit.addEventListener("click", submitAuth);
elements.authSwitch.addEventListener("click", () => setAuthMode(authMode === "login" ? "register" : "login"));
elements.userChip.addEventListener("click", () => setUserMenu(elements.userDropdown.hidden));
elements.onlineStat.addEventListener("click", toggleOnlinePopover);
elements.profileButton.addEventListener("click", openProfile);
elements.avatarButton.addEventListener("click", () => elements.avatarFile.click());
elements.avatarFile.addEventListener("change", () => {
  pickAvatar(elements.avatarFile.files[0]);
  elements.avatarFile.value = "";
});
elements.avatarReset.addEventListener("click", () => {
  pendingAvatar = "";
  renderAvatarPreview("");
});
elements.profileSubmit.addEventListener("click", submitProfile);
elements.nicknameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitProfile();
});
elements.inviteButton.addEventListener("click", openInviteModal);
elements.inviteCreate.addEventListener("click", createInvite);
elements.inviteClose.addEventListener("click", () => {
  elements.inviteModal.style.display = "none";
});
elements.deleteAccountButton.addEventListener("click", openDeleteModal);
elements.deleteSubmit.addEventListener("click", submitDeleteAccount);
elements.deletePassword.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitDeleteAccount();
});
elements.deleteCancel.addEventListener("click", () => {
  elements.deleteModal.style.display = "none";
});
elements.betStat.addEventListener("click", openBetModal);
elements.headerMenuButton.addEventListener("click", () => {
  setHeaderMenu(elements.headerMenu.classList.contains("open") === false);
});
elements.menuOnlineButton.addEventListener("click", () => {
  setHeaderMenu(false);
  toggleOnlinePopover();
});
elements.menuBetButton.addEventListener("click", () => {
  setHeaderMenu(false);
  openBetModal();
});
elements.betClose.addEventListener("click", () => {
  elements.betModal.style.display = "none";
  stopBetCountdown();
});
elements.betModal.addEventListener("click", (event) => {
  if (event.target === elements.betModal) {
    elements.betModal.style.display = "none";
    stopBetCountdown();
  }
});
elements.financeButton.addEventListener("click", openFinance);
elements.financeClose.addEventListener("click", () => {
  elements.financeModal.style.display = "none";
});
elements.financeModal.addEventListener("click", (event) => {
  if (event.target === elements.financeModal) elements.financeModal.style.display = "none";
});
elements.financeTabDetail.addEventListener("click", () => switchFinanceTab("detail"));
elements.financeTabTransfer.addEventListener("click", () => switchFinanceTab("transfer"));
elements.transferSubmit.addEventListener("click", submitTransfer);
elements.transferAmount.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitTransfer();
});
elements.logoutButton.addEventListener("click", () => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) send({ type: "logout", token });
  localStorage.removeItem(AUTH_TOKEN_KEY);
  setSignedIn(null);
});
document.addEventListener("click", (event) => {
  if (!elements.userArea.contains(event.target)) setUserMenu(false);
  if (!elements.leftHeader.contains(event.target)) {
    elements.onlinePopover.hidden = true;
    setHeaderMenu(false);
  }
});
elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) sendMessage();
});
elements.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitAuth();
});
elements.authModal.addEventListener("click", (event) => {
  if (event.target === elements.authModal) elements.authModal.style.display = "none";
});
elements.profileModal.addEventListener("click", (event) => {
  if (event.target === elements.profileModal) elements.profileModal.style.display = "none";
});
elements.deleteModal.addEventListener("click", (event) => {
  if (event.target === elements.deleteModal) elements.deleteModal.style.display = "none";
});
elements.inviteModal.addEventListener("click", (event) => {
  if (event.target === elements.inviteModal) elements.inviteModal.style.display = "none";
});
window.addEventListener("resize", resizeDanmakuTracks, { passive: true });
window.addEventListener("resize", syncChatHeight, { passive: true });
window.addEventListener("resize", syncComposerPosition, { passive: true });
window.addEventListener("beforeunload", () => {
  clearTimeout(streamReconnectTimer);
  clearTimeout(controlsHideTimer);
  chatHeightObserver?.disconnect();
  streamReader?.close();
});

updatePlaybackButton();
applySiteConfig();
updateMuteButton();
updateDanmakuButton();
updateFullscreenButton();
resizeDanmakuTracks();
syncComposerPosition();
chatHeightObserver = new ResizeObserver(syncChatHeight);
chatHeightObserver.observe(elements.player);
syncChatHeight();
setPlayerMessage(localStorage.getItem(AUTH_TOKEN_KEY) ? "正在连接直播…" : "登录后观看直播");
TransferSelect.init(
  {
    toggle: $("transferSelectToggle"),
    menu: $("transferSelectMenu"),
    list: $("transferSelectList"),
    hidden: $("transferTo"),
  },
  () => send({ type: "list_users" }),
  () => currentUser?.username,
);
connectChat();
