/* 装备页：六槽穿戴、背包、属性对比、锁定/出售与待领取。 */

import { rpc } from "../dgn-protocol.js";
import { applyState, dgn, notify, reportError, itemStatsText,
         QUALITY_LABELS, STAT_LABELS, statText } from "../dgn-state.js";
import { navigate } from "../dgn-router.js";
import { visualNode } from "../dgn-assets.js";
import { confirmDialog } from "../../dialog.js";
import { loadingNode } from "./prep.js";

function qualityClass(quality) {
  return `dgn-quality-${quality || "normal"}`;
}

function itemCard(item, { equipped = false, onSelect } = {}) {
  const card = document.createElement("article");
  card.className = `dgn-card dgn-item ${qualityClass(item.quality)}`;
  card.dataset.itemId = item.item_id;

  const head = document.createElement("header");
  head.className = "dgn-item-head";
  const visualBox = document.createElement("span");
  visualBox.className = "dgn-item-visual";
  visualBox.append(visualNode("item", item.visual_id, { size: 32, slot: item.slot }));
  const title = document.createElement("h4");
  title.textContent = item.name;
  title.className = "dgn-item-name";
  if (item.locked) title.textContent = `🔒 ${item.name}`;
  head.append(visualBox, title);
  const quality = document.createElement("span");
  quality.className = `dgn-tag ${qualityClass(item.quality)}`;
  quality.textContent = QUALITY_LABELS[item.quality] || item.quality;
  head.append(quality);
  card.append(head);

  const stats = document.createElement("p");
  stats.className = "dgn-meta";
  stats.textContent = itemStatsText(item) || "无属性";
  card.append(stats);

  const actions = document.createElement("div");
  actions.className = "dgn-item-actions";
  actions.append(...actionButtons(item, equipped, onSelect));
  card.append(actions);
  return card;
}

function actionButtons(item, equipped, onSelect) {
  const buttons = [];
  if (!equipped) {
    const compare = document.createElement("button");
    compare.type = "button";
    compare.className = "dgn-btn dgn-btn-small";
    compare.textContent = "对比";
    compare.addEventListener("click", () => onSelect?.(item));
    buttons.push(compare);
    const equip = document.createElement("button");
    equip.type = "button";
    equip.className = "dgn-btn dgn-btn-small dgn-btn-primary";
    equip.textContent = "穿戴";
    equip.addEventListener("click", () => equipItem(item));
    buttons.push(equip);
  }
  const lock = document.createElement("button");
  lock.type = "button";
  lock.className = "dgn-btn dgn-btn-small";
  lock.textContent = item.locked ? "解锁" : "锁定";
  lock.addEventListener("click", () => lockItem(item));
  buttons.push(lock);
  if (!equipped) {
    const sell = document.createElement("button");
    sell.type = "button";
    sell.className = "dgn-btn dgn-btn-small dgn-btn-danger";
    sell.textContent = `出售 +${item.sell_coins ?? 0}`;
    sell.addEventListener("click", () => {
      void confirmDialog(`出售「${item.name}」，获得 ${item.sell_coins ?? 0} 金币？`,
        { title: "出售装备", tone: "danger" }).then((ok) => { if (ok) void sellItem(item); });
    });
    buttons.push(sell);
  }
  return buttons;
}

function withVersion(payload) {
  return { ...payload, expected_version: dgn.snapshot.profile_version };
}

