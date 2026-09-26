"use strict";


const WIDTH = 320;
const HEIGHT = 420;
const GROUND = 388;
const BIRD_X = 72;
const BIRD_SIZE = 16;
const PIPE_WIDTH = 46;
const PIPE_GAP = 104;
const PIPE_SPEED = 135;
const PIPE_SPACING = 168;

const DEFAULT_API = {
  start: async () => (await import("./protocol.js")).estateRequest(
    "estate_flappy_start", {}, { timeoutMs: 15000 }),
  finish: async ({ session_id, flaps, frames }) => (await import("./protocol.js")).estateRequest(
    "estate_flappy_finish", { session_id, flaps, frames }, { timeoutMs: 15000 }),
  leaderboard: async () => (await import("./protocol.js")).estateRequest(
    "estate_flappy_leaderboard", {}, { timeoutMs: 12000 }),
};

export function renderFlappyGame(target, api = DEFAULT_API) {
  const game = document.createElement("section");
  game.className = "estate-flappy";
  const note = document.createElement("p");
  note.className = "estate-sheet-note";
  note.textContent = api === DEFAULT_API
    ? "每局花费 66 金币，结束后按得分 ×10 发放金币；刷新全服历史最高分另奖 6666 金币。点击画面、按空格或 ↑ 飞行。中途退出不退还入场费。"
    : "本地试玩模式，不扣除或发放真实金币。点击画面、按空格或 ↑ 飞行。";
  const canvas = document.createElement("canvas");
  canvas.className = "estate-flappy-canvas";
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "飞鸟游戏画面");
  const status = document.createElement("p");
  status.className = "estate-flappy-status";
  status.setAttribute("role", "status");
  const action = document.createElement("button");
  action.type = "button";
  action.className = "estate-button estate-button-gold";
  const leaderboardButton = document.createElement("button");
  leaderboardButton.type = "button";
  leaderboardButton.className = "estate-button";
  leaderboardButton.textContent = "查看最高分排行榜";
  const leaderboard = document.createElement("div");
  leaderboard.className = "estate-flappy-leaderboard";
  leaderboard.hidden = true;
  game.append(note, canvas, status, action, leaderboardButton, leaderboard);
  target.append(game);

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    status.textContent = "浏览器暂不支持画布小游戏。";
    action.hidden = true;
    return () => {};
  }

  let phase = "ready";
  let birdY = 176;
  let velocity = 0;
  let score = 0;
  let pipes = [];
  let frame = 0;
  let lastTime = 0;
  let disposed = false;
  let seed = 0;
  let sessionId = null;
  let flaps = [];
  let frames = 0;
  let accumulator = 0;

  function reset() {
    phase = "ready";
    birdY = 176;
    velocity = 0;
    score = 0;
    pipes = [];
    sessionId = null;
    flaps = [];
    frames = 0;
    accumulator = 0;
    status.textContent = api === DEFAULT_API ? "准备飞行 · 每局 66 金币" : "准备飞行 · 本地试玩";
    action.textContent = api === DEFAULT_API ? "支付 66 金币并开始" : "开始试玩";
    action.disabled = false;
    draw();
  }

  async function startGame() {
    phase = "starting";
    action.disabled = true;
    status.textContent = api === DEFAULT_API ? "正在支付入场费…" : "正在开始…";
    try {
      const result = await api.start();
      if (disposed) return;
      sessionId = result.session_id;
      seed = result.seed >>> 0;
      const gapY = nextGap();
      pipes = [{ x: WIDTH + 38, gapY, scored: false }];
      flaps = [0];
      frames = 0;
      accumulator = 0;
      birdY = 176;
      velocity = 0;
      score = 0;
      phase = "playing";
      status.textContent = "飞行中 · 得分 0";
      action.textContent = "拍动翅膀";
      action.disabled = false;
    } catch (error) {
      if (disposed) return;
      phase = "ready";
      status.textContent = error.message || "开始游戏失败";
      action.disabled = false;
    }
  }

  function nextGap() {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return 115 + seed % 146;
  }

  function endGame() {
    phase = "settling";
    status.textContent = `游戏结束 · 正在核算 ${score} 分…`;
    action.disabled = true;
    void settleGame();
  }

  async function settleGame() {
    try {
      const result = await api.finish({ session_id: sessionId, flaps, frames, preview_score: score });
      if (disposed) return;
      sessionId = null;
      phase = "ended";
      status.textContent = `游戏结束 · ${result.score} 分 · 获得 ${result.reward} 金币${result.global_record ? "（刷新全服纪录奖励 6666）" : ""}`;
      action.textContent = api === DEFAULT_API ? "再玩一次 · 66 金币" : "再玩一次";
      action.disabled = false;
      if (!leaderboard.hidden) void loadLeaderboard();
    } catch (error) {
      if (disposed) return;
      phase = "ended";
      status.textContent = error.message || "结算失败，请重试";
      action.textContent = "重试结算";
      action.disabled = false;
    }
  }

  function flap() {
    if (disposed || phase === "starting" || phase === "settling") return;
    if (phase === "ready" || (phase === "ended" && !sessionId)) {
      void startGame();
      return;
    }
    if (phase === "ended") {
      phase = "settling";
      action.disabled = true;
      void settleGame();
      return;
    }
    if (flaps[flaps.length - 1] !== frames) flaps.push(frames);
  }

  function update() {
    if (flaps[flaps.length - 1] === frames) velocity = -285;
    frames += 1;
    velocity += 920 / 60;
    birdY += velocity / 60;
    if (birdY < 0 || birdY + BIRD_SIZE >= GROUND) {
      birdY = Math.max(0, Math.min(birdY, GROUND - BIRD_SIZE));
      endGame();
      return;
    }
    for (const pipe of pipes) {
      pipe.x -= (PIPE_SPEED + Math.min(score, 10) * 4) / 60;
      if (!pipe.scored && pipe.x + PIPE_WIDTH < BIRD_X) {
        pipe.scored = true;
        score += 1;
        status.textContent = `飞行中 · 得分 ${score}`;
      }
      if (BIRD_X + BIRD_SIZE > pipe.x && BIRD_X < pipe.x + PIPE_WIDTH
          && (birdY < pipe.gapY - PIPE_GAP / 2
            || birdY + BIRD_SIZE > pipe.gapY + PIPE_GAP / 2)) {
        endGame();
        return;
      }
    }
    pipes = pipes.filter((pipe) => pipe.x + PIPE_WIDTH > 0);
    if (pipes.length && pipes[pipes.length - 1].x < WIDTH - PIPE_SPACING) {
      pipes.push({ x: WIDTH, gapY: nextGap(), scored: false });
    }
  }

  function rectangle(x, y, width, height, color) {
    ctx.fillStyle = color;
    ctx.fillRect(Math.round(x), Math.round(y), Math.round(width), Math.round(height));
  }

  function drawPipe(x, top, bottom) {
    rectangle(x, 0, PIPE_WIDTH, top, "#497747");
    rectangle(x + 5, 0, 8, top, "#81ae62");
    rectangle(x - 3, top - 13, PIPE_WIDTH + 6, 13, "#365f3b");
    rectangle(x, bottom, PIPE_WIDTH, GROUND - bottom, "#497747");
    rectangle(x + 5, bottom, 8, GROUND - bottom, "#81ae62");
    rectangle(x - 3, bottom, PIPE_WIDTH + 6, 13, "#365f3b");
  }

  function draw() {
    rectangle(0, 0, WIDTH, HEIGHT, "#a7d8d8");
    rectangle(22, 63, 49, 12, "#f8f1d8");
    rectangle(37, 55, 25, 10, "#f8f1d8");
    rectangle(222, 97, 62, 10, "#f8f1d8");
    rectangle(241, 88, 30, 10, "#f8f1d8");
    rectangle(0, GROUND - 27, WIDTH, 27, "#8cb980");
    for (const pipe of pipes) {
      drawPipe(pipe.x, pipe.gapY - PIPE_GAP / 2, pipe.gapY + PIPE_GAP / 2);
    }
    rectangle(0, GROUND, WIDTH, HEIGHT - GROUND, "#b88c59");
    rectangle(0, GROUND, WIDTH, 6, "#668b4d");
    rectangle(BIRD_X, birdY, BIRD_SIZE, BIRD_SIZE, "#e9b84b");
    rectangle(BIRD_X + 3, birdY + 9, 8, 5, "#f7d379");
    rectangle(BIRD_X + 10, birdY + 3, 3, 3, "#33312c");
    rectangle(BIRD_X + BIRD_SIZE, birdY + 7, 5, 4, "#ce7941");
    ctx.fillStyle = "#493c2d";
    ctx.font = "bold 18px monospace";
    ctx.fillText(String(score), 14, 28);
  }

  function tick(time) {
    if (disposed) return;
    const elapsed = lastTime ? Math.min(time - lastTime, 100) : 0;
    lastTime = time;
    if (phase === "playing") {
      accumulator += elapsed;
      while (accumulator >= 1000 / 60 && phase === "playing") {
        update();
        accumulator -= 1000 / 60;
      }
    }
    draw();
    frame = window.requestAnimationFrame(tick);
  }

  function onKey(event) {
    if (event.repeat || !["Space", "ArrowUp"].includes(event.code)) return;
    if (event.target instanceof HTMLElement
        && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    event.preventDefault();
    flap();
  }

  async function loadLeaderboard() {
    leaderboard.textContent = "正在读取排行榜…";
    try {
      const result = await api.leaderboard();
      if (disposed || leaderboard.hidden) return;
      leaderboard.replaceChildren();
      const own = document.createElement("p");
      own.textContent = result.my_rank
        ? `我的最高分：${result.my_best} · 第 ${result.my_rank} 名`
        : "我还没有已结算的成绩";
      leaderboard.append(own);
      for (const entry of result.entries) {
        const row = document.createElement("p");
        row.textContent = `第 ${entry.rank} 名 · ${entry.username} · ${entry.score} 分`;
        leaderboard.append(row);
      }
      if (!result.entries.length) {
        const empty = document.createElement("p");
        empty.textContent = "暂无成绩。";
        leaderboard.append(empty);
      }
    } catch (error) {
      if (!disposed) leaderboard.textContent = error.message || "读取排行榜失败";
    }
  }

  canvas.addEventListener("pointerdown", flap);
  action.addEventListener("click", flap);
  leaderboardButton.addEventListener("click", () => {
    leaderboard.hidden = !leaderboard.hidden;
    leaderboardButton.textContent = leaderboard.hidden ? "查看最高分排行榜" : "收起排行榜";
    if (!leaderboard.hidden) void loadLeaderboard();
  });
  window.addEventListener("keydown", onKey);
  reset();
  frame = window.requestAnimationFrame(tick);
  return () => {
    disposed = true;
    window.cancelAnimationFrame(frame);
    window.removeEventListener("keydown", onKey);
  };
}
