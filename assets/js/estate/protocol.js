"use strict";

import { elements, send, state, updateCoinChip } from "../core.js";
import { alertDialog } from "../dialog.js";
import { onMessage } from "../registry.js";
import { playGameSound } from "../game-audio.js";
import { clearEstate, estateStore, setEstateSnapshot, setVisitSnapshot } from "./state.js";

let sequence = 0;
const onlineWaiters = new Set();

/** 动作完成后播放的音效；没有对应音效的动作直接跳过。 */
const SOUND_CUES = {
  estate_plant: "plant",
  estate_harvest: "harvest",
  estate_fertilize: "plant",
  estate_visit_fertilize: "plant",
  estate_finish_fishing: "fish",
  estate_mine_cell: "mine",
  estate_finish_mining: "mine",
  estate_buy: "shop",
  estate_lottery_draw: "shop",
  estate_flappy_start: "shop",
  estate_flappy_finish: "shop",
  estate_market_trade: "shop",
  estate_use_land_upgrade_ticket: "shop",
  estate_buy_skin: "shop",
  estate_sell: "shop",
  estate_sell_all: "shop",
  estate_buy_tool: "shop",
  estate_upgrade_tool: "shop",
  estate_repair_tool: "shop",
  estate_pet: "shop",
  estate_penguin: "shop",
  estate_maodie: "shop",
  estate_select_pet: "shop",
};

