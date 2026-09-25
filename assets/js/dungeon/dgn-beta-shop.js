/**
 * 地下城 Beta 原型 · 局间商店。
 * 卡片池 / 定价 / 重抽均为本地模拟；正式版由服务端发价、发奖事务结算。
 */
import { ITEMS, STATS, UPGRADES, WEAPONS, MAX_WEAPON_SLOTS } from "./dgn-beta-data.js";
import { hasFreeWeaponSlot, statsOf } from "./dgn-beta-state.js";

const OFFER_COUNT = 4;
const BASE_REROLL_COST = 2;

function offerPrice(offer, wave) {
  const waveMarkup = Math.floor((wave - 1) * 0.6);
  return offer.price + waveMarkup;
}

function rollOffer(rand, wave) {
  const weapon = WEAPONS[Math.floor(rand() * WEAPONS.length)];
  const item = ITEMS[Math.floor(rand() * ITEMS.length)];
  // 武器出现概率 40%，其余为道具
  return rand() < 0.4
    ? { kind: "weapon", id: weapon.id }
    : { kind: "item", id: item.id };
}

export class Shop {
  constructor({ onMaterialsChanged, onBuy, onError }) {
    this.hooks = { onMaterialsChanged, onBuy, onError };
    this.el = {
      materials: document.getElementById("dgn-shop-materials"),
      wave: document.getElementById("dgn-shop-wave"),
      cards: document.getElementById("dgn-shop-cards"),
      reroll: document.getElementById("dgn-btn-reroll"),
      rerollCost: document.getElementById("dgn-reroll-cost"),
      nextWave: document.getElementById("dgn-btn-nextwave"),
      ownedWeapons: document.getElementById("dgn-owned-weapons"),
      ownedItems: document.getElementById("dgn-owned-items"),
      weaponCount: document.getElementById("dgn-weapon-count"),
      itemCount: document.getElementById("dgn-item-count"),
      statsPanel: document.getElementById("dgn-stats-panel"),
    };
    this.el.reroll.addEventListener("click", () => this.reroll());
  }

  findById(kind, id) {
    const pool = kind === "weapon" ? WEAPONS : ITEMS;
    return pool.find((entry) => entry.id === id);
  }

  open(run) {
    this.state = run;
    if (run.shopOffers.length === 0) {
      this.reroll({ free: true });
    } else {
      this.render();
    }
  }

