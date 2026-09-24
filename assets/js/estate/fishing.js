"use strict";

import { estateRequest } from "./protocol.js";
import { estateStore } from "./state.js";
import { FERTILIZER_ASSET, catchAsset, drawAsset, toolAsset } from "./assets.js";
import { tensionStep } from "./rules.js";
import { alertDialog } from "../dialog.js";

/** 张力条转警示色的阈值：纯客户端表现，不参与结算。 */
const DANGER_TENSION = .78;

export function openFishingGame(root, session, options = {}) {
  const catalog = estateStore.snapshot?.catalog;
  const rules = catalog?.fishing_rules;
  const factor = session.rod_level;
  const layer = document.createElement("div");
  layer.className = "estate-minigame fishing-game";
  layer.innerHTML = `
    <canvas aria-label="静谧湖钓鱼"></canvas>
    <div class="minigame-title"><b>静谧湖钓场</b><span>按住收线，松开暂停</span></div>
    <div class="fishing-meters">
      <label>捕获进度<i><span data-catch></span></i></label>
    </div>
    <button class="fishing-reel" type="button">按住<br><b>收线</b></button>
    <button class="minigame-exit" type="button">放弃本次</button>
    <section class="fishing-catch-card" hidden>
      <div class="fishing-catch-rays"></div>
      <div class="fishing-catch-rarity"></div>
      <div class="fishing-catch-portrait"><span class="catch-tail"></span><span class="catch-body"><i></i></span><b>🐟</b><img data-catch-art alt=""></div>
      <h2></h2><p></p>
      <div class="fishing-result-actions"><button class="estate-button estate-button-gold" data-fish-again>继续钓鱼</button><button class="estate-button" data-fish-back>返回静谧湖钓场</button></div>
    </section>`;
  root.append(layer);
  const canvas = layer.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  const reel = layer.querySelector(".fishing-reel");
  const catchBar = layer.querySelector("[data-catch]");
  const trace = [];
  let held = false; let tension = rules.tension_start; let progress = rules.progress_start;
  let accumulator = 0; let last = performance.now(); let frame = 0; let finished = false;

  function resize() {
    const box = canvas.getBoundingClientRect(); const ratio = Math.min(2, globalThis.devicePixelRatio || 1);
    canvas.width = box.width * ratio; canvas.height = box.height * ratio;
  }
  function draw(now) {
    const w = canvas.width; const h = canvas.height;
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, "#75d5d2"); gradient.addColorStop(1, "#176f8c");
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(225,255,239,.45)";
    for (let y = 30; y < h; y += 58) for (let x = -40 + (now * .04 % 90); x < w; x += 120) ctx.fillRect(x, y, 45, 4);
    const fishX = w * (.5 + Math.sin(now * .0023) * .25);
    const fishY = h * (.56 + Math.sin(now * .0041) * .08);
    ctx.fillStyle = "rgba(15,70,85,.65)"; ctx.fillRect(fishX - 34, fishY, 58, 14);
    ctx.beginPath(); ctx.moveTo(fishX - 34, fishY + 7); ctx.lineTo(fishX - 55, fishY - 8); ctx.lineTo(fishX - 55, fishY + 22); ctx.fill();
    ctx.strokeStyle = tension > DANGER_TENSION ? "#ffdf89" : "#edf7d4"; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(w * .5, 0); ctx.quadraticCurveTo(w * .58, h * .3, fishX, fishY); ctx.stroke();
    ctx.fillStyle = "#ef6951"; ctx.fillRect(w * .5 - 5, h * .25, 10, 22);
    ctx.fillStyle = "#fff0c1"; ctx.fillRect(w * .5 - 5, h * .25, 10, 6);
    drawAsset(ctx, toolAsset("rod", session.rod_level, true), w * .5 - 42, 0, 48, 96);
  }
  function step() {
    const force = session.pattern[Math.min(
      session.pattern.length - 1, Math.floor(trace.length / rules.steps_per_frame))];
    trace.push(Boolean(held));
    const next = tensionStep(rules, { tension, progress, held, force, factor });
    tension = next.tension; progress = next.progress;
    catchBar.style.width = `${Math.min(100, progress * 100)}%`;
    if (next.outcome || trace.length >= session.duration_limit * rules.steps_per_frame) void finish();
  }
  function showCatch(result) {
    const caught = result.outcome === "caught";
    const collectible = result.catch_kind === "collectible";
    const card = layer.querySelector(".fishing-catch-card");
    const art = card.querySelector("[data-catch-art]");
    const rarity = Math.max(1, Number(result.rarity || 1));
    card.dataset.rarity = String(rarity); card.classList.toggle("is-caught", caught); card.classList.toggle("is-collectible", collectible);
    card.querySelector(".fishing-catch-rarity").textContent = caught
      ? `${"★".repeat(Math.min(5, rarity))}${rarity > 5 ? ` · 稀有度 ${rarity}` : ""}` : "再接再厉";
    const fertilizer = result.catch_kind === "fertilizer";
    const asset = caught ? (fertilizer ? FERTILIZER_ASSET : catchAsset(result.catch_kind,
      collectible ? result.collectible_id : result.fish_id)) : null;
    card.classList.remove("has-catch-asset"); art.removeAttribute("src");
    if (asset) {
      art.onload = () => card.classList.add("has-catch-asset");
      art.onerror = () => { art.removeAttribute("src"); };
      art.src = asset;
    }
    card.querySelector(".fishing-catch-portrait > b").textContent = collectible ? "🎁" : fertilizer ? "🧪" : caught ? "🐟" : "🌊";
    card.querySelector("h2").textContent = caught ? (result.catch_name || result.fish_name) : result.outcome === "snapped" ? "鱼线断了" : "鱼儿逃走了";
    card.querySelector("p").textContent = result.released
      ? "今日渔获保留额度已用完，本次收获已放归。收集品获取不受影响。"
      : result.duplicate_collectible
      ? `重复收藏品，仓库中已保留 1 件 · 获得 ${result.xp_awarded} 经验`
      : caught
      ? `已放入仓库 · 获得 ${result.xp_awarded} 经验`
      : "鱼饵和耐久已经消耗，控制张力后再试一次。";
    card.hidden = false;
  }
  async function finish() {
    if (finished) return; finished = true; held = false;
    while (trace.length < 20) trace.push(false);
    reel.disabled = true; reel.textContent = "结算中";
    try {
      const result = await estateRequest("estate_finish_fishing", { session_id: session.session_id, trace });
      layer.classList.add("is-result");
      showCatch(result);
      if (result.release_notice && !result.replayed && !result.session_replayed) {
        void alertDialog(
          `依据静谧湖生态资源保护规定，每位玩家每日仅可保留前 ${result.daily_limit} 次普通渔获（含化肥）。`
          + "今日保留额度已用完，本次及今日后续的普通渔获将放归；收集品获取不受影响。感谢您共同维护水域生态。",
          { title: "静谧湖生态保护提示" },
        );
      }
    } catch { layer.remove(); }
  }
  function tick(now) {
    const delta = Math.min(200, now - last); last = now; accumulator += delta;
    while (!finished && accumulator >= 100) { step(); accumulator -= 100; }
    draw(now); frame = requestAnimationFrame(tick);
  }
  const down = (event) => { event.preventDefault(); if (!finished) { held = true; reel.classList.add("held"); } };
  const up = () => { held = false; reel.classList.remove("held"); };
  const keyDown = (event) => { if (["Space", "KeyE"].includes(event.code)) down(event); };
  const keyUp = (event) => { if (["Space", "KeyE"].includes(event.code)) up(); };
  reel.addEventListener("pointerdown", down); window.addEventListener("pointerup", up);
  window.addEventListener("keydown", keyDown); window.addEventListener("keyup", keyUp);
  layer.querySelector(".minigame-exit").addEventListener("click", () => { if (!finished) void finish(); });
  layer.querySelector("[data-fish-again]").addEventListener("click", async (event) => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const next = await estateRequest("estate_start_fishing", { bait_id: session.bait_id });
      layer.remove(); openFishingGame(root, next, options);
    } catch { button.disabled = false; }
  });
  layer.querySelector("[data-fish-back]").addEventListener("click", () => { layer.remove(); options.onBack?.(); });
  resize(); window.addEventListener("resize", resize); frame = requestAnimationFrame(tick);
  const observer = new MutationObserver(() => { if (!layer.isConnected) {
    cancelAnimationFrame(frame); observer.disconnect(); window.removeEventListener("resize", resize);
    window.removeEventListener("pointerup", up); window.removeEventListener("keydown", keyDown); window.removeEventListener("keyup", keyUp);
  }});
  observer.observe(root, { childList: true });
}
