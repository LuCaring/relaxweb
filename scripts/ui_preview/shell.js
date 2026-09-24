const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const waitingCapacity = {guandan: 4, mahjong: 4, holdem: 9, uno: 9,
  ludo: 4, liarsbar: 6, werewolf: 12};
for (let count = 1; count <= 12; count += 1) {
  const option = document.createElement("option");
  option.value = String(count);
  option.textContent = `${count} 人`;
  $("players").append(option);
}
for (const id of ["game", "scene", "size", "perspective", "watch", "players"]) {
  if ([...$(id).options].some(option => option.value === params.get(id))) $(id).value = params.get(id);
}
function syncPlayers() {
  const capacity = waitingCapacity[$("game").value];
  for (const option of $("players").options) option.disabled = Number(option.value) > capacity;
  if (Number($("players").value) > capacity) $("players").value = String(capacity);
  $("players-label").hidden = $("scene").value !== "waiting";
}
function resize() {
  const dimensions = $("size").value.split("x").map(Number);
  const auto = $("size").value === "auto";
  $("table").style.width = auto ? "100%" : `${dimensions[0]}px`;
  $("table").style.height = auto ? `${Math.max(320, innerHeight - $("table").getBoundingClientRect().top - 16)}px` : `${dimensions[1]}px`;
}
function updateURL() {
  const next = new URLSearchParams(["game", "scene", "size", "perspective", "watch", "players"].map(id => [id, $(id).value]));
  history.replaceState(null, "", `?${next}`);
  $("direct").href = `/game.html?${next}`;
}
function load(force = false) {
  const spectating = $("perspective").value === "spectator";
  $("watch-label").hidden = !spectating;
  $("scene").querySelector('[value="waiting"]').disabled = spectating;
  if (spectating && $("scene").value === "waiting") $("scene").value = "normal";
  syncPlayers();
  updateURL();
  if (force || $("table").src !== $("direct").href) $("table").src = $("direct").href;
  resize();
}
for (const id of ["game", "scene", "perspective", "watch", "players"]) $(id).addEventListener("change", () => load());
$("size").addEventListener("change", () => { updateURL(); resize(); });
$("mobile").addEventListener("click", () => {
  $("size").value = "390x844";
  updateURL(); resize();
});
$("rotate").addEventListener("click", () => {
  $("size").value = $("size").value === "844x390" ? "390x844" : "844x390";
  updateURL(); resize();
});
$("reset").addEventListener("click", () => load(true));
$("bubbles").addEventListener("click", () => $("table").contentWindow.postMessage({type: "preview-bubbles"}, location.origin));
window.addEventListener("resize", resize);
window.addEventListener("message", event => {
  if (event.origin !== location.origin || event.source !== $("table").contentWindow) return;
  if (event.data.type === "preview-feedback") $("feedback").textContent = event.data.text;
  if (event.data.type === "preview-watch" && [...$("watch").options].some(option => option.value === event.data.watch)) {
    $("watch").value = event.data.watch;
    updateURL();
  }
});
load();
let revision;
async function watch() {
  try {
    const response = await fetch("/__preview/revision");
    if (!response.ok) throw new Error("preview server unavailable");
    const next = (await response.json()).revision;
    if (revision && next !== revision && $("reload").checked) location.reload();
    revision = next;
  } catch {
    $("feedback").textContent = "本地预览服务已断开，重新运行启动命令后刷新页面。";
  } finally {
    setTimeout(watch, 1200);
  }
}
watch();