  makeRand() {
    // 每次开店基于波次重置可复现随机（正式版由服务端定序）
    let a = (0xbeef + this.state.wave * 31 + this.state.rerollCount * 7) >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  reroll({ free = false } = {}) {
    const run = this.state;
    const cost = BASE_REROLL_COST + run.rerollCount;
    if (!free) {
      if (run.materials < cost) {
        this.hooks.onError?.("材料不足，无法重抽");
        return;
      }
      run.materials -= cost;
      run.rerollCount += 1;
    }
    const rand = this.makeRand();
    const kept = run.shopOffers.filter((offer) => offer?.locked);
    const rolled = Array.from({ length: OFFER_COUNT - kept.length }, () => rollOffer(rand, run.wave));
    run.shopOffers = [...kept, ...rolled].slice(0, OFFER_COUNT);
    this.render();
    this.hooks.onMaterialsChanged?.(run.materials);
  }

  toggleLock(index) {
    const offer = this.state.shopOffers[index];
    offer.locked = !offer.locked;
    this.render();
  }

  buy(index) {
    const run = this.state;
    const offer = run.shopOffers[index];
    const entry = this.findById(offer.kind, offer.id);
    const price = offerPrice(offer, run.wave);
    if (run.materials < price) {
      this.hooks.onError?.("材料不足");
      return;
    }
    if (offer.kind === "weapon" && !hasFreeWeaponSlot(run)) {
      this.hooks.onError?.("武器栏已满（6），先卖掉一把");
      return;
    }
    run.materials -= price;
    if (offer.kind === "weapon") {
      run.weapons.push(offer.id);
    } else {
      run.items.push(offer.id);
    }
    run.shopOffers[index] = null;
    this.hooks.onBuy?.(offer);
    this.render();
    this.hooks.onMaterialsChanged?.(run.materials);
  }

  sell(index) {
    const run = this.state;
    const weaponId = run.weapons[index];
    const weapon = this.findById("weapon", weaponId);
    const refund = Math.max(1, Math.floor(weapon.price / 2));
    run.weapons.splice(index, 1);
    run.materials += refund;
    this.render();
    this.hooks.onMaterialsChanged?.(run.materials);
  }

  rerollCost() {
    return BASE_REROLL_COST + this.state.rerollCount;
  }

  render() {
    const run = this.state;
    const stats = statsOf(run, { ITEMS });
    this.el.materials.textContent = run.materials;
    this.el.wave.textContent = run.wave;
    this.el.rerollCost.textContent = this.rerollCost();
    this.el.reroll.disabled = run.materials < this.rerollCost();

    this.el.cards.replaceChildren(...this.state.shopOffers.map((offer, index) => {
      if (!offer) {
        const sold = document.createElement("div");
        sold.className = "dgn-card";
        sold.style.opacity = "0.35";
        sold.textContent = "已售出";
        return sold;
      }
      return this.renderCard(offer, index);
    }));

    this.el.weaponCount.textContent = run.weapons.length;
    this.el.itemCount.textContent = run.items.length;
    const ownedSlots = this.state.weapons.map((id) => this.findById("weapon", id));
    while (ownedSlots.length < MAX_WEAPON_SLOTS) ownedSlots.push(null);
    const slots = ownedSlots;
    this.el.ownedWeapons.replaceChildren(...slots.map((weapon, index) => {
      const slot = document.createElement("div");
      slot.className = "dgn-wslot";
      if (weapon) {
        if (weapon.icon) {
          const img = document.createElement("img");
          img.src = weapon.icon;
          img.alt = weapon.name;
          slot.appendChild(img);
        } else {
          slot.textContent = weapon.emoji;
        }
        const sell = document.createElement("button");
        sell.className = "dgn-sell";
        sell.textContent = "×";
        sell.title = `卖掉（返还 ${Math.max(1, Math.floor(weapon.price / 2))}）`;
        sell.addEventListener("click", () => this.sell(index));
        slot.appendChild(sell);
      } else {
        slot.textContent = "+";
      }
      return slot;
    }));

    this.el.ownedItems.replaceChildren();
    if (run.items.length === 0) {
      const none = document.createElement("span");
      none.className = "dgn-none";
      none.textContent = "还没有道具";
      this.el.ownedItems.appendChild(none);
    } else {
      for (const itemId of run.items) {
        const item = this.findById("item", itemId);
        const label = document.createElement("span");
        label.title = `${item.name}：${item.desc}`;
        if (item.icon) {
          const img = document.createElement("img");
          img.src = item.icon;
          img.alt = item.name;
          label.appendChild(img);
        } else {
          label.style.fontSize = "22px";
          label.textContent = item.emoji || "📦";
        }
        this.el.ownedItems.appendChild(label);
      }
    }

    this.el.statsPanel.replaceChildren(...STATS.map((stat) => {
      const row = document.createElement("div");
      row.className = "dgn-stat";
      const name = document.createElement("span");
      name.textContent = stat.name;
      const value = document.createElement("b");
      value.textContent = stat.format(stats[stat.key]);
      row.append(name, value);
      return row;
    }));
  }

  renderCard(offer, index) {
    const entry = this.findById(offer.kind, offer.id);
    const card = document.createElement("div");
    card.className = "dgn-card" + (offer.locked ? " dgn-card-locked" : "");
    card.dataset.rarity = entry.tier;

    const lock = document.createElement("button");
    lock.className = "dgn-card-lock" + (offer.locked ? " on" : "");
    lock.textContent = offer.locked ? "🔒" : "🔓";
    lock.title = "锁定后重抽时保留";
    lock.addEventListener("click", () => this.toggleLock(index));
    card.appendChild(lock);

    const head = document.createElement("div");
    head.className = "dgn-card-head";
    if (entry.icon) {
      const img = document.createElement("img");
      img.src = entry.icon;
      img.alt = entry.name;
      head.appendChild(img);
    } else {
      const emoji = document.createElement("div");
      emoji.style.fontSize = "30px";
      emoji.textContent = entry.emoji || "📦";
      head.appendChild(emoji);
    }
    const name = document.createElement("div");
    name.className = "dgn-card-name";
    name.textContent = entry.name;
    const tag = document.createElement("span");
    tag.className = "dgn-card-tag";
    tag.textContent = offer.kind === "weapon" ? `武器 · T${entry.tier}` : `道具 · T${entry.tier}`;
    head.append(name, tag);
    card.appendChild(head);

    const desc = document.createElement("div");
    desc.className = "dgn-card-desc";
    desc.textContent = entry.desc;
    card.appendChild(desc);

    const foot = document.createElement("div");
    foot.className = "dgn-card-foot";
    const price = document.createElement("span");
    price.className = "dgn-price";
    price.textContent = offerPrice(offer, this.state.wave);
    const buy = document.createElement("button");
    buy.className = "dgn-btn dgn-btn-primary";
    buy.textContent = "购买";
    buy.addEventListener("click", () => this.buy(index));
    foot.append(price, buy);
    card.appendChild(foot);
    return card;
  }
}
