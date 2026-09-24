"use strict";

import { alertDialog, confirmDialog } from "../dialog.js";
import { formatCoins, formatCoinsWhole } from "../format.js";
import { characterAsset } from "./characters.js";
import { cropAsset, cropStage, inventoryAsset, toolAsset } from "./assets.js";
import { catalogEntry, formatDuration, repairCost, reservedSlots } from "./rules.js";
import { estateCommand, estateRequest, pendingEstateAction, requestEstateOnlineUsers } from "./protocol.js";
import { estateNow, estateStore } from "./state.js";

/**
 * 面板按钮。`disabled` 由内部与 pending 状态合并，调用方不必再写 `||=`。
 */
function button(label, action, { className = "estate-button", disabled = false } = {}) {
  const node = document.createElement("button");
  node.type = "button"; node.className = className; node.textContent = label;
  node.disabled = pendingEstateAction() || Boolean(disabled);
  node.addEventListener("click", action);
  return node;
}

/** 各处卡片共用的「标题 + 说明」两行结构。 */
function itemInfo(title, meta, className = "estate-item-info") {
  const info = document.createElement("div");
  if (className) info.className = className;
  const name = document.createElement("b"); name.textContent = title;
  const note = document.createElement("span"); note.textContent = meta;
  info.append(name, note);
  return info;
}

/** 卡片外壳：外壳 class + 可选图标 + 说明 + 若干控件。 */
function itemCard({ cardClass = "estate-item-card", infoClass, icon = "", iconUrl = null,
                    iconClass = "estate-item-icon", title, meta, controls = [] }) {
  const card = document.createElement("div"); card.className = cardClass;
  if (icon || iconUrl) {
    const badge = document.createElement("span"); badge.className = iconClass;
    const fallback = document.createElement("span"); fallback.className = "estate-asset-fallback"; fallback.textContent = icon;
    badge.append(fallback);
    if (iconUrl) {
      const image = document.createElement("img"); image.alt = "";
      image.addEventListener("load", () => { badge.dataset.assetReady = "true"; });
      image.addEventListener("error", () => image.remove());
      image.src = iconUrl;
      badge.append(image);
    }
    card.append(badge);
  }
  card.append(itemInfo(title, meta, infoClass), ...controls);
  return card;
}

function note(text) {
  const node = document.createElement("p"); node.className = "estate-sheet-note"; node.textContent = text;
  return node;
}

function inventoryFallback(item) {
  return catalogEntry({ seed: "🌱", crop: "🌾", bait: "🪱", fish: "🐟",
    collectible: "🎁", mineral: "◆", supply: "🧪" }, item.kind) || "◆";
}

