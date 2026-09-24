"use strict";

/* 入口：装配各模块（模块加载时自行注册消息处理与视图）、绑定静态元素事件、启动连接。 */

import {
  $, AUTH_TOKEN_KEY, SITE, connectGame, elements, leaveRoom, send, setManageMenu,
  setSignedIn, setSpectateMenu, setUserMenu, state, stopHallTicker, transferSelect,
} from "./core.js";
import {
  openFinance, openLogin, setAuthMode, submitAuth, submitTransfer, switchFinanceTab,
} from "./auth.js";
import { closeChatOverlay } from "./room-chat.js";
import { confirmDialog } from "./dialog.js";
import { initializeGameAudio } from "./game-audio.js";
// 以下模块靠导入时的副作用完成注册（大厅视图、房间视图、各游戏牌桌）
import "./hall.js";
import "./room.js";
import "./games/holdem.js";
import "./games/uno.js";
import "./games/guandan.js";
import "./games/mahjong.js";
import "./games/ludo.js?v=2";
import "./games/liarsbar.js?v=2";
import "./games/werewolf.js?v=2";
import "./estate/view.js";

initializeGameAudio();

elements.loginButton.addEventListener("click", openLogin);
elements.authSubmit.addEventListener("click", submitAuth);
elements.authSwitch.addEventListener("click", () => setAuthMode(state.authMode === "login" ? "register" : "login"));
elements.userChip.addEventListener("click", () => setUserMenu(elements.userDropdown.hidden));
elements.financeButton.addEventListener("click", openFinance);
elements.coinChip.addEventListener("click", openFinance);
elements.financeClose.addEventListener("click", () => {
  elements.financeModal.style.display = "none";
});
elements.financeModal.addEventListener("click", (event) => {
  if (event.target === elements.financeModal) elements.financeModal.style.display = "none";
});
elements.financeTabDetail.addEventListener("click", () => switchFinanceTab("detail"));
elements.financeTabTransfer.addEventListener("click", () => switchFinanceTab("transfer"));
elements.transferSubmit.addEventListener("click", submitTransfer);
elements.transferAmount.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitTransfer();
});
elements.logoutButton.addEventListener("click", () => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) send({ type: "logout", token });
  localStorage.removeItem(AUTH_TOKEN_KEY);
  setSignedIn(null);
});
elements.leaveRoomButton.addEventListener("click", leaveRoom);
elements.manageButton.addEventListener("click", () => {
  setManageMenu(elements.manageMenu.hidden);
});
elements.manageDrawButton.addEventListener("click", async () => {
  setManageMenu(false);
  const ok = await confirmDialog("牌局结束，所有人按当前筹码退回金币。", {
    title: "确定流局？",
    tone: "danger",
  });
  if (ok) send({ type: "leave_room" });
});
elements.managePauseButton.addEventListener("click", () => {
  setManageMenu(false);
  send({ type: "pause_game", paused: !state.myRoom.paused });
});
elements.manageRestartButton.addEventListener("click", async () => {
  setManageMenu(false);
  const detail = state.myRoom?.game_type === "uno"
    ? "将收回本局所有手牌并重新发牌。"
    : "本手已投注的筹码将退回各家，并重新发牌。";
  if (await confirmDialog(detail, { title: "确定重新开始？" })) {
    send({ type: "restart_game" });
  }
});
elements.spectateButton.addEventListener("click", () => {
  setSpectateMenu(elements.spectateMenu.hidden);
});
document.addEventListener("click", (event) => {
  if (!elements.userArea.contains(event.target)) setUserMenu(false);
  if (!elements.roomManage.contains(event.target)) setManageMenu(false);
  if (!elements.spectateManage.contains(event.target)) setSpectateMenu(false);
});
elements.password.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) submitAuth();
});
elements.authModal.addEventListener("click", (event) => {
  if (event.target === elements.authModal) elements.authModal.style.display = "none";
});
window.addEventListener("beforeunload", () => {
  clearTimeout(state.reconnectTimer);
  stopHallTicker();
  closeChatOverlay();
});

if (SITE.game_title) {
  document.title = SITE.game_title;
  const brand = document.querySelector(".game-header .logo, .logo");
  if (brand && brand.textContent.trim() === "游戏厅") brand.textContent = SITE.game_title;
}

transferSelect()?.init(
  {
    toggle: $("transferSelectToggle"),
    menu: $("transferSelectMenu"),
    list: $("transferSelectList"),
    hidden: $("transferTo"),
  },
  () => send({ type: "list_users" }),
  () => state.currentUser?.username,
);
connectGame();
