/** Local Beta crafting model. Tiers follow PoE2DB's direction; odds are game tuning. */
export const TIER_WEIGHTS = { 3: 1000, 2: 450, 1: 120 };
export const TIER_LEVELS = { 3: 1, 2: 35, 1: 65 };
export const RARITY_NAMES = { normal: "普通", magic: "魔法", rare: "稀有" };
export const CURRENCIES = [
  { id: "transmutation", name: "蜕变石", emoji: "🔹", tier: 1, price: 6, desc: "普通 → 1 条词条的魔法装备", chance: 0.42 },
  { id: "augmentation", name: "增幅石", emoji: "🔸", tier: 1, price: 8, desc: "魔法装备增加第 2 条词条", chance: 0.3 },
  { id: "regal", name: "富豪石", emoji: "🟡", tier: 2, price: 15, desc: "魔法 → 稀有，并增加 1 条词条", chance: 0.14 },
  { id: "alchemy", name: "点金石", emoji: "🟠", tier: 2, price: 18, desc: "普通或魔法 → 4 条词条的稀有装备", chance: 0.1 },
  { id: "exalted", name: "崇高石", emoji: "✨", tier: 3, price: 28, desc: "稀有装备增加 1 条词条", chance: 0.04 },
  { id: "chaos", name: "混沌石", emoji: "🌀", tier: 3, price: 26, desc: "稀有装备移除并新增 1 条随机词条", chance: 0.035 },
  { id: "divine", name: "神圣石", emoji: "🌟", tier: 3, price: 32, desc: "重骰现有词条的数值", chance: 0.012 },
  { id: "annulment", name: "无效石", emoji: "⚪", tier: 3, price: 24, desc: "随机移除 1 条词条", chance: 0.025 },
];

const GROUPS = [
  { id: "life", name: "生命", kind: "prefix", stat: "maxHp", applies: "all", ranges: { 3: [1, 2], 2: [3, 4], 1: [5, 7] } },
  { id: "armor", name: "护甲", kind: "prefix", stat: "armor", applies: "item", ranges: { 3: [1, 1], 2: [2, 2], 1: [3, 4] } },
  { id: "power", name: "伤害", kind: "prefix", stat: "damage", applies: "weapon", ranges: { 3: [0.05, 0.08], 2: [0.09, 0.13], 1: [0.14, 0.2] } },
  { id: "recovery", name: "再生", kind: "prefix", stat: "regen", applies: "all", ranges: { 3: [0.1, 0.2], 2: [0.3, 0.4], 1: [0.5, 0.7] } },
  { id: "haste", name: "攻速", kind: "suffix", stat: "attackSpeed", applies: "all", ranges: { 3: [0.03, 0.05], 2: [0.06, 0.09], 1: [0.1, 0.14] } },
  { id: "speed", name: "移速", kind: "suffix", stat: "speed", applies: "all", ranges: { 3: [0.02, 0.03], 2: [0.04, 0.06], 1: [0.07, 0.09] } },
  { id: "fortune", name: "幸运", kind: "suffix", stat: "luck", applies: "all", ranges: { 3: [1, 1], 2: [2, 2], 1: [3, 4] } },
];

const validFor = (group, gear) => group.applies === "all" || group.applies === gear.kind;
const round = (n) => Math.round(n * 100) / 100;
const countKind = (gear, kind) => gear.affixes.filter((a) => a.kind === kind).length;

export function eligibleAffixes(gear, kind = null) {
  const used = new Set(gear.affixes.map((a) => a.group));
  const rows = [];
  for (const group of GROUPS) {
    if (!validFor(group, gear) || used.has(group.id) || (kind && group.kind !== kind)) continue;
    if (countKind(gear, group.kind) >= 3) continue;
    for (const tier of [3, 2, 1]) {
      if (gear.itemLevel >= TIER_LEVELS[tier]) rows.push({ group, tier, weight: TIER_WEIGHTS[tier] });
    }
  }
  return rows;
}

