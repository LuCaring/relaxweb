"use strict";

import { DEFAULT_SKIN_ID, drawCharacterSkin } from "./characters.js";

const ROOT = "assets/estate/xiaopang";
const CROP_STAGES = ["01_sprout", "02_seedling", "03_growing", "04_mature"];
const imageCache = new Map();

export function cropAsset(cropId, stage = 4) {
  const safeStage = Math.max(1, Math.min(4, Number(stage) || 1));
  return `${ROOT}/crops/${cropId}/${CROP_STAGES[safeStage - 1]}.png`;
}

export function catchAsset(kind, id) {
  if (kind === "fish") return `${ROOT}/fish/${id}.png`;
  if (kind === "collectible") return `${ROOT}/collectibles/${id}.png`;
  return null;
}

export function mineralAsset(id) {
  return `${ROOT}/minerals/${id}.png`;
}

export function toolAsset(type, level, held = false) {
  if (type !== "rod") return null;
  return `${ROOT}/tools/rod_${Math.max(1, Math.min(3, Number(level) || 1))}_${held ? "held" : "icon"}.png`;
}

export function inventoryAsset(item) {
  if (item.kind === "seed") return cropAsset(item.crop_id, 1);
  if (item.kind === "crop") return cropAsset(item.crop_id, 4);
  if (item.kind === "fish") return catchAsset("fish", item.fish_id);
  if (item.kind === "collectible") return catchAsset("collectible", item.collectible_id);
  if (item.kind === "mineral") return mineralAsset(item.mineral_id);
  return null;
}

export function cropStage(plot, now) {
  if (!plot?.crop_id) return 1;
  if (Number(plot.ready_at) <= now) return 4;
  const duration = Number(plot.ready_at) - Number(plot.planted_at);
  const progress = duration > 0 ? (now - Number(plot.planted_at)) / duration : 0;
  return Math.max(1, Math.min(3, Math.floor(Math.max(0, progress) * 4) + 1));
}

function cachedImage(src) {
  if (!src) return null;
  if (!imageCache.has(src)) {
    const image = new Image();
    const record = { image, ready: false, failed: false };
    image.addEventListener("load", () => { record.ready = true; });
    image.addEventListener("error", () => { record.failed = true; });
    image.src = src;
    imageCache.set(src, record);
  }
  return imageCache.get(src);
}

export function drawAsset(ctx, src, x, y, width, height, options = {}) {
  const record = cachedImage(src);
  if (!record?.ready || record.failed) return false;
  ctx.save();
  ctx.globalAlpha = options.alpha ?? 1;
  ctx.imageSmoothingEnabled = false;
  if (options.flipX) {
    ctx.translate(Math.round(x + width), Math.round(y));
    ctx.scale(-1, 1);
    ctx.drawImage(record.image, 0, 0, width, height);
  } else {
    ctx.drawImage(record.image, Math.round(x), Math.round(y), width, height);
  }
  ctx.restore();
  return true;
}

export function drawPlayerAsset(ctx, player, tick, skinId = DEFAULT_SKIN_ID) {
  return drawCharacterSkin(ctx, skinId, player, tick);
}
