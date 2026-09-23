/* 战斗页：血条、事件日志、暂停/倍速/放弃与周期同步。
 *
 * 纪律（交接文档第 3 节）：HP 以服务端权威检查点为准；断线或进入页面先
 * 恢复检查点再补文本日志，不重放历史伤害动画。离开本视图只停本地定时器，
 * 绝不发送 abandon——后台战斗继续推进，可随时回来。
 */

import { request } from "../protocol.js";
import { applyBattle, dgn, notify, reportError, ENEMY_TYPE_LABELS } from "../state.js";
import { navigate } from "../router.js";
import { visualNode } from "../assets.js";
import { confirmDialog } from "../../dialog.js";
import { loadingNode } from "./prep.js";

const SYNC_VISIBLE_MS = 500;
const SYNC_HIDDEN_MS = 2000;
const TERMINAL_STATUS = ["settled", "abandoned", "error"];

let active = null; // 当前挂载的战斗视图上下文；切视图时置空以停掉循环

function hpBar(key, label) {
  const wrap = document.createElement("div");
  wrap.className = "dgn-hp";
  const head = document.createElement("div");
  head.className = "dgn-hp-head";
  head.append(Object.assign(document.createElement("span"), { textContent: label }));
  const numbers = document.createElement("span");
  numbers.className = "dgn-hp-numbers";
  head.append(numbers);
  const track = document.createElement("div");
  track.className = "dgn-hp-track";
  const fill = document.createElement("div");
  fill.className = "dgn-hp-fill";
  track.append(fill);
  wrap.append(head, track);
  return { node: wrap, set(current, max) {
    const safeMax = Math.max(1, max || 0);
    const ratio = Math.max(0, Math.min(1, current / safeMax));
    fill.style.width = `${ratio * 100}%`;
    fill.classList.toggle("dgn-hp-low", ratio < 0.3);
    numbers.textContent = `${Math.max(0, current)} / ${safeMax}`;
  } };
}

function enemyMeta(battle) {
  const challenge = dgn.snapshot?.catalog?.challenges?.find(
    (row) => row.challenge_id === battle.challenge_id
          && row.difficulty_id === battle.difficulty_id);
  return challenge?.enemy || null;
}

function logLine(log, text, cls = "") {
  const line = document.createElement("div");
  line.className = `dgn-log-line ${cls}`.trim();
  line.textContent = text;
  log.append(line);
  while (log.children.length > 200) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
}

function actorName(source, enemy) {
  if (source === "player:0") return "你";
  if (source === "enemy:0") return enemy?.name || "敌人";
  return source || "—";
}

function describeEvent(event, enemy) {
  switch (event.event_type) {
    case "BattleStarted":
      return { text: `战斗开始，对手：${enemy?.name || event.value}`, cls: "dgn-log-start" };
    case "DamageApplied": {
      const critical = event.is_critical ? "（暴击！）" : "";
      return { text: `${actorName(event.source, enemy)} 对 ${actorName(event.target, enemy)}`
          + ` 造成 ${event.value} 伤害${critical}，剩余 ${event.hp_after}`,
        cls: event.is_critical ? "dgn-log-crit" : "" };
    }
    case "ActorDied":
      return { text: `${actorName(event.target, enemy)} 倒下了`, cls: "dgn-log-death" };
    case "BossPhaseChanged":
      return { text: `${enemy?.name || "首领"} 进入新阶段（${event.phase_id}）`, cls: "dgn-log-phase" };
    case "BattleEnded":
      return { text: `战斗结束：${event.value}`, cls: "dgn-log-end" };
    default:
      return null; // AttackStarted 等过程事件不进日志
  }
}

function setBarFromCheckpoint(bars, battle) {
  bars.player.set(battle.hp["player:0"], bars.playerMax);
  bars.enemy.set(battle.hp["enemy:0"], bars.enemyMax);
}

export function render(container, battleId) {
  const ctx = {
    battleId, destroyed: false, inFlight: false, timer: 0,
    bars: null, log: null, buttons: {}, statusNode: null, phaseNode: null,
    cursor: dgn.battleId === battleId ? dgn.cursor : 0,
  };
  active = ctx;
  container.replaceChildren(loadingNode("正在进入战斗…"));

  mount(ctx, container);
}

