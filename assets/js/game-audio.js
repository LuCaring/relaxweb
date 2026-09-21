/* Shared synthesized game sounds and their local controls. */

import { state } from "./core.js";

const SETTINGS_KEY = "gameAudioSettings";
const LEGACY_TURN_SOUND_KEY = "pokerTurnSound";
const DEFAULT_SETTINGS = Object.freeze({ enabled: true, volume: 60 });

let settings = loadSettings();
let context = null;
let masterGain = null;
let activeSources = new Set();
let playbackEpoch = 0;
let previewRequest = 0;
let initialized = false;
let opener = null;

function loadSettings() {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      return {
        enabled: parsed.enabled !== false,
        volume: Math.max(0, Math.min(100, Number.isFinite(Number(parsed.volume))
          ? Number(parsed.volume) : DEFAULT_SETTINGS.volume)),
      };
    }
    const legacy = localStorage.getItem(LEGACY_TURN_SOUND_KEY);
    const initial = {
      ...DEFAULT_SETTINGS,
      ...(legacy === "off" ? { enabled: false } : legacy === "on" ? { enabled: true } : {}),
    };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(initial));
    return initial;
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function persistSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* Storage may be disabled. */ }
}

function setMasterVolume() {
  if (!masterGain || !context) return;
  const value = settings.enabled ? settings.volume / 100 * 0.72 : 0;
  try {
    masterGain.gain.cancelScheduledValues(context.currentTime);
    masterGain.gain.setValueAtTime(value, context.currentTime);
    masterGain.gain.value = value;
  } catch {
    masterGain.gain.value = value;
  }
}

function ensureContext() {
  if (context) return context;
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return null;
    context = new Audio();
    masterGain = context.createGain();
    masterGain.connect(context.destination);
    setMasterVolume();
    return context;
  } catch {
    context = null;
    masterGain = null;
    return null;
  }
}

function cancelSources() {
  const now = context?.currentTime || 0;
  for (const voice of activeSources) {
    try { voice.source.stop(now); } catch { /* Already ended or cancelled. */ }
    for (const node of voice.nodes) {
      try { node.disconnect(); } catch { /* A disconnected node is harmless. */ }
    }
  }
  activeSources.clear();
}

export function stopGameAudio() {
  playbackEpoch += 1;
  previewRequest += 1;
  cancelSources();
}

/** Play a semantic cue from a successful non-room game response. */
export function playGameSound(cue) {
  playCue(cue);
}

function resumeFromGesture() {
  if (!settings.enabled || settings.volume <= 0) return;
  const audio = ensureContext();
  if (!audio || audio.state === "running") return;
  try { void audio.resume().catch(() => {}); } catch { /* Browser audio is optional. */ }
}

function tone(frequency, start, duration, options = {}) {
  if (!context || !masterGain || context.state !== "running" || settings.volume <= 0) return;
  try {
    const oscillator = context.createOscillator();
    const envelope = context.createGain();
    const peak = options.peak ?? 0.52;
    const end = start + duration;
    oscillator.type = options.wave || "sine";
    oscillator.frequency.setValueAtTime(frequency, start);
    if (options.to) oscillator.frequency.exponentialRampToValueAtTime(options.to, end);
    envelope.gain.setValueAtTime(0.0001, start);
    envelope.gain.exponentialRampToValueAtTime(peak, start + Math.min(0.014, duration * 0.18));
    envelope.gain.exponentialRampToValueAtTime(0.0001, end);
    oscillator.connect(envelope).connect(masterGain);
    const voice = { source: oscillator, nodes: [oscillator, envelope] };
    oscillator.onended = () => {
      activeSources.delete(voice);
      for (const node of voice.nodes) {
        try { node.disconnect(); } catch { /* Already disconnected. */ }
      }
    };
    activeSources.add(voice);
    oscillator.start(start);
    oscillator.stop(end + 0.008);
  } catch { /* Audio failures must not interfere with the game. */ }
}

