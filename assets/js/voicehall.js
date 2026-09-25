"use strict";

/* 语音聊天室视图：左栏麦克风调试面板与频道树（成员条目、按人音量齿轮、
   临时重命名），右栏分频道文字聊天；协议见 server/voice_hub.py。
   订阅在整个会话保持：离开视图不退订，大厅胶囊靠快照感知当前频道。 */

import { alertDialog } from "./dialog.js";
import { elements, formatClock, playerAvatarNode, renderGameView, send, state } from "./core.js";
import { onMessage, registerView } from "./registry.js";
import { attachPeerVolumeMenu } from "./peer-volume-menu.js";
import { getPeerVolume, micGateOpen, micLevel, setPeerVolume } from "./voice-mic.js";
import { enableVoiceMic, leaveVoice, toggleMic, voiceMicPublishing, voiceMicWanted,
  voiceStatus } from "./room-voice.js";
import { mountVoiceControls } from "./voice-controls.js";

// 视图 DOM 引用：voice_hub_state 只重建频道树与头部，保住聊天输入焦点
let tree = null;
let headHint = null;
let retryButton = null;
let chatHead = null;
let chatList = null;
let chatInput = null;
let micPanel = null;       // { root, controls }，voicestate 时刷新
let speakers = new Set();  // 最近一次 voicespeakers 名单，重建频道树后补高亮
let authorizationTimer = 0;
const quick = {
  root: document.getElementById("voiceQuick"),
  toggle: document.getElementById("voiceQuickToggle"),
  menu: document.getElementById("voiceQuickMenu"),
  status: document.getElementById("voiceQuickStatus"),
  channel: document.getElementById("voiceQuickChannel"),
  join: document.getElementById("voiceQuickJoin"),
  leave: document.getElementById("voiceQuickLeave"),
  mic: document.getElementById("voiceQuickMic"),
  members: document.getElementById("voiceQuickMembers"),
  detail: document.getElementById("voiceQuickDetail"),
};
let quickSelection = "";
let glowTimer = 0;