async function mount(ctx, container) {
  try {
    const data = await request("dungeon_sync",
      { battle_id: ctx.battleId, after_sequence: ctx.cursor },
      { volatile: true, timeoutMs: 4500 });
    if (ctx.destroyed) return;
    buildUi(ctx, container, data.battle);
    consume(ctx, data);
    scheduleTick(ctx);
  } catch (error) {
    if (ctx.destroyed) return;
    if (error?.code === "not_found") {
      notify("挑战不存在或已结束", "warn");
      navigate("#/prep");
      return;
    }
    if (error?.code === "auth_required") return; // 登录后 main 会重新渲染
    buildUi(ctx, container, dgn.battle);
    scheduleTick(ctx);
  }
}

function buildUi(ctx, container, battle) {
  if (!battle) {
    container.replaceChildren(loadingNode("战斗信息加载失败，稍后自动重试…"));
    return;
  }
  applyBattle(battle);
  const enemy = enemyMeta(battle);
  const stats = dgn.snapshot?.stats?.values;
  ctx.bars = {
    playerMax: stats?.max_hp ?? battle.hp["player:0"],
    enemyMax: enemy?.stats?.max_hp ?? battle.hp["enemy:0"],
  };

  container.replaceChildren();
  const head = document.createElement("div");
  head.className = "dgn-battle-head";
  const title = document.createElement("h2");
  title.textContent = `${enemy?.name || "战斗"}（${enemyMeta(battle) ? ENEMY_TYPE_LABELS[enemy.type] || enemy.type : ""}）`;
  ctx.statusNode = document.createElement("span");
  ctx.statusNode.className = "dgn-tag";
  head.append(title, ctx.statusNode);
  if (enemy) {
    const visualBox = document.createElement("div");
    visualBox.className = "dgn-battle-enemy";
    visualBox.append(visualNode("enemy", enemy.visual_id, { size: 96, enemyType: enemy.type }));
    head.append(visualBox);
  }
  container.append(head);

  ctx.phaseNode = document.createElement("p");
  ctx.phaseNode.className = "dgn-meta dgn-dim";
  container.append(ctx.phaseNode);

  const bars = document.createElement("div");
  bars.className = "dgn-bars";
  ctx.bars.enemy = { ...hpBar("enemy:0", enemy?.name || "敌人") };
  bars.append(ctx.bars.enemy.node);
  ctx.bars.player = { ...hpBar("player:0", "你") };
  bars.append(ctx.bars.player.node);
  container.append(bars);
  ctx.bars.player.set(battle.hp["player:0"], ctx.bars.playerMax);
  ctx.bars.enemy.set(battle.hp["enemy:0"], ctx.bars.enemyMax);

  ctx.log = document.createElement("div");
  ctx.log.className = "dgn-log";
  ctx.log.setAttribute("aria-live", "polite");
  container.append(ctx.log);

  container.append(controlBar(ctx, battle));
  refreshControlState(ctx, battle);
}

function controlBar(ctx, battle) {
  const bar = document.createElement("div");
  bar.className = "dgn-controls";
  const pause = document.createElement("button");
  pause.type = "button";
  pause.className = "dgn-btn";
  pause.addEventListener("click", () => control(ctx, battle?.status === "paused" ? "resume" : "pause"));
  const rate = document.createElement("button");
  rate.type = "button";
  rate.className = "dgn-btn";
  rate.addEventListener("click", () => control(ctx, "set_rate",
    { rate: (battle?.playback_rate ?? 1) === 1 ? 2 : 1 }));
  const back = document.createElement("button");
  back.type = "button";
  back.className = "dgn-btn";
  back.textContent = "返回准备页";
  back.title = "战斗不会中断，可随时回来";
  back.addEventListener("click", () => navigate("#/prep"));
  const abandon = document.createElement("button");
  abandon.type = "button";
  abandon.className = "dgn-btn dgn-btn-danger";
  abandon.textContent = "放弃挑战";
  abandon.addEventListener("click", () => {
    void confirmDialog("放弃当前挑战？没有挑战奖励。", { title: "放弃挑战", tone: "danger" })
      .then((ok) => { if (ok) control(ctx, "abandon"); });
  });
  ctx.buttons = { pause, rate, abandon };
  bar.append(pause, rate, back, abandon);
  return bar;
}

