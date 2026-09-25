"use strict";

import { elements, renderGameView, state } from "../core.js";
import { registerView } from "../registry.js";
import { createEstateInput } from "./input.js";
import { openFishingGame } from "./fishing.js";
import { createEstateMap } from "./map.js";
import { openMiningGame } from "./mining.js";
import { estateCommand, leaveEstateVisit, requestEstate, sendEstatePosition } from "./protocol.js";
import { createEstateUI } from "./ui.js";
import { estateStore, subscribeEstate } from "./state.js";
import { createEstateWardrobe } from "./wardrobe.js";

let cleanup = null;

/** 庄园视图骨架；HUD 由 ui.js 渲染，地图画在 canvas 上，其余是手机端控件。 */
const ESTATE_MARKUP = `
    <canvas class="estate-canvas" aria-label="休闲庄园地图"></canvas>
    <div class="estate-topbar">
      <button class="estate-back" type="button">← 游戏厅</button>
      <button class="estate-visit-open" type="button">拜访</button>
      <button class="estate-visit-return" type="button" hidden>返回我的庄园</button>
      <div class="estate-brand"><span>休闲庄园</span><small>田野 · 湖泊 · 矿脉</small></div>
      <div class="estate-hud-item"><small>金币</small><b data-estate-coins>--</b></div>
      <div class="estate-hud-item"><small>仓库</small><b data-estate-warehouse>--</b></div>
      <div class="estate-level"><b data-estate-level>Lv.1</b><span><i class="estate-xp-fill"></i></span></div>
      <button class="estate-wardrobe-open" type="button" aria-label="打开角色衣橱" aria-haspopup="dialog" aria-expanded="false" title="角色衣橱"><span aria-hidden="true">♧</span> 衣橱</button>
      <button class="estate-audio-open" id="estateAudioSettingsButton" type="button" aria-label="音效设置" title="音效设置">🔊</button>
    </div>
    <div class="estate-loading">正在走进庄园…</div>
    <div class="estate-interact-hint" hidden><kbd>E</kbd><span></span></div>
    <div class="estate-stick"><span class="estate-stick-nub"></span></div>
    <button class="estate-action" type="button"><span>✦</span><small>操作</small></button>
    <div class="estate-help">WASD / 方向键移动 · 左 Shift 疾跑 · E / 空格互动</div>
    <div class="estate-sheet" hidden><section><header><h2 class="estate-sheet-title"></h2><button class="estate-sheet-close" type="button">×</button></header><div class="estate-sheet-body"></div></section></div>`;

function destroyEstateView() {
  cleanup?.(); cleanup = null;
}

function renderEstate() {
  destroyEstateView();
  document.body.classList.add("estate-active");
  const root = document.createElement("section"); root.className = "estate-root";
  root.innerHTML = ESTATE_MARKUP;
  elements.gameMain.replaceChildren(root);
  const input = createEstateInput(root);
  let ui;
  ui = createEstateUI(root, {
    fishing: (session) => openFishingGame(root, session, { onBack: () => ui.openFishing() }),
    mining: (run) => openMiningGame(root, run),
  });
  const wardrobe = createEstateWardrobe(root, {
    onOpen: () => { input.clear(); ui.closeSheet(); },
    onClose: () => input.clear(),
    onShop: () => ui.openStore(),
  });
  const hint = root.querySelector(".estate-interact-hint");
  const map = createEstateMap(root.querySelector("canvas"), input, (target) => {
    input.clear();
    ui.interact(target);
  }, (target) => {
    hint.hidden = !target; hint.querySelector("span").textContent = target ? target.label : "";
  }, sendEstatePosition);
  const visitButton = root.querySelector(".estate-visit-open");
  const returnButton = root.querySelector(".estate-visit-return");
  const brand = root.querySelector(".estate-brand span");
  let notificationsShown = false;
  visitButton.addEventListener("click", () => ui.openVisits());
  returnButton.addEventListener("click", async () => { await leaveEstateVisit(); });
  const unsubscribe = subscribeEstate((snapshot) => {
    root.querySelector(".estate-loading").hidden = Boolean(snapshot);
    const visiting = Boolean(estateStore.visit);
    brand.textContent = visiting ? `${estateStore.visit.owner_username} 的庄园` : "休闲庄园";
    visitButton.hidden = visiting || Number(snapshot?.profile?.level || 0) < 3;
    returnButton.hidden = !visiting;
    ui.render();
    if (!visiting && !notificationsShown && estateStore.notifications.some((row) => !row.read)) {
      notificationsShown = true; ui.openNotifications();
    }
  });
  root.querySelector(".estate-back").addEventListener("click", () => {
    estateCommand("estate_leave_visit");
    state.hallPage = null; state.currentGameId = null; renderGameView();
  });
  root.querySelector(".estate-loading").hidden = Boolean(estateStore.snapshot);
  if (estateStore.snapshot) ui.render(); else requestEstate();
  // 在线停留时定期向服务端确认自动收获，离线时由下次访问或拜访触发补算。
  const autoHarvestPoll = window.setInterval(() => {
    if (!estateStore.visit && Number(estateStore.snapshot?.profile?.penguin_level || 0) > 0) {
      requestEstate();
    }
  }, 60_000);
  cleanup = () => {
    window.clearInterval(autoHarvestPoll);
    document.body.classList.remove("estate-active"); wardrobe.destroy(); unsubscribe(); map.destroy(); input.destroy(); ui.destroy();
  };
}

document.addEventListener("gameviewchange", () => {
  if (state.hallPage !== "estate") destroyEstateView();
});

registerView("estate", renderEstate);
