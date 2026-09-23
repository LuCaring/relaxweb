/* 语音连接管理：消费服务器的 voice_update（token 授权），直连 LiveKit。
   授权模型：能否进语音房间由服务端签发的短时 JWT 决定，客户端拿到
   token 才连接；token 为空表示服务端要求断开。
   麦克风采集受浏览器手势限制：第一次「上麦」点击会预热权限并记住意愿，
   之后连接/换房自动恢复发布；「闭麦」走 LiveKit 的麦克风静音。
   说话指示经 ActiveSpeakersChanged 广播成 voicespeakers 事件，
   牌桌据此高亮座位；连接状态变化广播 voicestate 事件。 */

import { onMessage } from "./registry.js";

const mic = { wanted: false, primed: false };
let current = null;        // 当前 LiveKit Room
let currentKey = "";       // url|room，防止重复连接

export function voiceMicWanted() {
  return mic.wanted;
}

export function voiceConnected() {
  return Boolean(current);
}

/** 测试与调试用：当前连接、房间名、远端音频路数与本机麦克风发布状态。 */
export function voiceDebug() {
  if (!current) return { connected: false };
  const remote = [...current.remoteParticipants.values()];
  const LK = window.LivekitClient;
  const micPub = (LK && current.localParticipant.getTrackPublication(
    LK.Track.Source.Microphone))
    ?? [...current.localParticipant.trackPublications.values()]
      .find((pub) => pub.kind === "audio");
  return {
    connected: true,
    room: current.name,
    peers: remote.length,
    remoteAudio: remote.reduce(
      (count, p) => count + [...(p.trackPublications?.values() ?? [])]
        .filter((pub) => pub.kind === "audio" && pub.isSubscribed
          && !pub.isMuted).length, 0),
    micPublished: Boolean(micPub) && !micPub.isMuted,
  };
}

export async function toggleMic() {
  mic.wanted = !mic.wanted;
  if (mic.wanted && !mic.primed) {
    // 首次上麦在点击手势里预热权限，后续自动恢复发布不再需要手势
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      stream.getTracks().forEach((track) => track.stop());
      mic.primed = true;
    } catch {
      mic.wanted = false;
      announceState();
      return mic.wanted;
    }
  }
  await applyMic();
  announceState();
  return mic.wanted;
}

async function applyMic() {
  if (!current) return;
  try {
    await current.localParticipant.setMicrophoneEnabled(mic.wanted);
  } catch (error) {
    console.warn("voice mic toggle failed", error);
    mic.wanted = false;
    announceState();
  }
}

function announceState() {
  document.dispatchEvent(new CustomEvent("voicestate", {
    detail: { connected: Boolean(current), mic: mic.wanted },
  }));
}

async function applyUpdate(update) {
  const LK = window.LivekitClient;
  if (!LK || !update.token || !update.room) {
    await disconnect("server cleared voice");
    return;
  }
  const key = `${update.url}|${update.room}`;
  if (current && currentKey === key) {
    // 同一房间：token 换发无需重连（旧 token 仍有效到过期）
    return;
  }
  await disconnect("switching voice room");
  const room = new LK.Room({ adaptiveStream: false, dynacast: false });
  room.on(LK.RoomEvent.ActiveSpeakersChanged, (speakers) => {
    document.dispatchEvent(new CustomEvent("voicespeakers", {
      detail: speakers.map((s) => s.identity),
    }));
  });
  room.on(LK.RoomEvent.TrackSubscribed, () => announceState());
  room.on(LK.RoomEvent.ParticipantConnected, () => announceState());
  room.on(LK.RoomEvent.ParticipantDisconnected, () => announceState());
  room.on(LK.RoomEvent.Disconnected, () => {
    if (current === room) {
      current = null;
      currentKey = "";
      announceState();
    }
  });
  try {
    await room.connect(update.url, update.token);
  } catch (error) {
    console.warn("voice connect failed", error);
    current = null;
    currentKey = "";
    announceState();
    return;
  }
  current = room;
  currentKey = key;
  if (mic.wanted) await applyMic();
  announceState();
}

async function disconnect(reason) {
  const room = current;
  current = null;
  currentKey = "";
  if (room) {
    try { room.removeAllListeners(); } catch { /* 忽略 */ }
    try { await room.disconnect(); } catch { /* 已断开 */ }
  }
  announceState();
  if (reason) console.info("voice:", reason);
}

onMessage("voice_update", (msg) => { applyUpdate(msg).catch((e) => console.warn(e)); });

document.addEventListener("visibilitychange", () => { /* 预留：后台节流 */ });
