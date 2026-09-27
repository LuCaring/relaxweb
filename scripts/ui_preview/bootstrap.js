/* Loaded before production modules, only by the loopback preview server. */
window.LIVE_CONFIG = {site: {game_title: "本地 UI 预览"}, chat_port: 0,
  voice: {enabled: true, preview: true}};
// Only initialize this isolated preview page; normal hall selection remains user-controlled.
if (new URLSearchParams(location.search).get("game") === "holdem") {
  localStorage.setItem("relaxweb:holdem-view",
    new URLSearchParams(location.search).get("holdemView") === "remastered" ? "remastered" : "classic");
}
window.WebSocket = class PreviewSocket extends EventTarget {
  static OPEN = 1;
  readyState = 1;
  send(data) {
    // Async delivery matches a server reply and lets button submission finish first.
    queueMicrotask(() => window.dispatchEvent(new CustomEvent("preview-send", {detail: JSON.parse(data)})));
  }
  close() { this.readyState = 3; }
};
