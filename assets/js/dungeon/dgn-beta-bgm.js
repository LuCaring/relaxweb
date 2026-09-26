/**
 * 地下城 Beta 原型 · 背景音乐（BGM）引擎。
 *
 * 内置曲目全部用 WebAudio 实时合成，没有音频文件：曲目数据与“某一步该弹哪些音”是纯函数
 * （可在 Node 里直接单测），发声部分复用全站共享的 AudioContext 与 masterGain——
 * 因此全站静音照旧生效，而 BGM 音量与音效音量彼此独立。
 *
 * 本地曲库（用户自己选的文件）通过 setBufferResolver 注入，本模块不关心文件从哪来。
 * 本文件刻意不在模块顶层 import 共享引擎（改为 initBgm 内动态 import），
 * 这样纯函数可以脱离浏览器与 audio.js 直接测试。
 */

export const BGM_STORAGE_KEY = "dungeonBetaBgm";
export const STEPS_PER_BAR = 16;
/** 每步 = 十六分音符。 */
const STEPS_PER_BEAT = 4;
const SCHEDULE_AHEAD = 0.45;
const TICK_MS = 110;
const VOICE_LIMIT = 40;
/** 总输出留出的余量，避免合成音叠加后削波。 */
const HEADROOM = 0.5;
export const DEFAULT_BGM_SETTINGS = Object.freeze({
  trackId: "ruins-hall", volume: 55, auto: true, sceneSource: "synth",
});

/** 曲目表。`silent` 是“关闭背景音乐”这个选项本身，便于界面用单选列表统一渲染。 */
export const BGM_TRACKS = Object.freeze([
  { id: "off", name: "关闭背景音乐", silent: true, desc: "完全静音，只保留游戏音效" },
  {
    id: "ruins-hall",
    name: "遗迹回廊",
    desc: "A 小调 · 舒缓铺底，适合普通波次",
    tempo: 72,
    root: 110,
    gain: 0.9,
    scale: [0, 3, 5, 7, 10, 12, 15],
    progression: [[0, 3, 7], [-4, 0, 5], [-2, 3, 7], [-5, 0, 3]],
    bass: { wave: "sine", gain: 0.3, duration: 1.7, attack: 0.05, octave: -1, pattern: [0, 8] },
    pad: { wave: "sine", gain: 0.1, duration: 3.6, attack: 1.1, octave: 0, pattern: [0, 8] },
    lead: { wave: "triangle", gain: 0.1, duration: 0.55, attack: 0.02, octave: 1, pattern: [2, 6, 10, 14] },
    perc: null,
  },
  {
    id: "deep-vault",
    name: "幽深地窖",
    desc: "E 小调 · 更慢更空，适合探索后半段",
    tempo: 58,
    root: 82.41,
    gain: 0.85,
    scale: [0, 2, 3, 7, 10, 12, 14],
    progression: [[0, 3, 7], [-3, 0, 5], [0, 3, 8], [-5, -2, 3]],
    bass: { wave: "sine", gain: 0.3, duration: 2.6, attack: 0.08, octave: -1, pattern: [0] },
    pad: { wave: "sine", gain: 0.13, duration: 4.4, attack: 1.6, octave: 0, pattern: [0] },
    lead: { wave: "triangle", gain: 0.08, duration: 0.7, attack: 0.03, octave: 1, pattern: [6, 14] },
    perc: null,
  },
  {
    id: "brute-raid",
    name: "巨物来袭",
    desc: "G 小调 · 鼓点驱动，首领波专用",
    tempo: 104,
    root: 98,
    gain: 0.8,
    scale: [0, 3, 5, 6, 7, 10, 12],
    progression: [[0, 3, 7], [0, 3, 7], [-2, 1, 5], [-4, -1, 3]],
    bass: { wave: "triangle", gain: 0.32, duration: 0.42, attack: 0.01, octave: -1, pattern: [0, 4, 8, 12] },
    pad: { wave: "sine", gain: 0.09, duration: 1.9, attack: 0.5, octave: 0, pattern: [0, 8] },
    lead: { wave: "triangle", gain: 0.1, duration: 0.34, attack: 0.01, octave: 1, pattern: [0, 3, 6, 10, 13] },
    perc: {
      kick: { pattern: [0, 4, 8, 12], gain: 0.55, from: 120, to: 44, duration: 0.18 },
      hat: { pattern: [2, 6, 10, 14], gain: 0.12, frequency: 7200, duration: 0.045 },
    },
  },
  {
    id: "campfire",
    name: "篝火歇脚",
    desc: "C 大调 · 温暖放松，商店与整备",
    tempo: 66,
    root: 130.81,
    gain: 0.8,
    scale: [0, 2, 4, 7, 9, 12, 14],
    progression: [[0, 4, 7], [-3, 2, 5], [-5, 0, 4], [-7, -3, 0]],
    bass: { wave: "sine", gain: 0.26, duration: 1.9, attack: 0.06, octave: -1, pattern: [0, 8] },
    pad: { wave: "sine", gain: 0.12, duration: 3.4, attack: 1.2, octave: 0, pattern: [0] },
    lead: { wave: "sine", gain: 0.09, duration: 0.6, attack: 0.03, octave: 1, pattern: [4, 12] },
    perc: null,
  },
]);

