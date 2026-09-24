/* Shared waiting room: room details, a full-width player list and start controls. */

import { elements, formatCoins, formatCoinsWhole, isRoomOwner, ratingBadge, send, state, stopHallTicker } from "./core.js";
import { gameMetaById } from "./game-config.js";
import { gameView } from "./registry.js";
import { chatOpenButton, reapplySeatBubbles } from "./room-chat.js";
import { enableVoiceAudio, toggleMic, voiceMicWanted, voiceStatus } from "./room-voice.js";
import { getPeerVolume, micGateSettings, micLevel, micPipelineActive, setMicGateSettings,
  setPeerVolume } from "./voice-mic.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function playerRow() {
  const row = el("div", "waiting-seat");
  row.setAttribute("role", "listitem");
  const number = el("span", "waiting-seat-number");
  const person = el("div", "waiting-seat-person");
  const avatar = el("span", "waiting-seat-avatar");
  const identity = el("div", "waiting-seat-identity");
  const name = el("strong", "waiting-seat-name");
  const handle = el("span", "waiting-seat-handle");
  const badges = el("span", "waiting-seat-badges");
  identity.append(name, handle, badges);
  person.append(avatar, identity);
  const rating = el("div", "waiting-seat-rating");
  const stack = el("div", "waiting-seat-stack");
  stack.append(el("small", "", "筹码"), el("strong"));
  row.append(number, person, rating, stack);
  row.classList.add("waiting-seat-enter");
  window.setTimeout(() => row.classList.remove("waiting-seat-enter"), 420);
  return row;
}

function updatePlayerRow(row, player, index, room) {
  row.dataset.username = player.username;
  row.dataset.seatIndex = String(index);
  row.classList.toggle("is-me", player.username === state.currentUser?.username);
  row.classList.toggle("is-owner", player.username === room.owner);
  row.classList.toggle("has-no-chips", !(player.stack > 0));
  row.querySelector(".waiting-seat-number").textContent = String(index + 1).padStart(2, "0");
  const avatar = row.querySelector(".waiting-seat-avatar");
  if (player.avatar) {
    if (avatar.querySelector("img")?.getAttribute("src") !== player.avatar) {
      const image = document.createElement("img");
      image.src = player.avatar;
      image.alt = "";
      avatar.replaceChildren(image);
    }
  } else {
    const initial = (player.nickname || player.username || "?").slice(0, 1).toUpperCase();
    if (avatar.textContent !== initial || avatar.querySelector("img")) avatar.textContent = initial;
  }
  row.querySelector(".waiting-seat-name").textContent = player.nickname || player.username;
  const handle = row.querySelector(".waiting-seat-handle");
  handle.textContent = player.nickname && player.nickname !== player.username
    ? `@${player.username}` : "";
  handle.hidden = !handle.textContent;
  const badges = row.querySelector(".waiting-seat-badges");
  badges.replaceChildren();
  if (player.username === room.owner) badges.append(el("span", "waiting-owner-badge", "房主"));
  if (player.username === state.currentUser?.username) badges.append(el("span", "waiting-me-badge", "我"));
  if (room.game_type === "guandan") {
    badges.append(el("span", `waiting-team-badge team-${index % 2}`,
      index % 2 === 0 ? "A 队" : "B 队"));
  }
  if (!(player.stack > 0)) badges.append(el("span", "waiting-no-chips-badge", "筹码不足"));
  row.querySelector(".waiting-seat-rating").replaceChildren(ratingBadge(player.rating));
  row.querySelector(".waiting-seat-stack strong").textContent = formatCoins(player.stack);
}

function syncPlayers(list, room) {
  const existing = [...list.querySelectorAll(":scope > .waiting-seat")];
  const byName = new Map(existing.map((row) => [row.dataset.username, row]));
  const rows = room.players.map((player, index) => {
    const row = byName.get(player.username) || playerRow();
    updatePlayerRow(row, player, index, room);
    return row;
  });
  const keep = new Set(rows);
  for (const row of existing) if (!keep.has(row)) row.remove();
  rows.forEach((row, index) => {
    const at = list.children[index];
    if (at !== row) list.insertBefore(row, at || null);
  });
  reapplySeatBubbles();
}

