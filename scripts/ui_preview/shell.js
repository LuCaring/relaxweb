const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
for (const id of ["game", "scene", "size"]) {
  if ([...$(id).options].some(option => option.value === params.get(id))) $(id).value = params.get(id);
}
function resize() {
  const dimensions = $("size").value.split("x").map(Number);
  const auto = $("size").value === "auto";
  $("table").style.width = auto ? "100%" : `${dimensions[0]}px`;
  $("table").style.height = auto ? `${Math.max(320, innerHeight - $("table").getBoundingClientRect().top - 16)}px` : `${dimensions[1]}px`;
}
function updateURL() {
  const next = new URLSearchParams(["game", "scene", "size"].map(id => [id, $(id).value]));
  history.replaceState(null, "", `?${next}`);
  $("direct").href = `/game.html?${next}`;
}
function load() {
  updateURL();
  $("table").src = $("direct").href;
  resize();
}
for (const id of ["game", "scene"]) $(id).addEventListener("change", load);
$("size").addEventListener("change", () => { updateURL(); resize(); });
$("mobile").addEventListener("click", () => {
  $("size").value = "390x844";
  updateURL(); resize();
});
$("rotate").addEventListener("click", () => {
  $("size").value = $("size").value === "844x390" ? "390x844" : "844x390";
  updateURL(); resize();
});
$("reset").addEventListener("click", load);
$("bubbles").addEventListener("click", () => $("table").contentWindow.postMessage({type: "preview-bubbles"}, location.origin));
window.addEventListener("resize", resize);
window.addEventListener("message", event => {
  if (event.origin !== location.origin || event.source !== $("table").contentWindow) return;
  if (event.data.type === "preview-feedback") $("feedback").textContent = event.data.text;
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
