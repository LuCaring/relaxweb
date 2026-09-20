"use strict";

import { elements, send, state, updateCoinChip } from "../core.js";
import { alertDialog } from "../dialog.js";
import { onMessage } from "../registry.js";
import { playGameSound } from "../game-audio.js";
import { clearEstate, estateStore, setEstateSnapshot } from "./state.js";

let sequence = 0;

/** 动作完成后播放的音效；没有对应音效的动作直接跳过。 */
const SOUND_CUES = {
  estate_plant: "plant",
  estate_harvest: "harvest",
  estate_finish_fishing: "fish",
  estate_mine_cell: "mine",
  estate_finish_mining: "mine",
  estate_buy: "shop",
  estate_sell: "shop",
  estate_sell_all: "shop",
  estate_buy_tool: "shop",
  estate_upgrade_tool: "shop",
  estate_repair_tool: "shop",
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

document.addEventListener("authstatechange", ({ detail }) => {
  if (!detail.user) {
    const reason = new Error("登录已结束");
    for (const id of [...estateStore.pending.keys()]) settleRequest(id, { error: reason });
    clearEstate();
    return;
  }
  if (state.hallPage === "estate") requestEstate();
});

export function pendingEstateAction() {
  return estateStore.pending.size > 0;
}
