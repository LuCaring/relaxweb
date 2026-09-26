/**
 * 地下城 Beta 原型 · 音效适配层。
 *
 * 真实发声复用全站共享的 `assets/js/game-audio.js`（同一个 AudioContext 与 masterGain，
 * 因此音量与静音设置和游戏厅、庄园一致）。本文件只做两件事：
 *   1. 把高频游戏事件合并/节流成合理的音效次数（一次挥砍命中多只敌人只响一下）；
 *   2. 在没有 WebAudio 或共享引擎加载失败时静默降级，绝不影响玩法。
 *
 * 本文件刻意不在模块顶层 import 共享引擎：动态 import 让本文件可以脱离浏览器直接单测，
 * 也避免地下城原型页意外拉入游戏厅的状态模块。
 */

/** 地下城使用到的全部音效名，与共享引擎的动作音效表一一对应。 */
export const DUNGEON_CUES = Object.freeze([
  "swing", "shoot", "hit", "kill", "hurt", "pickup", "levelup",
  "waveStart", "waveClear", "boss", "death", "victory",
  "buy", "reroll", "craft", "error", "select",
]);

/** 同一帧内可能触发多次的事件：窗口内只保留第一次。 */
export const COALESCED_CUES = new Set(["swing", "shoot", "hit", "kill", "pickup"]);

const DEFAULT_WINDOW_MS = 45;
const nowMs = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/**
 * 用一个 `play(cue)` 函数构造带合并/节流的声音出口。
 * @param {(cue: string) => boolean} play 真正发声的函数；返回 false 表示没有发声。
 * @param {{clock?: () => number, windowMs?: number}} [options]
 */
export function createDungeonAudio(play, { clock = nowMs, windowMs = DEFAULT_WINDOW_MS } = {}) {
  const lastPlayed = new Map();
  return {
    /** 返回是否真的转发给了发声层。 */
    sfx(cue) {
      const at = clock();
      const previous = lastPlayed.get(cue);
      if (COALESCED_CUES.has(cue) && previous !== undefined && at - previous < windowMs) return false;
      lastPlayed.set(cue, at);
      return play(cue) !== false;
    },
    /** 仅用于测试与调试：清空节流记录。 */
    reset() {
      lastPlayed.clear();
    },
  };
}

let engine = null;
let pending = null;
let audio = createDungeonAudio(() => false);

/** 播放一个地下城音效。共享引擎尚未就绪时静默丢弃，不排队。 */
export function sfx(cue) {
  return audio.sfx(cue);
}

/** 让页面的音效按钮反映共享设置（静音或音量为 0 时显示 🔇）。 */
function syncIndicator() {
  const button = document.getElementById("dungeonAudioSettingsButton");
  if (!button || !engine?.getGameAudioSettings) return;
  const { enabled, volume } = engine.getGameAudioSettings();
  const muted = !enabled || volume <= 0;
  button.textContent = muted ? "🔇" : "🔊";
  button.title = muted ? "音效已关闭，点击设置" : `音效设置 · 音量 ${volume}%`;
}

/**
 * 加载并初始化共享音频引擎（幂等）。页面加载时调用一次即可；
 * 共享引擎自身负责在首次用户手势后解锁 AudioContext。
 */
export function initDungeonAudio() {
  if (!pending) {
    pending = import("../game-audio.js").then((module) => {
      engine = module;
      // 复用游戏厅的音效设置弹窗；本页的“试听”播放地下城通关音。
      module.initializeGameAudio({ previewCue: "waveClear" });
      audio = createDungeonAudio((cue) => module.playActionSound(cue) === true);
      // change/input 会冒泡到弹窗，用它同步按钮图标，不必改动共享引擎。
      const modal = document.getElementById("gameAudioSettingsModal");
      modal?.addEventListener("change", syncIndicator);
      modal?.addEventListener("input", syncIndicator);
      syncIndicator();
      return module;
    }).catch(() => {
      engine = null;
      audio = createDungeonAudio(() => false);
      return null;
    });
  }
  return pending;
}

/** 共享音频引擎模块是否已加载（注意：加载成功不代表浏览器有可用的 WebAudio）。 */
export function dungeonAudioLoaded() {
  return Boolean(engine);
}

/** 用户手势后解锁音频；共享引擎不可用时为空操作。 */
export function unlockDungeonAudio() {
  engine?.unlockGameAudio?.();
}
