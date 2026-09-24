/* 狼人杀：月夜氛围桌面、身份卡与阶段横幅分区展示。
   座位即状态板：生死、角色可见性、得票数一眼可读；私有信息
   （角色/查验/药水）只来自服务器按用户投影的视图，前端不做推导。
   夜晚行动里狼人可改票直到全员提交，其余单角色一锤定音。 */

import {
  displayNameOf, elements, formatCoins, formatCoinsWhole, playerAvatarNode,
  renderGameView, selfUsername, send, startHallTicker, state,
} from "../core.js";
import { registerGame } from "../registry.js";
import { openChatOverlay, reapplySeatBubbles } from "../room-chat.js";
import { enableVoiceAudio, toggleMic, voiceMicWanted, voiceStatus } from "../room-voice.js";

const ROLE_META = {
  狼人: { icon: "🐺", cls: "wolf" },
  预言家: { icon: "🔮", cls: "god" },
  女巫: { icon: "⚗️", cls: "god" },
  猎人: { icon: "🏹", cls: "god" },
  守卫: { icon: "🛡️", cls: "god" },
  平民: { icon: "👤", cls: "civil" },
};
const FACTION_CLS = { wolf: "wolf", god: "god", civilian: "civil" };

let actionLock = false;
let lastRoomView = null;
let phaseDeadline = 0;
let pick = null;          // 女巫/投票的本地选择 {save:null,poison:null} 或 {vote:null}
let speakingSet = new Set();   // 正在说话的用户名（voicespeakers 事件驱动）

const VOICE_ENABLED = Boolean(window.LIVE_CONFIG?.voice?.enabled);
const desktopLayout = window.matchMedia("(min-width: 1024px)");

function refreshPhaseClock() {
  const timer = document.querySelector("#gameMain .ww-phase-timer");
  if (timer) timer.textContent = `${Math.ceil(Math.max(0, phaseDeadline - Date.now()) / 1000)} 秒`;
}
window.setInterval(refreshPhaseClock, 1000);

document.addEventListener("voicespeakers", (event) => {
  if (state.myRoom?.game_type !== "werewolf") return;
  speakingSet = new Set(event.detail || []);
  document.querySelectorAll("#gameMain .ww-seat").forEach((seat) => {
    seat.classList.toggle("speaking", speakingSet.has(seat.dataset.username));
  });
});
document.addEventListener("voicestate", () => {
  if (state.myRoom?.game_type === "werewolf") renderGameView();
});
desktopLayout.addEventListener("change", () => {
  if (state.myRoom?.game_type === "werewolf") renderGameView();
});

function wwAct(payload) {
  if (actionLock || state.myRoom?.spectator) return;
  if (send({ type: "poker_action", ...payload })) {
    actionLock = true;
    document.querySelectorAll(".ww-dock button").forEach((b) => { b.disabled = true; });
  }
}

document.addEventListener("gameactionerror", () => {
  if (state.myRoom?.game_type !== "werewolf") return;
  actionLock = false;
  renderGameView();
});

