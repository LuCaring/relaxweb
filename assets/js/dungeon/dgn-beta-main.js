/**
 * 地下城 Beta 原型 · 入口装配。
 * 屏幕流转：标题 → 战斗 → 结算 → 商店 → 下一波 …（死亡则结束）
 */
import { ITEMS, UPGRADES, WEAPONS, MAX_WAVE, MAX_WEAPON_SLOTS } from "./dgn-beta-data.js";
import { Arena } from "./dgn-beta-arena.js";
import { Shop } from "./dgn-beta-shop.js";
import { createState, statsOf } from "./dgn-beta-state.js";

function showScreen(id) {
  for (const screen of document.querySelectorAll(".dgn-screen")) {
    screen.classList.toggle("dgn-hidden", screen.id !== id);
  }
}

function el(id) {
  return document.getElementById(id);
}

class BetaApp {
  constructor() {
    this.state = createState();
    this.pendingLevels = 0;
    this.arena = new Arena(el("dgn-arena"), {
      onHud: (hud) => this.renderHud(hud),
      onLevelUp: (levels) => {
        this.pendingLevels += levels.length;
        this.openLevelUp();
      },
      onWaveEnd: (result) => this.onWaveEnd(result),
      onDeath: () => this.onDeath(),
    });
    this.shop = new Shop({
      onMaterialsChanged: () => this.updateMaterialViews(),
      onBuy: () => {},
      onError: (message) => this.toast(message),
    });
    el("dgn-btn-start").addEventListener("click", () => this.startWave());
    el("dgn-btn-shop").addEventListener("click", () => this.openShop());
    el("dgn-btn-nextwave").addEventListener("click", () => {
      this.state.wave += 1;
      this.startWave();
    });
    el("dgn-btn-restart").addEventListener("click", () => this.restart());
    this.updateMaterialViews();
    showScreen("dgn-screen-title");
  }

  startWave() {
    showScreen("dgn-screen-game");
    this.renderWeaponStrip();
    this.arena.start(this.state, this.state.wave);
  }

  openShop() {
    if (this.state.wave >= MAX_WAVE) {
      this.onVictory();
      return;
    }
    this.state.rerollCount = 0;
    showScreen("dgn-screen-shop");
    this.shop.open(this.state);
    this.updateMaterialViews();
  }

  onWaveEnd(result) {
    if (!result.victory) return;
    if (this.state.wave >= MAX_WAVE) {
      this.onVictory();
      return;
    }
    el("dgn-result-title").textContent = `第 ${this.state.wave} 波 结束`;
    el("dgn-result-materials").textContent = result.materialsEarned;
    el("dgn-result-level").textContent = this.state.level;
    showScreen("dgn-screen-result");
  }

  onDeath() {
    el("dgn-over-wave").textContent = this.state.wave;
    el("dgn-over-level").textContent = this.state.level;
    el("dgn-screen-over").querySelector("h2").textContent = "你倒在了遗迹里";
    showScreen("dgn-screen-over");
  }

  onVictory() {
    el("dgn-over-wave").textContent = MAX_WAVE;
    el("dgn-over-level").textContent = this.state.level;
    el("dgn-screen-over").querySelector("h2").textContent = "通关！打穿了全部波次";
    showScreen("dgn-screen-over");
  }

  restart() {
    this.state = createState();
    this.pendingLevels = 0;
    el("dgn-screen-over").querySelector("h2").textContent = "你倒在了遗迹里";
    showScreen("dgn-screen-title");
    this.updateMaterialViews();
  }

  renderHud(hud) {
    const hpRatio = Math.max(0, hud.hp / hud.maxHp);
    el("dgn-hp-fill").style.width = `${hpRatio * 100}%`;
    el("dgn-hp-text").textContent = `${Math.ceil(hud.hp)}/${hud.maxHp}`;
    el("dgn-xp-fill").style.width = `${Math.min(100, (hud.xp / hud.xpNeeded) * 100)}%`;
    el("dgn-level-text").textContent = `Lv.${hud.level}`;
    el("dgn-material-text").textContent = hud.materials;
    el("dgn-wave-text").textContent = `第 ${hud.wave} 波${hud.wave % 3 === 0 ? " · 首领" : ""}`;
    el("dgn-timer-text").textContent = hud.timeLeft;
  }

  renderWeaponStrip() {
    const strip = el("dgn-weapon-strip");
    strip.replaceChildren();
    for (let i = 0; i < MAX_WEAPON_SLOTS; i++) {
      const slot = document.createElement("div");
      const weaponId = this.state.weapons[i];
      if (weaponId) {
        const weapon = WEAPONS.find((entry) => entry.id === weaponId);
        slot.className = "dgn-wslot";
        if (weapon.icon) {
          const img = document.createElement("img");
          img.src = weapon.icon;
          img.alt = weapon.name;
          slot.appendChild(img);
        } else {
          slot.textContent = weapon.emoji;
        }
      } else {
        slot.className = "dgn-wslot-empty";
        slot.textContent = "·";
      }
      strip.appendChild(slot);
    }
  }

  updateMaterialViews() {
    el("dgn-material-text").textContent = this.state.materials;
    el("dgn-shop-materials").textContent = this.state.materials;
  }

  openLevelUp() {
    const overlay = el("dgn-levelup");
    const cards = el("dgn-levelup-cards");
    cards.replaceChildren();
    const choices = this.pickUpgradeChoices(3);
    for (const upgrade of choices) {
      const card = document.createElement("button");
      card.className = "dgn-card";
      card.dataset.rarity = "2";
      card.style.cursor = "pointer";
      const name = document.createElement("div");
      name.className = "dgn-card-name";
      name.textContent = upgrade.name;
      const desc = document.createElement("div");
      desc.className = "dgn-card-desc";
      desc.textContent = upgrade.desc;
      card.append(name, desc);
      card.addEventListener("click", () => {
        this.chooseUpgrade(upgrade);
        this.pendingLevels -= 1;
        if (this.pendingLevels > 0) {
          this.openLevelUp();
        } else {
          overlay.classList.add("dgn-hidden");
          this.arena.resume();
        }
      });
      cards.appendChild(card);
    }
    overlay.classList.remove("dgn-hidden");
  }

  pickUpgradeChoices(count) {
    const pool = [...UPGRADES];
    for (let j = pool.length - 1; j > 0; j--) {
      const k = Math.floor(Math.random() * (j + 1));
      [pool[j], pool[k]] = [pool[k], pool[j]];
    }
    return pool.slice(0, count);
  }

  chooseUpgrade(upgrade) {
    this.state.bonus = this.state.bonus || {};
    this.state.bonus[upgrade.key] = (this.state.bonus[upgrade.key] || 0) + upgrade.value;
    if (upgrade.key === "maxHp") {
      this.state.hp += upgrade.value;
    }
  }

  toast(message) {
    let toast = document.getElementById("dgn-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "dgn-toast";
      toast.style.cssText = [
        "position:fixed", "bottom:24px", "left:50%", "transform:translateX(-50%)",
        "background:#4a3417", "color:#f0e6d8", "border:1px solid #e8a33d",
        "border-radius:8px", "padding:8px 18px", "font-size:13px", "z-index:99",
        "transition:opacity .4s",
      ].join(";");
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = "1";
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { toast.style.opacity = "0"; }, 1600);
  }
}

new BetaApp();
