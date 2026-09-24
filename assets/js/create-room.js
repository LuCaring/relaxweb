/* Room creation form and per-game drafts. */

import { elements, formatCoins, formatCoinsWhole, renderGameView, send, state } from "./core.js";
import { registerView } from "./registry.js";
import { fillBlindOptions, gameMetaById, minimumBuyIn } from "./game-config.js";

const createDrafts = new Map();
let createPending = false;
let createError = "";
let createFormNodes = null;
let createPendingTimer = 0;

function draftFor(gameId) {
  if (!createDrafts.has(gameId)) {
    createDrafts.set(gameId, {
      name: "", buyin: "100", blind: "5",
      rules: {
        wild: 1, bomb: 8, ace: 1, min_fan: 8, flowers: 1, chow: 1, dianpao: 1,
        launch: 6, extra_roll: 1, jump4: 1, fly12: 1, payout: "champion",
        chambers: 6, cards: 5, max_play: 3, jokers: "wild", respin: 0,
        win_mode: "bian", witch_self_save: "first", last_words: "first",
        tie: "revote", guard_continuous: 0, board_text: "",
        speak_seconds: 30,
      },
    });
  }
  return createDrafts.get(gameId);
}

function button(label, className, onClick) {
  const node = document.createElement("button");
  node.className = className;
  node.type = "button";
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}


function field(labelText, control, hint = "") {
  const wrapper = document.createElement("label");
  wrapper.className = "create-field";
  const label = document.createElement("span");
  label.className = "create-field-label";
  label.textContent = labelText;
  wrapper.append(label, control);
  if (hint) {
    const help = document.createElement("span");
    help.className = "create-field-help";
    help.textContent = hint;
    wrapper.append(help);
  }
  return wrapper;
}