function refreshControlState(ctx, battle) {
  const { pause, rate, abandon } = ctx.buttons;
  if (!pause || !battle) return;
  const running = battle.status === "running";
  const paused = battle.status === "paused";
  pause.textContent = paused ? "继续" : "暂停";
  pause.disabled = !(running || paused);
  rate.textContent = `倍速 x${battle.playback_rate === 2 ? 2 : 1}`;
  rate.disabled = !(running || paused);
  abandon.disabled = !(running || paused);
  const statusText = { running: "进行中", paused: "已暂停", settled: "已结算",
    abandoned: "已放弃", error: "异常" }[battle.status] || battle.status;
  ctx.statusNode.textContent = statusText;
  const enemy = enemyMeta(battle);
  const phases = enemy?.phases || [];
  if (phases.length && battle.phase_index !== null && battle.phase_index !== undefined) {
    ctx.phaseNode.textContent = `首领阶段 ${battle.phase_index + 1}/${phases.length}`
      + `（当前：${phases[battle.phase_index]?.phase_id || "—"}）`;
  } else {
    ctx.phaseNode.textContent = "";
  }
}

async function control(ctx, command, extra = {}, retried = false) {
  const battle = dgn.battle;
  if (!battle) return;
  for (const button of Object.values(ctx.buttons)) button.disabled = true;
  try {
    const data = await request("dungeon_control", {
      battle_id: ctx.battleId,
      command,
      expected_revision: battle.revision,
      ...extra,
    });
    if (ctx.destroyed) return;
    if (data.result?.battle) {
      applyBattle(data.result.battle);
      refreshControlState(ctx, data.result.battle);
      if (command === "abandon") navigate(`#/result/${ctx.battleId}`);
    }
  } catch (error) {
    // 同步轮询会不断推进 revision，控制命令撞上 state_conflict 属预期：
    // 拉一次权威状态，用最新 revision 重试同一指令一次。
    if (!ctx.destroyed && error?.code === "state_conflict" && !retried) {
      const fresh = await request("dungeon_sync",
        { battle_id: ctx.battleId, after_sequence: ctx.cursor }, { volatile: true });
      if (!ctx.destroyed && fresh.battle
          && !["settled", "abandoned", "error"].includes(fresh.battle.status)) {
        applyBattle(fresh.battle);
        return control(ctx, command, extra, true);
      }
    }
    if (!ctx.destroyed) reportError(error);
  } finally {
    if (!ctx.destroyed) refreshControlState(ctx, dgn.battle);
  }
}

function scheduleTick(ctx) {
  if (ctx.destroyed) return;
  const hidden = document.visibilityState === "hidden";
  ctx.timer = window.setTimeout(() => void tick(ctx), hidden ? SYNC_HIDDEN_MS : SYNC_VISIBLE_MS);
}

async function tick(ctx) {
  if (ctx.destroyed || ctx.inFlight) return;
  ctx.inFlight = true;
  try {
    const data = await request("dungeon_sync",
      { battle_id: ctx.battleId, after_sequence: ctx.cursor },
      { volatile: true, timeoutMs: 4500 });
    if (ctx.destroyed) return;
    consume(ctx, data);
  } catch {
    // 轮询失败忽略，下个周期再试；连接断开时 protocol 层会提示
  } finally {
    ctx.inFlight = false;
    scheduleTick(ctx);
  }
}

function consume(ctx, data) {
  const battle = data.battle;
  applyBattle(battle);
  // 先以权威检查点恢复血条，再补文本日志（不重放历史伤害动画）。
  if (ctx.bars) setBarFromCheckpoint(ctx.bars, battle);
  for (const event of data.events || []) {
    ctx.cursor = Math.max(ctx.cursor, event.sequence_id);
    dgn.cursor = ctx.cursor;
    if (event.event_type === "BattleStarted" && ctx.bars) {
      ctx.bars.playerMax = event.player_hp || ctx.bars.playerMax;
      ctx.bars.enemyMax = event.enemy_hp || ctx.bars.enemyMax;
      setBarFromCheckpoint(ctx.bars, battle);
    }
    if (!ctx.log) continue;
    const described = describeEvent(event, enemyMeta(battle));
    if (described) logLine(ctx.log, described.text, described.cls);
  }
  refreshControlState(ctx, battle);
  if (TERMINAL_STATUS.includes(battle.status)) {
    ctx.destroyed = true;
    clearTimeout(ctx.timer);
    navigate(`#/result/${battle.battle_id}`);
  }
}

/** 离开战斗视图：只停本地定时器，不发 abandon。 */
export function destroy() {
  if (active) {
    active.destroyed = true;
    clearTimeout(active.timer);
    active = null;
  }
}
