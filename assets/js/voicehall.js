"use strict";

/* 语音聊天室视图：左栏麦克风调试面板与频道树（成员条目、按人音量齿轮、
   临时重命名），右栏分频道文字聊天；协议见 server/voice_hub.py。
   订阅在整个会话保持：离开视图不退订，大厅胶囊靠快照感知当前频道。 */

import { alertDialog } from "./dialog.js";
import { elements, formatClock, playerAvatarNode, renderGameView, send, state } from "./core.js";
import { onMessage, registerView } from "./registry.js";
import { attachPeerVolumeMenu } from "./peer-volume-menu.js";
import { voiceStatus } from "./room-voice.js";
import { mountVoiceControls } from "./voice-controls.js";

// 视图 DOM 引用：voice_hub_state 只重建频道树与头部，保住聊天输入焦点
let tree = null;
let headHint = null;
let chatHead = null;
let chatList = null;
let chatInput = null;
let micPanel = null;       // { root, controls }，voicestate 时刷新
let speakers = new Set();  // 最近一次 voicespeakers 名单，重建频道树后补高亮

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function channelName(channelId) {
  return state.voiceHub?.channels?.find((channel) => channel.id === channelId)?.name || channelId;
}

function voiceHintText() {
  if (!window.LIVE_CONFIG?.voice?.enabled) return "管理员未启用语音";
  return voiceStatus().connected ? "语音已连接" : "语音连接中…";
}

function channelRow(channel) {
  const current = channel.id === state.voiceHub.myChannel;
  const row = el("div", `voicehub-channel${current ? " is-current" : ""}`);
  row.dataset.channel = channel.id;
  const name = el("span", "voicehub-channel-name", channel.name);
  if (channel.custom) name.append(el("span", "voicehub-channel-mark", "✎"));
  row.append(name, el("span", "voicehub-channel-count", `${channel.members.length} 人`));
  if (!current) {
    row.addEventListener("click", () => send({ type: "voice_hub_join", channel: channel.id }));
    return row;
  }
  const leave = el("button", "voicehub-channel-leave", "离开频道");
  leave.type = "button";
  leave.addEventListener("click", (event) => {
    event.stopPropagation();
    send({ type: "voice_hub_leave" });
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
  state.voiceHub ||= { channels: [], myChannel: null, subscribed: false, joinedOnce: false, chat: [] };
  if (!state.voiceHub.subscribed) {
    state.voiceHub.subscribed = true;
    send({ type: "voice_hub_state" });
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
  head.append(back, el("h1", "voicehall-title", "语音聊天室"), headHint);

  const side = el("aside", "voicehall-side");
  const mic = el("div", "voicehall-mic");
  if (window.LIVE_CONFIG?.voice?.enabled) {
    // 复用等待页语音面板的 .waiting-voice 样式，挂载后立即同步连接状态
    mic.classList.add("waiting-voice");
    micPanel = { root: mic,
      controls: mountVoiceControls(mic, { note: "先试麦再开聊；进入游戏房间会自动切换到房间语音。" }) };
    micPanel.controls.refresh(voiceStatus());
  } else {
    micPanel = null;
    mic.append(el("div", "voicehall-voice-disabled", "管理员未启用语音，这里只提供频道文字聊天。"));
  }
  tree = el("div", "voicehall-channels");
  side.append(mic, tree);

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
}

document.addEventListener("voicestate", () => {
  if (headHint?.isConnected) headHint.textContent = voiceHintText();
  if (micPanel?.root.isConnected) micPanel.controls.refresh(voiceStatus());
});

// 说话指示：detail 为 identity（username）数组
document.addEventListener("voicespeakers", (event) => {
  speakers = new Set(event.detail || []);
  applySpeakers();
});

onMessage("voice_hub_state", (data) => {
  state.voiceHub ||= { channels: [], myChannel: null, subscribed: false, joinedOnce: false, chat: [] };
  const previousChannel = state.voiceHub.myChannel;
  state.voiceHub.channels = data.channels || [];
  state.voiceHub.myChannel = data.my_channel || null;
  // 换频道（含离开）先清空旧频道消息，加入后服务端会补发新频道历史
  if (previousChannel !== state.voiceHub.myChannel) state.voiceHub.chat = [];
  // 初次拿到快照还没进频道：整个会话自动加入一次默认频道
  if (!state.voiceHub.myChannel && !state.voiceHub.joinedOnce) {
    state.voiceHub.joinedOnce = true;
    send({ type: "voice_hub_join", channel: "default" });
  }
  renderChannels();
  renderChat();
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
  void alertDialog(data?.message || "语音聊天室出现问题", { title: "语音聊天室" });
});

registerView("voicehall", renderVoiceHall);
