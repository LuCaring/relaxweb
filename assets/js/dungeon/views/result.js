/* 结算页：拉取服务器终态，展示胜负、金币、掉落，并处理待领取。 */

import { request, refreshState } from "../protocol.js";
import { dgn, notify, reportError, itemStatsText, QUALITY_LABELS } from "../state.js";
import { navigate } from "../router.js";
import { visualNode } from "../assets.js";
import { loadingNode } from "./prep.js";

const OUTCOME_LABELS = {
  victory: "胜利！",
  defeat: "战败",
  timeout: "超时",
  abandoned: "已放弃",
  error: "异常结束",
};

function durationText(durationUs) {
  const seconds = (durationUs || 0) / 1_000_000;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
  return `${seconds.toFixed(1)} 秒`;
}

function itemRow(item) {
  const row = document.createElement("li");
  row.className = "dgn-result-item";
  const visualBox = document.createElement("span");
  visualBox.append(visualNode("item", item.visual_id, { size: 28, slot: item.slot }));
  row.append(visualBox);
  const info = document.createElement("div");
  const name = document.createElement("b");
  name.textContent = item.locked ? `🔒 ${item.name}` : item.name;
  const detail = document.createElement("span");
  detail.className = "dgn-meta";
  detail.textContent = `${QUALITY_LABELS[item.quality] || item.quality} · ${itemStatsText(item) || "无属性"}`;
  info.append(name, detail);
  row.append(info);
  if (item.location === "pending") {
    const tag = document.createElement("span");
    tag.className = "dgn-tag dgn-tag-pending";
    tag.textContent = "待领取";
    row.append(tag);
  } else {
    const tag = document.createElement("span");
    tag.className = "dgn-tag";
    tag.textContent = "已入包";
    row.append(tag);
  }
  return row;
}

export function render(container, battleId) {
  container.replaceChildren(loadingNode("正在读取结算…"));
  request("dungeon_get_result", { battle_id: battleId }).then((data) => {
    if (data.battle?.battle_id !== battleId) throw Object.assign(new Error("结算不存在"), { code: "not_found" });
    drawResult(container, data.battle, data.result);
  }).catch((error) => {
    if (error?.code === "not_found") {
      notify("挑战不存在", "warn");
      navigate("#/prep");
      return;
    }
    reportError(error);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "dgn-btn";
    retry.textContent = "重新加载";
    retry.addEventListener("click", () => render(container, battleId));
    container.replaceChildren(retry);
  });
}

function drawResult(container, battle, result) {
  container.replaceChildren();
  const outcome = document.createElement("h2");
  outcome.className = `dgn-outcome dgn-outcome-${result?.outcome || "error"}`;
  outcome.textContent = OUTCOME_LABELS[result?.outcome] || result?.outcome || "未知";
  container.append(outcome);

  const summary = document.createElement("div");
  summary.className = "dgn-card dgn-result-summary";
  const coins = document.createElement("p");
  coins.className = "dgn-result-coins";
  coins.textContent = `金币 +${result?.coins_gained ?? 0}`;
  summary.append(coins);
  const stats = document.createElement("p");
  stats.className = "dgn-meta";
  stats.textContent = `用时 ${durationText(result?.duration_us)} · 造成伤害 ${result?.damage_dealt ?? 0}`
    + ` · 受到伤害 ${result?.damage_taken ?? 0}`;
  summary.append(stats);
  if (result?.outcome === "error") {
    const err = document.createElement("p");
    err.className = "dgn-meta dgn-dim";
    err.textContent = `战斗模拟异常：${result?.error_code || "unknown"}（无奖励，请联系管理员）`;
    summary.append(err);
  }
  container.append(summary);

  const items = result?.items || [];
  const pendingIds = items.filter((item) => item.location === "pending").map((item) => item.item_id);
  if (items.length) {
    const card = document.createElement("div");
    card.className = "dgn-card";
    card.append(Object.assign(document.createElement("h3"), { textContent: "掉落" }));
    const list = document.createElement("ul");
    list.className = "dgn-result-items";
    for (const item of items) list.append(itemRow(item));
    card.append(list);
    if (pendingIds.length) {
      const claimButton = document.createElement("button");
      claimButton.type = "button";
      claimButton.className = "dgn-btn dgn-btn-primary";
      claimButton.textContent = `领取待入包装备（${pendingIds.length}）`;
      claimButton.addEventListener("click", () => void claim(claimButton, pendingIds, card));
      card.append(claimButton);
    }
    container.append(card);
  }

  const actions = document.createElement("div");
  actions.className = "dgn-controls";
  const prep = document.createElement("button");
  prep.type = "button";
  prep.className = "dgn-btn dgn-btn-primary";
  prep.textContent = "返回准备页";
  prep.addEventListener("click", () => navigate("#/prep"));
  actions.append(prep);
  if (battle.status === "running" || battle.status === "paused") {
    const back = document.createElement("button");
    back.type = "button";
    back.className = "dgn-btn";
    back.textContent = "战斗仍在进行，回到战斗";
    back.addEventListener("click", () => navigate(`#/battle/${battle.battle_id}`));
    actions.append(back);
  }
  container.append(actions);
}

async function claim(button, ids, card) {
  button.disabled = true;
  try {
    await request("dungeon_claim_items", { item_ids: ids });
    notify("装备已领取入包", "info");
    card.querySelectorAll(".dgn-tag-pending").forEach((tag) => {
      tag.textContent = "已入包";
      tag.classList.remove("dgn-tag-pending");
    });
    button.remove();
    void refreshState();
  } catch (error) {
    reportError(error);
    button.disabled = false;
  }
}
