/**
 * 地下城 Beta 原型 · 波次战斗画布。
 * 本地模拟：玩家移动 + 武器自动攻击 + 敌人追击 + 材料拾取。
 * 正式版中模拟逻辑属于 B 线 Simulator（create/step/snapshot/restore），输入经服务器裁决。
 */
import { ENEMY_TYPES, WEAPONS, ITEMS, XP_FOR_LEVEL, waveConfig } from "./dgn-beta-data.js";
import { applyDamage, grantXp, statsOf } from "./dgn-beta-state.js";

const WIDTH = 960;
const HEIGHT = 600;

/** 可复现的伪随机（正式版使用 SimulatorServices.random_int 命名流） */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function dist(ax, ay, bx, by) {
  return Math.hypot(ax - bx, ay - by);
}

export class Arena {
  constructor(canvas, { onHud, onLevelUp, onWaveEnd, onDeath }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.hooks = { onHud, onLevelUp, onWaveEnd, onDeath };
    this.dataDeps = { WEAPONS, ITEMS };
    this.running = false;
    this.paused = false;
    this.slimeSprites = null;
    this.keys = new Set();
    this.touch = null;
    this.onFrame = this.onFrame.bind(this);
    this.bindInput();
    this.loadSprites();
  }

  loadSprites() {
    const sheet = new Image();
    sheet.src = "assets/dungeon/enemies/ruins_slime/idle.png";
    this.slimeSprites = { sheet, frames: 6, frameWidth: 362, frameHeight: 724 };
  }

  bindInput() {
    window.addEventListener("keydown", (event) => {
      this.keys.add(event.key.toLowerCase());
      if (["arrowup", "arrowdown", "arrowleft", "arrowright", " "].includes(event.key.toLowerCase())) {
        event.preventDefault();
      }
    });
    window.addEventListener("keyup", (event) => this.keys.delete(event.key.toLowerCase()));
    // 左半屏拖动 = 虚拟摇杆（相对按下点位移）
    this.canvas.addEventListener("touchstart", (event) => {
      const point = event.touches[0];
      this.touch = { x: point.clientX, y: point.clientY };
      event.preventDefault();
    }, { passive: false });
    this.canvas.addEventListener("touchmove", (event) => {
      if (!this.touch) return;
      const point = event.touches[0];
      this.touch.dx = point.clientX - this.touch.x;
      this.touch.dy = point.clientY - this.touch.y;
      event.preventDefault();
    }, { passive: false });
    this.canvas.addEventListener("touchend", () => { this.touch = null; });
  }

  start(run, wave) {
    this.state = run;
    this.wave = wave;
    this.config = waveConfig(wave);
    this.rand = mulberry32(0xd1ce + wave * 977);
    this.player = { x: WIDTH / 2, y: HEIGHT / 2, radius: 15, attackTimers: {}, swing: null };
    this.enemies = [];
    this.projectiles = [];
    this.pickups = [];
    this.floatTexts = [];
    this.timeLeft = this.config.duration;
    this.spawnTimer = 0.6;
    this.elapsed = 0;
    this.materialsEarned = 0;
    this.paused = false;
    if (!this.running) {
      this.running = true;
      this.lastTime = performance.now();
      requestAnimationFrame(this.onFrame);
    }
    this.pushHud();
  }

  pause() { this.paused = true; }
  resume() { this.paused = false; this.lastTime = performance.now(); }
  stop() { this.running = false; }

  pushHud() {
    const stats = statsOf(this.state);
    this.hooks.onHud?.({
      hp: this.state.hp,
      maxHp: stats.maxHp,
      level: this.state.level,
      xp: this.state.xp,
      materials: this.state.materials,
      wave: this.wave,
      timeLeft: Math.ceil(this.timeLeft),
      xpNeeded: XP_FOR_LEVEL(this.state.level),
    });
  }

  onFrame(now) {
    if (!this.running) return;
    const dt = Math.min(0.05, (now - this.lastTime) / 1000);
    this.lastTime = now;
    if (!this.paused) {
      this.update(dt);
    }
    this.render();
    requestAnimationFrame(this.onFrame);
  }