function noiseBurst(start, duration, options = {}) {
  if (!context || !masterGain || context.state !== "running" || settings.volume <= 0) return;
  try {
    const sampleRate = context.sampleRate || 44100;
    const buffer = context.createBuffer(1, Math.max(1, Math.floor(sampleRate * duration)), sampleRate);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) samples[i] = Math.random() * 2 - 1;
    const source = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const envelope = context.createGain();
    const end = start + duration;
    source.buffer = buffer;
    filter.type = options.filter || "bandpass";
    filter.frequency.setValueAtTime(options.frequency || 1700, start);
    filter.Q.value = options.q || 0.8;
    envelope.gain.setValueAtTime(0.0001, start);
    envelope.gain.exponentialRampToValueAtTime(options.peak || 0.12, start + 0.008);
    envelope.gain.exponentialRampToValueAtTime(0.0001, end);
    source.connect(filter).connect(envelope).connect(masterGain);
    const voice = { source, nodes: [source, filter, envelope] };
    source.onended = () => {
      activeSources.delete(voice);
      for (const node of voice.nodes) {
        try { node.disconnect(); } catch { /* Already disconnected. */ }
      }
    };
    activeSources.add(voice);
    source.start(start);
    source.stop(end + 0.008);
  } catch { /* Filtered noise is an optional texture. */ }
}

function playCue(name) {
  if (!settings.enabled || settings.volume <= 0 || document.hidden) return;
  if (!context || context.state !== "running") return;
  cancelSources();
  const now = context.currentTime + 0.012;
  const note = (freq, delay, duration, opts) => tone(freq, now + delay, duration, opts);
  const tap = (freq, delay = 0, wave = "triangle", peak = 0.36) => note(freq, delay, 0.075, { wave, peak });
  switch (name) {
    case "deal":
      noiseBurst(now, 0.12, { frequency: 1450, peak: 0.1 });
      tap(470, 0, "triangle", 0.27); tap(590, 0.075, "triangle", 0.27); tap(720, 0.15, "sine", 0.32);
      break;
    case "draw":
      noiseBurst(now, 0.16, { frequency: 1050, q: 0.5, peak: 0.14 });
      note(360, 0, 0.12, { wave: "sine", to: 620, peak: 0.42 });
      break;
    case "play":
      noiseBurst(now, 0.07, { frequency: 2100, q: 1, peak: 0.1 });
      note(780, 0, 0.075, { wave: "triangle", to: 560, peak: 0.42 }); tap(660, 0.06, "sine", 0.24);
      break;
    case "bet":
      tap(1320, 0, "sine", 0.27); tap(1980, 0.012, "sine", 0.16);
      note(770, 0.055, 0.12, { wave: "sine", peak: 0.2 });
      break;
    case "check":
      tap(520, 0, "sine", 0.28);
      break;
    case "fold":
      note(490, 0, 0.2, { wave: "triangle", to: 330, peak: 0.34 });
      break;
    case "uno":
      tap(700, 0, "triangle", 0.35); tap(880, 0.09, "triangle", 0.42); tap(1050, 0.18, "sine", 0.44);
      break;
    case "pass":
      note(410, 0, 0.11, { wave: "triangle", to: 350, peak: 0.28 });
      break;
    case "bomb":
      note(220, 0, 0.18, { wave: "triangle", to: 160, peak: 0.46 });
      tap(540, 0.11, "triangle", 0.34); tap(810, 0.2, "sine", 0.43);
      break;
    case "meld":
      tap(620, 0, "triangle", 0.34); tap(790, 0.1, "sine", 0.37);
      break;
    case "gang":
      tap(440, 0, "triangle", 0.36); tap(660, 0.085, "triangle", 0.38); tap(880, 0.17, "sine", 0.42);
      break;
    case "turn":
      tap(660, 0, "sine", 0.42); tap(880, 0.15, "sine", 0.47);
      break;
    case "win":
    case "settlement":
      tap(523, 0, "triangle", 0.34); tap(659, 0.1, "triangle", 0.36);
      tap(784, 0.2, "sine", 0.4); tap(1047, 0.32, "sine", 0.46);
      break;
    case "plant":
      note(320, 0, 0.16, { wave: "triangle", to: 520, peak: 0.34 });
      tap(740, 0.13, "sine", 0.35);
      break;
    case "harvest":
      tap(660, 0, "triangle", 0.32); tap(880, 0.1, "sine", 0.38); tap(1047, 0.2, "sine", 0.43);
      break;
    case "fish":
      noiseBurst(now, 0.11, { frequency: 1300, q: 0.5, peak: 0.09 });
      note(480, 0, 0.12, { wave: "sine", to: 690, peak: 0.34 });
      tap(920, 0.13, "triangle", 0.38);
      break;
    case "mine":
      noiseBurst(now, 0.1, { frequency: 900, q: 0.7, peak: 0.12 });
      note(250, 0, 0.16, { wave: "triangle", to: 190, peak: 0.37 });
      tap(540, 0.12, "triangle", 0.34);
      break;
    case "shop":
      tap(920, 0, "sine", 0.35); tap(690, 0.08, "triangle", 0.31);
      break;
    default:
      tap(620, 0, "triangle", 0.32);
  }
}

