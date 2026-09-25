/* 语音调试面板（等待页等场景共用）：启用声音、开关麦克风、麦克风电平表
   （内嵌噪声门阈值滑块）与门控开关。面板自带 rAF 刷新循环，宿主容器
   从文档移除后自动停止；不含成员音量（见 peer-volume-menu.js）。 */

import { enableVoiceAudio, toggleMic, voiceMicPublishing, voiceMicWanted,
  voiceStatus } from "./room-voice.js";
import { micGateOpen, micGateSettings, micLevel, micPipelineActive,
  setMicGateSettings } from "./voice-mic.js";

// 电平条显示刻度：RMS × 200，实测语音（0.1–0.4）落在 20%–80%
const METER_SCALE = 200;
const METER_SILENT_RMS = 0.004;
const meterLoops = new WeakMap();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function refreshVoiceMeter(root, status) {
  const block = root.querySelector(".waiting-voice-meter");
  if (!block) return;
  block.hidden = !status.connected;
  if (block.hidden) return;
  if (meterLoops.has(root)) return;
  meterLoops.set(root, true);
  const fill = block.querySelector(".voice-meter-fill");
  const bar = block.querySelector(".voice-meter");
  const threshold = block.querySelector(".voice-meter-threshold");
  const note = block.querySelector(".voice-meter-note");
  let silentSince = 0;
  const setNote = (text, tone) => {
    if (note.textContent !== text) note.textContent = text;
    if (tone) note.dataset.tone = tone;
    else delete note.dataset.tone;
  };
  const frame = () => {
    if (!root.isConnected) {
      meterLoops.delete(root);
      return;
    }
    const active = voiceStatus().connected && voiceMicWanted();
    const level = active ? micLevel() : 0;
    fill.style.width = `${Math.min(100, Math.round(level * METER_SCALE))}%`;
    const settings = micGateSettings();
    const ready = micPipelineActive();
    const gateOn = settings.gateEnabled && ready;
    // 阈值滑块嵌在电平条上：0–50 线性映射与电平刻度（RMS×200）一致，
    // 把手位置即静默分界，拖动即可对着实时电平选阈值。
    bar.classList.toggle("gate-on", gateOn);
    bar.style.setProperty("--gate-at",
      `${Math.min(100, Math.round(settings.gateThreshold * METER_SCALE / 100))}%`);
    threshold.disabled = !ready;
    threshold.classList.toggle("is-active", gateOn);
    if (threshold.value !== String(settings.gateThreshold)) {
      threshold.value = String(settings.gateThreshold);
    }
    fill.classList.toggle("is-gated", gateOn && !micGateOpen());
    if (active && level < METER_SILENT_RMS) {
      silentSince = silentSince || performance.now();
      setNote(performance.now() - silentSince > 2000 ? "未检测到声音，请检查麦克风" : "", "warn");
    } else if (gateOn && !micGateOpen()) {
      silentSince = 0;
      setNote("低于阈值 · 静默中", "info");
    } else {
      silentSince = 0;
      setNote("", "");
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function refreshVoiceGate(root, status) {
  const block = root.querySelector(".waiting-voice-gate");
  if (!block) return;
  block.hidden = !status.connected;
  if (block.hidden) return;
  const settings = micGateSettings();
  block.querySelector(".voice-gate-toggle").checked = settings.gateEnabled;
  block.querySelector(".voice-gate-value").textContent = `阈值 ${settings.gateThreshold}%`;
  block.querySelector(".voice-gate-hint").hidden = micPipelineActive();
}

/** 在 container 内构建面板；返回 { refresh(status) }，宿主在每次连接状态变化时调用。 */
export function mountVoiceControls(container, opts = {}) {
  const info = el("div", "waiting-voice-info");
  info.append(el("strong", "", opts.title || "开局前语音调试"),
    el("span", "", opts.note || "等待区可以自由聊天；开局后会自动切换到游戏语音频道。"));
  const actions = el("div", "waiting-voice-actions");
  const status = el("span", "waiting-voice-status");
  status.setAttribute("aria-live", "polite");
  const audio = el("button", "waiting-voice-audio", "🔊 开启声音");
  audio.type = "button";
  audio.addEventListener("click", () => { void enableVoiceAudio(); });
  const mic = el("button", "waiting-voice-mic");
  mic.type = "button";
  mic.addEventListener("click", () => { void toggleMic(); });
  actions.append(status, audio, mic);
  const meter = el("div", "waiting-voice-meter");
  meter.hidden = true;
  const meterHead = el("div", "voice-meter-head");
  meterHead.append(el("strong", "", "麦克风电平"), el("span", "voice-meter-note"));
  const bar = el("div", "voice-meter");
  bar.setAttribute("aria-hidden", "true");
  bar.append(el("span", "voice-meter-fill"));
  const threshold = el("input", "voice-meter-threshold");
  threshold.type = "range";
  threshold.min = "0";
  threshold.max = "50";
  threshold.step = "1";
  threshold.setAttribute("aria-label", "噪声门阈值");
  bar.append(threshold);
  meter.append(meterHead, bar);
  const gate = el("div", "waiting-voice-gate");
  gate.hidden = true;
  const gateLabel = el("label", "voice-gate-check");
  const gateToggle = el("input", "voice-gate-toggle");
  gateToggle.type = "checkbox";
  gateLabel.append(gateToggle, el("span", "", "低于阈值时自动静默（噪声门）"));
  const gateValue = el("span", "voice-gate-value");
  const gateHint = el("span", "voice-gate-hint", "重新开启麦克风后生效");
  gateToggle.addEventListener("change", () => {
    setMicGateSettings({ gateEnabled: gateToggle.checked });
  });
  threshold.addEventListener("input", () => {
    const settings = setMicGateSettings({ gateThreshold: Number(threshold.value) || 0 });
    gateValue.textContent = `阈值 ${settings.gateThreshold}%`;
  });
  gate.append(gateLabel, gateValue, gateHint);
  container.append(info, actions, meter, gate);
  return {
    refresh(nextStatus) {
      const preview = Boolean(window.LIVE_CONFIG?.voice?.preview);
      const on = nextStatus.connected && nextStatus.canPublish && voiceMicPublishing();
      const wanted = voiceMicWanted();
      mic.disabled = preview || !nextStatus.connected || !nextStatus.canPublish;
      mic.classList.toggle("on", on);
      mic.textContent = wanted ? "🎙 关闭麦克风" : "🎙 开启麦克风";
      mic.setAttribute("aria-pressed", String(on));
      status.textContent = preview
        ? "布局预览 · 麦克风未连接"
        : !nextStatus.connected ? nextStatus.waitingText || "语音连接中，连接后可测试麦克风"
        : nextStatus.micError || (on ? "麦克风已开启，可以和房内玩家交谈"
          : wanted ? "麦克风开启中…" : "已连接 · 麦克风关闭");
      audio.hidden = !nextStatus.audioBlocked;
      refreshVoiceMeter(container, nextStatus);
      refreshVoiceGate(container, nextStatus);
    },
  };
}
