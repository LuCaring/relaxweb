/**
 * 地下城 Beta 原型 · 局内运行状态。
 * 只做本地模拟；正式版由服务端 dungeon_beta_* 状态读模型持有权威数据。
 */
import { BASE_STATS, MAX_WEAPON_SLOTS, XP_FOR_LEVEL } from "./dgn-beta-data.js";

export function createState() {
  return {
    wave: 1,
    hp: BASE_STATS.maxHp,
    level: 1,
    xp: 0,
    materials: 10,
    weapons: ["starter_blade"],
    items: [],
    bonus: {}, // 升级选择累计的属性增量
    rerollCount: 0,
    shopOffers: [],
    totalMaterialsEarned: 0,
  };
}

/** 聚合属性 = 基础 + 升级增量 + 道具增量 */
export function statsOf(run, deps = {}) {
  const itemPool = deps.ITEMS || [];
  const stats = { ...BASE_STATS };
  const add = (patch) => {
    for (const [key, value] of Object.entries(patch || {})) {
      if (key in stats) stats[key] += value;
    }
  };
  add(run.bonus);
  for (const itemId of run.items) {
    const item = itemPool.find((entry) => entry.id === itemId);
    if (item) add(item.stats);
  }
  return stats;
}

export function hasFreeWeaponSlot(run) {
  return run.weapons.length < MAX_WEAPON_SLOTS;
}

export function grantXp(run, amount) {
  const levelsGained = [];
  run.xp += amount;
  let needed = XP_FOR_LEVEL(run.level);
  while (run.xp >= needed) {
    run.xp -= needed;
    run.level += 1;
    levelsGained.push(run.level);
    needed = XP_FOR_LEVEL(run.level);
  }
  return levelsGained;
}

/** 受击结算：护甲提供百分比减伤（原型规则，正式版由服务端模拟器裁决） */
export function applyDamage(run, rawDamage, stats) {
  const reduction = stats.armor / (stats.armor + 12);
  const final = Math.max(1, Math.round(rawDamage * (1 - reduction)));
  run.hp = Math.max(0, run.hp - final);
  return final;
}
