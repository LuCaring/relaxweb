/* visual_id → 占位表现。
 *
 * 美术尚未交付：优先尝试加载 /assets/dungeon/<类别>/<visual_id>.png（命名
 * 规则见 assets/dungeon/README.md），加载失败或无图时保持 emoji 色块占位，
 * 绝不阻塞事件播放与结算展示。换正式图只动这里和素材目录。
 */

const KIND_PATH = { enemy: "enemies", item: "items" };

const ENEMY_EMOJI = { normal: "🟢", elite: "🟣", boss: "👹" };
const SLOT_EMOJI = {
  weapon: "⚔️", helmet: "🪖", chest: "🛡️", belt: "🧵", boots: "👢", accessory: "📿",
};
const PLAYER_EMOJI = "🧙";
const FALLBACK_EMOJI = "📦";

function glyphNode(glyph, size) {
  const node = document.createElement("span");
  node.className = "dgn-glyph";
  node.style.fontSize = `${size}px`;
  node.textContent = glyph;
  return node;
}

function fallbackGlyph(kind, { enemyType = "normal", slot = null } = {}) {
  if (kind === "enemy") return ENEMY_EMOJI[enemyType] || ENEMY_EMOJI.normal;
  if (kind === "item") return SLOT_EMOJI[slot] || FALLBACK_EMOJI;
  if (kind === "player") return PLAYER_EMOJI;
  return FALLBACK_EMOJI;
}

/**
 * 同步返回一个占位节点：先渲染 emoji 兜底，异步加载
 * /assets/dungeon/<类别>/<visual_id>.png，加载成功后原位替换。
 */
export function visualNode(kind, visualId, { size = 64, enemyType = "normal", slot = null } = {}) {
  const glyph = glyphNode(fallbackGlyph(kind, { enemyType, slot }), size);
  const pathKind = KIND_PATH[kind];
  if (!pathKind || !visualId || !/^[A-Za-z0-9_-]{1,96}$/.test(visualId)) return glyph;
  const image = document.createElement("img");
  image.className = "dgn-visual";
  image.width = size;
  image.height = size;
  image.alt = "";
  image.loading = "lazy";
  image.addEventListener("load", () => glyph.replaceWith(image), { once: true });
  image.src = `/assets/dungeon/${pathKind}/${encodeURIComponent(visualId)}.png`;
  return glyph;
}