async function runAction(type, payload, { button } = {}) {
  if (button) button.disabled = true;
  try {
    const data = await rpc(type, withVersion(payload));
    if (data.state) {
      applyState(data.state);
      if (mountEl) renderLoadout(mountEl); // 写操作成功后按最新存档重画本页
    }
    return data;
  } catch (error) {
    reportError(error);
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

function equipItem(item) {
  return runAction("dungeon_equip", { slot: item.slot, item_id: item.item_id });
}

function lockItem(item) {
  return runAction("dungeon_lock_item", { item_id: item.item_id, locked: !item.locked });
}

function sellItem(item) {
  return runAction("dungeon_sell_item", { item_id: item.item_id });
}

async function claimPending(pendingItems, button) {
  const ids = pendingItems.map((item) => item.item_id);
  for (let index = 0; index < ids.length; index += 20) {
    const ok = await runAction("dungeon_claim_items",
      { item_ids: ids.slice(index, index + 20) }, { button });
    if (!ok) return;
  }
  notify("装备已领取入包", "info");
}

function comparePanel(panel, item) {
  panel.replaceChildren(loadingNode("对比中…"));
  rpc("dungeon_compare_item", { item_id: item.item_id }).then((data) => {
    const body = document.createElement("div");
    body.append(Object.assign(document.createElement("h4"),
      { textContent: `替换预览：${item.name}` }));
    const table = document.createElement("table");
    table.className = "dgn-compare";
    for (const [stat, current] of Object.entries(data.current)) {
      const delta = data.delta[stat] ?? 0;
      const row = table.insertRow();
      row.insertCell().textContent = STAT_LABELS[stat] || stat;
      row.insertCell().textContent = statText(stat, current);
      row.insertCell().textContent = `→ ${statText(stat, data.preview.values[stat])}`;
      const deltaCell = row.insertCell();
      deltaCell.textContent = delta === 0 ? "±0" : `${delta > 0 ? "+" : ""}${statText(stat, delta)}`;
      deltaCell.className = delta > 0 ? "dgn-delta-up" : delta < 0 ? "dgn-delta-down" : "dgn-dim";
    }
    body.append(table);
    panel.replaceChildren(body);
  }).catch((error) => {
    reportError(error);
    panel.replaceChildren();
  });
}

function slotCell(slot, equippedItem) {
  const cell = document.createElement("div");
  cell.className = "dgn-slot";
  cell.append(Object.assign(document.createElement("h5"), { textContent: slot }));
  if (equippedItem) {
    cell.append(itemCard(equippedItem, {
      equipped: true,
    }));
  } else {
    const empty = document.createElement("div");
    empty.className = "dgn-slot-empty";
    empty.textContent = "空";
    cell.append(empty);
  }
  return cell;
}

let mountEl = null;

export function renderLoadout(container) {
  mountEl = container;
  const snapshot = dgn.snapshot;
  container.replaceChildren();
  if (!snapshot) {
    container.append(loadingNode());
    return;
  }

  const nav = document.createElement("div");
  nav.className = "dgn-page-nav";
  const prepTab = document.createElement("button");
  prepTab.type = "button";
  prepTab.className = "dgn-btn dgn-btn-small";
  prepTab.textContent = "关卡";
  prepTab.addEventListener("click", () => navigate("#/prep"));
  const loadoutTab = document.createElement("button");
  loadoutTab.type = "button";
  loadoutTab.className = "dgn-btn dgn-btn-small dgn-nav-active";
  loadoutTab.textContent = "装备与背包";
  nav.append(prepTab, loadoutTab);
  container.append(nav);

  const title = document.createElement("h2");
  title.textContent = "装备与背包";
  container.append(title);

  if (snapshot.active_job) {
    const banner = document.createElement("div");
    banner.className = "dgn-banner dgn-banner-warn";
    banner.textContent = "挑战进行中，暂不能换装；装备页可先浏览。";
    container.append(banner);
  }

  const layout = document.createElement("div");
  layout.className = "dgn-loadout-layout";

  const slots = document.createElement("section");
  slots.className = "dgn-card";
  slots.append(Object.assign(document.createElement("h3"), { textContent: "穿戴（六槽）" }));
  const slotGrid = document.createElement("div");
  slotGrid.className = "dgn-slot-grid";
  const byId = new Map(snapshot.items.map((item) => [item.item_id, item]));
  for (const slot of snapshot.catalog.slots) {
    const equippedItem = byId.get(snapshot.loadout[slot]) || null;
    slotGrid.append(slotCell(slot, equippedItem));
  }
  slots.append(slotGrid);

  const compare = document.createElement("section");
  compare.className = "dgn-card dgn-compare-panel";
  compare.append(Object.assign(document.createElement("h3"), { textContent: "属性对比" }));
  compare.append(Object.assign(document.createElement("p"),
    { className: "dgn-meta dgn-dim", textContent: "在背包里点「对比」查看换装差值。" }));
  slots.append(compare);
  layout.append(slots);

  const bagCard = document.createElement("section");
  bagCard.className = "dgn-card";
  bagCard.append(Object.assign(document.createElement("h3"), { textContent: "背包" }));
  const bagItems = snapshot.items.filter((item) => item.location === "bag");
  const bagCount = document.createElement("p");
  bagCount.className = "dgn-meta dgn-dim";
  bagCount.textContent = `已用 ${bagItems.length}/${snapshot.bag_capacity}`;
  bagCard.append(bagCount);

  const pendingItems = snapshot.items.filter((item) => item.location === "pending");
  if (pendingItems.length) {
    const pendingBox = document.createElement("div");
    pendingBox.className = "dgn-pending";
    pendingBox.append(Object.assign(document.createElement("p"),
      { className: "dgn-meta", textContent: `待领取 ${pendingItems.length} 件（背包满时暂存于此）` }));
    const claim = document.createElement("button");
    claim.type = "button";
    claim.className = "dgn-btn dgn-btn-primary dgn-btn-small";
    claim.textContent = "全部领取";
    claim.addEventListener("click", () => claimPending(pendingItems, claim));
    pendingBox.append(claim);
    for (const item of pendingItems) {
      const line = document.createElement("p");
      line.className = "dgn-meta";
      line.textContent = `· ${item.name}（${QUALITY_LABELS[item.quality] || item.quality}）`;
      pendingBox.append(line);
    }
    bagCard.append(pendingBox);
  }

  const bagList = document.createElement("div");
  bagList.className = "dgn-bag-list";
  if (!bagItems.length && !pendingItems.length) {
    bagList.append(Object.assign(document.createElement("p"),
      { className: "dgn-dim", textContent: "背包空空如也，去地下城打点装备吧。" }));
  }
  for (const item of bagItems) {
    const card = itemCard(item, {
      onSelect: (selected) => comparePanel(compare, selected),
    });
    bagList.append(card);
  }
  bagCard.append(bagList);
  layout.append(bagCard);
  container.append(layout);
}