/** 场景 → 曲目。探索曲按波次轮换，避免一局里反复听同一首。 */
export const SCENE_TRACKS = Object.freeze({
  menu: "ruins-hall",
  explore: "ruins-hall",
  exploreAlt: "deep-vault",
  boss: "brute-raid",
  shop: "campfire",
});

export function trackById(id) {
  return BGM_TRACKS.find((track) => track.id === id) || null;
}

/**
 * 某个场景在当前波次应该放哪首。
 * `source` 为 `synth` 时用内置合成曲；为 `library` 时优先用清单里声明了同场景角色的曲目，
 * 该场景没有可用曲目时自动回落到合成曲，不会变成没声音。
 */
export function sceneTrackForScene(scene, wave = 1, { source = "synth", library = [] } = {}) {
  const role = scene === "explore"
    ? (wave % 2 === 0 ? "exploreAlt" : "explore")
    : (SCENE_TRACKS[scene] ? scene : "menu");
  if (source === "library") {
    const match = (Array.isArray(library) ? library : []).find((track) => track?.scene === role && track.id);
    if (match) return match.id;
  }
  return SCENE_TRACKS[role] || SCENE_TRACKS.menu;
}

/** 一个完整循环有多少步（和弦进行走完一遍）。 */
export function loopSteps(track) {
  return STEPS_PER_BAR * track.progression.length;
}

const freqOf = (track, semitone) => Math.round(track.root * 2 ** (semitone / 12) * 1000) / 1000;

/**
 * 第 step 步应该发声的音符。纯函数：不碰 WebAudio，也不依赖时间。
 * step 超过一个循环长度时按循环取模，因此可以直接算出任意时刻的乐句。
 */
export function notesForStep(track, step) {
  const notes = [];
  if (!track || track.silent || !track.progression?.length) return notes;
  const bars = track.progression.length;
  // 小节号对进行长度取模：所有乐句都只依赖 bar % bars，整首才是真正可无缝循环的。
  const bar = Math.floor(step / STEPS_PER_BAR) % bars;
  const beat = step % STEPS_PER_BAR;
  const chord = track.progression[bar];
  if (track.bass?.pattern.includes(beat)) {
    notes.push({ kind: "bass", freq: freqOf(track, chord[0] + track.bass.octave * 12) });
  }
  if (track.pad?.pattern.includes(beat)) {
    for (const interval of chord) {
      notes.push({ kind: "pad", freq: freqOf(track, interval + track.pad.octave * 12) });
    }
  }
  if (track.lead?.pattern.includes(beat)) {
    const order = track.lead.pattern.indexOf(beat);
    const index = (bar * 2 + order) % track.scale.length;
    notes.push({ kind: "lead", freq: freqOf(track, chord[0] + track.scale[index] + track.lead.octave * 12) });
  }
  const perc = track.perc || {};
  if (perc.kick?.pattern.includes(beat)) notes.push({ kind: "kick", freq: perc.kick.from, toFreq: perc.kick.to });
  if (perc.hat?.pattern.includes(beat)) notes.push({ kind: "hat", freq: perc.hat.frequency });
  return notes;
}

/** 读回来的设置一律先过这里，坏数据不该让播放崩掉。 */
export function normalizeBgmSettings(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const volume = Number(source.volume);
  const trackId = typeof source.trackId === "string" && source.trackId.length > 0 && source.trackId.length <= 64
    ? source.trackId : DEFAULT_BGM_SETTINGS.trackId;
  return {
    trackId,
    volume: Number.isFinite(volume) ? Math.max(0, Math.min(100, Math.round(volume))) : DEFAULT_BGM_SETTINGS.volume,
    auto: source.auto !== false,
    sceneSource: source.sceneSource === "library" ? "library" : "synth",
  };
}