function ensureHub() {
  state.voiceHub ||= { channels: [], myChannel: null, subscribed: false,
    joinedOnce: false, chat: [], restoreChannel: null,
    snapshotReceived: false, voiceEnabled: true, pendingChannel: undefined,
    authorizationTimedOut: false };
  return state.voiceHub;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function channelName(channelId) {
  return state.voiceHub?.channels?.find((channel) => channel.id === channelId)?.name || channelId;
}

function channelVoiceState(status = voiceStatus()) {
  const room = state.voiceHub?.myChannel ? `vh-${state.voiceHub.myChannel}` : "";
  return {
    connected: Boolean(room && status.connected && status.room === room),
    connecting: Boolean(room && status.connecting && status.connectingRoom === room),
    error: room && status.errorRoom === room ? status.connectError : "",
  };
}

function voiceHintText() {
  if (!window.LIVE_CONFIG?.voice?.enabled) return "管理员未启用语音";
  if (state.socket?.readyState !== WebSocket.OPEN) return "游戏厅重连中…";
  const hub = state.voiceHub;
  if (!hub?.snapshotReceived) return "正在获取频道状态…";
  if (hub.voiceEnabled === false) return "语音服务暂不可用";
  if (hub.pendingChannel === null) return "正在离开频道…";
  if (hub.pendingChannel) return hub.myChannel ? "正在切换频道…" : "正在加入频道…";
  if (!hub.myChannel) return "未加入语音频道";
  const status = voiceStatus();
  const channel = channelVoiceState(status);
  if (channel.connected) return status.audioBlocked ? "语音已连接 · 请启用声音" : "语音已连接";
  if (channel.connecting) return "语音连接中…";
  if (channel.error) return channel.error;
  if (hub.authorizationTimedOut) return "语音授权超时，请重试";
  if (status.connected || status.connecting) return "正在切换语音频道…";
  return "等待语音授权…";
}

function refreshConnectionUi() {
  const status = voiceStatus();
  const channel = channelVoiceState(status);
  const hub = state.voiceHub;
  const waitingForAuthorization = window.LIVE_CONFIG?.voice?.enabled
    && state.socket?.readyState === WebSocket.OPEN && hub?.snapshotReceived
    && hub.voiceEnabled !== false && hub.myChannel && hub.pendingChannel === undefined
    && !channel.connected && !channel.connecting && !channel.error;
  if (waitingForAuthorization && !hub.authorizationTimedOut && !authorizationTimer) {
    authorizationTimer = window.setTimeout(() => {
      authorizationTimer = 0;
      if (state.voiceHub === hub) {
        hub.authorizationTimedOut = true;
        refreshConnectionUi();
      }
    }, 12000);
  } else if (!waitingForAuthorization && authorizationTimer) {
    clearTimeout(authorizationTimer);
    authorizationTimer = 0;
  }
  if (channel.connected || channel.connecting) {
    if (hub) hub.authorizationTimedOut = false;
  }
  if (headHint?.isConnected) headHint.textContent = voiceHintText();
  if (retryButton?.isConnected) retryButton.hidden = !(channel.error || hub?.authorizationTimedOut)
    || channel.connected || channel.connecting || !hub?.myChannel || !hub.snapshotReceived
    || hub.pendingChannel !== undefined || hub.voiceEnabled === false
    || state.socket?.readyState !== WebSocket.OPEN;
  if (micPanel?.root.isConnected) micPanel.controls.refresh({
    ...status, connected: channel.connected, waitingText: voiceHintText(),
  });
  refreshQuickUi();
}

function requestHubState(force = false) {
  if (!state.voiceHub || (!force && state.voiceHub.subscribed)) return;
  if (send({ type: "voice_hub_state" })) {
    state.voiceHub.subscribed = true;
    if (force) {
      state.voiceHub.authorizationTimedOut = false;
      refreshConnectionUi();
    }
  }
}

function joinHubChannel(channel) {
  if (state.myRoom) return false;
  if (!send({ type: "voice_hub_join", channel })) return false;
  const hub = ensureHub();
  hub.joinedOnce = true;
  hub.pendingChannel = channel;
  if (window.LIVE_CONFIG?.voice?.enabled && hub.voiceEnabled !== false) {
    // 立即请求浏览器权限，真正发布要等新频道的 LiveKit 授权。
    void enableVoiceMic({ publishNow: false });
  }
  refreshConnectionUi();
  return true;
}

function closeQuick() {
  quick.menu.hidden = true;
  quick.toggle.setAttribute("aria-expanded", "false");
}

function refreshGlow() {
  const active = !quick.root.hidden && document.visibilityState === "visible"
    && channelVoiceState().connected && voiceMicPublishing() && micGateOpen();
  const strength = active ? Math.min(1, Math.max(0, (micLevel() - 0.008) * 14)) : 0;
  quick.toggle.classList.toggle("transmitting", strength > 0);
  quick.toggle.style.setProperty("--voice-glow", strength.toFixed(3));
  if (active && !glowTimer) glowTimer = window.setInterval(refreshGlow, 80);
  if (!active && glowTimer) {
    clearInterval(glowTimer);
    glowTimer = 0;
  }
}

function refreshQuickUi() {
  const hub = state.voiceHub;
  const signedIn = Boolean(state.currentUser);
  quick.root.hidden = !signedIn;
  if (!signedIn) {
    closeQuick();
    refreshGlow();
    return;
  }
  const voice = channelVoiceState();
  const status = voiceStatus();
  const micStatus = voice.connected && status.micError ? status.micError
    : voice.connected && voiceMicWanted() && !voiceMicPublishing() ? "麦克风开启中…" : "";
  quick.status.textContent = `${voiceHintText()}${micStatus ? ` · ${micStatus}` : ""}`;
  const channel = hub?.myChannel ? channelName(hub.myChannel) : "语音聊天室";
  quick.toggle.querySelector(".voice-quick-label").textContent = channel;
  quick.toggle.setAttribute("aria-label", `${channel}，${voiceHintText()}，打开快捷设置`);
  quick.toggle.title = `${channel} · ${voiceHintText()}`;
  const busy = hub?.pendingChannel !== undefined;
  const ready = hub?.snapshotReceived && state.socket?.readyState === WebSocket.OPEN;
  quick.channel.disabled = !ready || busy;
  quick.join.disabled = !ready || busy || !quickSelection || quickSelection === hub?.myChannel;
  quick.join.textContent = hub?.myChannel ? "切换频道" : "加入频道";
  quick.leave.hidden = !hub?.myChannel;
  quick.leave.disabled = !ready || busy;
  quick.mic.disabled = !voice.connected || !status.canPublish;
  quick.mic.textContent = voiceMicWanted() ? "关闭麦克风" : "开启麦克风";
  quick.mic.classList.toggle("is-on", voice.connected && voiceMicPublishing());
  refreshGlow();
}

function renderQuickChannels() {
  const hub = ensureHub();
  const valid = hub.channels.some((channel) => channel.id === quickSelection);
  if (!valid || (hub.myChannel && quick.menu.hidden)) quickSelection = hub.myChannel || hub.channels[0]?.id || "";
  quick.channel.replaceChildren(...hub.channels.map((channel) => {
    const option = el("option", "", `${channel.name} · ${channel.members.length} 人`);
    option.value = channel.id;
    return option;
  }));
  quick.channel.value = quickSelection;
  const current = hub.channels.find((channel) => channel.id === hub.myChannel);
  if (!current) {
    quick.members.replaceChildren(el("div", "voice-quick-empty", "加入频道后可调节成员音量"));
  } else {
    quick.members.replaceChildren(...current.members.map((member) => {
      const row = el("label", "voice-quick-member");
      row.dataset.voicePeer = member.username;
      const name = el("span", "", member.nickname || member.username);
      row.append(name);
      if (member.username === state.currentUser?.username) {
        row.append(el("span", "voice-quick-self", "自己"));
      } else {
        const range = el("input");
        range.type = "range";
        range.min = "0";
        range.max = "100";
        range.step = "5";
        range.value = String(getPeerVolume(member.username));
        range.setAttribute("aria-label", `${member.nickname || member.username} 音量`);
        const value = el("output", "", `${range.value}%`);
        range.addEventListener("input", () => {
          value.textContent = `${setPeerVolume(member.username, Number(range.value))}%`;
        });
        row.append(range, value);
      }
      return row;
    }));
  }
  refreshQuickUi();
}

quick.toggle.addEventListener("click", () => {
  quick.menu.hidden = !quick.menu.hidden;
  quick.toggle.setAttribute("aria-expanded", String(!quick.menu.hidden));
  if (!quick.menu.hidden) {
    requestHubState();
    renderQuickChannels();
  }
});
quick.channel.addEventListener("change", () => {
  quickSelection = quick.channel.value;
  refreshQuickUi();
});
quick.join.addEventListener("click", () => {
  if (quick.join.disabled || !quickSelection) return;
  joinHubChannel(quickSelection);
});
quick.leave.addEventListener("click", () => {
  if (quick.leave.disabled) return;
  if (send({ type: "voice_hub_leave" })) {
    state.voiceHub.restoreChannel = null;
    state.voiceHub.pendingChannel = null;
    void leaveVoice();
    refreshConnectionUi();
  }
});
quick.mic.addEventListener("click", () => { void toggleMic(); });
quick.detail.addEventListener("click", () => {
  closeQuick();
  state.hallPage = "voicehall";
  renderGameView();
});
document.addEventListener("click", (event) => {
  if (!quick.root.contains(event.target)) closeQuick();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !quick.menu.hidden) {
    closeQuick();
    quick.toggle.focus();
  }
});
document.addEventListener("visibilitychange", refreshGlow);
document.addEventListener("gameviewchange", () => {
  if (state.myRoom) closeQuick();
});

