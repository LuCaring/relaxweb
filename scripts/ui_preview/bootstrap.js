/* Loaded before production modules, only by the loopback preview server. */
window.LIVE_CONFIG = {site: {game_title: "本地 UI 预览"}, chat_port: 0};
window.WebSocket = class PreviewSocket extends EventTarget {
  static OPEN = 1;
  readyState = 1;
  send(data) {
    // Async delivery matches a server reply and lets button submission finish first.
    queueMicrotask(() => window.dispatchEvent(new CustomEvent("preview-send", {detail: JSON.parse(data)})));
  }
  close() { this.readyState = 3; }
};
