"use strict";

import { estateNow, estateStore } from "./state.js";
import {
  PIXEL, drawBuilding, drawBush, drawCharacter, drawCrate, drawFence, drawGroundDetails,
  drawSignpost, drawTree, drawWater, pixelRect,
} from "./art.js";
import { cropAsset, cropStage, drawAsset, drawPlayerAsset } from "./assets.js";
import { drawSprite, stableSprite } from "./sprites.js";

const WORLD = { width: 960, height: 600 };
const BLOCKS = [
  { x: 32, y: 24, w: 250, h: 168 },
  { x: 675, y: 30, w: 245, h: 160 },
  { x: 24, y: 326, w: 230, h: 155 },
  { x: 680, y: 302, w: 280, h: 298 },
  { x: 0, y: 0, w: 960, h: 18 },
];
const PLOT_POSITIONS = [
  [330, 108], [430, 108], [530, 108], [330, 204],
  [430, 204], [530, 204], [330, 300], [430, 300],
];
const ZONES = [
  { kind: "shop", label: "小胖种子铺", x: 150, y: 205 },
  { kind: "warehouse", label: "小胖谷仓", x: 790, y: 208 },
  { kind: "fishing", label: "小胖湖钓场", x: 650, y: 430 },
  { kind: "mining", label: "小胖矿洞", x: 140, y: 500 },
];

// 角色与交互参数：数值本身就是手感，集中命名便于调整。
const PLAYER_BOX = 12;              // 角色碰撞盒的半边长
const SPRINT_SPEED = 190;           // 按住左 Shift 的移动速度（像素/秒）
const WALK_SPEED = 125;
const SPRINT_STEP_RATE = 18;        // 行走动画推进速度
const WALK_STEP_RATE = 12;
const INTERACT_RADIUS = 78;         // 与目标中心的距离小于它才能互动
const EDGE = { left: 18, top: 24, right: 18, bottom: 18 };
// 作物成熟时从农场图集里挑的精灵编号，按作物 ID 稳定散列。
const CROP_SPRITES = [4, 5, 6, 8, 17, 18, 20, 29, 30, 32, 41, 42, 44, 53, 54, 56, 65, 66, 68, 80, 81, 83];

function drawPathSurface(ctx, x, y, w, h, orientation) {
  pixelRect(ctx, x, y, w, h, "#b28b50");
  pixelRect(ctx, x + 3, y + 3, w - 6, h - 6, "#dbbd75");
  pixelRect(ctx, x + 6, y + 6, w - 12, h - 12, "#e3c985");
  const length = orientation === "horizontal" ? w : h;
  for (let offset = 12; offset < length - 8; offset += 28) {
    const px = orientation === "horizontal" ? x + offset : x + 12 + (offset % 3) * 5;
    const py = orientation === "horizontal" ? y + 11 + (offset % 4) * 5 : y + offset;
    pixelRect(ctx, px, py, 7, 3, "#c19d5c");
    pixelRect(ctx, px + 9, py + 8, 4, 3, "#f0d99c");
  }
}

function drawGround(ctx, tick) {
  drawGroundDetails(ctx, WORLD.width, WORLD.height, tick);
  pixelRect(ctx, 286, 72, 346, 410, "#cdb16a");
  pixelRect(ctx, 300, 84, 318, 386, "#e3cb82");
  for (let y = 92; y < 466; y += 32) {
    pixelRect(ctx, 306 + (y % 3) * 4, y, 16, 4, "#c6a761");
    pixelRect(ctx, 586 - (y % 4) * 5, y + 8, 20, 3, "#f1dc98");
  }
  drawPathSurface(ctx, 0, 500, 680, 42, "horizontal");
  drawPathSurface(ctx, 250, 450, 46, 92, "vertical");
  drawWater(ctx, 690, 310, 270, 290, tick);
  for (let y = 316; y < 585; y += 30) {
    pixelRect(ctx, 678, y, 5, 17, "#5c9a49"); pixelRect(ctx, 684, y + 4, 3, 15, "#80b955");
  }
}

