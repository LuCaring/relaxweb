/* hash 路由：#/prep、#/loadout、#/battle/<id>、#/result/<id>。
 *
 * 浏览器前进/后退可用；离开战斗视图只做视图切换，不发 abandon——
 * 战斗在服务端继续推进，回到 #/battle/<id> 即恢复。
 */

const ROUTES = [
  { pattern: /^#\/prep$/, name: "prep" },
  { pattern: /^#\/loadout$/, name: "loadout" },
  { pattern: /^#\/battle\/([A-Za-z0-9_-]+)$/, name: "battle" },
  { pattern: /^#\/result\/([A-Za-z0-9_-]+)$/, name: "result" },
];

export function currentRoute() {
  const hash = location.hash || "#/prep";
  for (const route of ROUTES) {
    const match = hash.match(route.pattern);
    if (match) return { name: route.name, param: match[1] || null, hash };
  }
  return { name: "prep", param: null, hash: "#/prep" };
}

export function navigate(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

let renderCurrent = () => {};

function render() {
  renderCurrent(currentRoute());
}

export function startRouter(renderFn) {
  renderCurrent = renderFn;
  window.addEventListener("hashchange", render);
  if (!location.hash) history.replaceState(null, "", "#/prep");
  render();
}
