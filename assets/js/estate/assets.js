"use strict";
import { drawCharacterSkin, DEFAULT_SKIN_ID } from "./characters.js";

const ROOT = "assets/estate/nature";
export const FERTILIZER_ASSET = `${ROOT}/supplies/fertilizer.png`;
export const ESTATE_SIGN = "assets/estate/signs/estate-sign.png";
export const PET_SPRITE = "assets/estate/pets/doudou-sheet.png";
export const PET_SLEEP_SPRITE = "assets/estate/pets/doudou-sleep-sheet.png";
export const BUILDING_ASSETS = {
  shop: "assets/estate/buildings/seed-shop.png",
  generalStore: "assets/estate/buildings/store.png",
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
  if (item.id === "supply:fertilizer") return FERTILIZER_ASSET;
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

export function drawPetAsset(ctx, pet, tick) {
  const sleeping = pet.mode === "sleeping";
  const sleepRecord = sleeping ? cachedImage(PET_SLEEP_SPRITE) : null;
  const hasSleepSheet = Boolean(sleepRecord?.ready && !sleepRecord.failed);
  const record = hasSleepSheet ? sleepRecord : cachedImage(PET_SPRITE);
  if (!record?.ready || record.failed) return false;
  const rows = { down: 0, left: 1, right: 2, up: 3 };
  const row = hasSleepSheet ? 0 : (rows[pet.direction] ?? 0);
  const moving = pet.walking > 0 && tick - (pet.lastMove || 0) < 160;
  const column = hasSleepSheet ? Math.floor(tick / 650) % 4
    : moving ? Math.floor(pet.walking * .7) % 4 : 0;
  const cellWidth = record.image.naturalWidth / 4;
  const regularCellHeight = record.image.naturalHeight / 4;
  const trimsRightFrameArtifacts = !hasSleepSheet && row === rows.right;
  // 睡觉原稿上下保留了大量透明区；统一裁掉空白并保留相同脚底线，
  // 避免逐帧按内容裁切造成呼吸动画忽大忽小。
  const sourceY = hasSleepSheet ? 64 : row * regularCellHeight;
  // 原始向右行走四帧底部各有两块脱离角色的黑色残留像素；只缩短这行的
  // 源裁切高度，目标尺寸按同比缩短，角色本身的大小与脚底锚点保持不变。
  const sourceHeight = hasSleepSheet ? 644 : trimsRightFrameArtifacts ? 285 : regularCellHeight;
  const baseHeight = sleeping && !hasSleepSheet ? 38 : 54;
  const height = trimsRightFrameArtifacts
    ? Math.round(baseHeight * sourceHeight / regularCellHeight) : baseHeight;
  const width = Math.round(cellWidth / sourceHeight * height);
  const x = Math.round(pet.x - width / 2);
  const y = Math.round(pet.y + 12 - height);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(record.image, column * cellWidth, sourceY, cellWidth, sourceHeight,
    x, y, width, height);
  if (sleeping) {
    const bob = Math.sin(tick / 550) * 2;
    ctx.font = 'bold 11px "Microsoft YaHei", sans-serif';
    ctx.fillStyle = "#fff3c2"; ctx.strokeStyle = "#42513e"; ctx.lineWidth = 2;
    ctx.strokeText("Zzz", pet.x + 15, pet.y - 34 + bob);
    ctx.fillText("Zzz", pet.x + 15, pet.y - 34 + bob);
  }
  ctx.restore();
  return true;
}