function channelRow(channel) {
  const current = channel.id === state.voiceHub.myChannel;
  const row = el("div", `voicehub-channel${current ? " is-current" : ""}`);
  row.dataset.channel = channel.id;
  const name = el("span", "voicehub-channel-name", channel.name);
  if (channel.custom) name.append(el("span", "voicehub-channel-mark", "✎"));
  row.append(name, el("span", "voicehub-channel-count", `${channel.members.length} 人`));
  if (!current) {
    row.addEventListener("click", () => {
      joinHubChannel(channel.id);
    });
    return row;
  }
  const leave = el("button", "voicehub-channel-leave", "离开频道");
  leave.type = "button";
  leave.addEventListener("click", (event) => {
    event.stopPropagation();
    if (send({ type: "voice_hub_leave" })) {
      state.voiceHub.restoreChannel = null;
      state.voiceHub.pendingChannel = null;
      void leaveVoice();
      refreshConnectionUi();
    }
  });
  row.append(leave);
  if (channel.id !== "default") {
    const rename = el("button", "voicehub-channel-rename", "✏️ 重命名");
    rename.type = "button";
    rename.addEventListener("click", (event) => {
      event.stopPropagation();
      openRenameInput(row);
    });
    row.append(rename);
  }
  return row;
}

// 行内展开的重命名输入：Enter 提交、Esc 取消，不用原生 prompt
function openRenameInput(row) {
  row.querySelector(".voicehub-rename-input")?.remove();
  const input = el("input", "voicehub-rename-input");
  input.type = "text";
  input.maxLength = 12;
  input.placeholder = "新频道名（1~12 个字）";
  input.setAttribute("aria-label", "新的频道名");
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") input.remove();
    if (event.key === "Enter" && !event.isComposing) {
      const name = input.value.trim();
      if (name) send({ type: "voice_hub_rename", name });
      input.remove();
    }
  });
  row.append(input);
  input.focus();
}