/* =========================================================
   以下为发声部分：需要浏览器与共享引擎。
========================================================= */

let shared = null;              // assets/js/game-audio.js 的模块命名空间
let graph = null;               // { context, masterGain }
let bgmGain = null;
let noise = null;
let timer = null;
let voices = new Set();
let settings = { ...DEFAULT_BGM_SETTINGS };
let current = null;             // { kind: "synth"|"buffer", id }
let currentScene = "menu";
let step = 0;
let nextStepTime = 0;
let bufferResolver = null;
let loadingLocal = false;
let localGain = 1;
let listeners = new Set();
let libraryTracks = [];
let missingId = null;
const decodedCache = new Map();

function notify() {
  const snapshot = bgmState();
  for (const listener of listeners) {
    try { listener(snapshot); } catch { /* 界面回调不能影响播放 */ }
  }
}

export function bgmState() {
  return {
    trackId: settings.trackId,
    volume: settings.volume,
    auto: settings.auto,
    sceneSource: settings.sceneSource,
    scene: currentScene,
    playing: settings.trackId !== "off" && Boolean(current) && Boolean(shared?.isGameAudioAudible?.()),
    // 选中的曲目文件不在本地（清单已登记但音频未安装）时给出 id，界面据此提示。
    missing: missingId && missingId === settings.trackId ? missingId : null,
    // 音乐链路的实际输出增益，供界面/测试确认音量确实生效。
    gain: Number(bgmGain?.gain?.value) || 0,
  };
}

/** 订阅播放状态变化；返回取消订阅函数。 */
export function onBgmChange(listener) {
  if (typeof listener !== "function") return () => {};
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 本地曲库注入：async (id) => ({ buffer, gain }) | null。 */
export function setBgmBufferResolver(resolver) {
  bufferResolver = typeof resolver === "function" ? resolver : null;
}

function loadStored() {
  try {
    const raw = localStorage.getItem(BGM_STORAGE_KEY);
    return normalizeBgmSettings(raw ? JSON.parse(raw) : null);
  } catch {
    return { ...DEFAULT_BGM_SETTINGS };
  }
}

function persist() {
  try { localStorage.setItem(BGM_STORAGE_KEY, JSON.stringify(settings)); } catch { /* 存储可能被禁用 */ }
}

function ensureGain() {
  if (!shared) return null;
  if (!graph) {
    graph = shared.getSharedAudioGraph();
    if (!graph) return null;
    bgmGain = graph.context.createGain();
    bgmGain.gain.value = 0;
    bgmGain.connect(graph.masterGain);
  }
  return graph;
}

function sharedNoise(context) {
  if (noise && noise.sampleRate === context.sampleRate) return noise;
  const buffer = context.createBuffer(1, Math.max(1, Math.floor(context.sampleRate * 0.5)), context.sampleRate);
  const samples = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i += 1) samples[i] = Math.random() * 2 - 1;
  noise = buffer;
  return buffer;
}

function trackVoice(source, nodes) {
  const voice = { source, nodes };
  source.onended = () => {
    voices.delete(voice);
    for (const node of nodes) {
      try { node.disconnect(); } catch { /* 已断开 */ }
    }
  };
  voices.add(voice);
  return voice;
}

function stopVoices() {
  const now = graph?.context.currentTime || 0;
  for (const voice of voices) {
    try { voice.source.stop(now); } catch { /* 可能已结束 */ }
    for (const node of voice.nodes) {
      try { node.disconnect(); } catch { /* 已断开 */ }
    }
  }
  voices.clear();
}

function targetGain() {
  const track = trackById(settings.trackId);
  const mix = track && !track.silent ? (track.gain ?? 1) : localGain;
  return (settings.volume / 100) * mix * HEADROOM;
}

function rampGain(value, seconds = 0.35) {
  if (!bgmGain || !graph) return;
  const now = graph.context.currentTime;
  try {
    bgmGain.gain.cancelScheduledValues(now);
    bgmGain.gain.setValueAtTime(bgmGain.gain.value, now);
    bgmGain.gain.linearRampToValueAtTime(Math.max(0, value), now + seconds);
  } catch {
    bgmGain.gain.value = Math.max(0, value);
  }
}