function startRule(room, meta) {
  const count = room.players.filter((player) => player.stack > 0).length;
  const minimum = meta.minStartPlayers || 2;
  const exactFour = room.game_type === "guandan" || room.game_type === "mahjong";
  if (exactFour) {
    return { count, minimum: 4, canStart: count === 4,
      text: count === 4 ? "4 人已就位，可以开始" : "需要正好 4 名有筹码玩家" };
  }
  if (room.game_type === "werewolf") {
    const board = room.rules?.board;
    if (Array.isArray(board) && board.length) {
      const canStart = count === board.length && count >= minimum;
      return { count, minimum: board.length, canStart,
        text: canStart ? "自定义板子人数已满足，可以开始" : `自定义板子需要 ${board.length} 名有筹码玩家` };
    }
    const presets = [6, 8, 9, 10, 12];
    const canStart = presets.includes(count);
    return { count, minimum: presets.find((size) => size >= count) || 12, canStart,
      text: canStart ? "预设板子人数已满足，可以开始"
        : count < 6 ? "预设板子至少需要 6 名有筹码玩家"
          : "当前人数没有预设板子，需达到 6、8、9、10 或 12 人" };
  }
  return { count, minimum, canStart: count >= minimum,
    text: count >= minimum ? "人数已满足，可以开始" : `至少需要 ${minimum} 名有筹码玩家` };
}

function ruleItems(room) {
  const rules = room.rules || {};
  switch (room.game_type) {
    case "holdem": return [`小盲注 ${formatCoinsWhole(room.blind)}`,
      `大盲注 ${formatCoinsWhole(room.blind * 2)}`, "无限注 · 边池自动结算"];
    case "uno": return [`每张赔付 ${formatCoinsWhole(room.blind)}`, "经典规则", "先出完手牌获胜"];
    case "guandan": return [rules.wild === false ? "逢人配关闭" : "逢人配开启",
      `炸弹封顶 ${rules.bomb_cap ?? 8}`, rules.ace_strict === false ? "宽松过 A" : "严格过 A",
      "对家组队"];
    case "mahjong": return [`${rules.min_fan ?? 8} 番起和`,
      rules.flowers === false ? "花牌关闭" : "花牌开启",
      rules.chow === false ? "吃牌关闭" : "可以吃牌",
      rules.dianpao_full === false ? "点炮分别赔付" : "点炮包三家"];
    case "ludo": return [rules.launch === 5 ? "掷 5 或 6 起飞" : "掷 6 起飞",
      rules.extra_roll === false ? "掷 6 不连投" : "掷 6 连投",
      rules.jump4 === false ? "同色跳格关闭" : "同色跳格开启",
      rules.fly12 === false ? "飞行捷径关闭" : "飞行捷径开启",
      rules.payout === "rank" ? "按名次结算" : "冠军通吃"];
    case "liarsbar": return [`${rules.cards ?? 5} 张手牌`,
      `每次最多 ${rules.max_play ?? 3} 张`, `${rules.chambers ?? 6} 格弹巢`,
      rules.jokers === "none" ? "无小丑" : "小丑百搭",
      rules.respin ? "每次重转" : "概率递增",
      rules.payout === "rank" ? "按出局顺序结算" : "冠军通吃"];
    case "werewolf": {
      const board = Array.isArray(rules.board) && rules.board.length
        ? `自定义 ${rules.board.length} 人板子` : "预设 6 / 8 / 9 / 10 / 12 人板子";
      const roleNames = { werewolf: "狼人", seer: "预言家", witch: "女巫",
        hunter: "猎人", guard: "守卫", villager: "平民" };
      const roleCounts = new Map();
      for (const role of Array.isArray(rules.board) ? rules.board : []) {
        roleCounts.set(role, (roleCounts.get(role) || 0) + 1);
      }
      const lineup = [...roleCounts].map(([role, count]) => `${roleNames[role] || role} ×${count}`);
      return [board, ...lineup, rules.win_mode === "cheng" ? "屠城局" : "屠边局",
        rules.witch_self_save === "always" ? "女巫可自救" : rules.witch_self_save === "never"
          ? "女巫不可自救" : "女巫首夜可自救",
        `白天 ${rules.day_seconds ?? 120} 秒`, `投票 ${rules.vote_seconds ?? 30} 秒`,
        `夜晚 ${rules.night_seconds ?? 25} 秒`];
    }
    default: return [];
  }
}