function drawPlotFrame(ctx, x, y, plot) {
  const locked = plot.locked;
  const upgraded = Number(plot.land_level) > 1;
  pixelRect(ctx, x + 3, y + 5, 82, 69, "#5e432f55");
  pixelRect(ctx, x, y, 82, 70, locked ? "#6e705e" : "#67402b");
  pixelRect(ctx, x + 4, y + 4, 74, 62, locked ? "#99977a" : upgraded ? "#a65d37" : "#8d5132");
  pixelRect(ctx, x + 6, y + 6, 70, 4, locked ? "#b7b392" : "#c77a47");
  pixelRect(ctx, x + 6, y + 61, 70, 3, locked ? "#7f806c" : "#623a28");
  for (let row = 0; row < 3; row += 1) {
    const furrow = y + 16 + row * 17;
    pixelRect(ctx, x + 8, furrow, 66, 4, locked ? "#7e806d" : "#633b29");
    pixelRect(ctx, x + 10, furrow + 4, 62, 2, locked ? "#aaa78a" : "#b96d40");
  }
  for (const [dx, dy] of [[11, 12], [69, 27], [24, 58], [53, 43]]) {
    pixelRect(ctx, x + dx, y + dy, 3, 2, locked ? "#c2bea0" : "#d18a52");
  }
  for (const [dx, dy] of [[-2, -2], [74, -2], [-2, 62], [74, 62]]) {
    pixelRect(ctx, x + dx, y + dy, 10, 10, "#70462e");
    pixelRect(ctx, x + dx + 2, y + dy + 2, 6, 5, upgraded ? "#ddb65b" : "#a66c3e");
  }
}

function drawPlot(ctx, plot) {
  const [x, y] = PLOT_POSITIONS[plot.index];
  const locked = plot.locked;
  drawPlotFrame(ctx, x, y, plot);
  if (locked) {
    pixelRect(ctx, x + 29, y + 26, 24, 22, "#505854");
    pixelRect(ctx, x + 33, y + 29, 16, 16, "#6b7470");
    pixelRect(ctx, x + 34, y + 17, 14, 15, "#d3c88c");
    pixelRect(ctx, x + 38, y + 20, 6, 10, "#777765");
    pixelRect(ctx, x + 39, y + 35, 4, 7, "#e9d999");
    return;
  }
  if (!plot.crop_id) {
    pixelRect(ctx, x + 18, y + 28, 3, 6, "#4d8848");
    pixelRect(ctx, x + 15, y + 30, 5, 3, "#62a452");
    pixelRect(ctx, x + 61, y + 48, 5, 3, "#d7a06a");
    return;
  }
  const now = estateNow();
  const mature = Number(plot.ready_at) <= now;
  const progress = mature ? 1 : Math.max(.12, (now - plot.planted_at) / (plot.ready_at - plot.planted_at));
  const color = estateStore.snapshot?.catalog?.crops?.[plot.crop_id]?.color || "#e6cb63";
  const cropSprite = stableSprite(plot.crop_id, CROP_SPRITES);
  const custom = cropAsset(plot.crop_id, cropStage(plot, now));
  for (let row = 0; row < 2; row += 1) {
    for (let col = 0; col < 3; col += 1) {
      const px = x + 7 + col * 24; const py = y + 10 + row * 25;
      if (drawAsset(ctx, custom, px, py, 24, 24)) continue;
      if (progress < .25) pixelRect(ctx, px + 4, py + 7, 4, 5, "#86b752");
      else {
        pixelRect(ctx, px + 4, py, 3, 13, "#378446");
        pixelRect(ctx, px, py + 4, 6, 5, "#56ad55");
        if (progress > .48) pixelRect(ctx, px + 5, py - 3, mature ? 9 : 6, mature ? 10 : 7, color);
      }
      if (progress > .42) drawSprite(ctx, "farm", cropSprite, px - 3, py - 7, mature ? 1.05 : .85);
    }
  }
  if (mature) {
    ctx.fillStyle = "#fff2a4"; ctx.font = "bold 18px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("✦", x + 70, y + 16);
  }
}

function nearTarget(player, snapshot) {
  let best = null;
  const candidates = [...ZONES];
  for (const plot of snapshot?.plots || []) {
    const pos = PLOT_POSITIONS[plot.index];
    candidates.push({ kind: "plot", label: plot.locked ? "解锁土地" : plot.crop_id ? "查看作物" : "播种", plot, x: pos[0] + 41, y: pos[1] + 35 });
  }
  for (const zone of candidates) {
    const distance = Math.hypot(player.x - zone.x, player.y - zone.y);
    if (distance < INTERACT_RADIUS && (!best || distance < best.distance)) best = { ...zone, distance };
  }
  return best;
}

function collides(x, y) {
  if (x < EDGE.left || y < EDGE.top
    || x > WORLD.width - EDGE.right || y > WORLD.height - EDGE.bottom) return true;
  return BLOCKS.some((block) => x + PLAYER_BOX > block.x && x - PLAYER_BOX < block.x + block.w
    && y + PLAYER_BOX > block.y && y - PLAYER_BOX < block.y + block.h);
}