function scheduleTone(when, freq, voice) {
  const duration = voice.duration;
  if (!Number.isFinite(freq) || !Number.isFinite(duration) || duration <= 0) return;
  const context = graph.context;
  const osc = context.createOscillator();
  const env = context.createGain();
  const attack = Math.min(voice.attack ?? 0.02, duration * 0.5);
  osc.type = voice.wave || "sine";
  osc.frequency.setValueAtTime(freq, when);
  if (Number.isFinite(voice.toFreq) && voice.toFreq > 0) {
    osc.frequency.exponentialRampToValueAtTime(Math.max(1, voice.toFreq), when + duration);
  }
  env.gain.setValueAtTime(0.0001, when);
  env.gain.linearRampToValueAtTime(Math.max(0.0001, voice.gain), when + attack);
  env.gain.exponentialRampToValueAtTime(0.0001, when + duration);
  osc.connect(env).connect(bgmGain);
  trackVoice(osc, [osc, env]);
  osc.start(when);
  osc.stop(when + duration + 0.02);
}

function scheduleHat(when, voice) {
  const duration = voice.duration;
  if (!Number.isFinite(duration) || duration <= 0) return;
  const context = graph.context;
  const source = context.createBufferSource();
  const filter = context.createBiquadFilter();
  const env = context.createGain();
  source.buffer = sharedNoise(context);
  filter.type = "highpass";
  filter.frequency.value = voice.frequency ?? 7000;
  env.gain.setValueAtTime(0.0001, when);
  env.gain.linearRampToValueAtTime(Math.max(0.0001, voice.gain), when + 0.004);
  env.gain.exponentialRampToValueAtTime(0.0001, when + duration);
  source.connect(filter).connect(env).connect(bgmGain);
  trackVoice(source, [source, filter, env]);
  source.start(when);
  source.stop(when + duration + 0.01);
}

function scheduleNote(track, note, when) {
  // 打击乐挂在 track.perc 下，旋律声部挂在 track[kind] 下。
  const spec = note.kind === "kick" || note.kind === "hat" ? track.perc?.[note.kind] : track[note.kind];
  if (!spec) return;
  if (note.kind === "hat") return scheduleHat(when, spec);
  return scheduleTone(when, note.freq, { ...spec, toFreq: note.toFreq });
}

function scheduleSynth() {
  const track = trackById(current.id);
  if (!track) return;
  const context = graph.context;
  const secondsPerStep = 60 / track.tempo / STEPS_PER_BEAT;
  if (!nextStepTime || nextStepTime < context.currentTime) nextStepTime = context.currentTime + 0.06;
  const length = loopSteps(track);
  let guard = 0;
  while (nextStepTime < context.currentTime + SCHEDULE_AHEAD && guard < 64) {
    if (voices.size < VOICE_LIMIT) {
      // 单个音色出错不能打断整条调度链，更不能把异常抛进定时器。
      for (const note of notesForStep(track, step)) {
        try { scheduleNote(track, note, nextStepTime); } catch { /* 忽略单个音 */ }
      }
    }
    step = (step + 1) % length;
    nextStepTime += secondsPerStep;
    guard += 1;
  }
}

async function resolveLocal(id) {
  if (!bufferResolver) return;
  const entry = await bufferResolver(id).catch(() => null);
  if (settings.trackId !== id) return;
  if (!entry?.data) {
    missingId = id;
    notify();
    return;
  }
  const ready = ensureGain();
  if (!ready || ready.context.state !== "running" || !shared.isGameAudioAudible()) return;
  let decoded = decodedCache.get(id);
  if (!decoded) {
    try {
      // decodeAudioData 会 detach 传入的 ArrayBuffer，必须给它副本。
      decoded = await ready.context.decodeAudioData(entry.data.slice(0));
    } catch {
      return;
    }
    decodedCache.set(id, decoded);
  }
  if (settings.trackId !== id) return;
  stopVoices();
  localGain = Number.isFinite(Number(entry.gain)) ? Number(entry.gain) : 1;
  const source = ready.context.createBufferSource();
  source.buffer = decoded;
  source.loop = true;
  source.connect(bgmGain);
  trackVoice(source, [source]);
  source.start();
  current = { kind: "buffer", id };
  missingId = null;
  notify();
}

