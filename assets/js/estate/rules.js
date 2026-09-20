"use strict";

/**
 * 休闲庄园纯规则层。
 *
 * 这里只放能被 node 直接 import 并断言的纯函数：不碰 DOM，也不 import 其它
 * estate 模块，数据一律由调用方从 catalog 快照取好再传进来。
 *
 * 数值规则一律来自服务端下发的 catalog（`fishing_rules`、`mining_rules`、
 * `tools`），本模块不保存任何玩法常量——否则调完服务端数值，玩家看到的
 * 进度条就会和服务端判定脱节。
 */

/**
 * 目录取值。
 *
 * 服务端下发的是 JSON，`tools`、`land_levels` 这类表在 Python 里是整数键，
 * 传输后变成字符串键，所以按字符串取，同时兼容仍是数值键的调用方。
 */
export function catalogEntry(table, key) {
  if (!table) return undefined;
  return table[String(key)] || table[key];
}

/** 鱼竿的张力容错系数；等级未知时回退 1，与服务端 `|| 1` 一致。 */
export function rodFactor(catalog, rodLevel) {
  const rule = catalogEntry(catalog?.tools?.rod, rodLevel);
  return (rule && rule.tension_factor) || 1;
}

/**
 * 推进一次张力采样，返回下一步状态与结局。
 *
 * `rules` 是服务端下发的 `catalog.fishing_rules`；`force` 是该帧的挣扎强度，
 * `factor` 是鱼竿系数。`outcome` 为 `null` 表示继续，否则是 `snapped` /
 * `caught`。判定顺序与服务端一致：先断线，再入护。
 */
export function tensionStep(rules, { tension, progress, held, force, factor }) {
  if (held) {
    tension += rules.hold_tension_gain * (rules.hold_tension_force_base + force) * factor;
    progress += rules.hold_progress_gain * (rules.hold_progress_base - force * rules.hold_progress_force_scale);
  } else {
    tension -= rules.release_tension_drop;
    progress -= rules.release_progress_drop * (rules.release_progress_force_base + force);
  }
  tension = Math.max(0, tension);
  progress = Math.max(0, progress);
  const outcome = tension >= rules.snapped_at ? "snapped"
    : progress >= rules.caught_at ? "caught" : null;
  return { tension, progress, outcome };
}

/**
 * 修理到满耐久所需金币，与服务端 `repair_tool` 同式。
 *
 * 注意：Python 的 `round` 是银行家舍入、JS 的 `Math.round` 是四舍五入，
 * 二者只在恰好落在半分位时相差 0.01。当前目录里的修理价与耐久都不会产生
 * 半分位，`test_estate_rules.py` 已对全部工具等级逐点比对过收费金额。
 */
export function repairCost(rule, durability) {
  const missing = rule.max_durability - durability;
  return Math.max(1, Math.round(rule.repair_price * missing / rule.max_durability * 100) / 100);
}

/** 一次矿局按最大产出预留的仓位：敲击次数加上固定的 +2 格。 */
export function reservedSlots(catalog, pickaxeRule) {
  return pickaxeRule.strikes + catalog.mining_rules.extra_cells;
}

/** 倒计时文案：满一小时显示时分，否则显示 `分:秒`。 */
export function formatDuration(seconds) {
  const value = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor(value % 3600 / 60);
  const secs = value % 60;
  return hours ? `${hours}时 ${minutes}分` : `${minutes}:${String(secs).padStart(2, "0")}`;
}

/**
 * 战利品摘要。
 *
 * 默认紧凑排版（`🪨×2`，两空格分隔），用于矿洞里的实时状态条；
 * `spaced` 为结算页的宽排版（`🪨 × 2`，全角空格分隔），两处排版本就不同。
 */
export function lootSummary(loot, icons, emptyLabel, spaced = false) {
  const entries = Object.entries(loot || {});
  const gem = (id) => icons[id] || "◆";
  const text = spaced
    ? entries.map(([id, count]) => `${gem(id)} × ${count}`).join("　")
    : entries.map(([id, count]) => `${gem(id)}×${count}`).join("  ");
  return text || emptyLabel;
}
