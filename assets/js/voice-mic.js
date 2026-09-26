/* 语音偏好与本地麦克风处理（游戏无关）。
   上半部分是偏好存取：按人音量、噪声门设置，存 localStorage，同一用户
   跨房间、跨对局继承；以后如需跨设备同步，可以只换这里的存取实现。
   下半部分是麦克风管线：采集 → 分析（电平表）→ 门控增益 → 输出音轨，
   输出音轨交给 room-voice.js 发布；低于阈值时发送静音而不是摘除音轨，
   其他人的「谁在说话」提示因此保持自然。
   浏览器 API 全部在函数体内访问，纯函数与存取可在 Node 中直接导入单测。 */

const PEER_VOLUMES_KEY = "voicePeerVolumes";
const MIC_SETTINGS_KEY = "voiceMicSettings";
const PEER_VOLUME_LIMIT = 200;
const PEER_VOLUME_DEFAULT = 100;
/* 上限 150%：≤100% 走 media element 自身音量，>100% 的增益由
   room-voice.js 的 WebAudio GainNode 补足。 */
export const PEER_VOLUME_MAX = 150;
/* 阈值上限 50：电平条按 RMS×200 绘制，50% 恰好是条满格，
   这样阈值滑块（0–50 线性映射到条上）与电平填充共用同一刻度。 */
const GATE_THRESHOLD_MAX = 50;
const GATE_THRESHOLD_DEFAULT = 8;

/** 迟滞噪声门参数：低于 0.6×阈值持续 250ms 才关门，防止字尾被咬掉。 */
export const GATE_CLOSE_RATIO = 0.6;
export const GATE_RELEASE_MS = 250;
const GATE_TICK_MS = 60;

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
  return Math.max(0, Math.min(PEER_VOLUME_MAX, Math.round(number)));
}

function clampThreshold(value, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(0, Math.min(GATE_THRESHOLD_MAX, Math.round(number)));
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

function announceEvent(name, detail) {
  try {
    if (typeof document === "undefined") return;
    document.dispatchEvent(new CustomEvent(name, { detail }));
  } catch { /* UI 联动事件，失败无碍 */ }
}

/** 某玩家在自己这的收听音量（0–150），默认 100。 */
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
  announceEvent("voicepeerchange", { username, volume: clamped });
  return clamped;
}

let micSettings = loadMicSettings();

function loadMicSettings() {
  try {
    const parsed = JSON.parse(storageGet(MIC_SETTINGS_KEY) || "{}");
    return {
      gateEnabled: parsed?.gateEnabled === true,
      gateThreshold: clampThreshold(parsed?.gateThreshold, GATE_THRESHOLD_DEFAULT),
    };
  } catch {
    return { gateEnabled: false, gateThreshold: GATE_THRESHOLD_DEFAULT };
  }
}

/** 噪声门设置：{ gateEnabled, gateThreshold }，阈值取 RMS 百分比。 */
export function micGateSettings() {
  return { ...micSettings };
}

/** 合并写入噪声门设置，返回归一化后的完整设置。 */
export function setMicGateSettings(patch) {
  if (patch && patch.gateEnabled !== undefined) micSettings.gateEnabled = patch.gateEnabled === true;
  if (patch && patch.gateThreshold !== undefined) {
    micSettings.gateThreshold = clampThreshold(patch.gateThreshold, micSettings.gateThreshold);
  }
  storageSet(MIC_SETTINGS_KEY, JSON.stringify(micSettings));
  applyGateGain();
  announceEvent("voicemicsettings", { ...micSettings });
  return { ...micSettings };
}

/**
 * 门控状态机（纯函数）。高于阈值立即开门；低于 0.6×阈值并持续
 * GATE_RELEASE_MS 后关门；两阈值之间保持当前状态。
 * prev: { open, belowSince }，now 为单调毫秒时间戳。
 */
export function gateNext(prev, rms, threshold, now) {
  if (rms >= threshold) return { open: true, belowSince: null };
  const closeZone = threshold > 0 && rms < threshold * GATE_CLOSE_RATIO;
  if (!closeZone) return { open: prev.open, belowSince: null };
  const belowSince = prev.belowSince ?? now;
  return { open: prev.open && now - belowSince < GATE_RELEASE_MS, belowSince };
}

let pipeline = null;

/** 采集管线是否可用（拿到过麦克风且音轨仍活着）。 */
export function micPipelineActive() {
  return Boolean(pipeline && pipeline.raw.getAudioTracks()
    .some((track) => track.readyState === "live"));
}

