"use strict";

const ROOT = "assets/estate/xiaopang";
export const ESTATE_SIGN = "assets/estate/signs/estate-sign.png";
export const BUILDING_ASSETS = {
  shop: "assets/estate/buildings/seed-shop.png",
  warehouse: "assets/estate/buildings/warehouse.png",
  mining: "assets/estate/buildings/mine.png",
};
const CROP_STAGES = ["01_sprout", "02_seedling", "03_growing", "04_mature"];
const imageCache = new Map();

export const PLAYER_SPRITE = {
  src: `${ROOT}/player/character-sheet.png`,
  // 原稿不是等距图集，逐帧记录透明内容边界，统一以脚底为锚点。
  frames: {
    down: [
      [22, 134, 78, 126], [133, 134, 80, 126], [242, 134, 81, 126], [349, 134, 81, 126],
      [461, 134, 78, 126], [572, 134, 79, 126], [679, 134, 78, 126], [790, 134, 76, 126],
    ],
    up: [
      [30, 267, 72, 126], [139, 267, 74, 126], [246, 267, 76, 126], [354, 267, 75, 126],
      [463, 267, 75, 126], [576, 267, 75, 126], [686, 267, 75, 126], [797, 267, 72, 126],
    ],
    right: [
      [18, 392, 76, 134], [127, 392, 77, 134], [236, 392, 76, 134], [343, 392, 78, 134],
      [451, 392, 79, 134], [562, 392, 79, 134], [675, 392, 81, 134], [785, 392, 80, 134],
    ],
  },
};

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

export function drawPlayerAsset(ctx, player, tick) {
  const record = cachedImage(PLAYER_SPRITE.src);
  if (!record?.ready || record.failed) return false;
  const moving = player.walking > 0 && tick - (player.lastMove || 0) < 120;
  const requested = player.direction || "down";
  const direction = requested === "left" ? "right" : requested;
  const frames = PLAYER_SPRITE.frames[direction] || PLAYER_SPRITE.frames.down;
  const frameIndex = moving ? Math.floor(player.walking * .55) % frames.length : 0;
  const [sx, sy, sw, sh] = frames[frameIndex];
  const height = direction === "right" ? 70 : 68;
  const width = Math.round(sw / sh * height);
  const x = Math.round(player.x - width / 2);
  const y = Math.round(player.y + 20 - height);

  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = "rgba(38,48,39,.28)";
  ctx.fillRect(Math.round(player.x - 13), Math.round(player.y + 14), 26, 6);
  if (requested === "left") {
    ctx.translate(x + width, y);
    ctx.scale(-1, 1);
    ctx.drawImage(record.image, sx, sy, sw, sh, 0, 0, width, height);
  } else {
    ctx.drawImage(record.image, sx, sy, sw, sh, x, y, width, height);
  }
  ctx.restore();
  return true;
}
