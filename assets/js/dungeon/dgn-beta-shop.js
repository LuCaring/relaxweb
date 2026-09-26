/**
 * 地下城 Beta 原型 · 局间商店。
 * 卡片池 / 定价 / 重抽均为本地模拟；正式版由服务端发价、发奖事务结算。
 */
import { ITEMS, STATS, WEAPONS, MAX_WEAPON_SLOTS } from "./dgn-beta-data.js";
import { hasFreeWeaponSlot, statsOf } from "./dgn-beta-state.js";
import { affixOdds, CURRENCIES, craftGear, formatAffix, makeGear, RARITY_NAMES } from "./dgn-beta-crafting.js";

const OFFER_COUNT = 4;
const BASE_REROLL_COST = 2;

function offerPrice(offer, wave) {
  const waveMarkup = Math.floor((wave - 1) * 0.6);
  return offer.price + waveMarkup;
}

function rollOffer(rand, wave) {
  const maxTier = Math.min(3, 1 + Math.floor((wave - 1) / 4));
  const kindRoll = rand();
  if (kindRoll >= 0.85) {
    const pool = CURRENCIES.filter((entry) => entry.tier <= maxTier);
    return { kind: "currency", id: pool[Math.floor(rand() * pool.length)].id };
  }
  const kind = kindRoll < 0.38 ? "weapon" : "item";
  const pool = (kind === "weapon" ? WEAPONS : ITEMS).filter((entry) => entry.tier <= maxTier);
  const entry = pool[Math.floor(rand() * pool.length)];
  return { kind, id: entry.id, gear: makeGear(kind, entry.id, wave, rand) };
}