function refreshWaitingVoice(root, room) {
  const panel = root.querySelector(".waiting-voice");
  panel.hidden = !gameMetaById(room.game_type)?.voice || !window.LIVE_CONFIG?.voice?.enabled;
  if (panel.hidden) return;
  const preview = Boolean(window.LIVE_CONFIG?.voice?.preview);
  const status = voiceStatus();
  const on = status.connected && status.canPublish && voiceMicWanted();
  const button = panel.querySelector(".waiting-voice-mic");
  button.disabled = preview || !status.connected || !status.canPublish;
  button.classList.toggle("on", on);
  button.textContent = on ? "🎙 关闭麦克风" : "🎙 开启麦克风";
  button.setAttribute("aria-pressed", String(on));
  panel.querySelector(".waiting-voice-status").textContent = preview
    ? "布局预览 · 麦克风未连接"
    : !status.connected ? "语音连接中，连接后可测试麦克风"
    : status.micError || (on ? "麦克风已开启，可以和房内玩家交谈" : "已连接 · 麦克风关闭");
  panel.querySelector(".waiting-voice-audio").hidden = !status.audioBlocked;
  refreshVoiceMeter(root, status);
  refreshVoiceGate(root, status);
  syncVoiceMixer(root, room);
}

// 电平条显示刻度：RMS × 200，实测语音（0.1–0.4）落在 20%–80%
const METER_SCALE = 200;
const METER_SILENT_RMS = 0.004;
const meterLoops = new WeakMap();