  update(dt) {
    this.elapsed += dt;
    this.timeLeft -= dt;
    if (this.timeLeft <= 0) {
      this.timeLeft = 0;
      this.endWave(true);
      return;
    }
    this.spawnTimer -= dt;
    if (this.spawnTimer <= 0) {
      this.spawnTimer = this.config.spawnInterval;
      this.spawnGroup();
    }
    this.updatePlayer(dt);
    this.updateWeapons(dt);
    this.updateEnemies(dt);
    this.updateProjectiles(dt);
    this.updatePickups(dt);
    for (const text of this.floatTexts) {
      text.life -= dt;
      text.y -= 26 * dt;
    }
    this.floatTexts = this.floatTexts.filter((text) => text.life > 0);
    if (this.state.hp <= 0) {
      this.hooks.onDeath?.();
      this.running = false;
      return;
    }
    this.pushHud();
  }

  spawnGroup() {
    let total = 0;
    for (const entry of this.config.pool) total += entry.weight;
    let roll = this.rand() * total;
    let chosen = this.config.pool[0];
    for (const entry of this.config.pool) {
      roll -= entry.weight;
      if (roll <= 0) { chosen = entry; break; }
    }
    for (let i = 0; i < chosen.count; i++) {
      this.spawnEnemy(chosen.type, chosen.hpScale, chosen.dmgScale);
    }
    if (this.config.boss && !this.bossSpawned && this.elapsed > this.config.duration / 2) {
      this.bossSpawned = true;
      const boss = this.spawnEnemy("brute", 2.2, 1.3);
      boss.isBoss = true;
      boss.radius *= 1.5;
    }
  }

  spawnEnemy(typeName, hpScale, dmgScale) {
    const type = ENEMY_TYPES[typeName];
    const angle = this.rand() * Math.PI * 2;
    const distance = 60 + this.rand() * 120;
    const enemy = {
      type: typeName,
      x: Math.min(WIDTH - 20, Math.max(20, WIDTH / 2 + Math.cos(angle) * (WIDTH / 2 + distance))),
      y: Math.min(HEIGHT - 20, Math.max(20, HEIGHT / 2 + Math.sin(angle) * (HEIGHT / 2 + distance))),
      radius: type.radius,
      hp: type.hp * hpScale,
      maxHp: type.hp * hpScale,
      speed: type.speed,
      damage: type.damage * dmgScale,
      materials: type.materials,
      color: type.color,
      shape: type.shape || "circle",
      attackCd: 0,
      hitFlash: 0,
      animStart: this.elapsed,
    };
    // 从画布边缘外生成
    const edge = Math.floor(this.rand() * 4);
    const offset = 30;
    if (edge === 0) enemy.y = -offset;
    if (edge === 1) enemy.y = HEIGHT + offset;
    if (edge === 2) enemy.x = -offset;
    if (edge === 3) enemy.x = WIDTH + offset;
    this.enemies.push(enemy);
    return enemy;
  }

  updatePlayer(dt) {
    const stats = statsOf(this.state, this.dataDeps);
    const keys = this.keys;
    let dx = 0;
    let dy = 0;
    if (keys.has("w") || keys.has("arrowup")) dy -= 1;
    if (keys.has("s") || keys.has("arrowdown")) dy += 1;
    if (keys.has("a") || keys.has("arrowleft")) dx -= 1;
    if (keys.has("d") || keys.has("arrowright")) dx += 1;
    if (this.touch && typeof this.touch.dx === "number") {
      const length = Math.hypot(this.touch.dx, this.touch.dy);
      if (length > 6) {
        dx = this.touch.dx / length;
        dy = this.touch.dy / length;
      }
    }
    const length = Math.hypot(dx, dy);
    if (length > 0) {
      const speed = 170 * stats.speed;
      this.player.x += (dx / length) * speed * dt;
      this.player.y += (dy / length) * speed * dt;
    }
    this.player.x = Math.max(16, Math.min(WIDTH - 16, this.player.x));
    this.player.y = Math.max(16, Math.min(HEIGHT - 16, this.player.y));
    // 生命再生
    if (stats.regen > 0 && this.state.hp > 0) {
      this.state.hp = Math.min(stats.maxHp, this.state.hp + stats.regen * dt);
    }
  }

