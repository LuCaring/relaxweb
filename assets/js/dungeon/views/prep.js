/* 准备页：属性面板、关卡列表、起局与进行中战斗的恢复入口。 */

import { request } from "../protocol.js";
import { applyBattle, dgn, progressOf, reportError,
         DIFFICULTY_LABELS, ENEMY_TYPE_LABELS, statText, STAT_LABELS } from "../state.js";
import { navigate } from "../router.js";
import { visualNode } from "../assets.js";

function statPanel(stats) {
  const panel = document.createElement("div");
  panel.className = "dgn-stats";
  for (const [stat, value] of Object.entries(stats?.values || {})) {
    const cell = document.createElement("div");
    cell.className = "dgn-stat";
    cell.append(Object.assign(document.createElement("span"),
      { className: "dgn-stat-label", textContent: STAT_LABELS[stat] || stat }));
    cell.append(Object.assign(document.createElement("b"),
      { textContent: statText(stat, value) }));
    panel.append(cell);
  }
  return panel;
}

function challengeCard(challenge) {
  const card = document.createElement("article");
  card.className = "dgn-card dgn-challenge";
  const progress = progressOf(challenge.challenge_id, challenge.difficulty_id);
  const unlocked = Boolean(progress?.unlocked);

  const visualBox = document.createElement("div");
  visualBox.className = "dgn-challenge-visual";
  const visual = visualNode("enemy", challenge.enemy.visual_id,
    { size: 56, enemyType: challenge.enemy.type });
  visualBox.append(visual);
  card.append(visualBox);

  const info = document.createElement("div");
  info.className = "dgn-challenge-info";
  const title = document.createElement("h3");
  title.textContent = challenge.enemy.name;
  const meta = document.createElement("p");
  meta.className = "dgn-meta";
  const preview = challenge.reward_preview || {};
  meta.textContent = `${DIFFICULTY_LABELS[challenge.difficulty_id] || challenge.difficulty_id}`
    + ` · ${ENEMY_TYPE_LABELS[challenge.enemy.type] || challenge.enemy.type}`
    + ` · 奖励 ${preview.coins ?? 0} 金币`
    + (progress?.clear_count ? ` · 已通关 ${progress.clear_count} 次` : "");
  info.append(title, meta);

  const enemyStats = Object.entries(challenge.enemy.stats || {})
    .filter(([stat]) => ["max_hp", "atk", "defense", "speed"].includes(stat))
    .map(([stat, value]) => `${STAT_LABELS[stat]} ${statText(stat, value)}`)
    .join(" / ");
  const enemyLine = document.createElement("p");
  enemyLine.className = "dgn-meta dgn-dim";
  enemyLine.textContent = lockedText(challenge, unlocked) || `敌方：${enemyStats}`;
  info.append(enemyLine);
  card.append(info);

  const action = document.createElement("div");
  action.className = "dgn-challenge-action";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "dgn-btn dgn-btn-primary";
  button.disabled = !unlocked || Boolean(dgn.snapshot?.active_job);
  button.textContent = !unlocked ? "未解锁" : dgn.snapshot?.active_job ? "挑战中" : "发起挑战";
  button.addEventListener("click", () => startChallenge(challenge, button));
  action.append(button);
  card.append(action);
  return card;
}

function lockedText(challenge, unlocked) {
  if (unlocked || !challenge.requires?.length) return null;
  const names = challenge.requires.map((required) => {
    const key = typeof required === "string"
      ? [required, challenge.difficulty_id]
      : [required.challenge_id, required.difficulty_id];
    return `${key[0]}（${DIFFICULTY_LABELS[key[1]] || key[1]}）`;
  });
  return `通关 ${names.join("、")} 后解锁`;
}

async function startChallenge(challenge, button) {
  button.disabled = true;
  try {
    const data = await request("dungeon_start", {
      challenge_id: challenge.challenge_id,
      difficulty_id: challenge.difficulty_id,
      expected_version: dgn.snapshot.profile_version,
    });
    const battle = data.result?.battle;
    if (!battle?.battle_id) throw new Error("起局响应缺少战斗编号");
    applyBattle(battle);
    navigate(`#/battle/${battle.battle_id}`);
  } catch (error) {
    reportError(error);
    emitRerender();
  }
}

function emitRerender() {
  window.dispatchEvent(new CustomEvent("dgn-refresh-prep"));
}

export function render(container) {
  const snapshot = dgn.snapshot;
  container.replaceChildren();

  if (!snapshot) {
    container.append(loadingNode());
    return;
  }

  const active = snapshot.active_job;
  if (active?.kind === "battle") {
    const banner = document.createElement("div");
    banner.className = "dgn-banner";
    banner.append(Object.assign(document.createElement("span"),
      { textContent: "有一场挑战正在进行，后台不会中断。" }));
    const resume = document.createElement("button");
    resume.type = "button";
    resume.className = "dgn-btn dgn-btn-primary";
    resume.textContent = "回到战斗";
    resume.addEventListener("click", () => navigate(`#/battle/${active.id}`));
    banner.append(resume);
    container.append(banner);
  }
  if (snapshot.pending_count > 0) {
    const banner = document.createElement("div");
    banner.className = "dgn-banner dgn-banner-warn";
    banner.append(Object.assign(document.createElement("span"),
      { textContent: `背包已满，有 ${snapshot.pending_count} 件装备待领取。` }));
    const go = document.createElement("button");
    go.type = "button";
    go.className = "dgn-btn";
    go.textContent = "去装备页领取";
    go.addEventListener("click", () => navigate("#/loadout"));
    banner.append(go);
    container.append(banner);
  }

  const statsCard = document.createElement("section");
  statsCard.className = "dgn-card";
  statsCard.append(Object.assign(document.createElement("h2"), { textContent: "我的属性" }));
  statsCard.append(statPanel(snapshot.stats));
  container.append(statsCard);

  const listCard = document.createElement("section");
  listCard.className = "dgn-card";
  listCard.append(Object.assign(document.createElement("h2"), { textContent: "关卡列表" }));
  const hint = document.createElement("p");
  hint.className = "dgn-meta dgn-dim";
  hint.textContent = "挑战由服务端自动战斗：起局后可暂停、倍速或离开，随时回来观看。";
  listCard.append(hint);
  for (const challenge of snapshot.catalog.challenges) listCard.append(challengeCard(challenge));
  container.append(listCard);
}

export function loadingNode(text = "加载中…") {
  const node = document.createElement("div");
  node.className = "dgn-loading";
  node.textContent = text;
  return node;
}
