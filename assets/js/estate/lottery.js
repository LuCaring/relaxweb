"use strict";

import { estateRequest } from "./protocol.js";
import { estateStore } from "./state.js";

const LABELS = ["谢谢惠顾", "250金币", "1000金币", "2000金币", "传说花",
  "纪念品", "化肥×2", "神秘大奖"];

function rewardLabel(result) {
  const award = result.award;
  if (award.startsWith("coins_")) return `恭喜获得 ${result.coins_awarded} 金币！`;
  if (award === "legendary_seed") return "恭喜获得传说花种子 ×1！基础成熟时间 72 小时，售价 36,888 金币。";
  if (award === "fertilizer_2") return "恭喜获得化肥 ×2！";
  if (award === "land_ticket") return "恭喜获得 4级农田升级券 ×1！可用于一块空置的 3级农田。";
  if (award === "missing_collectible") {
    if (result.all_collectibles_owned) return "已集齐全部纪念品，获得皮肤碎片 ×1！";
    const name = estateStore.snapshot?.catalog?.fishing_treasures?.[result.collectible_id]?.name || "纪念品";
    return `恭喜获得尚未拥有的纪念品：${name}！`;
  }
  return "谢谢惠顾，欢迎下次再来。";
}

function wait(ms) { return new Promise((resolve) => window.setTimeout(resolve, ms)); }

export function renderLotteryGame(target, snapshot, onBusyChange) {
  const price = snapshot.catalog.lottery.price;
  const wrap = document.createElement("div"); wrap.className = "estate-lottery";
  const note = document.createElement("p"); note.className = "estate-sheet-note";
  note.textContent = `8 个奖项等概率。每次 ${price.toLocaleString("zh-CN")} 金币；神秘大奖另按公布概率结算，其中“再抽一次”免费。抽奖前须留出 2 格仓位。`;
  const stage = document.createElement("div"); stage.className = "estate-lottery-stage";
  const wheel = document.createElement("div"); wheel.className = "estate-lottery-wheel";
  for (let index = 0; index < LABELS.length; index += 1) {
    const label = document.createElement("span"); label.className = "estate-lottery-sector";
    label.textContent = LABELS[index];
    label.dataset.dark = String(index % 2 === 0);
    label.style.setProperty("--sector-angle", `${22.5 + index * 45}deg`);
    label.style.setProperty("--sector-counter-angle", `${-22.5 - index * 45}deg`);
    wheel.append(label);
  }
  const pointer = document.createElement("div"); pointer.className = "estate-lottery-pointer";
  stage.append(wheel, pointer);
  const status = document.createElement("p"); status.className = "estate-lottery-status";
  status.setAttribute("role", "status"); status.textContent = "指针所指的奖项，就是本次抽奖结果。";
  const button = document.createElement("button"); button.type = "button";
  button.className = "estate-button estate-button-gold";
  button.textContent = `${price.toLocaleString("zh-CN")} 金币 · 开始抽奖`;
  const canDraw = (current) => Number(current.coins) >= price
    && Number(current.profile.warehouse_capacity) - Number(current.profile.warehouse_used) >= 2;
  button.disabled = !canDraw(snapshot);
  if (!button.disabled) status.textContent = "指针所指的奖项，就是本次抽奖结果。";
  else status.textContent = Number(snapshot.coins) < price
    ? "金币不足，暂时不能抽奖。" : "请先腾出至少 2 格仓位。";
  const rules = document.createElement("p"); rules.className = "estate-sheet-note";
  rules.textContent = "神秘大奖：10% 得 100,000 金币、40% 免费再抽一次、10% 得农田升级券、20% 得 5,000 金币、20% 得 10,000 金币。传说花需种植成熟后出售；纪念品集齐后抽中该奖项可获得 1 个皮肤碎片。";
  const historyButton = document.createElement("button"); historyButton.type = "button";
  historyButton.className = "estate-button"; historyButton.textContent = "查看抽奖记录";
  const historyList = document.createElement("div"); historyList.className = "estate-lottery-history";
  historyList.hidden = true;
  const loadHistory = async () => {
    historyList.textContent = "正在读取记录…";
    try {
      const rows = await estateRequest("estate_lottery_history", {}, { timeoutMs: 12000 });
      historyList.replaceChildren();
      if (!rows.length) historyList.textContent = "暂无抽奖记录。";
      for (const row of rows) {
        const entry = document.createElement("p");
        entry.textContent = `${new Date(row.created_at * 1000).toLocaleString("zh-CN")} · ${rewardLabel(row)}`;
        historyList.append(entry);
      }
    } catch (error) { historyList.textContent = error.message || "记录读取失败，请重试。"; }
  };
  historyButton.addEventListener("click", () => {
    historyList.hidden = !historyList.hidden;
    historyButton.textContent = historyList.hidden ? "查看抽奖记录" : "收起抽奖记录";
    if (!historyList.hidden) void loadHistory();
  });
  wrap.append(note, stage, status, button, rules, historyButton, historyList); target.append(wrap);

  let busy = false; let rotation = 0;
  button.addEventListener("click", async () => {
    if (busy) return;
    busy = true; onBusyChange(true); button.disabled = true;
    status.textContent = "正在确认抽奖结果…";
    try {
      const result = await estateRequest("estate_lottery_draw", {}, { timeoutMs: 15000 });
      for (const [index, spin] of result.spins.entries()) {
        const slot = snapshot.catalog.lottery.prizes.indexOf(spin.prize);
        if (slot < 0) throw new Error("抽奖落点无效");
        const targetAngle = (360 - 22.5 - slot * 45) % 360;
        const delta = (targetAngle - rotation % 360 + 360) % 360;
        rotation += 360 * 5 + delta;
        status.textContent = `第 ${index + 1} 次转动中…`;
        wheel.style.transform = `rotate(${rotation}deg)`;
        await wait(3000);
        if (spin.grand_prize === "reroll") status.textContent = "神秘大奖：免费再抽一次！";
      }
      status.textContent = rewardLabel(result);
      if (!historyList.hidden) void loadHistory();
    } catch (error) {
      status.textContent = error.message || "抽奖失败，请稍后重试";
    } finally {
      busy = false; onBusyChange(false);
      button.disabled = !canDraw(estateStore.snapshot || snapshot);
    }
  });
}