export function createEstateUI(root, activities = {}) {
  const hudCoins = root.querySelector("[data-estate-coins]");
  const hudLevel = root.querySelector("[data-estate-level]");
  const hudWarehouse = root.querySelector("[data-estate-warehouse]");
  const xpFill = root.querySelector(".estate-xp-fill");
  const sheet = root.querySelector(".estate-sheet");
  const sheetTitle = root.querySelector(".estate-sheet-title");
  const sheetBody = root.querySelector(".estate-sheet-body");
  const close = root.querySelector(".estate-sheet-close");
  let active = null;
  let timer = 0;

  function closeSheet() {
    active = null; sheet.hidden = true; sheetBody.replaceChildren();
  }
  close.addEventListener("click", closeSheet);
  sheet.addEventListener("click", (event) => { if (event.target === sheet) closeSheet(); });

  function show(title) {
    sheetTitle.textContent = title; sheetBody.replaceChildren(); sheet.hidden = false;
  }

  function cropCard(crop, actionLabel, action, multiplier = 1) {
    return itemCard({
      icon: crop.icon || "🌱",
      iconUrl: cropAsset(crop.id, 4),
      title: crop.name,
      meta: `成熟 ${formatDuration(Math.ceil(crop.grow_seconds * multiplier))}`
        + ` · 售价 ${formatCoinsWhole(crop.sell_price)}`
        + ` · 净赚 ${formatCoins(crop.sell_price * crop.yield - crop.seed_price)}金币`
        + ` · ${crop.xp}经验`,
      controls: [button(actionLabel, action)],
    });
  }

  function renderShop() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    show("种子铺");
    const crops = Object.values(snapshot.catalog.crops)
      .sort((a, b) => a.unlock_level - b.unlock_level || a.name.localeCompare(b.name, "zh-CN"));
    const unlockedCount = crops.filter((crop) => snapshot.profile.level >= crop.unlock_level).length;
    sheetBody.append(note(
      `共 ${crops.length} 种作物 · 已解锁 ${unlockedCount} 种。播种后离线也会生长，`
      + `不用浇水；净收益已扣除种子成本，升级土地可缩短等待。`));
    crops.forEach((crop) => {
      const locked = snapshot.profile.level < crop.unlock_level;
      const card = cropCard(crop, locked ? `${crop.unlock_level}级解锁` : `${formatCoinsWhole(crop.seed_price)} 金币 · 买1颗`,
        () => { estateCommand("estate_buy", { kind: "seed", item_id: crop.id, quantity: 1 }); });
      card.querySelector("button").disabled = pendingEstateAction() || locked;
      sheetBody.append(card);
    });
  }

  function renderGeneralStore() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    show("商店");
    sheetBody.append(note("角色皮肤 · 普通皮肤每套 5,000 金币，永久解锁后到衣橱换装。"));
    for (const skin of Object.values(snapshot.catalog.skins)) {
      const owned = snapshot.skins.owned.includes(skin.id);
      const special = skin.unlock === "collection";
      const insufficient = snapshot.coins < skin.price;
      const missing = snapshot.skins.missing_collectibles.map(id => snapshot.catalog.fishing_treasures[id]?.name || id);
      const meta = special ? `指定皮肤（还差 ${snapshot.skins.missing_skins.length} 套）与指定收集品${missing.length ? `：还差${missing.join("、")}` : "（收集品已集齐）"}`
        : skin.unlock === "default" ? "默认皮肤 · 免费" : `${formatCoinsWhole(skin.price)} 金币 · 永久解锁`;
      const card = itemCard({ iconUrl: characterAsset(skin.id, "portrait.png"), title: skin.name, meta,
        controls: [button(owned ? "已解锁" : special ? "收集解锁" : insufficient ? `金币不足 · ${formatCoinsWhole(skin.price)}` : `${formatCoinsWhole(skin.price)} 金币 · 购买`,
          () => estateCommand("estate_buy_skin", { skin_id: skin.id }),
          { disabled: owned || special || insufficient, className: "estate-button estate-button-gold" })] });
      card.dataset.shopSkin = skin.id;
      sheetBody.append(card);
    }
    sheetBody.append(note("宠物"));
    const level = Number(snapshot.profile.pet_level || 0);
    const rules = snapshot.catalog.pet_levels;
    if (!level) {
      const starter = catalogEntry(rules, 1);
      const defendChance = Math.round(starter.defend_chance * 100);
      sheetBody.append(note(`豆豆会跟随主人。有人来偷菜时，它有 ${defendChance}% 概率阻止偷窃，并让对方掉落金币。`));
      sheetBody.append(itemCard({ icon: "🐕", title: "豆豆", meta: `宠物 · Lv.1 · 防守概率 ${defendChance}%`,
        controls: [button(`${formatCoinsWhole(10000)} 金币 · 带豆豆回家`,
          () => estateCommand("estate_pet"), { className: "estate-button estate-button-gold" })] }));
      return;
    }
    const current = catalogEntry(rules, level);
    const next = catalogEntry(rules, level + 1);
    sheetBody.append(itemCard({ icon: "🐕", title: `豆豆 Lv.${level}`,
      meta: `偷菜防守概率 ${Math.round(current.defend_chance * 100)}%`,
      controls: next ? [button(`${formatCoinsWhole(current.upgrade_price)} 金币 · 升到 Lv.${level + 1}`,
        () => estateCommand("estate_pet"), { className: "estate-button estate-button-gold" })]
        : [button("已经达到最高等级", () => {}, { disabled: true })] }));
    sheetBody.append(note("防守成功会保住作物，消耗对方一次偷菜机会，并把对方掉落的 1–1000 金币交给主人。"));
  }

  function renderWarehouse() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    show("仓库");
    if (!snapshot.inventory.length) {
      sheetBody.append(note("谷仓空空的，先去种子铺买种子吧。"));
    }
    for (const item of snapshot.inventory) {
      const controls = [];
      if (item.sellable) {
        controls.push(button(`出售1个 · +${formatCoinsWhole(item.sell_price)}`,
          () => estateCommand("estate_sell", { item_id: item.id, quantity: 1 })));
      } else {
        const keep = document.createElement("span"); keep.className = "estate-tag";
        keep.textContent = catalogEntry({ bait: "钓鱼用品", collectible: "稀有收藏" }, item.kind) || "种植用品";
        controls.push(keep);
      }
      sheetBody.append(itemCard({
        cardClass: "estate-inventory-row", icon: inventoryFallback(item), iconUrl: inventoryAsset(item),
        title: item.name,
        // 谷仓行刻意不带 estate-item-info：那套排版会改字号与配色，
        // 该行的布局由 `.estate-inventory-row > div { flex: 1 }` 单独负责。
        infoClass: "",
        meta: ` × ${item.quantity}`, controls,
      }));
    }
    const actions = document.createElement("div"); actions.className = "estate-sheet-actions";
    actions.append(button("一键出售全部产品", async () => {
      if (await confirmDialog("种子和鱼饵会保留；作物、鱼、矿物及化肥会出售。", { title: "确认出售？" })) {
        estateCommand("estate_sell_all");
      }
    }, { className: "estate-button estate-button-gold" }));
    const level = snapshot.profile.warehouse_level;
    const rule = catalogEntry(snapshot.catalog.warehouse_levels, level);
    if (rule?.upgrade_price != null) {
      const locked = snapshot.profile.level < rule.unlock_level;
      actions.append(button(
        locked ? `${rule.unlock_level}级可扩容` : `扩容仓库 · ${formatCoinsWhole(rule.upgrade_price)}金币`,
        () => estateCommand("estate_buy", { kind: "warehouse", item_id: level, quantity: 1 }),
        { disabled: locked }));
    }
    sheetBody.append(actions);
  }

  function toolPanel(toolType, icon) {
    const snapshot = estateStore.snapshot;
    const owned = snapshot.tools[toolType];
    const rules = snapshot.catalog.tools[toolType];
    const currentRule = owned
      ? catalogEntry(rules, owned.level)
      : (catalogEntry(rules, 1));
    const controls = [];
    if (!owned) {
      controls.push(button(`${formatCoinsWhole(currentRule.price)}金币购买`,
        () => estateCommand("estate_buy_tool", { tool_type: toolType })));
    } else {
      if (owned.durability < owned.max_durability) {
        const cost = repairCost(currentRule, owned.durability);
        controls.push(button(owned.repair_available === false ? "今日已修理" : `修理 · ${formatCoins(cost)}金币`,
          () => estateCommand("estate_repair_tool", { tool_type: toolType }),
          { disabled: owned.repair_available === false }));
      }
      if (currentRule.upgrade_price != null) {
        const next = catalogEntry(rules, owned.level + 1);
        controls.push(button(`${formatCoinsWhole(currentRule.upgrade_price)}金币升级`,
          () => estateCommand("estate_upgrade_tool", { tool_type: toolType }),
          { disabled: snapshot.profile.level < next.unlock_level }));
      }
    }
    sheetBody.append(itemCard({
      cardClass: "estate-tool-card", icon, iconClass: "estate-tool-icon",
      iconUrl: toolAsset(toolType, owned?.level || 1),
      title: owned ? owned.name : currentRule.name,
      meta: owned ? `Lv.${owned.level} · 耐久 ${owned.durability}/${owned.max_durability}` : "尚未拥有",
      controls,
    }));
    return owned;
  }

  function renderFishing() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    show("静谧湖钓场");
    const daily = snapshot.fishing_daily || { retained: 0, remaining: 30, limit: 30 };
    sheetBody.append(note(
      `湖中有 ${Object.keys(snapshot.catalog.fish).length} 种鱼类，`
      + `还有 ${Object.keys(snapshot.catalog.fishing_treasures || {}).length} 种神秘收藏物。`
      + `每轮消耗1份鱼饵和1点耐久，失败也会消耗。按住收线，张力过高时松开卸力；`
      + `钓获时蚯蚓鱼饵有5%、荧光虫饵有10%概率额外获得化肥。高级鱼竿与荧光虫饵能提高稀有鱼机会。今日已保留普通鱼获 ${daily.retained}/${daily.limit}；`
      + `额度用完后普通鱼将自动放生，收集品获取不受影响。`));
    const rod = toolPanel("rod", "🎣");
    if (snapshot.fishing_session) {
      sheetBody.append(button("继续未完成的钓鱼", () => {
        const session = snapshot.fishing_session; closeSheet(); activities.fishing?.(session);
      }, { className: "estate-button estate-button-gold" }));
      return;
    }
    const rodRule = rod && catalogEntry(snapshot.catalog.tools.rod, rod.level);
    const baits = new Map(snapshot.inventory
      .filter((item) => item.kind === "bait")
      .map((item) => [item.bait_id, item.quantity]));
    Object.values(snapshot.catalog.baits).forEach((bait) => {
      const locked = snapshot.profile.level < bait.unlock_level;
      const amount = baits.get(bait.id) || 0;
      const cost = rodRule ? formatCoins(bait.price + rodRule.repair_price / rodRule.max_durability) : null;
      const controls = [
        button(locked ? `${bait.unlock_level}级解锁` : "购买1个",
          () => estateCommand("estate_buy", { kind: "bait", item_id: bait.id, quantity: 1 }),
          { disabled: locked }),
        button("开始钓鱼", async () => {
          try {
            const session = await estateRequest("estate_start_fishing", { bait_id: bait.id });
            closeSheet(); activities.fishing?.(session);
          } catch { /* 弹层由协议统一显示 */ }
        }, {
          className: "estate-button estate-button-gold",
          disabled: locked || !rod || rod.durability < 1 || amount < 1,
        }),
      ];
      sheetBody.append(itemCard({
        title: `🪱 ${bait.name}`,
        meta: `库存 ${amount} · 单价 ${formatCoinsWhole(bait.price)}金币`
          + (cost ? ` · 每轮约${cost}金币（含维修分摊）` : ""),
        controls,
      }));
    });
  }

  function renderMining() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    show("矿洞");
    sheetBody.append(note(
      "每次下矿消耗一点矿镐耐久，空手或触雷也会消耗。越深奖励越好、炸弹越多；"
      + "第三层每敲碎一格有1%概率额外获得化肥。触雷立即结束，已获得的矿物可以保留并结算经验。矿镐每天北京时间 0 点自动修满，"
      + "每天还可主动修理一次。"));
    const pickaxe = toolPanel("pickaxe", "⛏️");
    if (snapshot.mining_run) {
      sheetBody.append(button("继续本次采矿", () => {
        const run = snapshot.mining_run; closeSheet(); activities.mining?.(run);
      }, { className: "estate-button estate-button-gold" }));
      return;
    }
    const pickaxeRule = pickaxe && catalogEntry(snapshot.catalog.tools.pickaxe, pickaxe.level);
    Object.entries(snapshot.catalog.mining_levels).forEach(([level, mine]) => {
      const unlocked = Boolean(pickaxe) && pickaxe.level >= Number(level)
        && snapshot.profile.level >= mine.unlock_level;
      const cost = pickaxeRule ? formatCoins(pickaxeRule.repair_price / pickaxeRule.max_durability) : null;
      sheetBody.append(itemCard({
        title: `第${level}层 · ${mine.name}`,
        meta: `${mine.risk} · ${mine.bombs}枚炸弹 · 庄园 ${mine.unlock_level} 级 · 矿镐 Lv.${level}`
          + (cost ? ` · 每点耐久维修约${cost}金币 · 预留${reservedSlots(snapshot.catalog, pickaxeRule)}格仓位` : ""),
        controls: [button(unlocked ? "进入矿层" : "尚未解锁", async () => {
          try {
            const run = await estateRequest("estate_start_mining", { mine_level: Number(level) });
            closeSheet(); activities.mining?.(run);
          } catch { /* 弹层由协议统一显示 */ }
        }, {
          className: "estate-button estate-button-gold",
          disabled: !unlocked || pickaxe.durability < 1,
        })],
      }));
    });
  }

  function renderPlot(plot) {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    active = { kind: "plot", index: plot.index };
    show(`休闲农田 · 第 ${plot.index + 1} 块`);
    const fertilizerCount = (estateStore.homeSnapshot?.inventory || snapshot.inventory || [])
      .find((item) => item.id === "supply:fertilizer")?.quantity || 0;
    if (estateStore.visit) {
      if (!plot.crop_id) { sheetBody.append(note("这块土地目前没有作物。")); return; }
      const crop = snapshot.catalog.crops[plot.crop_id];
      const ready = Number(plot.ready_at) <= estateNow();
      const limits = snapshot.steal_limits || {};
      sheetBody.append(note(ready
        ? `${crop.name}已经成熟。偷取后整块收成会进入你的仓库。`
        : `${crop.name}还在生长，距离成熟 ${formatDuration(plot.ready_at - estateNow())}。`));
      if (!ready) sheetBody.append(button(`施肥 · 缩短1小时（库存${fertilizerCount}）`,
        () => estateCommand("estate_visit_fertilize", {
          owner_username: estateStore.visit.owner_username, plot_id: plot.index,
        }), { disabled: !fertilizerCount }));
      sheetBody.append(button(ready ? "偷走全部收成" : "尚未成熟", async () => {
        try {
          const result = await estateRequest("estate_steal_crop", {
            owner_username: estateStore.visit.owner_username, plot_id: plot.index,
          });
          closeSheet();
          if (result?.outcome === "defended") {
            void alertDialog(`豆豆发现了你！作物没有偷到，并掉落了 ${formatCoinsWhole(result.coins_dropped)} 金币。`,
              { title: "偷菜失败" });
          } else {
            void alertDialog(`成功获得 ${result.quantity} 个${result.crop_name}。`, { title: "偷菜成功" });
          }
        } catch { /* 协议层统一提示 */ }
      }, { className: "estate-button estate-button-gold",
        disabled: !ready || limits.visitor_remaining < 1 || limits.owner_remaining < 1 }));
      sheetBody.append(note(`你今天还能在这里尝试 ${limits.visitor_remaining ?? 0} 次；`
        + `该庄园今日还可被偷 ${limits.owner_remaining ?? 0} 块。`));
      return;
    }
    if (plot.locked) {
      const rule = catalogEntry(snapshot.catalog.plot_unlocks, plot.index);
      sheetBody.append(note(rule
        ? `需要庄园 ${rule.unlock_level} 级，购买价格 ${formatCoinsWhole(rule.price)} 金币。`
        : "这块土地暂未开放。"));
      if (rule) {
        sheetBody.append(button(`解锁土地 · ${formatCoinsWhole(rule.price)}金币`,
          () => estateCommand("estate_buy", { kind: "plot", item_id: plot.index, quantity: 1 }),
          { disabled: snapshot.profile.level < rule.unlock_level
            || plot.index !== snapshot.profile.plot_count }));
      }
      return;
    }
    if (plot.crop_id) {
      const crop = snapshot.catalog.crops[plot.crop_id];
      const ready = Number(plot.ready_at) <= estateNow();
      const hero = document.createElement("div"); hero.className = "estate-crop-hero";
      const badge = document.createElement("span"); badge.className = "estate-crop-art";
      const fallback = document.createElement("span"); fallback.textContent = crop.icon || "🌱";
      const image = document.createElement("img");
      image.alt = "";
      image.addEventListener("load", () => { badge.dataset.assetReady = "true"; });
      image.addEventListener("error", () => image.remove());
      image.src = cropAsset(crop.id, cropStage(plot, estateNow()));
      badge.append(fallback, image);
      const text = document.createElement("div");
      const name = document.createElement("b"); name.textContent = crop.name;
      const hint = document.createElement("small");
      hint.textContent = ready ? "已经成熟，可以收获啦！"
        : `距离成熟 ${formatDuration(plot.ready_at - estateNow())}`;
      text.append(name, hint); hero.append(badge, text);
      sheetBody.append(hero);
      sheetBody.append(button(ready ? "收获" : "还在生长",
        () => estateCommand("estate_harvest", { plot_id: plot.index }),
        { className: "estate-button estate-button-gold", disabled: !ready }));
      if (!ready) sheetBody.append(button(`施肥 · 缩短1小时（库存${fertilizerCount}）`,
        () => estateCommand("estate_fertilize", { plot_id: plot.index }),
        { disabled: !fertilizerCount }));
      return;
    }
    const seeds = new Map(snapshot.inventory
      .filter((item) => item.kind === "seed")
      .map((item) => [item.crop_id, item.quantity]));
    sheetBody.append(note(`Lv.${plot.land_level} 土地 · 选择一种仓库里的种子。`));
    const multiplier = catalogEntry(snapshot.catalog.land_levels, plot.land_level).multiplier;
    Object.values(snapshot.catalog.crops).forEach((crop) => {
      const amount = seeds.get(crop.id) || 0;
      const card = cropCard(crop, amount ? `播种 · 库存${amount}` : "没有种子",
        () => estateCommand("estate_plant", { plot_id: plot.index, crop_id: crop.id }),
        multiplier);
      card.querySelector("button").disabled = pendingEstateAction() || !amount;
      sheetBody.append(card);
    });
    const landRule = catalogEntry(snapshot.catalog.land_levels, plot.land_level);
    if (landRule?.upgrade_price != null) {
      sheetBody.append(button(`升级土地 · ${formatCoinsWhole(landRule.upgrade_price)}金币`,
        () => estateCommand("estate_buy", { kind: "land", item_id: plot.index, quantity: 1 }),
        { disabled: snapshot.profile.level < landRule.unlock_level }));
    }
  }

  async function renderVisits(query = "") {
    active = { kind: "visits" }; show("拜访其他庄园");
    const search = document.createElement("input");
    search.className = "estate-search"; search.placeholder = "输入账号用户名"; search.value = query;
    const searchButton = button("搜索", () => renderVisits(search.value));
    const searchRow = document.createElement("div"); searchRow.className = "estate-sheet-actions";
    searchRow.append(search, searchButton); sheetBody.append(searchRow, note("无论对方是否在线，都可以拜访已经创建的庄园。"));
    try {
      const [directory, onlineUsers] = await Promise.all([
        estateRequest("estate_list_visits", { query }),
        requestEstateOnlineUsers(),
      ]);
      const entries = directory.map((entry) => ({
        ...entry, online: onlineUsers.has(String(entry.username).toLowerCase()),
      })).sort((left, right) => Number(right.online) - Number(left.online)
        || left.username.localeCompare(right.username, "zh-CN"));
      if (!entries.length) sheetBody.append(note("没有找到可拜访的庄园。"));
      for (const entry of entries) {
        sheetBody.append(itemCard({ title: `${entry.username} 的庄园`,
          meta: entry.online ? "online" : "offline",
          controls: [button("拜访", async () => {
            try {
              await estateRequest("estate_enter_visit", { owner_username: entry.username });
              closeSheet();
            } catch { /* 协议层统一提示 */ }
          })] }));
      }
    } catch { /* 协议层统一提示 */ }
  }

  function renderNotifications() {
    active = { kind: "notifications" }; show("访客记录");
    const rows = estateStore.notifications;
    if (!rows.length) { sheetBody.append(note("最近 30 天没有失窃记录。")); return; }
    for (const row of rows) {
      const defended = row.outcome === "defended";
      const title = row.role === "owner"
        ? (defended ? `豆豆赶跑了 ${row.visitor_username}` : `${row.visitor_username} 偷走了 ${row.crop_name}`)
        : (defended ? `偷取 ${row.owner_username} 的${row.crop_name}失败`
          : `成功偷走 ${row.owner_username} 的${row.crop_name}`);
      const meta = defended ? `豆豆守住了作物 · 掉落 ${formatCoinsWhole(row.coins_dropped)} 金币`
        : `数量 ${row.quantity}`;
      sheetBody.append(itemCard({ title,
        meta: `${meta} · ${new Date(row.created_at * 1000).toLocaleString("zh-CN")}` }));
    }
    const unread = rows.filter((row) => !row.read).map((row) => row.id);
    if (unread.length) estateCommand("estate_mark_notifications_read", { ids: unread });
  }

  /** 面板分派表：`render()` 与 `interact()` 共用同一条链。 */
  const PANELS = {
    shop: () => renderShop(),
    general_store: () => renderGeneralStore(),
    warehouse: () => renderWarehouse(),
    fishing: () => renderFishing(),
    mining: () => renderMining(),
    plot: (plot) => plot && renderPlot(plot),
  };

  function openPanel(kind, plot) {
    active = kind === "plot" ? { kind, index: plot.index } : { kind };
    PANELS[kind]?.(plot);
  }

  /** 当前地块的最新数据；刷新后可能已被收获。 */
  function activePlot() {
    return estateStore.snapshot?.plots.find((item) => item.index === active.index);
  }

  function render() {
    const snapshot = estateStore.snapshot; if (!snapshot) return;
    const home = estateStore.homeSnapshot || snapshot;
    hudCoins.textContent = home.coins == null ? "--" : formatCoins(home.coins);
    hudLevel.textContent = `Lv.${snapshot.profile.level}`;
    hudWarehouse.textContent = home.profile?.warehouse_used == null ? "--"
      : `${home.profile.warehouse_used}/${home.profile.warehouse_capacity}`;
    xpFill.style.width = snapshot.profile.xp_next
      ? `${Math.min(100, snapshot.profile.xp / snapshot.profile.xp_next * 100)}%` : "0%";
    if (!sheet.hidden && active) {
      PANELS[active.kind]?.(active.kind === "plot" ? activePlot() : undefined);
    }
  }

  function interact(target) {
    openPanel(target.kind, target.plot);
  }

  timer = window.setInterval(() => {
    if (!sheet.hidden && active?.kind === "plot") {
      const plot = activePlot();
      if (plot?.crop_id) renderPlot(plot);
    }
  }, 1000);

  return {
    render, interact, closeSheet,
    openVisits: renderVisits,
    openNotifications: renderNotifications,
    openStore() { openPanel("general_store"); },
    openFishing() { openPanel("fishing"); },
    destroy() { window.clearInterval(timer); close.removeEventListener("click", closeSheet); },
  };
}
