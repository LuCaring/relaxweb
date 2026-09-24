/* 地下城独立入口：装配协议、状态、路由与视图。
 *
 * 与庄园前端零依赖：登录、连接、渲染全部由本目录模块完成；只共享
 * localStorage 里的登录令牌（liveAuthToken）与服务端注入的 LIVE_CONFIG。
 */

import { auth, connect, signIn, logout, on } from "./dgn-protocol.js";
import { applyState, dgn, subscribe } from "./dgn-state.js";
import { currentRoute, navigate, startRouter } from "./dgn-router.js";
import * as prepView from "./views/prep.js";
import * as loadoutView from "./views/loadout.js";
import * as battleView from "./views/battle.js";
import * as resultView from "./views/result.js";

const $ = (id) => document.getElementById(id);
const elements = {
  conn: $("dgnConn"), coins: $("dgnCoins"), coinsValue: $("dgnCoinsValue"),
  login: $("dgnLogin"), loginError: $("dgnLoginError"), loginForm: $("dgnLoginForm"),
  password: $("dgnPassword"), username: $("dgnUsername"),
  logout: $("dgnLogout"), user: $("dgnUser"), view: $("dgnView"),
};

/* ---------------------------------------------------------
   轻提示
--------------------------------------------------------- */
let toastTimer = 0;
window.addEventListener("dgn-toast", ({ detail }) => {
  const toast = $("dgnToast");
  toast.textContent = detail.message;
  toast.className = `dgn-toast dgn-toast-${detail.tone || "info"}`;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3000);
});

/* 视图内部请求的当前页重渲染（如起局失败后恢复按钮状态）。 */
window.addEventListener("dgn-refresh-prep", () => {
  const route = currentRoute();
  if (route.name === "prep" || route.name === "loadout") renderRoute();
});

/* ---------------------------------------------------------
   连接与登录态
--------------------------------------------------------- */

function renderAuthState() {
  const signedIn = Boolean(auth.user);
  elements.login.hidden = signedIn;
  elements.view.hidden = !signedIn;
  elements.coins.hidden = !signedIn;
  elements.user.hidden = !signedIn;
  elements.logout.hidden = !signedIn;
  if (signedIn) {
    elements.user.textContent = auth.user.nickname || auth.user.username;
    renderRoute();
  } else {
    battleView.teardown();
    elements.view.replaceChildren();
  }
}

on("auth", (user) => {
  renderAuthState();
  if (!user) elements.loginError.textContent = "";
});

on("login_success", () => {
  elements.password.value = "";
  elements.loginError.textContent = "";
});

on("auth_error", (message) => {
  elements.loginError.textContent = message || "登录失败";
});

on("need_login", () => renderAuthState());

on("conn", (isConnected) => {
  elements.conn.textContent = isConnected ? "已连接" : "重连中…";
  elements.conn.classList.toggle("dgn-conn-off", !isConnected);
});

on("request_error", (error) => {
  if (["auth_required", "invalid_request"].includes(error?.code)) return;
  window.dispatchEvent(new CustomEvent("dgn-toast",
    { detail: { message: error?.message || "请求失败", tone: "warn" } }));
});

elements.loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const username = elements.username.value.trim();
  const password = elements.password.value;
  if (!username || !password) {
    elements.loginError.textContent = "请输入用户名和密码";
    return;
  }
  elements.loginError.textContent = "";
  void signIn(username, password).catch((error) => {
    elements.loginError.textContent = error.message || "登录失败";
  });
});

elements.logout.addEventListener("click", () => {
  logout();
  navigate("#/prep");
});

/* ---------------------------------------------------------
   状态 → 顶栏
--------------------------------------------------------- */

function renderHeader() {
  if (auth.user && dgn.snapshot) {
    elements.coinsValue.textContent = String(dgn.snapshot.coins ?? 0);
  }
}

subscribe(renderHeader);

/* 服务端状态到达：初次加载/重载时若战斗仍在进行，自动跳回战斗页；
 * 准备页/装备页是数据驱动视图，随最新快照重渲染。战斗页与结算页
 * 自管理事件播放，不随状态推送重建，避免打断本地动画与日志。
 */
let autoResumed = false;
on("state", (data) => {
  applyState(data);
  if (!autoResumed && auth.user) {
    autoResumed = true;
    const route = currentRoute();
    const active = dgn.snapshot?.active_job;
    if (active?.kind === "battle" && (route.name === "prep" || route.name === "loadout")) {
      navigate(`#/battle/${active.id}`);
      return;
    }
  }
  const route = currentRoute();
  if (!elements.view.hidden && (route.name === "prep" || route.name === "loadout")) renderRoute();
});

/* ---------------------------------------------------------
   路由与视图
--------------------------------------------------------- */

function renderRoute() {
  if (!auth.user) return;
  const route = currentRoute();
  battleView.teardown();
  window.scrollTo(0, 0);
  switch (route.name) {
    case "loadout":
      loadoutView.renderLoadout(elements.view);
      break;
    case "battle":
      battleView.renderBattle(elements.view, route.param);
      break;
    case "result":
      resultView.renderResult(elements.view, route.param);
      break;
    case "prep":
    default:
      prepView.renderPrep(elements.view);
      break;
  }
}

startRouter(renderRoute);

/* ---------------------------------------------------------
   启动
--------------------------------------------------------- */
connect();
renderAuthState();