function isMe(name) {
  return Boolean(name) && name === selfUsername() && !state.myRoom?.spectator;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function roleChip(roleName, extra = "") {
  const meta = ROLE_META[roleName] || { icon: "❔", cls: "civil" };
  return el("span", `ww-role-chip r-${meta.cls}${extra ? ` ${extra}` : ""}`,
    `${meta.icon} ${roleName}`);
}

function alivePlayers(room) {
  return (room.players || []).filter((p) => p.alive);
}

/* =========================================================
   阶段横幅与文案
========================================================= */

function phaseBanner(room) {
  const dayNo = room.day_no || 0;
  const box = el("div", `ww-phase is-${room.phase}`);
  const icon = el("span", "ww-phase-icon");
  const label = el("span", "ww-phase-label");
  if (room.phase === "night") {
    icon.textContent = "🌙";
    label.textContent = `第 ${dayNo} 夜 · ${room.night_role ? `${room.night_role}请行动` : "天黑请闭眼"}`;
  } else if (room.phase === "day") {
    icon.textContent = "☀️";
    label.textContent = `第 ${dayNo} 天 · 自由讨论`;
  } else if (room.phase === "vote") {
    icon.textContent = "🗳️";
    label.textContent = `第 ${dayNo} 天 · ${room.vote_round > 1 ? "平票重投" : "投票放逐"}`;
  } else if (room.phase === "last_words") {
    icon.textContent = "🕯️";
    label.textContent = `遗言 · ${room.last_words_current || ""}`;
  } else if (room.phase === "shot") {
    icon.textContent = "🏹";
    label.textContent = "猎人开枪";
  } else {
    icon.textContent = "⚖️";
    label.textContent = "对局结束 · 等待结算投票";
  }
  const alive = el("span", "ww-phase-alive",
    `存活 ${alivePlayers(room).length}/${(room.players || []).length}`);
  box.append(icon, label);
  if (room.turn_left > 0) {
    box.append(el("span", "ww-phase-timer",
      `${Math.ceil(Math.max(0, phaseDeadline - Date.now()) / 1000)} 秒`));
  }
  box.append(alive);
  return box;
}

function phaseHintText(room) {
  const me = selfUsername();
  const options = room.your_options;
  if (room.paused) return "对局已暂停，等待房主继续";
  if (room.phase === "showdown" || room.settlement) return "本局已结束，等待下一局投票";
  if (room.phase === "night") {
    if (options) return options.kind === "witch" ? "轮到你 · 女巫的药水" : `轮到你 · ${options.role}行动`;
    return room.night_role ? `${room.night_role}正在行动，请闭眼等待…` : "天黑请闭眼…";
  }
  if (room.phase === "day") return "自由讨论中 · 观点说完等待投票";
  if (room.phase === "vote") {
    if (options?.voted) return "已投票 · 等待其他人（投票公开可见）";
    return "请选择放逐对象";
  }
  if (room.phase === "shot") {
    if (options) return "你已亮出猎枪 · 选择带走一人";
    return "猎人正在决定是否开枪…";
  }
  if (room.phase === "last_words") {
    return room.last_words_current && isMe(room.last_words_current)
      ? "轮到你发表遗言 · 打开聊天说出判断"
      : `等待 ${room.last_words_current ? displayNameOf(room.last_words_current) : ""} 发表遗言…`;
  }
  return "";
}

/* =========================================================
   座位：生死 / 角色可见性 / 得票 / 狼队标记
========================================================= */

function seatNode(p, index) {
  const room = state.myRoom;
  const me = selfUsername();
  const seat = el("div", "ww-seat");
  seat.dataset.seat = String(index);
  seat.dataset.username = p.username;
  if (!p.alive) seat.classList.add("dead");
  if (isMe(p.username)) seat.classList.add("me");
  if (speakingSet.has(p.username)) seat.classList.add("speaking");
  const myRole = room.your_role;
  if (myRole?.faction === "wolf"
    && myRole.teammates.some((t) => t.username === p.username)) {
    seat.classList.add("mate");
  }
  if (room.phase === "vote" && room.vote?.[me] === p.username) {
    seat.classList.add("voted-target");
  }

  const avatar = playerAvatarNode(p, "ww-avatar");
  const identity = el("div", "ww-seat-identity");
  const name = el("div", "ww-seat-name",
    isMe(p.username) ? "我" : (p.nickname || displayNameOf(p.username)));
  name.title = p.nickname || displayNameOf(p.username);
  identity.append(name);

  const statusText = !p.alive ? "出局"
    : room.phase === "vote" && room.vote?.[p.username] ? "已投票"
    : room.phase === "night" && room.night_role && p.username === me
    && room.your_options ? "行动中"
    : "存活";
  identity.append(el("div", `ww-seat-status${p.alive ? "" : " out"}`, statusText));

  const foot = el("div", "ww-seat-foot");
  if (p.role) foot.append(roleChip(p.role, p.alive ? "alive-chip" : ""));
  else foot.append(el("span", "ww-seat-unknown", p.alive ? "身份未知" : "未翻牌"));
  if (myRole?.faction === "wolf" && isMe(p.username)) {
    foot.append(el("span", "ww-mate-note", "狼队视角"));
  }
  const stack = el("span", "ww-seat-stack", formatCoinsWhole(p.stack));
  stack.title = `当前筹码 ${formatCoins(p.stack)}`;
  foot.append(stack);

  seat.append(avatar, identity, foot);

  if (room.phase === "vote") {
    const count = Object.values(room.vote || {})
      .filter((target) => target === p.username).length;
    if (count) seat.append(el("span", "ww-vote-badge", `${count} 票`));
  }
  return seat;
}

/* =========================================================
   侧栏：身份卡 + 事件历史
========================================================= */

function roleCardNode(room) {
  const role = room.your_role;
  const card = el("section", "ww-role-card");
  if (!role) {
    card.append(el("div", "ww-role-spectator", "👀 观战视角 · 身份保密中"));
    return card;
  }
  const meta = ROLE_META[role.name] || { icon: "❔", cls: "civil" };
  card.classList.add(`f-${FACTION_CLS[role.faction] || "civil"}`);
  if (!alivePlayers(room).some((p) => isMe(p.username))) {
    card.classList.add("is-out");
  }
  const head = el("div", "ww-role-head");
  head.append(el("span", "ww-role-icon", meta.icon));
  const title = el("div", "ww-role-title");
  title.append(el("div", "ww-role-name", role.name),
    el("div", "ww-role-faction", role.faction_name));
  head.append(title);
  card.append(head);

  if (role.faction === "wolf") {
    const mates = el("div", "ww-role-extra");
    mates.append(el("div", "ww-role-extra-label",
      role.teammates.length ? "你的狼队" : "你是唯一的狼"));
    for (const mate of role.teammates) {
      mates.append(el("div", "ww-mate-row", `🐺 ${mate.nickname || displayNameOf(mate.username)}`));
    }
    card.append(mates);
  }
  if (role.key === "seer") {
    const log = el("div", "ww-role-extra");
    log.append(el("div", "ww-role-extra-label", "查验记录"));
    const rows = room.check_log || [];
    if (!rows.length) log.append(el("div", "ww-log-empty", "还没有查验过"));
    for (const row of rows) {
      log.append(el("div", "ww-check-row",
        `第${row.day}夜 · ${row.target_name} → ${row.is_wolf ? "🐺 狼人" : "✅ 好人"}`));
    }
    card.append(log);
  }
  if (role.key === "witch") {
    const stock = room.witch_stock || {};
    const potions = el("div", "ww-potions");
    potions.append(el("div", "ww-role-extra-label", "药水"));
    potions.append(el("span", `ww-potion${stock.save ? "" : " used"}`,
      `💊 解药 ${stock.save ? "可用" : "已用"}`));
    potions.append(el("span", `ww-potion${stock.poison ? "" : " used"}`,
      `☠️ 毒药 ${stock.poison ? "可用" : "已用"}`));
    card.append(potions);
  }
  if (role.key === "guard") {
    const guard = el("div", "ww-role-extra");
    guard.append(el("div", "ww-role-extra-label", "昨晚守护"));
    guard.append(el("div", "ww-log-empty",
      room.last_protect ? displayNameOf(room.last_protect) : "无记录"));
    card.append(guard);
  }
  if (card.classList.contains("is-out")) {
    card.append(el("div", "ww-role-out-note", "你已出局 · 可在死者频道交流，观战至终局"));
  }
  return card;
}

function historyNode(room) {
  const panel = el("section", "ww-history");
  panel.append(el("div", "ww-history-head", "对局记录"));
  const list = el("div", "ww-history-list");
  const rows = (room.history || []).slice(-24).reverse();
  if (!rows.length) list.append(el("div", "ww-log-empty", "等待开局…"));
  for (const item of rows) {
    const row = el("div", `ww-history-row k-${item.kind || "system"}`);
    row.append(el("span", "ww-history-day", `${item.day}d`),
      el("span", "ww-history-text", item.text));
    list.append(row);
  }
  panel.append(list);
  return panel;
}

/* =========================================================
   行动区：目标选择 / 女巫两段决策 / 投票 / 开枪
========================================================= */

function targetButton(p, selected, onClick, disabled) {
  const chip = el("button", `ww-target${selected ? " selected" : ""}`);
  chip.type = "button";
  chip.disabled = Boolean(disabled);
  chip.setAttribute("aria-pressed", String(Boolean(selected)));
  chip.dataset.username = p.username;
  const dot = playerAvatarNode(p, "ww-target-avatar");
  const label = el("span", "ww-target-name",
    isMe(p.username) ? "我" : (p.nickname || displayNameOf(p.username)));
  chip.append(dot, label);
  if (onClick) chip.addEventListener("click", onClick);
  return chip;
}

function noteNode(text) {
  return el("div", "ww-action-note", text);
}

function targetLabel(options, username) {
  const row = (options.targets || []).find((t) => t.username === username);
  const name = row ? (row.nickname || displayNameOf(username)) : displayNameOf(username);
  return isMe(username) ? "我" : name;
}

function nightActionNode(room, options) {
  const wrap = el("div", "ww-action-block");
  if (options.role === "狼人") {
    wrap.append(el("div", "ww-action-title",
      options.current ? `当前刀口：${targetLabel(options, options.current)}` : "选择今晚的刀口"));
    const grid = el("div", "ww-targets");
    for (const target of options.targets) {
      grid.append(targetButton(target, options.current === target.username,
        () => wwAct({ action: "night", target: target.username }),
        actionLock));
    }
    wrap.append(grid);
    const skip = el("button", "ww-action secondary");
    skip.type = "button";
    skip.textContent = options.current ? "改为空刀" : "今晚空刀";
    skip.disabled = actionLock;
    skip.addEventListener("click", () => wwAct({ action: "night", target: "" }));
    wrap.append(skip, noteNode("狼人行动以最后一次提交为准，全员提交或超时后生效。"));
    return wrap;
  }
  const titles = { 预言家: "选择要查验的人", 守卫: "选择今晚守护的人" };
  wrap.append(el("div", "ww-action-title", titles[options.role] || "选择目标"));
  const grid = el("div", "ww-targets");
  for (const target of options.targets) {
    grid.append(targetButton(target, false,
      () => wwAct({ action: "night", target: target.username }), actionLock));
  }
  wrap.append(grid);
  if (options.role === "守卫") {
    const skip = el("button", "ww-action secondary");
    skip.type = "button";
    skip.textContent = "今晚不守";
    skip.disabled = actionLock;
    skip.addEventListener("click", () => wwAct({ action: "night", target: "" }));
    wrap.append(skip);
  } else {
    const skip = el("button", "ww-action secondary");
    skip.type = "button";
    skip.textContent = "今晚不验";
    skip.disabled = actionLock;
    skip.addEventListener("click", () => wwAct({ action: "night", target: "" }));
    wrap.append(skip);
  }
  if (options.role === "守卫" && options.forbidden) {
    wrap.append(noteNode(`连守禁制：今晚不能继续守 ${displayNameOf(options.forbidden)}`));
  }
  return wrap;
}

function witchActionNode(room, options) {
  const wrap = el("div", "ww-action-block");
  if (!pick || pick.kind !== "witch") {
    pick = { kind: "witch", save: null, poison: null };
  }
  const killed = options.kill_target;
  wrap.append(el("div", "ww-action-title", killed
    ? `今晚 ${killed.nickname || displayNameOf(killed.username)} 被杀`
    : "今晚是空刀，无人被杀"));

  const saveRow = el("div", "ww-potion-row");
  saveRow.append(el("span", "ww-potion-row-label",
    `💊 解药${options.can_save ? "" : options.kill_target ? "（不可用）" : "（今晚无人被杀）"}`));
  const saveYes = el("button", `ww-action small${pick.save === true ? " primary" : ""}`);
  const saveNo = el("button", `ww-action small${pick.save === false ? " primary" : ""}`);
  saveYes.type = saveNo.type = "button";
  saveYes.textContent = "使用解药";
  saveNo.textContent = "不使用";
  saveYes.disabled = !options.can_save || actionLock;
  saveNo.disabled = actionLock;
  saveYes.addEventListener("click", () => { pick.save = true; renderGameView(); });
  saveNo.addEventListener("click", () => { pick.save = false; renderGameView(); });
  saveRow.append(saveYes, saveNo);
  wrap.append(saveRow);

  const poisonRow = el("div", "ww-potion-row");
  poisonRow.append(el("span", "ww-potion-row-label",
    `☠️ 毒药${options.can_poison ? "" : "（已用完）"}`));
  const poisonNo = el("button", `ww-action small${pick.poison ? "" : " primary"}`);
  poisonNo.type = "button";
  poisonNo.textContent = "不使用";
  poisonNo.disabled = !options.can_poison || actionLock;
  poisonNo.addEventListener("click", () => { pick.poison = null; renderGameView(); });
  poisonRow.append(poisonNo);
  wrap.append(poisonRow);

  if (options.can_poison && pick.poison) {
    const grid = el("div", "ww-targets");
    for (const target of options.poison_targets) {
      grid.append(targetButton(target, pick.poison === target.username,
        () => { pick.poison = target.username; renderGameView(); }, actionLock));
    }
    wrap.append(grid);
  } else if (options.can_poison) {
    const use = el("button", "ww-action secondary");
    use.type = "button";
    use.textContent = "使用毒药 · 选择目标";
    use.disabled = actionLock;
    use.addEventListener("click", () => {
      pick.poison = room.players.find((p) => p.alive
        && !isMe(p.username))?.username || null;
      renderGameView();
    });
    wrap.append(use);
  }
  if (pick.poison) {
    wrap.append(noteNode(`毒药将对 ${displayNameOf(pick.poison)} 使用，被毒者天亮公布且猎人无法开枪。`));
  }

  const confirm = el("button", "ww-action primary");
  confirm.type = "button";
  confirm.textContent = "确认女巫决策";
  confirm.disabled = pick.save === null || actionLock;
  confirm.addEventListener("click", () => {
    wwAct({ action: "witch", save: pick.save === true, poison: pick.poison });
  });
  wrap.append(confirm, noteNode("救与毒同时结算：解药抵刀口，毒药无视守护与解药。"));
  return wrap;
}

function voteActionNode(room, options) {
  const wrap = el("div", "ww-action-block");
  wrap.append(el("div", "ww-action-title",
    room.vote_round > 1 ? "平票重投 · 只能在候选人中选择" : "选择要放逐的人"));
  if (!pick || pick.kind !== "vote") pick = { kind: "vote", vote: options.voted || null };
  if (pick.vote && !options.targets.some((t) => t.username === pick.vote)) {
    pick.vote = null;
  }
  const grid = el("div", "ww-targets");
  for (const target of options.targets) {
    grid.append(targetButton(target, pick.vote === target.username,
      () => { pick.vote = target.username; renderGameView(); }, actionLock));
  }
  const confirm = el("button", "ww-action primary");
  confirm.type = "button";
  confirm.textContent = `投出 ${pick.vote ? targetLabel(options, pick.vote) : "…"}${
    options.voted && options.voted !== pick.vote ? "（改票）" : ""}`;
  confirm.disabled = !pick.vote || actionLock;
  confirm.addEventListener("click", () => {
    wwAct({ action: "vote", target: pick.vote });
  });
  wrap.append(grid, confirm, noteNode("投票公开可见；全员投完或超时后开票。"));
  return wrap;
}

function shotActionNode(room, options) {
  const wrap = el("div", "ww-action-block");
  if (!pick || pick.kind !== "shot") pick = { kind: "shot", target: null };
  wrap.append(el("div", "ww-action-title", "选择要带走的人"));
  const grid = el("div", "ww-targets");
  for (const target of options.targets) {
    grid.append(targetButton(target, pick.target === target.username,
      () => { pick.target = target.username; renderGameView(); }, actionLock));
  }
  const confirm = el("button", "ww-action danger");
  confirm.type = "button";
  confirm.textContent = `开枪带走 ${pick.target ? displayNameOf(pick.target) : "…"}`;
  confirm.disabled = !pick.target || actionLock;
  confirm.addEventListener("click", () => wwAct({ action: "shoot", target: pick.target }));
  const skip = el("button", "ww-action secondary");
  skip.type = "button";
  skip.textContent = "放弃开枪";
  skip.disabled = actionLock;
  skip.addEventListener("click", () => wwAct({ action: "shoot", target: "" }));
  wrap.append(grid, confirm, skip);
  wrap.append(noteNode("开枪不可撤销；枪口目标立即出局并公开身份。"));
  return wrap;
}

function actionAreaNode(room) {
  const options = room.your_options;
  const area = el("div", "ww-action-area");
  if (!options) return area;
  if (options.kind === "witch") area.append(witchActionNode(room, options));
  else if (options.kind === "vote") area.append(voteActionNode(room, options));
  else if (options.kind === "shot") area.append(shotActionNode(room, options));
  else area.append(nightActionNode(room, options));
  return area;
}

function voiceButtonNode() {
  const status = voiceStatus();
  const on = status.canPublish && voiceMicWanted();
  const button = el("button", `ww-voice${on ? " on" : ""}${status.connected ? " connected" : ""}`);
  button.type = "button";
  button.disabled = !status.connected;
  button.textContent = !status.connected ? "🔇 语音未连接"
    : status.audioBlocked ? "🔊 开启声音"
    : !status.canPublish ? "🔊 开启收听"
    : on ? "🎙 闭麦" : "🎙 上麦";
  button.title = !status.connected ? "当前阶段没有语音频道，或连接尚未建立"
    : !status.canPublish ? "当前频道只可收听；点击开启声音"
    : "语音已连接，点击切换麦克风";
  button.setAttribute("aria-pressed", String(on && !status.audioBlocked));
  button.addEventListener("click", () => {
    if (status.audioBlocked) void enableVoiceAudio();
    else if (status.canPublish) void toggleMic();
    else void enableVoiceAudio();
  });
  return button;
}

function dockNode() {
  const room = state.myRoom;
  const dock = el("section", "ww-dock");
  const amAlive = alivePlayers(room).some((p) => isMe(p.username));
  const amOut = Boolean(room.your_role) && !amAlive;
  const acting = Boolean(room.your_options) && !room.paused && room.phase !== "showdown";
  if (acting) dock.classList.add("is-my-turn");

  const head = el("div", "ww-dock-head");
  head.append(el("div", "ww-dock-title", phaseHintText(room)));
  const right = el("div", "ww-dock-side");
  if (amOut) right.append(el("span", "ww-out-badge", "☠️ 已出局"));
  if (VOICE_ENABLED) right.append(voiceButtonNode());
  const chatToggle = el("button", "dock-chat-toggle");
  chatToggle.type = "button";
  chatToggle.textContent = room.phase === "night" && room.your_role?.faction === "wolf"
    ? "💬 狼队频道" : room.phase !== "showdown" && !amAlive && room.your_role
    ? "💬 死者频道" : "💬 聊天";
  chatToggle.addEventListener("click", openChatOverlay);
  right.append(chatToggle);
  head.append(right);
  dock.append(head);

  const body = el("div", "ww-dock-body");
  const info = el("div", "ww-dock-info");
  if (room.phase === "showdown" || room.settlement) {
    info.append(el("div", "ww-dock-note", "结算面板中投票「再来一局」或「解散房间」。"));
  } else if (amOut) {
    info.append(el("div", "ww-dock-note", "你已出局：白天可以旁观讨论，夜晚请闭眼。遗言阶段轮到你时再开口。"));
  } else if (!room.your_role) {
    info.append(el("div", "ww-dock-note", "观战中：可阅读公开讨论，语音仅可收听；私密频道不可见。"));
  } else {
    info.append(el("div", "ww-dock-note",
      room.phase === "night" ? "夜晚静默：好人无法发言，狼人可在狼队频道密谋。"
        : room.phase === "day" ? "自由发言阶段：打开聊天输出你的推理。"
        : room.phase === "vote" ? "投票进行中：每个人的票都公开显示在座位上。"
        : "按提示完成当前行动。"));
  }
  body.append(info);
  if (acting) {
    if (room.turn_left > 0) {
      const countdown = el("div", "countdown");
      const fill = el("div", "countdown-fill");
      countdown.append(fill);
      body.append(countdown);
      startHallTicker(fill, Math.max(0, (phaseDeadline - Date.now()) / 1000));
    }
    body.append(actionAreaNode(room));
  }
  dock.append(body);
  return dock;
}

/* =========================================================
   结算
========================================================= */

function resultNode(result) {
  const box = el("div", `ww-result ${result.winner === "wolf" ? "wolf-win" : "good-win"}`);
  const banner = el("div", "ww-result-banner",
    `${result.winner === "wolf" ? "🐺 狼人阵营获胜" : "✨ 好人阵营获胜"} · ${result.reason}（共 ${result.days || 0} 天）`);
  box.append(banner);
  box.append(el("div", "ww-payout-note", "输方每人支付一份底注，胜方全体（含阵亡队友）均分。"));
  const list = el("div", "ww-result-list");
  for (const row of result.players || []) {
    const line = el("div", "ww-result-row");
    if (row.alive) line.classList.add("alive");
    if (result.roles?.[row.username] === "狼人") line.classList.add("was-wolf");
    const net = Math.round(Number(row.net || 0) * 100) / 100;
    line.append(
      el("span", "ww-result-name", `${row.alive ? "" : "☠️ "}${row.nickname || displayNameOf(row.username)}`),
      roleChip(row.role),
      el("span", `ww-result-net ${net > 0 ? "win" : net < 0 ? "lose" : "flat"}`,
        `${net > 0 ? "+" : ""}${formatCoins(net)}`));
    list.append(line);
  }
  box.append(list);
  return box;
}

/* =========================================================
   主渲染
========================================================= */

function boardSummary(rules) {
  if (!rules) return "";
  const names = { werewolf: "狼", seer: "预", witch: "女", hunter: "猎", guard: "守", villager: "民" };
  const parts = [];
  if (rules.board?.length) {
    const counts = {};
    for (const key of rules.board) counts[names[key] || key] = (counts[names[key] || key] || 0) + 1;
    for (const [name, count] of Object.entries(counts)) parts.push(`${name}×${count}`);
  } else {
    parts.push("按人数自动配板");
  }
  return `${parts.join(" ")} · ${rules.win_mode === "cheng" ? "屠城局" : "屠边局"}`;
}

function renderWerewolfTable() {
  const room = state.myRoom;
  const body = elements.gameMain;
  if (lastRoomView !== room) {
    actionLock = false;
    lastRoomView = room;
    phaseDeadline = room.turn_left > 0 ? Date.now() + room.turn_left * 1000 : 0;
    if (room.phase !== "vote") pick = null;
  }
  body.replaceChildren();

  const wrap = el("div", "ww-page");
  const table = el("div", `ww-table is-${room.phase}`);

  const topbar = el("div", "ww-topbar");
  topbar.append(
    el("div", "ww-topbar-title", `狼人杀 · 第 ${room.match_no || 1} 局`),
    el("div", "ww-topbar-rules",
      `底注 ${formatCoinsWhole(room.blind)} · ${boardSummary(room.rules)}`));
  table.append(topbar);
  table.append(phaseBanner(room));

  const arena = el("div", "ww-arena");
  const seatsPanel = el("section", "ww-seats");
  seatsPanel.append(el("div", "ww-seats-head",
    `玩家状态 · ${alivePlayers(room).length} 人存活${room.phase === "vote" ? " · 得票见角标" : ""}`));
  const grid = el("div", "ww-seat-grid");
  (room.players || []).forEach((p, index) => grid.append(seatNode(p, index)));
  seatsPanel.append(grid);
  arena.append(seatsPanel);

  const side = el("div", "ww-side");
  side.append(roleCardNode(room), historyNode(room));
  arena.append(side);
  table.append(arena);

  if (room.result) table.append(resultNode(room.result));
  if (room.paused) table.append(el("div", "paused-overlay", "⏸ 对局已暂停，等待房主继续"));

  wrap.append(table);
  wrap.append(dockNode());
  body.append(wrap);
  reapplySeatBubbles();
}

registerGame("werewolf", {
  stakeLabel: "底注",
  blindLabel: "下一局底注",
  waitingHint: "狼人杀 4–12 人开局：夜晚狼人刀人，女巫救毒、预言家查验、守卫守护；白天讨论并投票放逐。6/8/9/10/12 人自动配板，其他人数需在开房时填写自定义板子。",
  noNextHint: () => "人数不足 4 人或有人筹码不足，过半数投「解散」后房间将按当前筹码退还所有人。",
  renderTable: renderWerewolfTable,
  renderReview: (result) => {
    const review = document.createElement("div");
    const title = el("div", "hall-section-title");
    title.style.marginTop = "0";
    title.textContent = "本局回顾";
    review.append(title, resultNode(result));
    return review;
  },
  seatElement: (index) => document.querySelector(`#gameMain .ww-seat[data-seat="${index}"]`),
});