  updateWeapons(dt) {
    const stats = statsOf(this.state, this.dataDeps);
    for (const weaponId of this.state.weapons) {
      const weapon = this.weaponById(weaponId);
      if (!weapon) continue;
      const timer = (this.player.attackTimers[weaponId] ?? 0.4) - dt * stats.attackSpeed;
      if (timer <= 0) {
        const target = this.nearestEnemy(this.player.x, this.player.y, weapon.range);
        if (target) {
          this.player.attackTimers[weaponId] = weapon.cooldown;
          if (weapon.kind === "melee") {
            this.meleeAttack(weapon, target, stats);
          } else {
            this.rangedAttack(weapon, target, stats);
          }
        } else {
          this.player.attackTimers[weaponId] = 0.08;
        }
      } else {
        this.player.attackTimers[weaponId] = timer;
      }
    }
    if (this.player.swing) {
      this.player.swing.life -= dt;
      if (this.player.swing.life <= 0) this.player.swing = null;
    }
  }

  weaponById(weaponId) {
    return (this.dataDeps?.WEAPONS || []).find((entry) => entry.id === weaponId);
  }

  nearestEnemy(x, y, range) {
    let best = null;
    let bestDist = range;
    for (const enemy of this.enemies) {
      const d = dist(x, y, enemy.x, enemy.y);
      if (d < bestDist) { best = enemy; bestDist = d; }
    }
    return best;
  }

  weaponDamage(weapon, stats) {
    const scale = weapon.kind === "melee" ? stats.meleeDmg : stats.rangedDmg;
    return weapon.damage * scale;
  }

