"use strict";

/** 摇杆最大位移相对控件半径的比例。 */
const STICK_RADIUS_RATIO = .3;

export function createEstateInput(root) {
  const keys = new Set();
  const vector = { x: 0, y: 0 };
  let actionPressed = false;
  let stickPointer = null;
  const stick = root.querySelector(".estate-stick");
  const nub = root.querySelector(".estate-stick-nub");
  const action = root.querySelector(".estate-action");

  const recalc = () => {
    vector.x = Number(keys.has("ArrowRight") || keys.has("KeyD")) - Number(keys.has("ArrowLeft") || keys.has("KeyA"));
    vector.y = Number(keys.has("ArrowDown") || keys.has("KeyS")) - Number(keys.has("ArrowUp") || keys.has("KeyW"));
  };
  const keydown = (event) => {
    if (!root.querySelector(".estate-sheet")?.hidden || !document.getElementById("gameAudioSettingsModal")?.hidden) return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (["KeyE", "Space"].includes(event.code) && !event.repeat) actionPressed = true;
    keys.add(event.code);
    recalc();
    if (event.code.startsWith("Arrow") || event.code === "Space") event.preventDefault();
  };
  const keyup = (event) => { keys.delete(event.code); recalc(); };
  const resetStick = () => {
    stickPointer = null;
    vector.x = 0; vector.y = 0;
    nub.style.transform = "translate(-50%, -50%)";
  };
  const moveStick = (event) => {
    if (event.pointerId !== stickPointer) return;
    const box = stick.getBoundingClientRect();
    const dx = event.clientX - (box.left + box.width / 2);
    const dy = event.clientY - (box.top + box.height / 2);
    const distance = Math.hypot(dx, dy) || 1;
    const radius = box.width * STICK_RADIUS_RATIO;
    const scale = Math.min(1, radius / distance);
    const px = dx * scale;
    const py = dy * scale;
    vector.x = px / radius;
    vector.y = py / radius;
    nub.style.transform = `translate(calc(-50% + ${px}px), calc(-50% + ${py}px))`;
  };
  const stickDown = (event) => {
    stickPointer = event.pointerId;
    stick.setPointerCapture(event.pointerId);
    moveStick(event);
  };
  const stickUp = (event) => { if (event.pointerId === stickPointer) resetStick(); };
  const actionDown = (event) => { event.preventDefault(); actionPressed = true; };
  const clear = () => { keys.clear(); resetStick(); actionPressed = false; };

  // 绑定与解绑共用同一张表，避免 destroy 漏删或多删。
  const bindings = [
    [window, "keydown", keydown, { passive: false }],
    [window, "keyup", keyup],
    [window, "blur", clear],
    [document, "gameaudiosettingsopen", clear],
    [stick, "pointerdown", stickDown],
    [stick, "pointermove", moveStick],
    [stick, "pointerup", stickUp],
    [stick, "pointercancel", stickUp],
    [action, "pointerdown", actionDown],
  ];
  for (const [target, type, handler, options] of bindings) {
    target.addEventListener(type, handler, options);
  }

  return {
    vector,
    get sprinting() { return keys.has("ShiftLeft"); },
    consumeAction() { const value = actionPressed; actionPressed = false; return value; },
    clear,
    destroy() {
      for (const [target, type, handler] of bindings) target.removeEventListener(type, handler);
    },
  };
}