/** 最近一次测得的麦克风 RMS（0–1），无管线时为 0。 */
export function micLevel() {
  return pipeline ? pipeline.level : 0;
}

/** 门控当前是否放行（未启用门控或无管线时恒为放行）。 */
export function micGateOpen() {
  return !pipeline || !micSettings.gateEnabled || pipeline.gateState.open;
}

/**
 * 接管采集到的麦克风流：建分析器与门控增益。管起来后调用方不要再把
 * 这条原始流交给 LiveKit，发布时改用 micGateTrack() 的输出音轨。
 */
export async function startMicPipeline(stream) {
  if (!stream?.getAudioTracks?.().length) throw new Error("no audio track");
  if (pipeline) stopMicPipeline();
  const AudioCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtor) throw new Error("WebAudio unavailable");
  const context = new AudioCtor();
  try {
    if (context.state === "suspended") await context.resume().catch(() => {});
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    const gateGain = context.createGain();
    source.connect(analyser);
    analyser.connect(gateGain);
    pipeline = {
      raw: stream,
      context,
      analyser,
      gateGain,
      buffer: new Float32Array(analyser.fftSize),
      canMeter: typeof analyser.getFloatTimeDomainData === "function",
      level: 0,
      gateState: { open: true, belowSince: null },
      gateGainTarget: 1,
      dest: null,
      timer: 0,
    };
    for (const track of stream.getAudioTracks()) {
      track.addEventListener?.("ended", () => {
        if (pipeline && pipeline.raw === stream) {
          stopMicPipeline();
          announceEvent("voicemicstreamended", {});
        }
      });
    }
    startLevelLoop();
    applyGateGain();
  } catch (error) {
    try { void context.close(); } catch { /* 已关闭 */ }
    throw error;
  }
}

/** 停止并丢弃管线：停掉采集轨、断开节点、关闭 AudioContext。 */
export function stopMicPipeline() {
  const active = pipeline;
  pipeline = null;
  if (!active) return;
  if (active.timer) clearInterval(active.timer);
  for (const track of active.raw.getAudioTracks()) {
    try { track.stop(); } catch { /* 已停止 */ }
  }
  try { active.gateGain.disconnect(); } catch { /* 已断开 */ }
  try { void active.context.close(); } catch { /* 已关闭 */ }
}

/**
 * 生成一条经过门控的输出音轨供本次发布使用。LiveKit 断开时会 stop 掉
 * 已发布的音轨，所以每次发布都新建输出节点，原采集流不受影响。
 */
export function micGateTrack() {
  if (!micPipelineActive()) return null;
  if (pipeline.dest) {
    try { pipeline.gateGain.disconnect(pipeline.dest); } catch { /* 旧输出已废弃 */ }
  }
  const dest = pipeline.context.createMediaStreamDestination();
  pipeline.gateGain.connect(dest);
  pipeline.dest = dest;
  return dest.stream.getAudioTracks()[0] || null;
}

function startLevelLoop() {
  if (!pipeline || pipeline.timer) return;
  pipeline.timer = setInterval(levelTick, GATE_TICK_MS);
}

function levelTick() {
  if (!pipeline) return;
  let rms = 0;
  if (pipeline.canMeter) {
    try {
      pipeline.analyser.getFloatTimeDomainData(pipeline.buffer);
      let sum = 0;
      for (let i = 0; i < pipeline.buffer.length; i += 1) sum += pipeline.buffer[i] * pipeline.buffer[i];
      rms = Math.sqrt(sum / pipeline.buffer.length);
    } catch { rms = 0; }
  }
  pipeline.level = rms;
  if (micSettings.gateEnabled && pipeline.canMeter) {
    pipeline.gateState = gateNext(pipeline.gateState, rms,
      micSettings.gateThreshold / 100, performance.now());
    applyGateGain();
  }
}

function applyGateGain() {
  if (!pipeline) return;
  const open = micGateOpen();
  const target = open ? 1 : 0;
  if (pipeline.gateGainTarget === target) return;
  pipeline.gateGainTarget = target;
  const { context, gateGain } = pipeline;
  try {
    const now = context.currentTime;
    // 开门要快（不吞字头），关门放缓（防爆音）
    gateGain.gain.cancelScheduledValues(now);
    gateGain.gain.setTargetAtTime(target, now, open ? 0.005 : 0.045);
  } catch {
    gateGain.gain.value = target;
  }
}