function requestId(prefix) {
  sequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${sequence.toString(36)}`;
}

export function requestEstate() {
  if (state.currentUser) send({ type: "get_estate" });
}

/**
 * 发出一条庄园指令，并在 `estateStore.pending` 里登记。
 *
 * 等待结果的 Promise（若调用方需要）就挂在同一条记录上，不再另开一张表。
 */
export function estateCommand(type, payload = {}) {
  const id = requestId(type.replace("estate_", ""));
  estateStore.pending.set(id, { type, at: Date.now() });
  if (!send({ type, request_id: id, ...payload })) {
    estateStore.pending.delete(id);
    return null;
  }
  return id;
}

export function estateRequest(type, payload = {}, { timeoutMs = 0 } = {}) {
  const id = estateCommand(type, payload);
  if (!id) return Promise.reject(new Error("游戏厅尚未连接"));
  return new Promise((resolve, reject) => {
    const record = estateStore.pending.get(id);
    record.resolve = resolve; record.reject = reject;
    if (timeoutMs > 0) record.timeout = window.setTimeout(() => {
      settleRequest(id, { error: new Error("保存超时，请重试或重新进入庄园确认") });
    }, timeoutMs);
  });
}

/** 取出并移除某条请求记录；返回前先兑现它的等待者。 */
function settleRequest(id, { error, result } = {}) {
  const record = id ? estateStore.pending.get(id) : null;
  if (id) estateStore.pending.delete(id);
  if (!record) return null;
  window.clearTimeout(record.timeout);
  if (record.reject) error ? record.reject(error) : record.resolve(result);
  return record;
}

onMessage("estate_state", (data) => {
  const settled = settleRequest(data.request_id, { result: data.result });
  setEstateSnapshot(data);
  if (state.hallPage === "estate" && settled && data.result && !data.result.replayed) {
    const cue = SOUND_CUES[settled.type];
    if (cue) playGameSound(cue);
  }
  if (state.currentUser) {
    state.currentUser.coins = data.coins;
    updateCoinChip();
  }
});

onMessage("estate_error", (data) => {
  settleRequest(data.request_id, { error: new Error(data.message || "庄园操作失败") });
  if (data.state) setEstateSnapshot(data.state);
  void alertDialog(data.message || "庄园操作失败");
});

onMessage("estate_visit_list", (data) => {
  settleRequest(data.request_id, { result: data.estates || [] });
});

onMessage("estate_market_state", (data) => {
  // 挂单可能在上一次打开面板之后成交，金币余额要跟着快照一起刷新。
  if (data.market && state.currentUser && typeof data.market.coins === "number") {
    state.currentUser.coins = data.market.coins;
    updateCoinChip();
  }
  settleRequest(data.request_id, { result: data.market });
});

onMessage("estate_lottery_history", (data) => {
  settleRequest(data.request_id, { result: data.history || [] });
});

onMessage("estate_flappy_leaderboard", (data) => {
  settleRequest(data.request_id, { result: data.leaderboard });
});

onMessage("online_users", (data) => {
  const users = Array.isArray(data.users) ? data.users : [];
  estateStore.onlineUsers = new Set(users.map((user) => String(user.username || "").toLowerCase()));
  for (const waiter of [...onlineWaiters]) {
    window.clearTimeout(waiter.timeout);
    onlineWaiters.delete(waiter);
    waiter.resolve(estateStore.onlineUsers);
  }
});

export function requestEstateOnlineUsers() {
  return new Promise((resolve) => {
    const waiter = { resolve, timeout: 0 };
    waiter.timeout = window.setTimeout(() => {
      onlineWaiters.delete(waiter);
      resolve(estateStore.onlineUsers);
    }, 5000);
    onlineWaiters.add(waiter);
    if (!send({ type: "get_online" })) {
      window.clearTimeout(waiter.timeout);
      onlineWaiters.delete(waiter);
      resolve(estateStore.onlineUsers);
    }
  });
}

onMessage("estate_visit_state", (data) => {
  settleRequest(data.request_id, { result: data });
  setVisitSnapshot(data);
});

function presenceBelongsHere(data) {
  const currentOwner = estateStore.visit?.owner_username
    || estateStore.snapshot?.profile?.username;
  return Boolean(currentOwner && data.owner_username
    && String(currentOwner).toLowerCase() === String(data.owner_username).toLowerCase());
}

onMessage("estate_steal_result", (data) => {
  settleRequest(data.request_id, { result: data.result });
  if (data.result?.coins_dropped && estateStore.homeSnapshot) {
    estateStore.homeSnapshot.coins = Math.max(0,
      Number(estateStore.homeSnapshot.coins || 0) - Number(data.result.coins_dropped));
    if (state.currentUser) {
      state.currentUser.coins = estateStore.homeSnapshot.coins;
      updateCoinChip();
    }
  }
  setVisitSnapshot({ ...data.state, players: [...estateStore.players.values()] });
});
onMessage("estate_visit_fertilize_result", (data) => {
  settleRequest(data.request_id, { result: data.result });
  if (data.home_state) estateStore.homeSnapshot = data.home_state;
  setVisitSnapshot({ ...data.state, players: [...estateStore.players.values()] });
  if (state.hallPage === "estate" && !data.result?.replayed) playGameSound("plant");
});
onMessage("estate_crop_fertilized", (data) => {
  if (!estateStore.visit || !presenceBelongsHere(data)) return;
  const plot = estateStore.snapshot.plots.find((item) => item.index === data.plot_id);
  if (plot) plot.ready_at = data.ready_at;
  estateStore.listeners.forEach((listener) => listener(estateStore.snapshot));
});

onMessage("estate_visit_joined", (data) => {
  if (presenceBelongsHere(data)) estateStore.players.set(data.username, data);
});
onMessage("estate_visit_left", (data) => {
  if (presenceBelongsHere(data)) estateStore.players.delete(data.username);
});
onMessage("estate_visit_moved", (data) => {
  if (presenceBelongsHere(data)) estateStore.players.set(data.username, data);
});
onMessage("estate_crop_stolen", (data) => {
  if (!estateStore.visit || !presenceBelongsHere(data)) return;
  const plot = estateStore.snapshot.plots.find((item) => item.index === data.plot_id);
  if (plot) { plot.crop_id = null; plot.planted_at = null; plot.ready_at = null; }
  if (estateStore.snapshot.steal_limits) {
    estateStore.snapshot.steal_limits.owner_remaining = Math.max(0,
      estateStore.snapshot.steal_limits.owner_remaining - 1);
  }
  estateStore.listeners.forEach((listener) => listener(estateStore.snapshot));
});
onMessage("estate_notifications", (data) => {
  estateStore.notifications = data.notifications || [];
  settleRequest(data.request_id, { result: estateStore.notifications });
  estateStore.listeners.forEach((listener) => listener(estateStore.snapshot));
});
onMessage("estate_notifications_read", (data) => {
  settleRequest(data.request_id, { result: data.result });
});
onMessage("estate_visit_left_self", (data) => {
  settleRequest(data.request_id, { result: true });
});

export function sendEstatePosition(player) {
  if (!estateStore.snapshot) return;
  send({ type: "estate_visit_move", x: player.x, y: player.y,
    direction: player.direction, walking: Boolean(player.moving) });
}

export async function leaveEstateVisit() {
  if (estateStore.visit) await estateRequest("estate_leave_visit");
  requestEstate();
}

document.addEventListener("authstatechange", ({ detail }) => {
  if (!detail.user) {
    const reason = new Error("登录已结束");
    for (const id of [...estateStore.pending.keys()]) settleRequest(id, { error: reason });
    for (const waiter of [...onlineWaiters]) {
      window.clearTimeout(waiter.timeout);
      onlineWaiters.delete(waiter);
      waiter.resolve(new Set());
    }
    clearEstate();
    return;
  }
  if (state.hallPage === "estate") requestEstate();
});

export function pendingEstateAction() {
  return estateStore.pending.size > 0;
}