function labeledSelect(label, ariaLabel, choices, value, onChange) {
  const select = document.createElement("select");
  select.className = "login-input";
  select.setAttribute("aria-label", ariaLabel);
  for (const [choiceValue, text] of choices) {
    const option = document.createElement("option");
    option.value = String(choiceValue);
    option.textContent = text;
    if (String(choiceValue) === String(value)) option.selected = true;
    select.append(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  return field(label, select);
}

function labeledText(label, ariaLabel, value, placeholder, onChange) {
  const input = document.createElement("input");
  input.className = "login-input";
  input.type = "text";
  input.setAttribute("aria-label", ariaLabel);
  input.value = value || "";
  input.placeholder = placeholder;
  input.addEventListener("input", () => onChange(input.value));
  return field(label, input);
}

/* 自定义板子文本 → 角色键数组；留空返回 null（按人数自动配板）。 */
const WEREWOLF_ROLE_ALIASES = {
  "狼": "werewolf", "狼人": "werewolf",
  "民": "villager", "平民": "villager",
  "预": "seer", "预言家": "seer",
  "女": "witch", "女巫": "witch",
  "猎": "hunter", "猎人": "hunter",
  "守": "guard", "守卫": "guard",
};

function parseWerewolfBoard(text) {
  const parts = String(text || "").split(/[，,、\s]+/).map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return null;
  const board = [];
  for (const part of parts) {
    const key = WEREWOLF_ROLE_ALIASES[part];
    if (!key) return null;
    board.push(key);
  }
  return board;
}

function createRules(game, draft, details) {
  const rules = document.createElement("div");
  rules.className = "create-rules-grid";
  const save = (key, number = true) => (value) => {
    draft.rules[key] = number ? Number(value) : value;
    updateCreateSummary();
  };
  if (game.id === "guandan") {
    rules.append(
      labeledSelect("逢人配", "逢人配", [[1, "开启（红桃级牌可代任意牌）"], [0, "关闭"]], draft.rules.wild, save("wild")),
      labeledSelect("炸弹翻倍", "炸弹翻倍", [[0, "不翻倍"], [4, "翻倍，×4 封顶"], [8, "翻倍，×8 封顶"], [16, "翻倍，×16 封顶"], [999, "翻倍，不封顶"]], draft.rules.bomb, save("bomb")),
      labeledSelect("过 A 条件", "过A条件", [[1, "严格（需要双上）"], [0, "宽松（搭档非末游即可）"]], draft.rules.ace, save("ace")),
    );
  } else if (game.id === "mahjong") {
    rules.append(
      labeledSelect("起和番数", "起和番数", [[8, "八番起和（国标标准）"], [4, "四番起和（低门槛）"], [0, "不起和"]], draft.rules.min_fan, save("min_fan")),
      labeledSelect("花牌", "花牌", [[1, "开启（144 张，每张 1 分）"], [0, "关闭（136 张）"]], draft.rules.flowers, save("flowers")),
      labeledSelect("吃牌", "吃牌", [[1, "开启（仅上家）"], [0, "关闭"]], draft.rules.chow, save("chow")),
      labeledSelect("点炮计法", "点炮计法", [[1, "点炮者付三份（官方）"], [0, "点炮者付一份"]], draft.rules.dianpao, save("dianpao")),
    );
  } else if (game.id === "ludo") {
    rules.append(
      labeledSelect("起飞点数", "起飞点数", [[6, "掷 6 起飞（经典）"], [5, "掷 5 或 6 起飞（快节奏）"]], draft.rules.launch, save("launch")),
      labeledSelect("掷 6 连投", "掷6连投", [[1, "开启（连掷三个 6 受罚）"], [0, "关闭"]], draft.rules.extra_roll, save("extra_roll")),
      labeledSelect("同色跳格", "同色跳格", [[1, "开启（落在己色格跳 4 格）"], [0, "关闭"]], draft.rules.jump4, save("jump4")),
      labeledSelect("飞行捷径", "飞行捷径", [[1, "开启（飞行格直飞 12 格）"], [0, "关闭"]], draft.rules.fly12, save("fly12")),
      labeledSelect("金币结算", "金币结算", [["champion", "冠军通吃（每家付一份底注）"], ["rank", "按名次递增（第 2/3/4 名付 1/2/3 份）"]], draft.rules.payout, save("payout", false)),
    );
  } else if (game.id === "liarsbar") {
    rules.append(
      labeledSelect("弹巢容量", "弹巢容量", [[4, "4 弹巢（更快见血）"], [6, "6 弹巢（经典）"], [8, "8 弹巢（更长对局）"]], draft.rules.chambers, save("chambers")),
      labeledSelect("开枪概率", "开枪概率", [[0, "递增（1/6 → 1/5 → …，空仓不重转）"], [1, "每次重转（始终 1/弹巢）"]], draft.rules.respin, save("respin")),
      labeledSelect("每人手牌", "每人手牌", [[4, "4 张"], [5, "5 张（经典）"], [6, "6 张"]], draft.rules.cards, save("cards")),
      labeledSelect("单次出牌上限", "单次出牌上限", [[2, "最多 2 张"], [3, "最多 3 张（经典）"]], draft.rules.max_play, save("max_play")),
      labeledSelect("小丑牌", "小丑牌", [["wild", "加入 2 张小丑（百搭，翻牌永远算真话）"], ["none", "不加小丑（纯 K/Q/A）"]], draft.rules.jokers, save("jokers", false)),
      labeledSelect("金币结算", "金币结算", [["champion", "冠军通吃（每家付一份底注）"], ["rank", "按出局顺序递增（越早出局付越多）"]], draft.rules.payout, save("payout", false)),
    );
  } else if (game.id === "werewolf") {
    rules.append(
      labeledSelect("胜负规则", "胜负规则", [["bian", "屠边局（神职或平民全灭即狼胜，经典）"], ["cheng", "屠城局（好人全灭才狼胜）"]], draft.rules.win_mode, save("win_mode", false)),
      labeledSelect("女巫自救", "女巫自救", [["first", "仅首夜可自救（经典）"], ["always", "始终可自救"], ["never", "不可自救"]], draft.rules.witch_self_save, save("witch_self_save", false)),
      labeledSelect("遗言规则", "遗言规则", [["first", "首夜死者与被放逐者有遗言"], ["all", "所有死者都有遗言"], ["none", "无遗言"]], draft.rules.last_words, save("last_words", false)),
      labeledSelect("平票处理", "平票处理", [["revote", "平票后在候选人中重投一轮"], ["no_exile", "平票直接无人出局"]], draft.rules.tie, save("tie", false)),
      labeledSelect("每人发言时长", "每人发言时长",
        [[15, "15 秒"], [20, "20 秒"], [30, "30 秒（默认）"], [45, "45 秒"],
         [60, "60 秒"], [90, "90 秒"], [120, "120 秒"]],
        draft.rules.speak_seconds, save("speak_seconds")),
      labeledSelect("守卫连守", "守卫连守", [[0, "不能连守同一人（经典）"], [1, "可以连守同一人"]], draft.rules.guard_continuous, save("guard_continuous")),
      labeledText("自定义板子", "自定义板子", draft.rules.board_text,
        "留空按人数自动；如：狼,狼,预言家,女巫,猎人,民,民,民",
        (value) => save("board_text", false)(value)),
    );
  } else {
    const text = document.createElement("p");
    text.className = "create-rules-note";
    text.textContent = game.id === "holdem"
      ? "无限注德州扑克：小盲注轮转，支持边池与全下。房主可在牌局中结束本局。"
      : "UNO 经典规则：先出完手牌者获胜，其他玩家按剩余牌数支付底注。";
    rules.append(text);
  }
  details.append(rules);
}

function createDescription(game) {
  if (game.id === "mahjong") return "国标麻将需 4 人开局。自摸三家各付一份，点炮按所选计法赔付；花牌每张 1 分计入总番。荒庄不计分且庄家连庄。";
  if (game.id === "werewolf") return "狼人杀 4–12 人开局，6/8/9/10/12 人自动配板。夜晚按守卫→狼人→女巫→预言家行动，白天按座次依次发言（每人限时，可设置）后投票放逐；输方各付一份底注，胜方全体均分。";
  if (game.id === "guandan") return "掼蛋需 4 人开局，隔位玩家自动组队。各自从 2 打到 A，头游方获胜升级；线上暂不支持进贡还贡。";
  if (game.id === "uno") return "UNO 至少 2 人开局。一手结束后，赢家按各家剩余牌数乘以底注收注，离桌时按筹码自动结算。";
  if (game.id === "ludo") return "飞行棋 2–4 人开局，按加入顺序执红黄蓝绿。先送 4 架飞机到家者夺冠，其余按到达进度排名；超终点的点数从终点反弹，落点敌机全部撞回机场。";
  if (game.id === "liarsbar") return "骗子酒馆 2–6 人开局。每轮随机桌面牌（K/Q/A），轮流暗打 1 至上限张声称是桌面牌，或质疑上家；翻牌定真假，说谎者或误质疑者对自己开枪，阵亡淘汰，最后独存者按所选方式收走赔付。";
  return "德州扑克至少 2 名有筹码的玩家开局。开局后房主可结束牌局，所有人按当前筹码结算。";
}

function updateCreateSummary() {
  if (!createFormNodes) return;
  const { game, draft, name, buyin, blind, summary, minimum, submit, error } = createFormNodes;
  const min = minimumBuyIn(blind.value);
  minimum.textContent = `最低买入 ${formatCoins(min)} 金币（底注的 20 倍）`;
  summary.querySelector(".create-summary-game").textContent = game.name;
  summary.querySelector(".create-summary-name").textContent = name.value.trim() || "未命名房间";
  summary.querySelector(".create-summary-blind").textContent = `${game.id === "holdem" ? "盲注" : game.id === "uno" ? "每张赔付" : "底注"} ${formatCoinsWhole(blind.value)}`;
  summary.querySelector(".create-summary-buyin").textContent = `${formatCoins(Number(buyin.value) || 0)} 金币`;
  summary.querySelector(".create-summary-seats").textContent = `${game.seats} 个座位`;
  const ruleSummary = game.id === "guandan"
    ? `逢人配${draft.rules.wild ? "开" : "关"} · 炸弹${draft.rules.bomb ? `×${draft.rules.bomb === 999 ? "∞" : draft.rules.bomb}` : "不翻倍"} · ${draft.rules.ace ? "严格过 A" : "宽松过 A"}`
    : game.id === "mahjong"
      ? `${draft.rules.min_fan || 0} 番起和 · 花牌${draft.rules.flowers ? "开" : "关"} · 吃牌${draft.rules.chow ? "开" : "关"} · 点炮${draft.rules.dianpao ? "包三家" : "付一份"}`
      : game.id === "ludo"
        ? `掷${draft.rules.launch === 5 ? "5或6" : "6"}起飞 · ${draft.rules.extra_roll ? "掷6连投" : "不连投"} · 跳格${draft.rules.jump4 ? "开" : "关"} · 飞行${draft.rules.fly12 ? "开" : "关"} · ${draft.rules.payout === "rank" ? "按名次结算" : "冠军通吃"}`
        : game.id === "liarsbar"
          ? `${draft.rules.chambers} 弹巢${draft.rules.respin ? "重转" : "递增"} · ${draft.rules.cards} 张手牌 · 至多出 ${draft.rules.max_play} 张 · ${draft.rules.jokers === "wild" ? "小丑百搭" : "无小丑"} · ${draft.rules.payout === "rank" ? "按出局结算" : "冠军通吃"}`
          : game.id === "werewolf"
            ? `${draft.rules.win_mode === "cheng" ? "屠城局" : "屠边局"} · 女巫${draft.rules.witch_self_save === "always" ? "始终可自救" : draft.rules.witch_self_save === "never" ? "不可自救" : "仅首夜可自救"} · ${draft.rules.last_words === "none" ? "无遗言" : draft.rules.last_words === "all" ? "全遗言" : "首夜遗言"} · ${draft.rules.tie === "no_exile" ? "平票流局" : "平票重投"} · 每人发言 ${draft.rules.speak_seconds} 秒 · 身份隐藏至终局${draft.rules.board_text.trim() ? " · 自定义板子" : " · 自动配板"}`
            : game.id === "holdem" ? "无限注德州扑克" : "UNO 经典规则";
  summary.querySelector(".create-summary-rules").textContent = ruleSummary;
  buyin.min = String(min);
  const amount = Number(buyin.value);
  const amountError = !buyin.value || !Number.isFinite(amount) || amount < min
    ? `请输入至少 ${formatCoins(min)} 金币。`
    : state.currentUser && amount > state.currentUser.coins ? "金币余额不足。" : "";
  buyin.setAttribute("aria-invalid", String(Boolean(amountError)));
  const amountErrorNode = createFormNodes.amountError;
  amountErrorNode.textContent = amountError;
  amountErrorNode.hidden = !amountError;
  const valid = !amountError;
  submit.disabled = createPending || !valid;
  submit.textContent = createPending ? "正在创建…" : "创建并进入房间";
  error.textContent = createError;
  error.hidden = !createError;
  draft.name = name.value;
  draft.buyin = buyin.value;
  draft.blind = blind.value;
}

function renderCreate() {
  const game = gameMetaById(state.currentGameId);
  const draft = draftFor(game.id);
  const body = elements.gameMain;
  body.replaceChildren();
  const toolbar = document.createElement("div");
  toolbar.className = "hall-toolbar create-toolbar";
  toolbar.append(button("← 房间列表", "online-stat hall-back", () => {
    state.hallPage = "rooms";
    renderGameView();
  }));
  const heading = document.createElement("div");
  heading.className = "hall-page-title";
  heading.textContent = "创建房间";
  const gameName = document.createElement("div");
  gameName.className = "create-toolbar-game";
  gameName.textContent = `${game.icon} ${game.name}`;
  toolbar.append(heading, gameName);
  body.append(toolbar);

  const layout = document.createElement("div");
  layout.className = "create-room-layout";
  const form = document.createElement("form");
  form.className = "create-room-form";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    createRoom();
  });
  const fields = document.createElement("div");
  fields.className = "create-fields-grid";
  const name = document.createElement("input");
  name.className = "login-input";
  name.maxLength = 20;
  name.value = draft.name;
  name.placeholder = "例如：周末好友桌";
  name.autocomplete = "off";
  fields.append(field("房间名称", name, "可留空，最多 20 个字"));
  const buyin = document.createElement("input");
  buyin.className = "login-input";
  buyin.type = "number";
  buyin.step = "0.01";
  buyin.value = draft.buyin;
  buyin.placeholder = "买入金币";
  const buyinField = field("买入金币", buyin);
  const buyinLabel = buyinField.querySelector(".create-field-label");
  buyinLabel.id = "createBuyinLabel";
  buyin.setAttribute("aria-labelledby", buyinLabel.id);
  const amountError = document.createElement("span");
  amountError.className = "create-field-error";
  amountError.id = "createBuyinError";
  amountError.setAttribute("aria-live", "polite");
  amountError.hidden = true;
  buyin.setAttribute("aria-describedby", amountError.id);
  const buyinGroup = document.createElement("div");
  buyinGroup.className = "create-field-group";
  buyinGroup.append(buyinField, amountError);
  fields.append(buyinGroup);
  const blindWrap = document.createElement("label");
  blindWrap.className = "create-field";
  const blindLabel = document.createElement("span");
  blindLabel.className = "create-field-label";
  blindLabel.textContent = game.id === "holdem" ? "小盲注" : game.id === "uno" ? "每张赔付" : "底注";
  const blind = document.createElement("select");
  blind.className = "login-input";
  blindLabel.id = "createBlindLabel";
  blind.setAttribute("aria-labelledby", blindLabel.id);
  fillBlindOptions(blind, game.id, draft.blind);
  blindWrap.append(blindLabel, blind);
  fields.append(blindWrap);
  const minimum = document.createElement("div");
  minimum.className = "create-minimum";
  fields.append(minimum);
  form.append(fields);

  const details = document.createElement("details");
  details.className = "create-rules-details";
  details.open = true;
  const summaryTitle = document.createElement("summary");
  summaryTitle.textContent = "常用规则设置";
  details.append(summaryTitle);
  createRules(game, draft, details);
  form.append(details);
  const longRules = document.createElement("details");
  longRules.className = "create-long-rules";
  const longRulesTitle = document.createElement("summary");
  longRulesTitle.textContent = "查看完整玩法说明";
  const longRulesText = document.createElement("p");
  longRulesText.textContent = game.desc;
  longRules.append(longRulesTitle, longRulesText);
  form.append(longRules);
  const error = document.createElement("div");
  error.className = "create-error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  form.append(error);
  const createRoom = () => {
    const amount = Math.round(Number(buyin.value) * 100) / 100;
    const minimumAmount = minimumBuyIn(blind.value);
    if (!Number.isFinite(amount) || amount < minimumAmount) {
      createError = `买入金额至少为 ${formatCoins(minimumAmount)} 金币。`;
      updateCreateSummary();
      buyin.focus();
      return;
    }
    if (state.currentUser && amount > state.currentUser.coins) {
      createError = "金币不足，请降低买入金额。";
      updateCreateSummary();
      buyin.focus();
      return;
    }
    const payload = { type: "create_room", game: game.id, name: name.value.trim(), buy_in: amount, blind: Number(blind.value) };
    if (game.id === "guandan") payload.rules = {
      wild: Boolean(Number(draft.rules.wild)),
      bomb_cap: Number(draft.rules.bomb),
      ace_strict: Boolean(Number(draft.rules.ace)),
    };
    if (game.id === "mahjong") payload.rules = {
      min_fan: Number(draft.rules.min_fan),
      flowers: Boolean(Number(draft.rules.flowers)),
      chow: Boolean(Number(draft.rules.chow)),
      dianpao_full: Boolean(Number(draft.rules.dianpao)),
    };
    if (game.id === "ludo") payload.rules = {
      launch: Number(draft.rules.launch),
      extra_roll: Boolean(Number(draft.rules.extra_roll)),
      jump4: Boolean(Number(draft.rules.jump4)),
      fly12: Boolean(Number(draft.rules.fly12)),
      payout: draft.rules.payout === "rank" ? "rank" : "champion",
    };
    if (game.id === "liarsbar") payload.rules = {
      chambers: Number(draft.rules.chambers),
      cards: Number(draft.rules.cards),
      max_play: Number(draft.rules.max_play),
      jokers: draft.rules.jokers === "none" ? "none" : "wild",
      respin: Boolean(Number(draft.rules.respin)),
      payout: draft.rules.payout === "rank" ? "rank" : "champion",
    };
    if (game.id === "werewolf") {
      payload.rules = {
        win_mode: draft.rules.win_mode === "cheng" ? "cheng" : "bian",
        witch_self_save: ["always", "never"].includes(draft.rules.witch_self_save)
          ? draft.rules.witch_self_save : "first",
        last_words: ["all", "none"].includes(draft.rules.last_words)
          ? draft.rules.last_words : "first",
        tie: draft.rules.tie === "no_exile" ? "no_exile" : "revote",
        speak_seconds: Number(draft.rules.speak_seconds) || 30,
        guard_continuous: Boolean(Number(draft.rules.guard_continuous)),
      };
      const board = parseWerewolfBoard(draft.rules.board_text);
      if (board) payload.rules.board = board;
      else if (draft.rules.board_text.trim()) {
        createError = "自定义板子无法识别，可用角色：狼/狼人、民/平民、预/预言家、女/女巫、猎/猎人、守/守卫。";
      }
    }
    createError = "";
    if (send(payload)) {
      createPending = true;
      clearTimeout(createPendingTimer);
      createPendingTimer = window.setTimeout(() => {
        createPending = false;
        createError = "创建请求超时，请检查连接后重试。";
        updateCreateSummary();
      }, 15000);
      updateCreateSummary();
    }
  };
  const submit = button("创建并进入房间", "login-submit create-submit", () => {});
  submit.type = "submit";
  form.append(submit);

  const summaryCard = document.createElement("aside");
  summaryCard.className = "create-summary-card";
  const summaryHeading = document.createElement("h2");
  summaryHeading.textContent = "房间预览";
  summaryCard.append(summaryHeading);
  for (const [key, label] of [["game", "游戏"], ["name", "房间名"], ["blind", "玩法单位"], ["buyin", "买入"], ["seats", "座位"], ["rules", "规则"]]) {
    const row = document.createElement("div");
    row.className = "create-summary-row";
    const term = document.createElement("span");
    term.textContent = label;
    const value = document.createElement("strong");
    value.className = `create-summary-${key}`;
    row.append(term, value);
    summaryCard.append(row);
  }
  const explanation = document.createElement("p");
  explanation.className = "create-summary-hint";
  explanation.textContent = createDescription(game);
  summaryCard.append(explanation);
  layout.append(form, summaryCard);
  body.append(layout);

  createFormNodes = { game, draft, name, buyin, blind, summary: summaryCard, minimum, amountError, submit, error };
  const update = () => { createError = ""; updateCreateSummary(); };
  for (const input of [name, buyin, blind]) input.addEventListener(input === blind ? "change" : "input", update);
  updateCreateSummary();
}

document.addEventListener("gamejoined", () => {
  clearTimeout(createPendingTimer);
  createPendingTimer = 0;
  createPending = false;
  createError = "";
  createFormNodes = null;
});

document.addEventListener("gameactionerror", (event) => {
  if (!createPending) return;
  clearTimeout(createPendingTimer);
  createPendingTimer = 0;
  createPending = false;
  createError = event.detail?.message || "创建失败，请检查设置后重试。";
  updateCreateSummary();
});

registerView("create", renderCreate);
