"use strict";

// Only registered, local assets can become a skin; never turn a server ID into an arbitrary URL.
export const CHARACTER_IDS = Object.freeze([
  "berry", "steve", "dva", "little_gwen",
  "jamie", "xiaofei", "weichong", "xiaoxiaopang",
]);
export const DEFAULT_SKIN_ID = "berry";
export const CHARACTER_SCALE = 1.5;
const cache = new Map();

export function characterAsset(id, file = "character.png") {
  const safeId = CHARACTER_IDS.includes(id) ? id : DEFAULT_SKIN_ID;
  return `assets/estate/characters/${safeId}/${file}`;
}

export function loadCharacter(id) {
  const safeId = CHARACTER_IDS.includes(id) ? id : DEFAULT_SKIN_ID;
  if (!cache.has(safeId)) {
    const record = { value: null, error: null, promise: null };
    record.promise = (async () => {
      const response = await fetch(characterAsset(safeId, "character.json"));
      if (!response.ok) throw new Error("角色配置加载失败");
      const manifest = await response.json();
      const assetScale = Number(manifest.assetScale || 1);
      if (manifest.id !== safeId || manifest.frameWidth / assetScale !== 36
          || manifest.frameHeight / assetScale !== 48
          || manifest.anchorX / assetScale !== 18 || manifest.anchorY / assetScale !== 46
          || !manifest.animations?.idle_down) {
        throw new Error("角色图集格式不正确");
      }
      const image = new Image();
      const loaded = new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error("角色图片加载失败"));
      });
      image.src = characterAsset(safeId);
      await loaded;
      record.value = { manifest, image };
      return record.value;
    })().catch((error) => { record.error = error; throw error; });
    // Drawing is synchronous. The wardrobe can still await the original rejection and show a retry.
    record.promise.catch(() => {});
    cache.set(safeId, record);
  }
  return cache.get(safeId).promise;
}

export function retryCharacter(id) {
  if (cache.get(id)?.error) cache.delete(id);
  return loadCharacter(id);
}

/** Resolve the shared grid, animation and foot anchor without touching the DOM. */
export function characterFrame(manifest, player, tick, animation) {
  const requested = player.direction || "down";
  const moving = player.walking > 0 && tick - (player.lastMove ?? -Infinity) < 120;
  const action = animation || (moving ? (player.sprinting ? "run" : "walk") : "idle");
  const fallback = manifest.fallbacks?.[action] || action;
  const hasLeft = manifest.animations[`${action}_left`] || manifest.animations[`${fallback}_left`];
  const direction = requested === "left" && !hasLeft ? "right" : requested;
  const track = manifest.animations[`${action}_${direction}`]
    || manifest.animations[`${fallback}_${direction}`] || manifest.animations[`idle_${direction}`]
    || manifest.animations.idle_down;
  const seconds = !animation && moving ? player.walking / 12 : tick / 1000;
  const index = Math.floor(seconds * track.fps) % track.frames;
  return {
    sx: index * (manifest.frameWidth + manifest.spacing),
    sy: track.row * (manifest.frameHeight + manifest.spacing),
    flipX: requested === "left" && !hasLeft,
    width: manifest.frameWidth, height: manifest.frameHeight,
    anchorX: manifest.anchorX, anchorY: manifest.anchorY,
  };
}

export function drawCharacterSkin(ctx, id, player, tick, options = {}) {
  const safeId = CHARACTER_IDS.includes(id) ? id : DEFAULT_SKIN_ID;
  if (!cache.has(safeId)) void loadCharacter(safeId).catch(() => {});
  const value = cache.get(safeId)?.value;
  if (!value) return false;
  const frame = characterFrame(value.manifest, player, tick, options.animation);
  const assetScale = Number(value.manifest.assetScale || 1);
  const scale = options.scale ?? CHARACTER_SCALE;
  const width = Math.round(frame.width / assetScale * scale);
  const height = Math.round(frame.height / assetScale * scale);
  const x = Math.round(player.x - frame.anchorX / assetScale * scale);
  const y = Math.round(player.y - frame.anchorY / assetScale * scale);
  ctx.save();
  ctx.imageSmoothingEnabled = assetScale > 1;
  if (assetScale > 1) ctx.imageSmoothingQuality = "high";
  if (options.shadow !== false) {
    ctx.fillStyle = "#42613a";
    ctx.fillRect(Math.round(player.x - 9 * scale), Math.round(player.y - scale), Math.round(18 * scale), Math.round(3 * scale));
  }
  if (frame.flipX) { ctx.translate(x + width, y); ctx.scale(-1, 1); }
  ctx.drawImage(value.image, frame.sx, frame.sy, frame.width, frame.height,
    frame.flipX ? 0 : x, frame.flipX ? 0 : y, width, height);
  ctx.restore();
  return true;
}
