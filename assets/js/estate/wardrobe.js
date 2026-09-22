"use strict";

import { DEFAULT_SKIN_ID, characterAsset, drawCharacterSkin, loadCharacter, retryCharacter } from "./characters.js";
import { formatCoinsWhole } from "../format.js";
import { estateRequest } from "./protocol.js";
import { estateStore, subscribeEstate } from "./state.js";

/** Preview stays local; the map only equips the server-confirmed profile.skin_id. */
export function createEstateWardrobe(root, { onOpen, onClose, onShop } = {}) {
  const trigger = root.querySelector(".estate-wardrobe-open");
  const layer = document.createElement("div");
  layer.className = "estate-sheet estate-wardrobe"; layer.hidden = true;
  layer.innerHTML = `
    <section role="dialog" aria-modal="true" aria-labelledby="estate-wardrobe-title" tabindex="-1">
      <header><div><small>换个模样，继续热爱田野</small><h2 id="estate-wardrobe-title">角色衣橱</h2></div>
        <button class="estate-sheet-close" type="button" aria-label="关闭衣橱">×</button></header>
      <div class="estate-wardrobe-body">
        <div class="estate-fitting">
          <div class="estate-fitting-label">试衣镜 <span>仅预览</span></div>
          <div class="estate-fitting-stage"><span class="estate-fitting-cloud"></span><canvas width="240" height="228" aria-label="角色动态试穿"></canvas><span class="estate-fitting-load" role="status"></span></div>
          <div class="estate-fitting-directions" role="group" aria-label="预览方向">
            <button type="button" data-direction="left" aria-label="向左预览">←</button>
            <button type="button" data-direction="down" aria-label="正面预览">正面</button>
            <button type="button" data-direction="up" aria-label="背面预览">背面</button>
            <button type="button" data-direction="right" aria-label="向右预览">→</button>
          </div>
          <div class="estate-fitting-actions" role="group" aria-label="预览动作"><button type="button" data-animation="idle">站一会儿</button><button type="button" data-animation="walk">走两步</button></div>
        </div>
        <div class="estate-wardrobe-collection"><div class="estate-collection-title"><b>我的皮肤</b><span>试穿 · 换装 · 商店解锁</span></div><div class="estate-skin-list" role="group" aria-label="可选角色皮肤"></div><p class="estate-wardrobe-note">只换心情，不改属性。<br>换装后自动保存到当前账号。</p></div>
      </div>
      <footer class="estate-wardrobe-footer"><div><b class="estate-selected-name"></b><p class="estate-wardrobe-status" role="status" aria-live="polite"></p></div><button class="estate-button estate-button-gold estate-equip" type="button">穿上这套</button></footer>
    </section>`;
  root.append(layer);
  const dialog = layer.querySelector("section");
  const close = layer.querySelector(".estate-sheet-close");
  const list = layer.querySelector(".estate-skin-list");
  const equip = layer.querySelector(".estate-equip");
  const status = layer.querySelector(".estate-wardrobe-status");
  const loading = layer.querySelector(".estate-fitting-load");
  const canvas = layer.querySelector("canvas"); const ctx = canvas.getContext("2d");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const cards = new Map();
  let selected = DEFAULT_SKIN_ID; let direction = "down"; let animation = "idle";
  let saving = false; let loaded = false; let destroyed = false; let frame = 0; let generation = 0;
  let previousFocus = null; let catalog = {};

  function owned(id) { return estateStore.snapshot?.skins?.owned?.includes(id) || id === DEFAULT_SKIN_ID; }
  function hint(id) {
    if (owned(id)) return id === equipped() ? "这是你当前穿戴的皮肤" : "已解锁，点击「穿上这套」保存";
    if (catalog[id]?.unlock === "collection") {
      const progress = estateStore.snapshot?.skins;
      const names = (progress?.missing_collectibles || []).map(key => estateStore.snapshot.catalog.fishing_treasures?.[key]?.name || key);
      return `解锁条件：指定皮肤（还差 ${progress?.missing_skins?.length || 0} 套），指定收集品${names.length ? `（还差：${names.join("、")}）` : "（已集齐）"}`;
    }
    return `前往商店花费 ${formatCoinsWhole(catalog[id]?.price || 0)} 金币永久解锁，再来衣橱换装`;
  }
  function equipped() { return estateStore.snapshot?.profile.skin_id || DEFAULT_SKIN_ID; }
  function update() {
    const snapshot = estateStore.snapshot;
    trigger.disabled = !snapshot || Boolean(estateStore.visit);
    catalog = snapshot?.catalog.skins || {};
    for (const id of cards.keys()) {
      if (!Object.hasOwn(catalog, id)) { cards.get(id).remove(); cards.delete(id); }
    }
    for (const id of Object.keys(catalog)) {
      const skin = Object.hasOwn(catalog, id) ? catalog[id] : null;
      if (!skin) { cards.get(id)?.remove(); cards.delete(id); continue; }
      if (!cards.has(id)) {
        const card = document.createElement("button");
        card.type = "button"; card.className = "estate-skin-card"; card.dataset.skinId = id;
        const portrait = document.createElement("span"); portrait.className = "estate-skin-portrait";
        const image = document.createElement("img"); image.src = characterAsset(id, "portrait.png"); image.alt = "";
        portrait.append(image);
        const info = document.createElement("span"); info.className = "estate-skin-info";
        const name = document.createElement("b"); name.textContent = skin.name;
        const description = document.createElement("small"); description.textContent = skin.description;
        info.append(name, description);
        const badge = document.createElement("span"); badge.className = "estate-skin-badge";
        card.append(portrait, info, badge);
        card.addEventListener("click", () => { if (!saving) select(id); });
        cards.set(id, card); list.append(card);
      }
      const card = cards.get(id);
      card.setAttribute("aria-pressed", String(id === selected));
      card.disabled = saving;
      const wearing = id === equipped();
      card.classList.toggle("is-equipped", wearing);
      card.querySelector(".estate-skin-badge").textContent = wearing ? "已穿戴" : owned(id) ? "已解锁" : skin.unlock === "collection" ? "收集解锁" : `${formatCoinsWhole(skin.price)} 金币`;
    }
    layer.querySelector(".estate-selected-name").textContent = catalog[selected]?.name || "角色衣橱";
    const locked = !owned(selected);
    const special = catalog[selected]?.unlock === "collection";
    equip.disabled = (locked && special) || saving || !loaded || !snapshot || !Object.hasOwn(catalog, selected) || selected === equipped();
    equip.textContent = saving ? "正在保存…" : selected === equipped() ? "正在穿戴" : locked ? special ? "尚未解锁" : "前往商店" : "穿上这套";
    for (const control of layer.querySelectorAll("[data-direction]")) control.setAttribute("aria-pressed", String(control.dataset.direction === direction));
    for (const control of layer.querySelectorAll("[data-animation]")) control.setAttribute("aria-pressed", String(control.dataset.animation === animation));
  }

  function select(id, retry = false) {
    selected = id; loaded = false;
    const current = ++generation;
    loading.replaceChildren(); loading.textContent = "正在准备新衣…";
    status.textContent = hint(id);
    update();
    (retry ? retryCharacter(id) : loadCharacter(id)).then(() => {
      if (destroyed || current !== generation) return;
      loaded = true; loading.textContent = ""; update();
    }).catch(() => {
      if (destroyed || current !== generation) return;
      loading.textContent = "角色素材暂时没有加载好 ";
      const retryButton = document.createElement("button"); retryButton.type = "button";
      retryButton.textContent = "重试"; retryButton.addEventListener("click", () => select(id, true));
      loading.append(retryButton); status.textContent = "请重试加载，当前穿戴不会改变"; update();
    });
  }

  function draw(now) {
    if (layer.hidden || destroyed) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (loaded) drawCharacterSkin(ctx, selected, { x: 120, y: 205, direction },
      reducedMotion.matches ? 0 : now, { scale: 4, animation });
    frame = requestAnimationFrame(draw);
  }

  function open() {
    if (!estateStore.snapshot || estateStore.visit || !layer.hidden) return;
    onOpen?.(); previousFocus = document.activeElement;
    layer.hidden = false; trigger.setAttribute("aria-expanded", "true");
    direction = "down"; animation = "idle";
    select(equipped()); close.focus(); frame = requestAnimationFrame(draw);
  }
  function hide() {
    if (layer.hidden) return;
    layer.hidden = true; trigger.setAttribute("aria-expanded", "false");
    cancelAnimationFrame(frame); onClose?.();
    if (previousFocus?.isConnected) previousFocus.focus();
  }
  function keyboard(event) {
    if (layer.hidden || document.body.dataset.dialogOpen === "true") return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); hide(); }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button:not(:disabled)")];
    const first = focusable[0]; const last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
      event.preventDefault(); first?.focus();
    }
  }

  for (const control of layer.querySelectorAll("[data-direction]")) control.addEventListener("click", () => { direction = control.dataset.direction; update(); });
  for (const control of layer.querySelectorAll("[data-animation]")) control.addEventListener("click", () => { animation = control.dataset.animation; update(); });
  equip.addEventListener("click", async () => {
    if (equip.disabled) return;
    const id = selected;
    if (!owned(id)) { hide(); onShop?.(); return; }
    saving = true; status.textContent = "正在把新装扮保存到账号…"; update();
    try {
      await estateRequest("estate_set_skin", { skin_id: id }, { timeoutMs: 15000 });
      if (!destroyed) status.textContent = equipped() === id
        ? `已换上「${catalog[id]?.name || id}」，已保存到账号` : "账号装扮已同步，请查看当前穿戴标记";
    } catch (error) {
      if (!destroyed) status.textContent = error.message || "换装未完成，请重试";
    } finally { saving = false; if (!destroyed) update(); }
  });
  close.addEventListener("click", hide);
  layer.addEventListener("click", (event) => { if (event.target === layer) hide(); });
  trigger.addEventListener("click", open);
  window.addEventListener("keydown", keyboard, true);
  const unsubscribe = subscribeEstate(() => { if (!estateStore.snapshot || estateStore.visit) hide(); update(); if (!saving && !layer.hidden) status.textContent = hint(selected); });
  update();
  return {
    open,
    destroy() { destroyed = true; hide(); unsubscribe(); window.removeEventListener("keydown", keyboard, true); trigger.removeEventListener("click", open); layer.remove(); },
  };
}
