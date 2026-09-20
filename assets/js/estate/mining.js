"use strict";

import { estateRequest } from "./protocol.js";
import { mineralAsset } from "./assets.js";

const ICONS = { empty: "·", extra: "+2", bomb: "💣", stone: "🪨", coal: "◆", copper: "⬟", iron: "⬢", amethyst: "♦", star_gem: "✦" };

function mineralImage(id) {
  if (!(id in ICONS) || ["empty", "extra", "bomb"].includes(id)) return null;
  const image = document.createElement("img"); image.alt = "";
  image.addEventListener("error", () => image.remove());
  image.src = mineralAsset(id);
  return image;
}

function revealCell(cell, outcome) {
  cell.className = `mine-cell revealed outcome-${outcome}`;
  const fallback = document.createElement("span"); fallback.className = "mine-asset-fallback";
  fallback.textContent = ICONS[outcome] || "◆";
  cell.replaceChildren(fallback);
  const image = mineralImage(outcome);
  if (image) {
    image.addEventListener("load", () => { cell.dataset.assetReady = "true"; });
    cell.append(image);
  }
}

function renderLoot(target, loot, emptyText, spacious = false) {
  target.replaceChildren();
  const entries = Object.entries(loot || {});
  if (!entries.length) { target.textContent = emptyText; return; }
  for (const [id, count] of entries) {
    const item = document.createElement("span"); item.className = "mine-loot-item";
    const image = mineralImage(id); if (image) item.append(image);
    const label = document.createElement("span");
    label.textContent = `${ICONS[id] || "◆"}${spacious ? " × " : "×"}${count}`;
    item.append(label); target.append(item);
  }
}

export function openMiningGame(root, run) {
  const layer = document.createElement("div"); layer.className = `estate-minigame mining-game mine-depth-${run.mine_level}`;
  layer.innerHTML = `
    <div class="mine-cave-glow"></div>
    <div class="mine-timbers"><i></i><i></i><i></i></div>
    <div class="minigame-title"><b>小胖矿洞 · ${run.mine_name}</b><span>第 ${run.mine_level} 层 · 小心藏在岩层里的炸弹</span></div>
    <div class="mine-status"><b>剩余敲击 <span data-strikes>${run.strikes_left}</span></b><span data-loot>背包还是空的</span></div>
    <div class="mine-grid" role="grid"></div>
    <div class="mine-blast" hidden><b>BOOM!</b><span>挖到炸弹，本轮采矿结束</span></div>
    <button class="minigame-exit" type="button">带着收获离开</button>`;
  root.append(layer);
  const grid = layer.querySelector(".mine-grid"); let active = true; let busy = false; let loot = run.loot || {};
  grid.style.setProperty("--mine-size", String(run.size));
  const lootLine = layer.querySelector("[data-loot]");
  renderLoot(lootLine, loot, "背包还是空的");
  for (let index = 0; index < run.size * run.size; index += 1) {
    const cell = document.createElement("button"); cell.type = "button"; cell.className = `mine-cell vein-${(index * 7 + run.mine_level) % 5}`;
    cell.setAttribute("aria-label", `矿格 ${index + 1}`); cell.dataset.cell = index; cell.innerHTML = "<i></i><i></i><i></i>";
    cell.addEventListener("click", async () => {
      if (!active || busy || cell.disabled) return; busy = true; cell.disabled = true; cell.classList.add("striking");
      try {
        const result = await estateRequest("estate_mine_cell", { run_id: run.run_id, cell: index });
        loot = result.loot || {}; revealCell(cell, result.outcome);
        layer.querySelector("[data-strikes]").textContent = result.strikes_left;
        renderLoot(lootLine, loot, "这块是空洞");
        if (result.exploded) {
          layer.classList.add("mine-explosion");
          layer.querySelector(".mine-blast").hidden = false;
        }
        if (result.finished) {
          active = false;
          window.setTimeout(() => showResult(result.result), result.exploded ? 650 : 0);
        }
      } catch { cell.disabled = false; cell.classList.remove("striking"); }
      finally { busy = false; }
    });
    if (run.revealed?.includes(index)) {
      const outcome = run.revealed_cells?.[String(index)] || "empty";
      cell.disabled = true; revealCell(cell, outcome);
    }
    grid.append(cell);
  }
  function showResult(result) {
    active = false; grid.querySelectorAll("button").forEach((cell) => { cell.disabled = true; });
    layer.classList.add("is-result");
    const exploded = result?.reason === "bomb";
    const title = layer.querySelector(".minigame-title");
    const heading = document.createElement("b");
    heading.textContent = exploded ? "💥 炸弹引爆，采矿结束" : "本次采矿结束";
    const summary = document.createElement("span");
    if (exploded) summary.append("已找到的矿物成功带回 · ");
    renderLoot(summary, result?.loot || loot, "没有挖到矿物", true);
    summary.append(` · 获得 ${result?.xp_awarded || 0} 经验`);
    title.replaceChildren(heading, summary);
    layer.querySelector(".minigame-exit").textContent = "返回庄园";
  }
  layer.querySelector(".minigame-exit").addEventListener("click", async () => {
    if (busy) return;
    if (!active) { layer.remove(); return; }
    active = false;
    try { showResult(await estateRequest("estate_finish_mining", { run_id: run.run_id })); }
    catch { active = true; }
  });
}