  meleeAttack(weapon, target, stats) {
    const angle = Math.atan2(target.y - this.player.y, target.x - this.player.x);
    this.player.swing = { angle, range: weapon.range, arc: weapon.arc, life: 0.12 };
    for (const enemy of [...this.enemies]) {
      const toEnemy = Math.atan2(enemy.y - this.player.y, enemy.x - this.player.x);
      let diff = Math.abs(((toEnemy - angle + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
      if (diff <= weapon.arc / 2 && dist(this.player.x, this.player.y, enemy.x, enemy.y) <= weapon.range + enemy.radius) {
        this.hitEnemy(enemy, this.weaponDamage(weapon, stats), weapon.knockback);
      }
    }
  }

  rangedAttack(weapon, target, stats) {
    const angle = Math.atan2(target.y - this.player.y, target.x - this.player.x);
    this.projectiles.push({
      x: this.player.x,
      y: this.player.y,
      vx: Math.cos(angle) * weapon.projectileSpeed,
      vy: Math.sin(angle) * weapon.projectileSpeed,
      damage: this.weaponDamage(weapon, stats),
      pierce: weapon.pierce || 0,
      blast: weapon.blast || 0,
      life: weapon.range / weapon.projectileSpeed,
      emoji: weapon.emoji,
    });
  }
  }

  updateProjectiles(dt) {
    for (const proj of this.projectiles) {
      proj.x += proj.vx * dt;
      proj.y += proj.vy * dt;
      proj.life -= dt;
      for (const enemy of [...this.enemies]) {
        if (proj.hit?.has(enemy)) continue;
        if (dist(proj.x, proj.y, enemy.x, enemy.y) <= enemy.radius + 5) {
          if (proj.blast) {
            for (const near of [...this.enemies]) {
              if (dist(proj.x, proj.y, near.x, near.y) <= proj.blast + near.radius) {
                this.hitEnemy(near, proj.damage, 40);
              }
            }
            this.floatTexts.push({ x: proj.x, y: proj.y - 10, text: "💥", life: 0.3, size: 16 });
            proj.life = 0;
            break;
          }
          this.hitEnemy(enemy, proj.damage, 30);
          proj.hit = proj.hit || new Set();
          proj.hit.add(enemy);
          if (proj.pierce <= 0) {
            proj.life = 0;
            break;
          }
          proj.pierce -= 1;
        }
      }
    }
    this.projectiles = this.projectiles.filter((proj) => proj.life > 0 && proj.x > -30 && proj.x < WIDTH + 30 && proj.y > -30 && proj.y < HEIGHT + 30);
  }

  hitEnemy(enemy, damage, knockback) {
    enemy.hp -= damage;
    enemy.hitFlash = 0.1;
    const angle = Math.atan2(enemy.y - this.player.y, enemy.x - this.player.x);
    enemy.x += Math.cos(angle) * knockback * 0.06;
    enemy.y += Math.sin(angle) * knockback * 0.06;
    this.floatTexts.push({
      x: enemy.x + (this.rand() - 0.5) * 12,
      y: enemy.y - enemy.radius - 4,
      text: `${Math.round(damage)}`,
      life: 0.55,
      size: 13,
    });
    if (enemy.hp <= 0) this.killEnemy(enemy);
  }

  killEnemy(enemy) {
    const index = this.enemies.indexOf(enemy);
    if (index >= 0) this.enemies.splice(index, 1);
    for (let i = 0; i < enemy.materials; i++) {
      this.pickups.push({
        x: enemy.x + (this.rand() - 0.5) * 24,
        y: enemy.y + (this.rand() - 0.5) * 24,
        magnet: false,
      });
    }
    const levels = grantXp(this.state, enemy.isBoss ? 6 : 1);
    if (levels.length > 0) {
      this.paused = true;
      this.hooks.onLevelUp?.(levels);
    }
  }

  updateEnemies(dt) {
    const stats = statsOf(this.state, this.dataDeps);
    for (const enemy of this.enemies) {
      const angle = Math.atan2(this.player.y - enemy.y, this.player.x - enemy.x);
      enemy.x += Math.cos(angle) * enemy.speed * dt;
      enemy.y += Math.sin(angle) * enemy.speed * dt;
      if (enemy.hitFlash > 0) enemy.hitFlash -= dt;
      enemy.attackCd -= dt;
      if (dist(enemy.x, enemy.y, this.player.x, this.player.y) <= enemy.radius + this.player.radius) {
        if (enemy.attackCd <= 0) {
          enemy.attackCd = 0.8;
          const final = applyDamage(this.state, enemy.damage, stats);
          this.floatTexts.push({
            x: this.player.x,
            y: this.player.y - 22,
            text: `-${final}`,
            life: 0.6,
            size: 14,
            danger: true,
          });
        }
      }
    }
  }

  updatePickups(dt) {
    const magnetRadius = 60;
    const collectRadius = 18;
    for (const pickup of this.pickups) {
      const d = dist(pickup.x, pickup.y, this.player.x, this.player.y);
      if (d <= magnetRadius || pickup.magnet) {
        pickup.magnet = true;
        const angle = Math.atan2(this.player.y - pickup.y, this.player.x - pickup.x);
        const speed = 320;
        pickup.x += Math.cos(angle) * speed * dt;
        pickup.y += Math.sin(angle) * speed * dt;
      }
      if (d <= collectRadius) {
        pickup.collected = true;
        this.state.materials += 1;
        this.state.totalMaterialsEarned += 1;
        this.materialsEarned += 1;
      }
    }
    this.pickups = this.pickups.filter((pickup) => !pickup.collected);
  }

  endWave(victory) {
    this.running = false;
    this.hooks.onWaveEnd?.({ victory, materialsEarned: this.materialsEarned });
  }

  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    // 地面网格
    ctx.strokeStyle = "#241c14";
    ctx.lineWidth = 1;
    for (let x = 0; x <= WIDTH; x += 48) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, HEIGHT);
      ctx.stroke();
    }
    for (let y = 0; y <= HEIGHT; y += 48) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(WIDTH, y);
      ctx.stroke();
    }
    this.renderPickups(ctx);
    this.renderEnemies(ctx);
    this.renderPlayer(ctx);
    this.renderProjectiles(ctx);
    this.renderFloatTexts(ctx);
  }

  renderPlayer(ctx) {
    const { x, y } = this.player;
    // 土豆占位形象
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = "#c89858";
    ctx.strokeStyle = "#7a5a30";
    ctx.beginPath();
    ctx.ellipse(0, 0, 15, 18, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#2c2013";
    ctx.beginPath();
    ctx.arc(-5, -4, 2, 0, Math.PI * 2);
    ctx.arc(5, -4, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#2c2013";
    ctx.beginPath();
    ctx.arc(0, 2, 5, 0.2, Math.PI - 0.2);
    ctx.stroke();
    ctx.restore();
    if (this.player.swing) {
      const swing = this.player.swing;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(swing.angle);
      ctx.fillStyle = "rgba(240, 230, 216, .3)";
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, swing.range, -swing.arc / 2, swing.arc / 2);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
    // 血条
    const stats = statsOf(this.state, this.dataDeps);
    const ratio = Math.max(0, this.state.hp / stats.maxHp);
    ctx.fillStyle = "#12100e";
    ctx.fillRect(x - 18, y - 30, 36, 5);
    ctx.fillStyle = ratio > 0.35 ? "#58c25c" : "#d9534f";
    ctx.fillRect(x - 17, y - 29, 34 * ratio, 3);
  }

  renderEnemies(ctx) {
    for (const enemy of this.enemies) {
      const isSlime = enemy.type === "slime" && this.slimeSprites?.sheet?.complete && this.slimeSprites.sheet.naturalWidth > 0;
      ctx.save();
      ctx.translate(enemy.x, enemy.y);
      if (isSlime) {
        const sheet = this.slimeSprites;
        const frame = Math.floor((this.elapsed - enemy.animStart) * 7) % sheet.frames;
        const scale = (enemy.radius * 2.4) / sheet.frameHeight;
        const w = sheet.frameWidth * scale;
        const h = sheet.frameHeight * scale;
        if (enemy.hitFlash > 0) ctx.filter = "brightness(1.8)";
        ctx.drawImage(sheet.sheet, frame * sheet.frameWidth, 0, sheet.frameWidth, sheet.frameHeight, -w / 2, -h / 2, w, h);
        ctx.filter = "none";
      } else if (enemy.shape === "triangle") {
        ctx.rotate(Math.atan2(this.player.y - enemy.y, this.player.x - enemy.x));
        ctx.fillStyle = enemy.hitFlash > 0 ? "#fff" : enemy.color;
        ctx.beginPath();
        ctx.moveTo(enemy.radius, 0);
        ctx.lineTo(-enemy.radius, enemy.radius * 0.8);
        ctx.lineTo(-enemy.radius, -enemy.radius * 0.8);
        ctx.closePath();
        ctx.fill();
      } else {
        ctx.fillStyle = enemy.hitFlash > 0 ? "#fff" : enemy.color;
        if (enemy.shape === "square") {
          ctx.fillRect(-enemy.radius, -enemy.radius, enemy.radius * 2, enemy.radius * 2);
        } else {
          ctx.beginPath();
          ctx.arc(0, 0, enemy.radius, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
      if (enemy.hp < enemy.maxHp) {
        ctx.fillStyle = "#12100e";
        ctx.fillRect(enemy.x - 16, enemy.y - enemy.radius - 10, 32, 4);
        ctx.fillStyle = "#d9534f";
        ctx.fillRect(enemy.x - 15, enemy.y - enemy.radius - 9, 30 * Math.max(0, enemy.hp / enemy.maxHp), 2);
      }
    }
  }

  renderProjectiles(ctx) {
    for (const proj of this.projectiles) {
      ctx.save();
      ctx.translate(proj.x, proj.y);
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(proj.emoji || "•", 0, 0);
      ctx.restore();
    }
  }

  renderPickups(ctx) {
    ctx.fillStyle = "#4fc46f";
    for (const pickup of this.pickups) {
      ctx.save();
      ctx.translate(pickup.x, pickup.y);
      ctx.rotate(Math.PI / 4);
      ctx.fillRect(-4, -4, 8, 8);
      ctx.restore();
    }
  }

  renderFloatTexts(ctx) {
    ctx.textAlign = "center";
    for (const text of this.floatTexts) {
      ctx.globalAlpha = Math.min(1, text.life * 2.5);
      ctx.font = `bold ${text.size || 13}px sans-serif`;
      ctx.fillStyle = text.danger ? "#ff8a80" : "#ffe9b8";
      ctx.fillText(text.text, text.x, text.y);
    }
    ctx.globalAlpha = 1;
  }
}