function currentUsername() {
  // 观战时不下场：不触发“轮到你”之类的提示音。
  if (state.myRoom?.spectator) return null;
  return state.currentUser?.username || null;
}

function actionable(room) {
  const username = currentUsername();
  if (!room || !username || room.paused) return false;
  const options = room.your_options || {};
  switch (room.game_type) {
    case "holdem": return room.to_act === username && Object.keys(options).length > 0;
    case "uno": return room.to_act === username && Boolean(options.draw || options.pass);
    case "guandan": return room.to_act === username && Boolean(options.play || options.pass);
    case "mahjong":
      return Boolean(options.discard || options.zimo || (options.claim && !options.passed));
    default: return room.to_act === username && Object.keys(options).length > 0;
  }
}

function actionSignature(room) {
  const last = room.last_action || {};
  const base = [last.username || "", last.text || ""];
  switch (room.game_type) {
    case "holdem":
      return JSON.stringify([...base, room.hand_no, room.stage, room.to_act, room.pot,
        room.current_bet]);
    case "uno": {
      const event = room.action_event || {};
      return JSON.stringify([event.id ?? null, event.kind || "", ...base,
        room.to_act, room.active?.c, room.active?.v, room.direction]);
    }
    case "guandan":
      return JSON.stringify([...base, room.hand_no, room.to_act, room.bombs,
        room.standing?.type, room.standing?.by, room.standing?.label,
        room.free_lead, room.passed, room.finish]);
    case "mahjong":
      return JSON.stringify([...base, room.hand_no, room.phase, room.to_act,
        room.last_discard?.by, room.last_discard?.tile, room.claim?.waiting,
        Object.values(room.discards || {}).map((pile) => pile.length)]);
    default:
      return JSON.stringify([...base, room.hand_no, room.to_act]);
  }
}

function resultIdentity(room) {
  const result = room?.result;
  if (!result) return "";
  return JSON.stringify([room.hand_no, result.winner, result.zimo, result.draw_game, result.payouts, result.gains]);
}

function actionCue(room, previous) {
  const text = String(room.last_action?.text || "");
  if (room.game_type === "holdem") {
    if (room.stage !== previous?.stage || (room.board?.length || 0) > (previous?.board?.length || 0)) return "deal";
    if (/弃牌/.test(text)) return "fold";
    if (/看牌/.test(text)) return "check";
    if (/跟注|加注|全下|下注/.test(text)) return "bet";
    return "play";
  }
  if (room.game_type === "uno") {
    const event = room.action_event || {};
    if (event.id !== previous?.action_event?.id) {
      if (event.kind === "draw") return "draw";
      if (event.kind === "uno") return "uno";
      if (event.kind === "play" && ["skip", "rev", "d2", "wild", "wd4"].includes(event.card?.v)) return "uno";
    }
    if (/留下摸的牌/.test(text)) return "pass";
    if (/摸牌/.test(text)) return "draw";
    if (/喊出 UNO/.test(text)) return "uno";
    return "play";
  }
  if (room.game_type === "guandan") {
    if (/不出/.test(text)) return "pass";
    const explosive = ["bomb", "flush_straight", "king_bomb"].includes(room.standing?.type)
      || Number(room.bombs || 0) > Number(previous?.bombs || 0);
    if (explosive && /出/.test(text)) return "bomb";
    return /出完/.test(text) ? "win" : "play";
  }
  if (room.game_type === "mahjong") {
    if (/放弃/.test(text)) return "pass";
    if (/暗杠|明杠|补杠/.test(text)) return "gang";
    if (/吃|碰/.test(text)) return "meld";
    if (/打出/.test(text)) return "play";
  }
  return "play";
}

function publicConcealedTotal(room) {
  return (room.players || []).reduce((sum, player) => sum + Number(player.concealed || 0), 0);
}

let lastRoomId = null;

/**
 * Observe authoritative room messages before the room coordinator replaces state.myRoom.
 * Initial snapshots seed the comparison without playing; repeated snapshots and timer-only
 * updates have identical action signatures and remain silent.
 */
