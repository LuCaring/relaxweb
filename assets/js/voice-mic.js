/* 语音偏好设置（游戏无关）：按人音量混音器。
   偏好存 localStorage，同一用户跨房间、跨对局继承；以后如需跨设备同步，
   可以只换这个模块的存取实现而不动调用方。
   浏览器 API 全部在函数体内访问，可在 Node 中直接导入做单测。 */

const PEER_VOLUMES_KEY = "voicePeerVolumes";
const PEER_VOLUME_LIMIT = 200;
const PEER_VOLUME_DEFAULT = 100;

function storageGet(key) {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem(key);
  } catch { /* 隐私模式等场景下不可用 */ return null; }
}

function storageSet(key, value) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(key, value);
  } catch { /* 写失败保持内存态 */ }
}

function clampVolume(value, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(0, Math.min(100, Math.round(number)));
}

let peerVolumes = loadPeerVolumes();

function loadPeerVolumes() {
  try {
    const parsed = JSON.parse(storageGet(PEER_VOLUMES_KEY) || "[]");
    if (!Array.isArray(parsed)) return new Map();
    const entries = [];
    for (const pair of parsed) {
      if (Array.isArray(pair) && typeof pair[0] === "string" && pair[0]) {
        entries.push([pair[0], clampVolume(pair[1], PEER_VOLUME_DEFAULT)]);
      }
    }
    return new Map(entries.slice(-PEER_VOLUME_LIMIT));
  } catch { return new Map(); }
}

function persistPeerVolumes() {
  storageSet(PEER_VOLUMES_KEY, JSON.stringify([...peerVolumes.entries()]));
}

function announcePeerVolume(username, volume) {
  try {
    if (typeof document === "undefined") return;
    document.dispatchEvent(new CustomEvent("voicepeerchange", {
      detail: { username, volume },
    }));
  } catch { /* UI 联动事件，失败无碍 */ }
}

/** 某玩家在自己这的收听音量（0–100），默认 100。 */
export function getPeerVolume(username) {
  if (!username) return PEER_VOLUME_DEFAULT;
  return peerVolumes.get(username) ?? PEER_VOLUME_DEFAULT;
}

/** 设置并持久化某玩家的收听音量，返回归一化后的值。 */
export function setPeerVolume(username, volume) {
  if (!username) return PEER_VOLUME_DEFAULT;
  const clamped = clampVolume(volume, PEER_VOLUME_DEFAULT);
  peerVolumes.delete(username);
  peerVolumes.set(username, clamped);
  while (peerVolumes.size > PEER_VOLUME_LIMIT) {
    const oldest = peerVolumes.keys().next().value;
    peerVolumes.delete(oldest);
  }
  persistPeerVolumes();
  announcePeerVolume(username, clamped);
  return clamped;
}