export function createEstateMap(canvas, input, onInteract, onTarget) {
  const ctx = canvas.getContext("2d");
  const player = { x: 275, y: 440, facing: 1, direction: "down", walking: 0, lastMove: 0 };
  let frame = 0;
  let last = performance.now();
  let target = null;

  function resize() {
    const box = canvas.getBoundingClientRect();
    const ratio = Math.min(2, globalThis.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.floor(box.width * ratio));
    canvas.height = Math.max(1, Math.floor(box.height * ratio));
    ctx.imageSmoothingEnabled = false;
  }

  function draw() {
    const scale = Math.max(canvas.width / WORLD.width, canvas.height / WORLD.height);
    const ox = Math.min(0, Math.max(canvas.width - WORLD.width * scale,
      canvas.width / 2 - player.x * scale));
    const oy = Math.min(0, Math.max(canvas.height - WORLD.height * scale,
      canvas.height / 2 - player.y * scale));
    ctx.setTransform(scale, 0, 0, scale, ox, oy);
    drawGround(ctx, performance.now());
    drawBuilding(ctx, 34, 24, 242, 166, "#efbd70", "#b8503d", "小胖种子铺");
    drawBuilding(ctx, 684, 28, 230, 164, "#dca75d", "#844237", "小胖谷仓");
    drawBuilding(ctx, 24, 326, 230, 155, "#776d66", "#4d4655", "小胖矿洞", "#b9a7bd");
    drawFence(ctx, 302, 72, 316);
    drawFence(ctx, 302, 478, 316);
    drawBush(ctx, 280, 28, true); drawBush(ctx, 622, 42); drawBush(ctx, 642, 164, true);
    drawCrate(ctx, 652, 158); drawCrate(ctx, 658, 134);
    drawSprite(ctx, "farm", 74, 55, 150, 2.5);
    drawSprite(ctx, "farm", 90, 218, 145, 2.5);
    drawSprite(ctx, "farm", 98, 707, 144, 2.5);
    drawSprite(ctx, "farm", 123, 752, 145, 2.5);
    drawSprite(ctx, "town", 115, 52, 430, 3);
    drawSprite(ctx, "town", 116, 93, 430, 3);
    drawSprite(ctx, "town", 117, 184, 430, 3, { flipX: true });
    drawSignpost(ctx, 604, 370, "小胖湖");
    for (let x = 6; x < 650; x += 72) {
      drawTree(ctx, x, 540 + (x % 3) * 3, x % 2, performance.now());
    }
    [[292, 30, 29], [317, 43, 2], [638, 64, 16], [650, 214, 93], [278, 500, 94]].forEach(([x, y, sprite]) => {
      drawSprite(ctx, "town", sprite, x, y, 2.5);
    });
    (estateStore.snapshot?.plots || []).forEach((plot) => drawPlot(ctx, plot));
    pixelRect(ctx, 625, 420, 75, 12, "#835431");
    pixelRect(ctx, 645, 397, 8, 38, "#67452f");
    pixelRect(ctx, 682, 397, 8, 38, "#67452f");
    ctx.fillStyle = "#f5eed2"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("小胖湖钓场", 820, 292);
    if (target) {
      ctx.strokeStyle = "#fff4a8"; ctx.lineWidth = 3; ctx.setLineDash([6, 4]);
      ctx.strokeRect(target.x - 24, target.y - 24, 48, 48); ctx.setLineDash([]);
    }
    if (!drawPlayerAsset(ctx, player, performance.now())) drawCharacter(ctx, player, performance.now());
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  function tick(now) {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    const length = Math.hypot(input.vector.x, input.vector.y) || 1;
    const speed = input.sprinting ? SPRINT_SPEED : WALK_SPEED;
    const dx = input.vector.x / length * speed * dt;
    const dy = input.vector.y / length * speed * dt;
    if (dx && !collides(player.x + dx, player.y)) player.x += dx;
    if (dy && !collides(player.x, player.y + dy)) player.y += dy;
    if (dx || dy) {
      player.walking += dt * (input.sprinting ? SPRINT_STEP_RATE : WALK_STEP_RATE); player.lastMove = now;
      if (Math.abs(dx) > Math.abs(dy)) {
        player.facing = Math.sign(dx); player.direction = dx < 0 ? "left" : "right";
      } else player.direction = dy < 0 ? "up" : "down";
    }
    const nextTarget = nearTarget(player, estateStore.snapshot);
    if (nextTarget?.kind !== target?.kind || nextTarget?.plot?.index !== target?.plot?.index) {
      target = nextTarget; onTarget(target);
    } else target = nextTarget;
    if (input.consumeAction() && target) onInteract(target);
    draw();
    frame = requestAnimationFrame(tick);
  }

  resize();
  window.addEventListener("resize", resize);
  frame = requestAnimationFrame(tick);
  return {
    player,
    destroy() { cancelAnimationFrame(frame); window.removeEventListener("resize", resize); },
  };
}