export class Shop {
  constructor({ onMaterialsChanged, onBuy, onError, onSound }) {
    this.hooks = { onMaterialsChanged, onBuy, onError, onSound };
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
      currencyPanel: document.getElementById("dgn-currency-panel"),
      gearDetail: document.getElementById("dgn-gear-detail"),
      affixOdds: document.getElementById("dgn-affix-odds"),
      affixOddsDetail: document.getElementById("dgn-affix-odds-detail"),
    };
    this.el.reroll.addEventListener("click", () => this.reroll());
  }

  /** 音效出口：由入口装配注入，原型页接共享音频引擎，测试可注入记录器。 */
  sound(cue) {
    this.hooks.onSound?.(cue);
  }

  findById(kind, id) {
    const pool = kind === "weapon" ? WEAPONS : kind === "item" ? ITEMS : CURRENCIES;
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
        this.sound("error");
        return;
      }
      run.materials -= cost;
      run.rerollCount += 1;
      this.sound("reroll");
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
    this.sound("select");
    this.render();
  }

  buy(index) {
    const run = this.state;
    const offer = run.shopOffers[index];
    const entry = this.findById(offer.kind, offer.id);
    const price = offerPrice(entry, run.wave);
    if (run.materials < price) {
      this.hooks.onError?.("材料不足");
      this.sound("error");
      return;
    }
    if (offer.kind === "weapon" && !hasFreeWeaponSlot(run)) {
      this.hooks.onError?.("武器栏已满（6），先卖掉一把");
      this.sound("error");
      return;
    }
    const beforeHp = statsOf(run, { ITEMS, WEAPONS }).maxHp;
    run.materials -= price;
    if (offer.kind === "weapon") run.weapons.push(offer.gear);
    else if (offer.kind === "item") run.items.push(offer.gear);
    else run.currencies[offer.id] = (run.currencies[offer.id] || 0) + 1;
    const afterHp = statsOf(run, { ITEMS, WEAPONS }).maxHp;
    run.hp = Math.min(afterHp, run.hp + Math.max(0, afterHp - beforeHp));
    run.shopOffers[index] = null;
    this.sound("buy");
    this.hooks.onBuy?.(offer);
    this.render();
    this.hooks.onMaterialsChanged?.(run.materials);
  }

  sell(index) {
    const run = this.state;
    const weapon = this.findById("weapon", run.weapons[index].id);
    const refund = Math.max(1, Math.floor(weapon.price / 2));
    run.weapons.splice(index, 1);
    run.hp = Math.min(run.hp, statsOf(run, { ITEMS, WEAPONS }).maxHp);
    if (this.selected?.kind === "weapon") this.selected = null;
    run.materials += refund;
    this.sound("buy");
    this.render();
    this.hooks.onMaterialsChanged?.(run.materials);
  }

  rerollCost() {
    return BASE_REROLL_COST + this.state.rerollCount;
  }

  selectedGear() {
    if (!this.selected) return null;
    return this.state[this.selected.kind === "weapon" ? "weapons" : "items"][this.selected.index] || null;
  }

  selectGear(kind, index) {
    this.selected = { kind, index };
    this.sound("select");
    this.render();
  }

  useCurrency(currencyId) {
    const gear = this.selectedGear();
    if (!gear) {
      this.hooks.onError?.("先选择一件装备");
      this.sound("error");
      return;
    }
    if (!this.state.currencies[currencyId]) {
      this.hooks.onError?.("通货数量不足");
      this.sound("error");
      return;
    }
    try {
      const crafted = craftGear(gear, currencyId, Math.random);
      const before = statsOf(this.state, { ITEMS, WEAPONS }).maxHp;
      const pool = this.selected.kind === "weapon" ? this.state.weapons : this.state.items;
      pool[this.selected.index] = crafted;
      this.state.currencies[currencyId] -= 1;
      const after = statsOf(this.state, { ITEMS, WEAPONS }).maxHp;
      this.state.hp = Math.min(after, this.state.hp + Math.max(0, after - before));
      this.sound("craft");
      this.render();
    } catch (error) {
      this.hooks.onError?.(error.message);
      this.sound("error");
    }
  }

  render() {
    const run = this.state;
    const stats = statsOf(run, { ITEMS, WEAPONS });
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
    const ownedSlots = this.state.weapons.map((gear) => ({ gear, entry: this.findById("weapon", gear.id) }));
    while (ownedSlots.length < MAX_WEAPON_SLOTS) ownedSlots.push(null);
    const slots = ownedSlots;
    this.el.ownedWeapons.replaceChildren(...slots.map((owned, index) => {
      const slot = document.createElement("div");
      slot.className = "dgn-wslot";
      if (owned) {
        const { gear, entry: weapon } = owned;
        if (this.selected?.kind === "weapon" && this.selected.index === index) slot.classList.add("dgn-selected");
        slot.title = `${weapon.name} · ${RARITY_NAMES[gear.rarity]} · 物品等级 ${gear.itemLevel}`;
        slot.addEventListener("click", () => this.selectGear("weapon", index));
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
        sell.addEventListener("click", (event) => { event.stopPropagation(); this.sell(index); });
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
      for (const [index, gear] of run.items.entries()) {
        const item = this.findById("item", gear.id);
        const label = document.createElement("span");
        label.title = `${item.name} · ${RARITY_NAMES[gear.rarity]} · ${gear.affixes.map((a) => formatAffix(a, gear)).join("，")}`;
        label.className = "dgn-owned-item" + (this.selected?.kind === "item" && this.selected.index === index ? " dgn-selected" : "");
        label.addEventListener("click", () => this.selectGear("item", index));
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
    this.renderCrafting();
  }

  renderCrafting() {
    const gear = this.selectedGear();
    const entry = gear ? this.findById(gear.kind, gear.id) : null;
    this.el.gearDetail.textContent = gear
      ? `${entry.name} · ${RARITY_NAMES[gear.rarity]} · 物品等级 ${gear.itemLevel}\n${gear.affixes.length ? gear.affixes.map((a) => formatAffix(a, gear)).join("\n") : "尚无词条"}`
      : "点击上方武器或道具选择改造目标";
    this.el.currencyPanel.replaceChildren(...CURRENCIES.map((currency) => {
      const button = document.createElement("button");
      button.className = "dgn-btn dgn-currency-btn";
      button.textContent = `${currency.emoji} ${currency.name} ×${this.state.currencies[currency.id] || 0}`;
      button.title = `${currency.desc} · 每波独立掉落概率 ${(currency.chance * 100).toFixed(1)}%`;
      button.disabled = !gear || !this.state.currencies[currency.id];
      button.addEventListener("click", () => this.useCurrency(currency.id));
      return button;
    }));
    const augmentKind = gear?.rarity === "magic" && gear.affixes.length === 1
      ? (gear.affixes[0].kind === "prefix" ? "suffix" : "prefix") : null;
    const odds = gear ? affixOdds(gear, augmentKind) : [];
    const oddsScope = augmentKind ? "增幅石新增词条" : "自由新增词条";
    const grouped = [1, 2, 3].map((tier) => ({ tier, chance: odds.filter((row) => row.tier === tier)
      .reduce((sum, row) => sum + row.probability, 0) })).filter((row) => row.chance > 0);
    this.el.affixOdds.textContent = gear
      ? `${oddsScope}：${grouped.map((row) => `T${row.tier} ${(row.chance * 100).toFixed(1)}%`).join(" · ") || "无可用词条"}。单条概率按当前可选词条权重计算。`
      : "词条概率会随物品等级和已有词条变化。";
    this.el.affixOddsDetail.replaceChildren();
    if (odds.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "查看每条词条概率";
      const list = document.createElement("ul");
      for (const row of odds.sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name))) {
        const line = document.createElement("li");
        line.textContent = `T${row.tier} ${row.name}：${(row.probability * 100).toFixed(2)}%（权重 ${row.weight}）`;
        list.appendChild(line);
      }
      details.append(summary, list);
      this.el.affixOddsDetail.appendChild(details);
    }
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
    tag.textContent = offer.kind === "currency" ? "通货" : `${offer.kind === "weapon" ? "武器" : "道具"} · 基底 T${entry.tier} · ${RARITY_NAMES[offer.gear.rarity]}`;
    head.append(name, tag);
    card.appendChild(head);

    const desc = document.createElement("div");
    desc.className = "dgn-card-desc";
    desc.textContent = offer.gear
      ? `${entry.desc} · 物品等级 ${offer.gear.itemLevel}${offer.gear.affixes.length ? "\n" + offer.gear.affixes.map((a) => formatAffix(a, offer.gear)).join(" · ") : ""}`
      : entry.desc;
    card.appendChild(desc);

    const foot = document.createElement("div");
    foot.className = "dgn-card-foot";
    const price = document.createElement("span");
    price.className = "dgn-price";
    price.textContent = offerPrice(entry, this.state.wave);
    const buy = document.createElement("button");
    buy.className = "dgn-btn dgn-btn-primary";
    buy.textContent = "购买";
    buy.addEventListener("click", () => this.buy(index));
    foot.append(price, buy);
    card.appendChild(foot);
    return card;
  }
}