function memberRow(member) {
  const row = el("div", "voicehub-member");
  row.dataset.voicePeer = member.username;
  row.append(playerAvatarNode(member, "voicehub-member-avatar"),
    el("span", "voicehub-member-name", member.nickname || member.username));
  // 自己的音量不该在这里调，只有其他成员挂齿轮
  if (member.username !== state.currentUser?.username) {
    attachPeerVolumeMenu(row).update(member.username, true);
  }
  return row;
}

function applySpeakers() {
  for (const node of document.querySelectorAll(".voicehub-member[data-voice-peer]")) {
    node.classList.toggle("speaking", speakers.has(node.dataset.voicePeer));
  }
}

function renderChannels() {
  if (!tree?.isConnected) return;
  const frag = document.createDocumentFragment();
  for (const channel of state.voiceHub.channels) {
    frag.append(channelRow(channel));
    if (channel.id !== state.voiceHub.myChannel) continue;
    const members = el("div", "voicehub-members");
    members.setAttribute("role", "list");
    members.setAttribute("aria-label", `${channel.name} · 频道成员`);
    for (const member of channel.members) members.append(memberRow(member));
    frag.append(members);
  }
  tree.replaceChildren(frag);
  if (chatHead?.isConnected) {
    chatHead.replaceChildren(el("span", "voicehall-chat-title", "频道聊天"),
      el("span", "voicehall-chat-channel",
        state.voiceHub.myChannel ? channelName(state.voiceHub.myChannel) : "未加入频道"));
  }
  applySpeakers();
}

function chatRow(message) {
  const row = el("div", "rc-message");
  row.append(el("span", "rc-name", message.nickname || message.username),
    el("span", "rc-time", message.time ? formatClock(message.time) : ""),
    el("div", "rc-text", message.text));
  return row;
}

function renderChat() {
  if (!chatList?.isConnected) return;
  if (!state.voiceHub.myChannel) {
    chatList.replaceChildren(el("div", "rc-message rc-system", "尚未加入频道，先在左侧选一个频道再开聊。"));
    return;
  }
  if (!state.voiceHub.chat.length) {
    chatList.replaceChildren(el("div", "rc-message rc-system", "频道里还没有消息，说点什么打个招呼吧。"));
    return;
  }
  chatList.replaceChildren(...state.voiceHub.chat.map(chatRow));
  chatList.scrollTop = chatList.scrollHeight;
}

function sendChat() {
  if (!chatInput?.isConnected) return;
  const text = chatInput.value.trim();
  if (!text || text.length > 200) return;
  if (send({ type: "voice_hub_chat", text })) chatInput.value = "";
}

function renderVoiceHall() {
  const hub = ensureHub();
  requestHubState();
  if (hub.snapshotReceived && !hub.myChannel && !hub.joinedOnce && hub.pendingChannel === undefined) {
    joinHubChannel("default");
  }
  const root = el("section", "voicehall");
  const head = el("header", "voicehall-head");
  const back = el("button", "online-stat hall-back", "← 游戏厅");
  back.type = "button";
  back.addEventListener("click", () => {
    state.hallPage = null;
    renderGameView();
  });
  headHint = el("span", "voicehall-hint", voiceHintText());
  headHint.setAttribute("role", "status");
  retryButton = el("button", "voicehall-retry", "重试连接");
  retryButton.type = "button";
  retryButton.hidden = true;
  retryButton.addEventListener("click", () => requestHubState(true));
  head.append(back, el("h1", "voicehall-title", "语音聊天室"), headHint, retryButton);

  const side = el("aside", "voicehall-side");
  const mic = el("div", "voicehall-mic");
  if (window.LIVE_CONFIG?.voice?.enabled) {
    // 复用等待页语音面板的 .waiting-voice 样式，挂载后立即同步连接状态
    mic.classList.add("waiting-voice");
    micPanel = { root: mic,
      controls: mountVoiceControls(mic, { title: "语音设置",
        note: "加入频道后自动开麦；进入游戏房间会自动切换到房间语音。" }) };
    micPanel.controls.refresh({ ...voiceStatus(),
      connected: channelVoiceState().connected, waitingText: voiceHintText() });
  } else {
    micPanel = null;
    mic.append(el("div", "voicehall-voice-disabled", "管理员未启用语音，这里只提供频道文字聊天。"));
  }
  tree = el("div", "voicehall-channels");
  const channelHeader = el("div", "voicehall-section-heading");
  channelHeader.append(el("strong", "", "语音频道"),
    el("span", "", "选择频道后即可加入"));
  side.append(mic, channelHeader, tree);

  chatHead = el("header", "voicehall-chat-head");
  chatList = el("div", "room-chat-list voicehall-chat-list");
  chatList.setAttribute("role", "log");
  chatList.setAttribute("aria-label", "频道聊天消息");
  const composer = el("div", "room-chat-composer voicehall-composer");
  chatInput = el("input", "chat-input");
  chatInput.maxLength = 200;
  chatInput.placeholder = "说点什么…";
  chatInput.autocomplete = "off";
  chatInput.setAttribute("aria-label", "发送频道消息");
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) sendChat();
  });
  const sendButton = el("button", "send-button", "发送");
  sendButton.type = "button";
  sendButton.addEventListener("click", sendChat);
  composer.append(chatInput, sendButton);
  const chat = el("section", "voicehall-chat");
  chat.append(chatHead, chatList, composer);

  const body = el("div", "voicehall-body");
  body.append(side, chat);
  root.append(head, body);
  elements.gameMain.replaceChildren(root);
  renderChannels();
  renderChat();
  refreshConnectionUi();
}