export function affixOdds(gear, kind = null) {
  const rows = eligibleAffixes(gear, kind);
  const total = rows.reduce((sum, row) => sum + row.weight, 0);
  return rows.map(({ group, tier, weight }) => ({ group: group.id, name: group.name, tier, weight,
    probability: weight / total }));
}

function chooseWeighted(rows, rand) {
  const total = rows.reduce((sum, row) => sum + row.weight, 0);
  if (!total) throw new Error("没有可用词条");
  let draw = rand() * total;
  for (const row of rows) {
    draw -= row.weight;
    if (draw < 0) return row;
  }
  return rows.at(-1);
}

function addAffix(gear, rand, kind = null) {
  const { group, tier } = chooseWeighted(eligibleAffixes(gear, kind), rand);
  const [min, max] = group.ranges[tier];
  const steps = Number.isInteger(min) ? 1 : 0.01;
  const value = round(min + Math.floor(rand() * (Math.round((max - min) / steps) + 1)) * steps);
  gear.affixes.push({ group: group.id, name: group.name, kind: group.kind, tier, stat: group.stat, value });
}

export function makeGear(kind, id, wave, rand, rarity = null) {
  const chance = rand();
  const selectedRarity = rarity || (chance < 0.48 ? "normal" : chance < 0.88 ? "magic" : "rare");
  const gear = { kind, id, itemLevel: Math.min(100, wave * 10), rarity: selectedRarity, affixes: [] };
  const count = selectedRarity === "rare" ? 4 : selectedRarity === "magic" ? 1 : 0;
  for (let i = 0; i < count; i++) addAffix(gear, rand, i < 2 && count === 4 ? "prefix" : i >= 2 ? "suffix" : null);
  return gear;
}

export function craftGear(source, currencyId, rand) {
  const gear = { ...source, affixes: source.affixes.map((affix) => ({ ...affix })) };
  const count = gear.affixes.length;
  if (currencyId === "transmutation" && gear.rarity === "normal") {
    gear.rarity = "magic"; addAffix(gear, rand);
  } else if (currencyId === "augmentation" && gear.rarity === "magic" && count === 1) {
    addAffix(gear, rand, gear.affixes[0].kind === "prefix" ? "suffix" : "prefix");
  } else if (currencyId === "regal" && gear.rarity === "magic" && count >= 1) {
    gear.rarity = "rare"; addAffix(gear, rand);
  } else if (currencyId === "alchemy" && ["normal", "magic"].includes(gear.rarity)) {
    gear.rarity = "rare"; gear.affixes = [];
    for (const kind of ["prefix", "suffix", "prefix", "suffix"]) addAffix(gear, rand, kind);
  } else if (currencyId === "exalted" && gear.rarity === "rare" && count < 6) {
    addAffix(gear, rand);
  } else if (currencyId === "chaos" && gear.rarity === "rare" && count > 0) {
    gear.affixes.splice(Math.floor(rand() * count), 1); addAffix(gear, rand);
  } else if (currencyId === "divine" && count > 0) {
    for (const affix of gear.affixes) {
      const group = GROUPS.find((entry) => entry.id === affix.group);
      const [min, max] = group.ranges[affix.tier];
      const steps = Number.isInteger(min) ? 1 : 0.01;
      affix.value = round(min + Math.floor(rand() * (Math.round((max - min) / steps) + 1)) * steps);
    }
  } else if (currencyId === "annulment" && count > 0) {
    gear.affixes.splice(Math.floor(rand() * count), 1);
  } else {
    throw new Error("这件装备当前不能使用该通货");
  }
  return gear;
}

export function formatAffix(affix, gear) {
  const percent = ["attackSpeed", "speed", "damage"].includes(affix.stat);
  const stat = affix.stat === "damage" ? (gear.kind === "weapon" ? "伤害" : affix.name) : affix.name;
  return `T${affix.tier} ${stat} +${percent ? Math.round(affix.value * 100) + "%" : affix.value}`;
}

export function rollCurrencyDrops(rand) {
  return CURRENCIES.filter((currency) => rand() < currency.chance).map((currency) => currency.id);
}