export function observeGameRoom(previous, next, { initial = false } = {}) {
  if (!next) {
    stopGameAudio();
    lastRoomId = null;
    return;
  }
  if (initial || !previous || next.room_id !== lastRoomId || previous.room_id !== next.room_id) {
    stopGameAudio();
    lastRoomId = next.room_id;
    return;
  }
  lastRoomId = next.room_id;
  if (next.paused || document.hidden || !settings.enabled || settings.volume <= 0) {
    stopGameAudio();
    return;
  }
  if (previous.paused && !next.paused) {
    stopGameAudio();
    return;
  }

  const previousResult = resultIdentity(previous);
  const nextResult = resultIdentity(next);
  if (nextResult && nextResult !== previousResult) {
    playCue("settlement");
    return;
  }

  if (next.status === "playing" && previous.status !== "playing") {
    playCue("deal");
    return;
  }
  if (next.status === "playing" && previous.hand_no != null && next.hand_no !== previous.hand_no) {
    playCue("deal");
    return;
  }
  if (next.status !== "playing") return;

  const previousTurn = actionable(previous);
  const nextTurn = actionable(next);
  if (nextTurn && (!previousTurn || previous.paused)) {
    playCue("turn");
    return;
  }

  if (next.game_type === "mahjong" && Number(next.wall_count) < Number(previous.wall_count)
      && publicConcealedTotal(next) > publicConcealedTotal(previous)) {
    playCue("draw");
    return;
  }

  if (actionSignature(previous) !== actionSignature(next)) {
    playCue(actionCue(next, previous));
  }
}

export function resetGameAudioRoom() {
  stopGameAudio();
  lastRoomId = null;
}

function getSettingsNodes() {
  const enabled = document.getElementById("gameAudioEnabled");
  const volume = document.getElementById("gameAudioVolume");
  const readout = document.getElementById("gameAudioVolumeValue");
  const preview = document.getElementById("gameAudioPreviewButton");
  return { enabled, volume, readout, preview };
}

function syncSettingsUi() {
  const { enabled, volume, readout, preview } = getSettingsNodes();
  if (!enabled || !volume || !readout) return;
  enabled.checked = settings.enabled;
  volume.value = String(settings.volume);
  readout.textContent = `${settings.volume}%`;
  if (preview) preview.disabled = !settings.enabled || settings.volume <= 0;
  setMasterVolume();
}

function openSettings(button) {
  const modal = document.getElementById("gameAudioSettingsModal");
  if (!modal) return;
  opener = button;
  syncSettingsUi();
  modal.hidden = false;
  document.dispatchEvent(new Event("gameaudiosettingsopen"));
  document.getElementById("gameAudioCloseButton")?.focus();
}

function closeSettings() {
  const modal = document.getElementById("gameAudioSettingsModal");
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  document.dispatchEvent(new Event("gameaudiosettingsclose"));
  opener?.focus?.();
  opener = null;
}

export function initializeGameAudio() {
  if (initialized) return;
  initialized = true;
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("#hallAudioSettingsButton, #roomAudioSettingsButton, #estateAudioSettingsButton");
    if (button) openSettings(button);
  });

  const { enabled, volume, preview } = getSettingsNodes();
  enabled?.addEventListener("change", () => {
    settings.enabled = enabled.checked;
    persistSettings();
    if (!settings.enabled) stopGameAudio();
    else resumeFromGesture();
    syncSettingsUi();
  });
  volume?.addEventListener("input", () => {
    settings.volume = Math.max(0, Math.min(100, Number(volume.value) || 0));
    persistSettings();
    if (settings.volume === 0) stopGameAudio();
    syncSettingsUi();
  });
  preview?.addEventListener("click", async () => {
    if (!settings.enabled || settings.volume <= 0) return;
    const request = ++previewRequest;
    const epoch = playbackEpoch;
    const audio = ensureContext();
    if (!audio) return;
    try { await audio.resume(); } catch { return; }
    if (request !== previewRequest || epoch !== playbackEpoch || !settings.enabled
        || settings.volume <= 0 || document.hidden) return;
    playCue("settlement");
  });
  document.getElementById("gameAudioCloseButton")?.addEventListener("click", closeSettings);
  document.getElementById("gameAudioSettingsModal")?.addEventListener("click", (event) => {
    if (event.target.id === "gameAudioSettingsModal") closeSettings();
  });
  document.addEventListener("keydown", (event) => {
    resumeFromGesture();
    const modal = document.getElementById("gameAudioSettingsModal");
    if (!modal || modal.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeSettings();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...modal.querySelectorAll("button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex='-1'])")]
      .filter((node) => !node.hidden);
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  }, true);
  document.addEventListener("pointerdown", resumeFromGesture, { capture: true, passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopGameAudio();
      try { if (context?.state === "running") void context.suspend().catch(() => {}); } catch { /* Optional. */ }
    }
  });
  syncSettingsUi();
}