function tick() {
  if (settings.trackId === "off") {
    stopVoices();
    current = null;
    rampGain(0, 0.2);
    stopTicking();
    return;
  }
  const ready = ensureGain();
  if (!ready) return;
  // 静音、音量为 0 或切到后台：立即收声，等条件恢复后由下一次 tick 重新起播。
  if (!shared.isGameAudioAudible() || ready.context.state !== "running") {
    if (current) { stopVoices(); current = null; notify(); }
    rampGain(0, 0.08);
    return;
  }
  if (current && current.id !== settings.trackId) {
    stopVoices();
    current = null;
  }
  if (!current) {
    const track = trackById(settings.trackId);
    if (track && !track.silent) {
      current = { kind: "synth", id: track.id };
      step = 0;
      nextStepTime = 0;
      notify();
    } else if (!loadingLocal) {
      loadingLocal = true;
      resolveLocal(settings.trackId).finally(() => { loadingLocal = false; });
      rampGain(0, 0.1);
      return;
    }
  }
  if (current?.kind === "synth") scheduleSynth();
  rampGain(targetGain());
}

function ensureTicking() {
  if (timer || settings.trackId === "off") return;
  if (typeof setInterval !== "function") return;
  timer = setInterval(tick, TICK_MS);
}

function stopTicking() {
  if (!timer) return;
  clearInterval(timer);
  timer = null;
}

/** 加载共享引擎并恢复上次的选择（幂等）。 */
export async function initBgm() {
  if (!shared) {
    try { shared = await import("../game-audio.js"); } catch { shared = null; }
  }
  settings = loadStored();
  ensureTicking();
  tick();
  notify();
  return Boolean(shared);
}

/** 选一首曲子；`off` 表示关闭。手动选曲会关掉自动切场景。 */
export function selectTrack(id, { auto = null } = {}) {
  const track = trackById(id);
  settings.trackId = track ? track.id : (typeof id === "string" ? id : DEFAULT_BGM_SETTINGS.trackId);
  if (auto !== null) settings.auto = auto !== false;
  persist();
  stopVoices();
  current = null;
  step = 0;
  nextStepTime = 0;
  missingId = null;
  ensureTicking();
  tick();
  notify();
  return settings.trackId;
}

export function setBgmVolume(volume) {
  const value = Number(volume);
  if (Number.isFinite(value)) settings.volume = Math.max(0, Math.min(100, Math.round(value)));
  persist();
  rampGain(settings.trackId === "off" ? 0 : targetGain(), 0.15);
  notify();
  return settings.volume;
}

/** 是否跟随场景自动切换。打开时立刻切到当前场景该放的曲子。 */
export function setBgmAuto(auto, wave = 1) {
  settings.auto = auto !== false;
  persist();
  if (settings.auto) applySceneTrack(wave);
  notify();
  return settings.auto;
}

/** 清单里声明了场景角色的曲目；由界面在读到清单后推给引擎。 */
export function setBgmSceneTracks(tracks) {
  libraryTracks = Array.isArray(tracks) ? tracks.filter((track) => track?.id && track.scene) : [];
}

/** 自动切换用哪一套：内置合成曲，还是清单里的免费曲库。 */
export function setBgmSceneSource(source, wave = 1) {
  settings.sceneSource = source === "library" ? "library" : "synth";
  persist();
  if (settings.auto) {
    applySceneTrack(wave);
    tick();
  }
  notify();
  return settings.sceneSource;
}

function applySceneTrack(wave) {
  const id = sceneTrackForScene(currentScene, wave, { source: settings.sceneSource, library: libraryTracks });
  if (settings.trackId === id) return false;
  settings.trackId = id;
  persist();
  stopVoices();
  current = null;
  step = 0;
  nextStepTime = 0;
  missingId = null;
  ensureTicking();
  return true;
}

/** 进入某个场景（menu/explore/boss/shop）。自动模式关闭时只记录场景。 */
export function setBgmScene(scene, wave = 1) {
  currentScene = scene;
  if (!settings.auto) return bgmState();
  applySceneTrack(wave);
  tick();
  notify();
  return bgmState();
}

/** 停止播放但保留设置（页面离开时用）。 */
export function stopBgm() {
  stopTicking();
  stopVoices();
  current = null;
  rampGain(0, 0.1);
  notify();
}

export function bgmTrackCount() {
  return BGM_TRACKS.filter((track) => !track.silent).length;
}