function refreshVoiceMeter(root, status) {
  const block = root.querySelector(".waiting-voice-meter");
  if (!block) return;
  block.hidden = !status.connected;
  if (block.hidden) return;
  if (meterLoops.has(root)) return;
  meterLoops.set(root, true);
  const fill = block.querySelector(".voice-meter-fill");
  const marker = block.querySelector(".voice-meter-gate");
  const note = block.querySelector(".voice-meter-note");
  let silentSince = 0;
  const frame = () => {
    if (!root.isConnected) {
      meterLoops.delete(root);
      return;
    }
    const active = voiceStatus().connected && voiceMicWanted();
    const level = active ? micLevel() : 0;
    fill.style.width = `${Math.min(100, Math.round(level * METER_SCALE))}%`;
    const settings = micGateSettings();
    marker.hidden = !settings.gateEnabled || !micPipelineActive();
    marker.style.left = `${Math.min(100, Math.round(settings.gateThreshold * METER_SCALE / 100))}%`;
    if (active && level < METER_SILENT_RMS) {
      silentSince = silentSince || performance.now();
      note.textContent = performance.now() - silentSince > 2000 ? "未检测到声音，请检查麦克风" : "";
    } else {
      silentSince = 0;
      note.textContent = "";
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function refreshVoiceGate(root, status) {
  const block = root.querySelector(".waiting-voice-gate");
  if (!block) return;
  block.hidden = !status.connected;
  if (block.hidden) return;
  const settings = micGateSettings();
  const toggle = block.querySelector(".voice-gate-toggle");
  const range = block.querySelector(".voice-gate-range");
  const value = block.querySelector(".voice-gate-value");
  toggle.checked = settings.gateEnabled;
  range.disabled = !micPipelineActive();
  if (range.value !== String(settings.gateThreshold)) range.value = String(settings.gateThreshold);
  value.textContent = `${settings.gateThreshold}%`;
  block.querySelector(".voice-gate-hint").hidden = micPipelineActive();
}

function voiceMixerRow() {
  const row = el("div", "voice-mixer-row");
  const name = el("span", "voice-mixer-name");
  const slider = el("input", "voice-mixer-range");
  slider.type = "range";
  slider.min = "0";
  slider.max = "100";
  slider.step = "5";
  const value = el("span", "voice-mixer-value");
  slider.addEventListener("input", () => {
    if (!row.dataset.voicePeer) return;
    const volume = setPeerVolume(row.dataset.voicePeer, Number(slider.value) || 0);
    value.textContent = `${volume}%`;
  });
  row.append(name, slider, value);
  return row;
}

function syncVoiceMixer(root, room) {
  const rows = root.querySelector(".voice-mixer-rows");
  if (!rows) return;
  const me = state.currentUser?.username;
  const others = (room.players || []).filter((player) => player.username && player.username !== me);
  const existing = new Map([...rows.querySelectorAll(".voice-mixer-row")]
    .map((row) => [row.dataset.voicePeer, row]));
  const keep = new Set();
  for (const player of others) {
    keep.add(player.username);
    let row = existing.get(player.username);
    if (!row) {
      row = voiceMixerRow();
      rows.append(row);
    }
    row.dataset.voicePeer = player.username;
    const label = player.nickname || player.username;
    row.querySelector(".voice-mixer-name").textContent = label;
    const slider = row.querySelector(".voice-mixer-range");
    const value = row.querySelector(".voice-mixer-value");
    const volume = getPeerVolume(player.username);
    if (slider.value !== String(volume)) slider.value = String(volume);
    if (value.textContent !== `${volume}%`) value.textContent = `${volume}%`;
    if (slider.getAttribute("aria-label") !== `${label} 音量`) {
      slider.setAttribute("aria-label", `${label} 音量`);
    }
  }
  for (const [username, row] of existing) {
    if (!keep.has(username)) row.remove();
  }
}

function refreshWaitingRoom(root) {
  const room = state.myRoom;
  if (!room) return;
  const meta = gameMetaById(room.game_type);
  const game = gameView(room.game_type);
  const capacity = meta.seats;
  const rule = startRule(room, meta);
  const openSeats = Math.max(0, capacity - room.players.length);
  root.dataset.game = room.game_type;
  root.querySelector(".waiting-room-game").textContent = `${meta.icon} ${meta.name}`;
  root.querySelector(".waiting-room-id").textContent = `房间 #${room.room_id}`;
  root.querySelector(".waiting-room-title").textContent = room.name;
  root.querySelector(".waiting-room-owner").textContent = `房主 ${room.owner_name || room.owner}`;
  root.querySelector(".waiting-room-desc").textContent = meta.desc;
  const facts = {
    players: `${room.players.length} / ${capacity}`,
    threshold: room.game_type === "werewolf"
      ? room.rules?.board?.length ? `${room.rules.board.length} 人板子`
        : "6 / 8 / 9 / 10 / 12 人"
      : room.game_type === "guandan" || room.game_type === "mahjong"
        ? "正好 4 人" : `至少 ${rule.minimum} 人`,
    stake: `${game?.stakeLabel || "底注"} ${formatCoinsWhole(room.blind)}`,
    buyin: `${formatCoins(room.buy_in)} 金币`,
  };
  for (const [key, value] of Object.entries(facts)) {
    root.querySelector(`.waiting-fact[data-key="${key}"] strong`).textContent = value;
  }
  root.querySelector(".waiting-room-status").textContent = rule.text;
  root.querySelector(".waiting-room-status").dataset.ready = String(rule.canStart);
  root.querySelector(".waiting-progress-fill").style.width = `${Math.min(100, rule.count / Math.max(1, rule.minimum) * 100)}%`;
  root.querySelector(".waiting-roster-count").textContent = `${room.players.length} 人已入座 · ${openSeats} 个空位`;
  const list = root.querySelector(".waiting-seats");
  syncPlayers(list, room);
  root.querySelector(".waiting-empty").hidden = !openSeats;
  root.querySelector(".waiting-empty").textContent = openSeats ? `＋ 还有 ${openSeats} 个空位，等待更多玩家加入` : "";
  root.querySelector(".waiting-rules-tags").replaceChildren(...ruleItems(room).map((text) => el("span", "waiting-rule-tag", text)));
  root.querySelector(".waiting-rules-note").textContent = game?.waitingHint || "等待房主开始游戏。";
  const start = root.querySelector(".waiting-start");
  start.hidden = !isRoomOwner();
  start.disabled = !rule.canStart;
  start.title = rule.canStart ? "开始游戏" : rule.text;
  root.querySelector(".waiting-room-hint").textContent = isRoomOwner()
    ? "人数和规则满足后，点击右侧按钮开局。"
    : "房主开始后自动进入对局。";
  refreshWaitingVoice(root, room);
}

function voicePanel() {
  const voice = el("div", "waiting-voice");
  const info = el("div", "waiting-voice-info");
  info.append(el("strong", "", "开局前语音调试"),
    el("span", "", "等待区可以自由聊天；开局后会自动切换到游戏语音频道。"));
  const actions = el("div", "waiting-voice-actions");
  const status = el("span", "waiting-voice-status");
  status.setAttribute("aria-live", "polite");
  const audio = el("button", "waiting-voice-audio", "🔊 开启声音");
  audio.type = "button";
  audio.addEventListener("click", () => { void enableVoiceAudio(); });
  const mic = el("button", "waiting-voice-mic");
  mic.type = "button";
  mic.addEventListener("click", () => { void toggleMic(); });
  actions.append(status, audio, mic);
  const meter = el("div", "waiting-voice-meter");
  meter.hidden = true;
  const meterHead = el("div", "voice-meter-head");
  meterHead.append(el("strong", "", "麦克风电平"), el("span", "voice-meter-note"));
  const bar = el("div", "voice-meter");
  bar.setAttribute("aria-hidden", "true");
  bar.append(el("span", "voice-meter-fill"), el("span", "voice-meter-gate"));
  meter.append(meterHead, bar);
  const gate = el("div", "waiting-voice-gate");
  gate.hidden = true;
  const gateLabel = el("label", "voice-gate-check");
  const gateToggle = el("input", "voice-gate-toggle");
  gateToggle.type = "checkbox";
  gateLabel.append(gateToggle, el("span", "", "低于阈值时自动静默（噪声门）"));
  const gateRange = el("input", "voice-gate-range");
  gateRange.type = "range";
  gateRange.min = "1";
  gateRange.max = "40";
  gateRange.step = "1";
  gateRange.setAttribute("aria-label", "噪声门阈值");
  const gateValue = el("span", "voice-gate-value");
  const gateHint = el("span", "voice-gate-hint", "重新开启麦克风后生效");
  gateToggle.addEventListener("change", () => {
    setMicGateSettings({ gateEnabled: gateToggle.checked });
  });
  gateRange.addEventListener("input", () => {
    const settings = setMicGateSettings({ gateThreshold: Number(gateRange.value) || 0 });
    gateValue.textContent = `${settings.gateThreshold}%`;
  });
  gate.append(gateLabel, gateRange, gateValue, gateHint);
  const mixer = el("div", "waiting-voice-mixer");
  const mixerHead = el("div", "waiting-voice-mixer-head");
  mixerHead.append(el("strong", "", "成员音量"),
    el("span", "waiting-voice-mixer-note", "只调自己听到的音量，下次进房自动沿用"));
  mixer.append(mixerHead, el("div", "voice-mixer-rows"));
  voice.append(info, actions, meter, gate, mixer);
  return voice;
}

export function renderRoomLobby() {
  stopHallTicker();
  const body = elements.gameMain;
  let root = body.querySelector(".waiting-room");
  if (!root) {
    body.replaceChildren();
    root = el("section", "waiting-room");
    const head = el("header", "waiting-room-head");
    const eyebrow = el("div", "waiting-room-eyebrow");
    eyebrow.append(el("span", "waiting-room-game"), el("span", "waiting-room-id"));
    head.append(eyebrow, el("h1", "waiting-room-title"),
      el("div", "waiting-room-owner"), el("p", "waiting-room-desc"));
    const overview = el("div", "waiting-overview");
    overview.setAttribute("aria-label", "房间概览");
    for (const [key, label] of [["players", "入座人数"], ["threshold", "开局条件"],
      ["stake", "本局计费"], ["buyin", "入场买入"]]) {
      const fact = el("div", "waiting-fact");
      fact.dataset.key = key;
      fact.append(el("span", "", label), el("strong"));
      overview.append(fact);
    }
    const controls = el("section", "waiting-room-controls");
    const readiness = el("div", "waiting-readiness");
    const status = el("div", "waiting-room-status");
    status.setAttribute("aria-live", "polite");
    const progress = el("div", "waiting-progress");
    progress.append(el("span", "waiting-progress-fill"));
    readiness.append(status, progress, el("div", "game-hint waiting-room-hint"));
    const start = el("button", "login-submit waiting-start", "开始游戏");
    start.type = "button";
    start.addEventListener("click", () => send({ type: "start_game" }));
    controls.append(readiness, start);
    const roster = el("section", "waiting-roster");
    const rosterHead = el("div", "waiting-section-head");
    rosterHead.append(el("h2", "", "入座玩家"), el("span", "waiting-roster-count"));
    const columns = el("div", "waiting-roster-columns");
    columns.setAttribute("aria-hidden", "true");
    for (const label of ["座位", "玩家", "段位", "当前筹码"]) columns.append(el("span", "", label));
    const players = el("div", "waiting-seats");
    players.setAttribute("role", "list");
    players.setAttribute("aria-label", "已入座玩家");
    roster.append(rosterHead, columns, players, el("div", "waiting-empty"));
    const rules = el("section", "waiting-rules");
    rules.append(el("h2", "", "本局规则"), el("div", "waiting-rules-tags"),
      el("p", "waiting-rules-note"));
    root.append(head, overview, controls, voicePanel(), roster, rules);
    body.append(root, chatOpenButton());
  }
  refreshWaitingRoom(root);
}
