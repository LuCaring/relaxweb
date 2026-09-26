/* 成员条目上的按人音量菜单：行尾齿轮按钮展开右对齐浮层滑杆。
   音量存取走 voice-mic.js 的按人音量缓存（localStorage），远端音轨的
   实时同步由 room-voice.js 监听 voicepeerchange 完成，这里只写偏好。
   document 级的关闭监听是模块级单例，首次挂载时注册一次；
   宿主节点从文档移除后监听器自动旁路，不残留行为。 */

import { getPeerVolume, setPeerVolume } from "./voice-mic.js";

let active = null;  // 当前展开的菜单 { root, close }
let bound = false;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function bindDocumentClose() {
  if (bound) return;
  bound = true;
  document.addEventListener("click", (event) => {
    if (!active) return;
    if (!active.root.isConnected) { active = null; return; }
    if (active.root.contains(event.target)) return;
    active.close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !active) return;
    if (!active.root.isConnected) { active = null; return; }
    active.close();
  });
}

/** 在成员条目 row 内挂齿轮按钮与音量浮层，返回 { update(username, visible) }。 */
export function attachPeerVolumeMenu(row) {
  const host = row.querySelector(".waiting-seat-volume") || row;
  const gear = el("button", "seat-volume-gear", "⚙");
  gear.type = "button";
  gear.setAttribute("aria-label", "调整音量");
  gear.setAttribute("aria-haspopup", "true");
  gear.setAttribute("aria-expanded", "false");
  gear.hidden = true;
  const popover = el("div", "peer-volume-popover");
  popover.hidden = true;
  const range = el("input", "peer-volume-range");
  range.type = "range";
  range.min = "0";
  range.max = "150";
  range.step = "5";
  const value = el("span", "peer-volume-value");
  const reset = el("button", "peer-volume-reset", "重置");
  reset.type = "button";
  popover.append(range, value, reset);
  host.append(gear, popover);
  bindDocumentClose();

  let username = "";
  const menu = { root: host, close };

  function open() {
    if (active && active !== menu) active.close();
    const volume = getPeerVolume(username);
    range.setAttribute("aria-label", `${username} 音量`);
    range.value = String(volume);
    value.textContent = `${volume}%`;
    popover.hidden = false;
    // 展开时抬高层级：宿主 transform 形成层叠上下文，后续座位行会盖住浮层
    host.classList.add("volume-open");
    gear.setAttribute("aria-expanded", "true");
    active = menu;
  }

  function close() {
    popover.hidden = true;
    host.classList.remove("volume-open");
    gear.setAttribute("aria-expanded", "false");
    if (active === menu) active = null;
  }

  gear.addEventListener("click", () => {
    if (!username) return;
    if (popover.hidden) open();
    else close();
  });
  range.addEventListener("input", () => {
    if (!username) return;
    const volume = setPeerVolume(username, Number(range.value) || 0);
    value.textContent = `${volume}%`;
  });
  reset.addEventListener("click", () => {
    if (!username) return;
    const volume = setPeerVolume(username, 100);
    range.value = String(volume);
    value.textContent = `${volume}%`;
  });

  return {
    /** visible=false 时隐藏齿轮并收起浮层；否则记住要调整的成员。 */
    update(name, visible) {
      if (!visible) {
        username = "";
        gear.hidden = true;
        close();
        return;
      }
      username = name;
      gear.hidden = false;
    },
  };
}
