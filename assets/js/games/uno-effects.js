/* 服务器公开动作驱动的动画；不根据私有手牌差分猜测对手牌面。 */
import { selfUsername, state } from "../core.js";

let seenRoom = "";
let seenEvent = 0;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function clearEffects() {
  document.querySelectorAll(".uno-flight, .uno-effect-banner").forEach((node) => node.remove());
}
document.addEventListener("gameviewchange", () => {
  if (state.myRoom?.game_type !== "uno") {
    seenRoom = "";
    seenEvent = 0;
    clearEffects();
  }
  if (state.myRoom?.paused) clearEffects();
});
reducedMotion.addEventListener("change", clearEffects);

function seatFor(username) {
  return [...document.querySelectorAll(".uno-seat")].find((seat) => seat.dataset.username === username);
}

function flyCard(from, to, card, delay = 0) {
  if (!from || !to || reducedMotion.matches) return;
  const a = from.getBoundingClientRect();
  const b = to.getBoundingClientRect();
  const node = card.cloneNode(true);
  node.classList.add("uno-flight");
  node.setAttribute("aria-hidden", "true");
  node.style.left = `${a.x + a.width / 2 - 23}px`;
  node.style.top = `${a.y + a.height / 2 - 32}px`;
  document.body.append(node);
  const dx = b.x + b.width / 2 - a.x - a.width / 2;
  const dy = b.y + b.height / 2 - a.y - a.height / 2;
  const motion = node.animate([
    { transform: "translate(0, 0) rotate(-12deg) scale(.7)", opacity: 0 },
    { opacity: 1, offset: .15 },
    { transform: `translate(${dx}px, ${dy}px) rotate(5deg) scale(1)`, opacity: 0 },
  ], { duration: 620, delay, easing: "cubic-bezier(.2,.7,.25,1)", fill: "both" });
  motion.finished.finally(() => node.remove());
}

export function animateUnoEvent(cardNode) {
  const room = state.myRoom;
  const key = `${room.room_id}:${room.hand_no}`;
  const event = room.action_event;
  if (key !== seenRoom) { seenRoom = key; seenEvent = event?.id || 0; return; }
  if (!event || event.id <= seenEvent) return;
  seenEvent = event.id;
  if (room.paused) return;
  requestAnimationFrame(() => {
    if (state.myRoom?.room_id !== room.room_id || state.myRoom?.hand_no !== room.hand_no || state.myRoom.paused) return;
    const table = document.querySelector(".uno-table");
    const destination = document.querySelector(".uno-discard") || document.querySelector("#gameMain .poker-result");
    const source = event.username === selfUsername()
      ? document.querySelector(".uno-hand") : seatFor(event.username);
    if (event.kind === "play") flyCard(source, destination, cardNode(event.card));
    const target = event.target === selfUsername()
      ? document.querySelector(".uno-hand") : seatFor(event.target);
    for (let i = 0; i < Math.min(4, event.count || 0); i += 1) {
      flyCard(document.querySelector(".uno-pile"), target, cardNode(null, { back: true }), 160 + i * 110);
    }
    const value = event.card?.v;
    const labels = { rev: "方向反转", skip: "禁止出牌", d2: "+2 · 罚摸并跳过", wd4: "+4 · 罚摸并跳过", wild: "切换颜色" };
    const label = event.kind === "challenge" ? `质疑成功 · 罚摸 ${event.count} 张`
      : event.kind === "uno" ? "UNO!" : labels[value];
    if (!label) return;
    const banner = document.createElement("div");
    banner.className = `uno-effect-banner effect-${value || event.kind}`;
    banner.textContent = label;
    banner.setAttribute("role", "status");
    (table || document.getElementById("gameMain")).append(banner);
    window.setTimeout(() => banner.remove(), 1600);
    const victim = seatFor(event.target);
    if (victim && !reducedMotion.matches) {
      victim.animate([{ filter: "brightness(1)" }, { filter: "brightness(1.5)", outline: "4px solid #ffce5c" }, { filter: "brightness(1)" }], { duration: 900 });
    }
  });
}
