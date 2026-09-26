/* Shared game metadata used by the hall and room views. */

export const GAME_TYPES = [
  {
    id: "estate",
    mode: "solo",
    view: "estate",
    name: "休闲庄园",
    icon: "🌾",
    desc: "经营你的像素庄园：种田、钓鱼、挖矿，让每一枚金币慢慢生长。",
  },
  {
    // 独立页面（不占用牌桌），且只在 config.json 打开 dungeon.beta_enabled 时出现。
    id: "dungeon",
    mode: "link",
    href: "/dungeon-beta.html",
    flag: "dungeonBeta",
    action: "进入遗迹 →",
    name: "遗迹深探 · 地下城 Beta",
    icon: "🗝️",
    desc: "俯视动作波次玩法：走位、武器自动攻击、波次之间进商店改造装备。当前是本地原型，不读写正式账号与金币。",
  },
  {
    id: "holdem",
    name: "德州扑克 · 无限注",
    icon: "♠",
    desc: "经典规则：盲注轮转、边池分配、全下自动跑马。金币买入，离桌自动结算。",
    seats: 9,
    minStartPlayers: 2,
  },
  {
    id: "uno",
    name: "UNO · 经典牌局",
    icon: "🃏",
    desc: "同色同数出牌，功能牌逆转战局。剩牌按张赔给赢家，先出完者通吃本局。",
    seats: 9,
    minStartPlayers: 2,
  },
  {
    id: "guandan",
    name: "掼蛋 · 组队升级",
    icon: "🎴",
    seats: 4,
    minStartPlayers: 4,
    desc: "双副牌四人组队，级牌为王前第二大，逢人配、炸弹翻倍。头游定胜负，从 2 打到 A。",
  },
  {
    id: "mahjong",
    name: "国标麻将 · 八番起和",
    icon: "🀄",
    seats: 4,
    minStartPlayers: 4,
    desc: "144 张牌吃碰杠胡，圈风门风随庄轮转，76 个常用番种、花牌计分。自摸三家各付，点炮包三家。",
  },
  {
    id: "ludo",
    name: "飞行棋 · 经典竞速",
    icon: "✈️",
    seats: 4,
    minStartPlayers: 2,
    desc: "掷点起飞、同色跳格、飞行捷径、撞机回机场。先送 4 架飞机到家者获胜，可冠军通吃或按名次结算底注。",
  },
  {
    id: "liarsbar",
    name: "骗子酒馆 · 轮盘对峙",
    icon: "🔫",
    seats: 6,
    minStartPlayers: 2,
    desc: "暗打出牌声称是桌面牌，质疑翻牌定真假：说谎者或误质疑者对自己开枪，最后独存者收走赔付。弹巢、手牌与小丑规则可在开房时自定义。",
  },
];

/** 牌桌游戏：排除单人玩法和跳转到独立页面的入口。 */
export const ROOM_GAME_TYPES = GAME_TYPES.filter((game) => game.mode !== "solo" && game.mode !== "link");
export const DEFAULT_GAME = ROOM_GAME_TYPES[0];

/**
 * 游戏厅实际展示的入口。带 flag 的条目只有在对应开关为 true 时才出现，
 * 用于把未接正式协议的地下城原型挡在正式入口之外（见 config.json 的 dungeon.beta_enabled）。
 */
export function hallGameTypes(flags = {}) {
  return GAME_TYPES.filter((game) => !game.flag || flags[game.flag] === true);
}

export function gameMetaById(id) {
  return GAME_TYPES.find((game) => game.id === id) || DEFAULT_GAME;
}

export function fillBlindOptions(select, gameId, selected) {
  for (const blind of [1, 2, 5, 10]) {
    const option = document.createElement("option");
    option.value = String(blind);
    option.textContent = gameId === "holdem" ? `盲注 ${blind}/${blind * 2}`
      : gameId === "uno" ? `每张赔付 ${blind}` : `底注 ${blind}`;
    if (String(blind) === String(selected)) option.selected = true;
    select.append(option);
  }
}

export function minimumBuyIn(blind) {
  return Number(blind || 0) * 20;
}