document.addEventListener("voicestate", () => {
  refreshConnectionUi();
});

document.addEventListener("gamesocketclose", () => {
  if (!state.voiceHub) return;
  state.voiceHub.restoreChannel = state.voiceHub.myChannel;
  state.voiceHub.subscribed = false;
  state.voiceHub.snapshotReceived = false;
  state.voiceHub.pendingChannel = undefined;
  state.voiceHub.authorizationTimedOut = false;
  refreshConnectionUi();
});

document.addEventListener("authstatechange", (event) => {
  if (!event.detail?.user) {
    state.voiceHub = null;
    quickSelection = "";
    refreshQuickUi();
    return;
  }
  ensureHub();
  requestHubState();
  renderQuickChannels();
});

// 说话指示：detail 为 identity（username）数组
document.addEventListener("voicespeakers", (event) => {
  speakers = new Set(event.detail || []);
  applySpeakers();
});

onMessage("voice_hub_state", (data) => {
  ensureHub();
  const previousChannel = state.voiceHub.myChannel;
  state.voiceHub.snapshotReceived = true;
  state.voiceHub.voiceEnabled = data.voice_enabled !== false;
  state.voiceHub.channels = data.channels || [];
  state.voiceHub.myChannel = data.my_channel || null;
  if (state.voiceHub.myChannel === state.voiceHub.pendingChannel) {
    state.voiceHub.pendingChannel = undefined;
  }
  // 换频道（含离开）先清空旧频道消息，加入后服务端会补发新频道历史
  if (previousChannel !== state.voiceHub.myChannel) {
    clearTimeout(authorizationTimer);
    authorizationTimer = 0;
    state.voiceHub.chat = [];
    state.voiceHub.authorizationTimedOut = false;
  }
  // 初次拿到快照还没进频道：整个会话自动加入一次默认频道
  if (!state.myRoom && !state.voiceHub.myChannel && state.voiceHub.restoreChannel) {
    const channel = state.voiceHub.restoreChannel;
    state.voiceHub.restoreChannel = null;
    joinHubChannel(channel);
  } else if (!state.myRoom && !state.voiceHub.myChannel && !state.voiceHub.joinedOnce
    && state.hallPage === "voicehall" && state.voiceHub.pendingChannel === undefined) {
    joinHubChannel("default");
  } else if (previousChannel !== state.voiceHub.myChannel && state.voiceHub.myChannel
    && window.LIVE_CONFIG?.voice?.enabled && state.voiceHub.voiceEnabled !== false) {
    // 断线恢复或另一个入口加入时，也保持“加入后默认开麦”。
    void enableVoiceMic({ publishNow: channelVoiceState().connected });
  }
  renderChannels();
  renderChat();
  renderQuickChannels();
  refreshConnectionUi();
});

onMessage("voice_hub_chat", (data) => {
  if (data.channel !== state.voiceHub?.myChannel) return;
  state.voiceHub.chat.push(data);
  if (state.voiceHub.chat.length > 60) state.voiceHub.chat.shift();
  renderChat();
});

onMessage("voice_hub_chat_history", (data) => {
  if (data.channel !== state.voiceHub?.myChannel) return;
  state.voiceHub.chat = data.messages || [];
  renderChat();
});

onMessage("voice_hub_error", (data) => {
  if (state.voiceHub) state.voiceHub.pendingChannel = undefined;
  refreshConnectionUi();
  void alertDialog(data?.message || "语音聊天室出现问题", { title: "语音聊天室" });
});

registerView("voicehall", renderVoiceHall);
refreshQuickUi();
