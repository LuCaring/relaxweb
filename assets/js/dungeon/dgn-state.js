/* 地下城共享状态：最新 dungeon_state、当前战斗与全局提示。
 *
 * 服务端推送（dungeon_state，request_id 为 null）与写操作回执里的 state
 * 都从这里整体刷新；视图层订阅变更后重渲染，不各自缓存副本。
 */

import { refreshState } from "./dgn-protocol.js";

export const dgn = {
  snapshot: null,   // 最新一次 dungeon_state 内容（不含 type/request_id）
  battle: null,     // 最新一次战斗公开视图（_public：status/revision/hp/…）
  cursor: 0,        // 已消费的事件序号；battle_id 变化时归零
  battleId: null,
};

const subscribers = new Set();

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function emitChange() {
  for (const fn of [...subscribers]) {
    try {
      fn();
    } catch (error) {
      console.error("dungeon render error", error);
    }
  }
}

/** 整体替换本地状态快照；active_job 决定当前战斗编号。 */
export function applyState(snapshot) {
  dgn.snapshot = snapshot;
  if (snapshot?.active_job?.kind === "battle") {
    if (snapshot.active_job.id !== dgn.battleId) {
      dgn.battleId = snapshot.active_job.id;
      dgn.battle = null;
      dgn.cursor = 0;
    }
  } else {
    // 战斗结束清场后 active_job 消失；保留 battleId 供结算页读取，
    // 但不再把旧的 battle 当成进行中。
    dgn.battle = dgn.battle && ["settled", "abandoned", "error"].includes(dgn.battle.status)
      ? dgn.battle : null;
  }
  emitChange();
}

export function applyBattle(battle) {
  if (!battle) return;
  if (battle.battle_id !== dgn.battleId) {
    dgn.battleId = battle.battle_id;
    dgn.cursor = 0;
  }
  dgn.battle = battle;
  emitChange();
}

export function progressOf(challengeId, difficultyId) {
  return dgn.snapshot?.progress?.find(
    (row) => row.challenge_id === challengeId && row.difficulty_id === difficultyId) || null;
}

export function itemById(itemId) {
  return dgn.snapshot?.items?.find((item) => item.item_id === itemId) || null;
}

/** 全局轻提示：main.js 挂载监听后即可用，避免视图反向依赖入口模块。 */
export function notify(message, tone = "info") {
  window.dispatchEvent(new CustomEvent("dgn-toast", { detail: { message, tone } }));
}

/* =========================================================
   展示用标签与通用错误处理
========================================================= */

export const STAT_LABELS = {
  max_hp: "生命", atk: "攻击", defense: "防御",
  crit_bp: "暴击率", crit_damage_bp: "暴击伤害", speed: "速度",
};

export const QUALITY_LABELS = { normal: "普通", excellent: "优良", rare: "稀有", epic: "史诗" };
export const DIFFICULTY_LABELS = { normal: "普通", hard: "困难", expert: "精英" };
export const ENEMY_TYPE_LABELS = { normal: "普通", elite: "精英", boss: "首领" };

export function statText(stat, value) {
  if (stat === "crit_bp" || stat === "crit_damage_bp") return `${Math.round(value / 100)}%`;
  return String(value);
}

export function itemStatsText(item) {
  return Object.entries(item?.stats || {})
    .map(([stat, value]) => `${STAT_LABELS[stat] || stat}${value >= 0 ? "+" : ""}${statText(stat, value)}`)
    .join(" ");
}

const ERROR_MESSAGES = {
  version_conflict: "存档已更新，已为你刷新",
  state_conflict: "战斗状态已更新，已为你刷新",
  challenge_locked: "挑战尚未解锁",
  pending_items: "有待领取的装备，请先去装备页领取",
  active_job: "已有进行中的挑战",
  item_unavailable: "装备当前不可用",
  item_protected: "装备已锁定或正在使用",
  inventory_full: "背包空间不足，请先出售或清理装备",
  not_found: "目标不存在",
  auth_required: "请先登录",
  timeout: "请求超时",
  disconnected: "连接断开，正在重连",
};

/** 统一把协议错误转成提示；版本/状态冲突顺带向服务器刷新最新状态。 */
export function reportError(error) {
  notify(ERROR_MESSAGES[error?.code] || error?.message || "操作失败",
    ["version_conflict", "state_conflict"].includes(error?.code) ? "info" : "warn");
  if (["version_conflict", "state_conflict"].includes(error?.code)) void refreshState();
}
