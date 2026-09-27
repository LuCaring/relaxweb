"use strict";

/* 消息与视图注册表。
   core 只管分发，各功能模块在加载时自己注册处理函数与视图，
   这样模块之间不需要互相 import，也就不会出现循环依赖。
   游戏视图（牌桌 / 结算回顾 / 座位选择器）同样在这里挂载，
   与后端 games/ 的 @register_room_type 是同一套思路。 */

const messageHandlers = new Map();
const viewRenderers = new Map();
const gameViews = new Map();

/** 注册一条服务器消息的处理函数（同一个 type 后注册的覆盖先注册的）。 */
export function onMessage(type, handler) {
  messageHandlers.set(type, handler);
}

export function dispatchMessage(data) {
  messageHandlers.get(data?.type)?.(data);
}

/** 注册一个页面视图：entry / hall / rooms / create / room。 */
export function registerView(name, render) {
  viewRenderers.set(name, render);
}

export function renderView(name) {
  return viewRenderers.get(name)?.();
}

/** 注册一个游戏的牌桌实现，见各 games/*.js 的 registerGame 调用。 */
export function registerGame(id, view) {
  gameViews.set(id, view);
}

export function gameView(id) {
  if (id === "holdem" && localStorage.getItem("relaxweb:holdem-view") === "remastered") {
    return gameViews.get("holdem-remastered") || gameViews.get("holdem") || null;
  }
  return gameViews.get(id) || null;
}

/** Look up an exact registration, bypassing a player's selected visual variant. */
export function registeredGameView(id) {
  return gameViews.get(id) || null;
}
