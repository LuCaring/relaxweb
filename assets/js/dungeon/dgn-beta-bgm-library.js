/**
 * 地下城 Beta 原型 · 背景音乐本地曲库。
 *
 * 两条来源，都不经过服务器、不写数据库：
 *  1. 仓库内清单 `assets/dungeon/beta/bgm/library.json`：随仓库分发的自由许可曲目；
 *     音频文件本身在 .gitignore 里，所以清单在、文件按需本地放置。
 *  2. 浏览器内 IndexedDB 曲库：用户在界面里自己挑的文件，只存在本机浏览器里，
 *     刷新后仍在、随时可删，永远不会被提交或上传。
 *
 * 元数据与音频字节分两个 store：列曲目时只读元数据，不会把几十 MB 音频读进内存。
 */

const DB_NAME = "dungeonBetaBgm";
const DB_VERSION = 1;
const META_STORE = "meta";
const DATA_STORE = "data";

export const LOCAL_PREFIX = "local:";
export const MANIFEST_URL = "assets/dungeon/beta/bgm/library.json";
const MAX_TRACK_BYTES = 32 * 1024 * 1024;
const AUDIO_EXT = /\.(mp3|ogg|oga|wav|m4a|aac|flac|opus|webm)$/i;
/** 清单里的曲目可以声明自己属于哪个场景，供"跟随场景自动切换"使用。 */
const SCENE_ROLES = new Set(["explore", "exploreAlt", "boss", "shop"]);

let dbPromise = null;
let manifestCache = null;
const dataCache = new Map();

export function isLocalLibrarySupported() {
  return typeof indexedDB !== "undefined";
}

export function isLocalTrackId(id) {
  return typeof id === "string" && id.startsWith(LOCAL_PREFIX);
}

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (!isLocalLibrarySupported()) {
      reject(new Error("当前浏览器不支持 IndexedDB"));
      return;
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(META_STORE)) db.createObjectStore(META_STORE, { keyPath: "id" });
      if (!db.objectStoreNames.contains(DATA_STORE)) db.createObjectStore(DATA_STORE, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("打开本地曲库失败"));
  });
  return dbPromise;
}

function done(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("本地曲库操作失败"));
  });
}

function isAudioFile(file) {
  if (!file) return false;
  if (typeof file.type === "string" && file.type.startsWith("audio/")) return true;
  return AUDIO_EXT.test(file.name || "");
}

/** 由文件名+大小+修改时间算稳定 id：同一个文件重复添加不会产生重复条目。 */
function localId(file) {
  const key = `${file.name}|${file.size}|${file.lastModified || 0}`;
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return LOCAL_PREFIX + hash.toString(36) + "-" + (file.size || 0).toString(36);
}

export async function listLocalTracks() {
  const db = await openDb();
  const all = await done(db.transaction(META_STORE, "readonly").objectStore(META_STORE).getAll());
  return all.sort((a, b) => (a.addedAt || 0) - (b.addedAt || 0));
}

/** 加入若干本地文件；返回 { added, skipped }，skipped 带原因便于界面提示。 */
export async function addLocalTracks(files) {
  const db = await openDb();
  const added = [];
  const skipped = [];
  for (const file of files || []) {
    if (!isAudioFile(file)) {
      skipped.push({ name: file?.name || "未命名", reason: "不是音频文件" });
      continue;
    }
    if (file.size > MAX_TRACK_BYTES) {
      skipped.push({ name: file.name, reason: "文件超过 32MB" });
      continue;
    }
    try {
      const data = await file.arrayBuffer();
      const meta = {
        id: localId(file),
        name: (file.name || "本地曲目").replace(/\.[^.]+$/, ""),
        size: file.size,
        type: file.type || "",
        gain: 1,
        addedAt: Date.now(),
      };
      const transaction = db.transaction([META_STORE, DATA_STORE], "readwrite");
      transaction.objectStore(META_STORE).put(meta);
      transaction.objectStore(DATA_STORE).put({ id: meta.id, data });
      await new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error || new Error("写入失败"));
        transaction.onabort = () => reject(transaction.error || new Error("写入被中断"));
      });
      dataCache.delete(meta.id);
      added.push(meta);
    } catch (error) {
      skipped.push({ name: file?.name || "未命名", reason: error?.message || "读取失败" });
    }
  }
  return { added, skipped };
}

export async function removeLocalTrack(id) {
  const db = await openDb();
  const transaction = db.transaction([META_STORE, DATA_STORE], "readwrite");
  transaction.objectStore(META_STORE).delete(id);
  transaction.objectStore(DATA_STORE).delete(id);
  await new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("删除失败"));
  });
  dataCache.delete(id);
}

/** 读一条本地曲目的原始字节；命中缓存时不再重复读盘。 */
export async function readLocalTrack(id) {
  const cached = dataCache.get(id);
  if (cached) return cached;
  const db = await openDb();
  const row = await done(db.transaction(DATA_STORE, "readonly").objectStore(DATA_STORE).get(id));
  if (!row?.data) return null;
  const entry = { data: row.data, gain: 1 };
  dataCache.set(id, entry);
  return entry;
}

export function normalizeManifestEntry(raw) {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  const file = typeof raw.file === "string" ? raw.file.trim() : "";
  if (!id || !file || isLocalTrackId(id)) return null;
  const gain = Number(raw.gain);
  const scene = typeof raw.scene === "string" ? raw.scene.trim() : "";
  return {
    id,
    file,
    title: typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : id,
    scene: SCENE_ROLES.has(scene) ? scene : "",
    author: typeof raw.author === "string" ? raw.author.trim() : "",
    license: typeof raw.license === "string" ? raw.license.trim() : "",
    licenseUrl: typeof raw.licenseUrl === "string" ? raw.licenseUrl.trim() : "",
    source: typeof raw.source === "string" ? raw.source.trim() : "",
    desc: typeof raw.desc === "string" ? raw.desc.trim() : "",
    gain: Number.isFinite(gain) && gain > 0 ? Math.min(2, gain) : 1,
  };
}

/** 仓库清单里的自由许可曲目；清单缺失或损坏时返回空数组，不影响合成曲。 */
export async function listBundledTracks() {
  if (manifestCache) return manifestCache;
  try {
    const response = await fetch(MANIFEST_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("manifest missing");
    const parsed = await response.json();
    manifestCache = Array.isArray(parsed?.tracks)
      ? parsed.tracks.map(normalizeManifestEntry).filter(Boolean)
      : [];
  } catch {
    manifestCache = [];
  }
  return manifestCache;
}

/**
 * BGM 引擎需要的字节解析器：本地曲库优先，其次仓库清单里的文件。
 * 找不到时返回 null，由引擎保持静音并在界面上提示。
 */
export async function resolveTrackBytes(id) {
  if (isLocalTrackId(id)) return readLocalTrack(id);
  const entry = (await listBundledTracks()).find((track) => track.id === id);
  if (!entry) return null;
  try {
    const response = await fetch(entry.file, { cache: "force-cache" });
    if (!response.ok) return null;
    return { data: await response.arrayBuffer(), gain: entry.gain };
  } catch {
    return null;
  }
}
