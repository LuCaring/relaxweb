"""可复现的战斗快照、时间轴和结算结果。"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Optional

from estate.dungeon.catalog import CATALOG, validate_catalog
from estate.dungeon.effects import STAT_FIELDS, validate_base_stats

PLAYER_ID = "player:0"
ENEMY_ID = "enemy:0"
MAX_SAFE_DAMAGE = 2**53 - 1


class CombatError(ValueError):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class BattleSnapshot:
    snapshot_json: str
    snapshot_hash: str

    def as_dict(self):
        return json.loads(self.snapshot_json)


@dataclass(frozen=True)
class CombatStep:
    checkpoint: dict
    events: list
    result: Optional[dict]


def make_battle_snapshot(challenge_id, difficulty_id, player_stats, equipment,
                         effect_sources, seed, catalog=CATALOG, battle_id=None):
    """冻结一次遭遇的内容和属性；调用者单独保存并保护种子。"""
    validate_catalog(catalog)
    validate_base_stats(player_stats)
    if not isinstance(seed, bytes) or len(seed) != 32:
        raise ValueError("战斗种子必须为 32 字节")
    if not isinstance(equipment, list) or not isinstance(effect_sources, list):
        raise ValueError("装备和效果来源必须为列表")
    challenge = next((row for row in catalog["challenges"]
                      if row["challenge_id"] == challenge_id
                      and row["difficulty_id"] == difficulty_id), None)
    if challenge is None:
        raise ValueError("挑战不存在")
    enemy = next(row for row in catalog["enemies"]
                 if row["enemy_id"] == challenge["enemy_id"])
    reward = next(row for row in catalog["reward_tables"]
                  if row["reward_table_id"] == challenge["reward_table_id"])
    templates = {row["template_id"]: row for row in catalog["items"]}
    reward_table = {"reward_table_id": reward["reward_table_id"],
                    "coins": reward["coins"], "rolls": reward["rolls"],
                    "entries": [{"weight": entry["weight"],
                                 "item": templates[entry["template_id"]]}
                                for entry in reward["entries"]]}
    unlock_rules = [{"challenge_id": row["challenge_id"],
                     "difficulty_id": row["difficulty_id"],
                     "requires": row["requires"]} for row in catalog["challenges"]]
    # Canonical JSON creates a value snapshot. Mutating later item/catalog objects
    # cannot affect a live run or its hash.
    payload = {"battle_id": battle_id, "config_version": catalog["config_version"],
               "simulation_version": catalog["simulation_version"],
               "rng_version": catalog["rng_version"], "seed_hex": seed.hex(),
               "challenge_id": challenge_id, "difficulty_id": difficulty_id,
               "player_stats": player_stats, "equipment": equipment,
               "effect_sources": effect_sources, "enemy": enemy,
               "combat": catalog["combat"], "reward_table": reward_table,
               "unlock_rules": unlock_rules}
    if set(player_stats) != set(STAT_FIELDS):
        raise ValueError("玩家属性字段不完整")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return BattleSnapshot(raw, hashlib.sha256(raw.encode("utf-8")).hexdigest())


def _payload(snapshot):
    if not isinstance(snapshot, BattleSnapshot):
        raise CombatError("invalid_snapshot", "战斗快照类型无效")
    digest = hashlib.sha256(snapshot.snapshot_json.encode("utf-8")).hexdigest()
    if digest != snapshot.snapshot_hash:
        raise CombatError("invalid_snapshot", "战斗快照摘要不匹配")
    data = snapshot.as_dict()
    if data.get("simulation_version") != 1 or data.get("rng_version") != 1:
        raise CombatError("unsupported_version", "不支持的战斗规则版本")
    return data


def _round_half_up(numerator, denominator):
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (2 * remainder >= denominator)


def attack_interval_us(speed, combat):
    if isinstance(speed, bool) or not isinstance(speed, int) or speed <= 0:
        raise CombatError("invalid_speed", "攻击速度无效")
    interval = _round_half_up(combat["base_interval_us"] * 100, speed)
    return max(combat["min_interval_us"], min(combat["max_interval_us"], interval))


def draw_uniform(seed_hex, counter, low, high):
    """以种子和计数器确定抽样值；每次攻击固定调用两次。"""
    if not isinstance(counter, int) or not 0 <= counter < 2**64 or low > high:
        raise CombatError("invalid_rng", "随机计数器或区间无效")
    try:
        seed = bytes.fromhex(seed_hex)
    except (TypeError, ValueError) as error:
        raise CombatError("invalid_seed", "战斗种子无效") from error
    if len(seed) != 32:
        raise CombatError("invalid_seed", "战斗种子长度无效")
    digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
    return low + int.from_bytes(digest, "big") % (high - low + 1), counter + 1


def calculate_damage(attacker, defender, variance_bp, is_critical,
                     attack_scale_bp=10_000):
    """所有倍率合并计算，最后一次整除；返回计算伤害，不截断目标剩余 HP。"""
    validate_base_stats(attacker)
    validate_base_stats(defender)
    if isinstance(variance_bp, bool) or not isinstance(variance_bp, int) or variance_bp <= 0:
        raise CombatError("invalid_damage", "伤害波动无效")
    if (isinstance(attack_scale_bp, bool) or not isinstance(attack_scale_bp, int)
            or attack_scale_bp <= 0):
        raise CombatError("invalid_damage", "攻击倍率无效")
    critical_bp = attacker["crit_damage_bp"] if is_critical else 10_000
    numerator = attacker["atk"] * 100 * variance_bp * critical_bp * attack_scale_bp
    denominator = (100 + defender["defense"]) * 10_000**3
    damage = max(1, numerator // denominator)
    if damage > MAX_SAFE_DAMAGE:
        raise CombatError("numeric_overflow")
    return damage


def _enemy_stats(data, checkpoint):
    stats = dict(data["enemy"]["stats"])
    phases = data["enemy"].get("phases", [])
    if phases:
        phase = phases[checkpoint["phase_index"]]
        stats["atk"] = min(1_000_000, stats["atk"] * phase["atk_bp"] // 10_000)
        stats["speed"] = max(25, min(500, stats["speed"] * phase["speed_bp"] // 10_000))
    return stats


def _stats(data, checkpoint, actor_id):
    return data["player_stats"] if actor_id == PLAYER_ID else _enemy_stats(data, checkpoint)


def _emit(data, checkpoint, events, when_us, event_type, source=None, target=None,
          value=None, **details):
    checkpoint["sequence_id"] += 1
    event = {"battle_id": data["battle_id"], "sequence_id": checkpoint["sequence_id"],
             "battle_time_us": when_us, "event_type": event_type,
             "source": source, "target": target, "value": value}
    event.update(details)
    events.append(event)


def _finish(data, checkpoint, events, when_us, outcome):
    checkpoint["status"] = "resolved"
    checkpoint["outcome"] = outcome
    checkpoint["sim_time_us"] = when_us
    _emit(data, checkpoint, events, when_us, "BattleEnded", value=outcome)
    checkpoint["result"] = {"outcome": outcome, "duration_us": when_us,
                            "player_remaining_hp": checkpoint["hp"][PLAYER_ID],
                            "damage_dealt": checkpoint["damage_dealt"],
                            "damage_taken": checkpoint["damage_taken"],
                            "event_count": checkpoint["sequence_id"],
                            "snapshot_hash": hashlib.sha256(
                                json.dumps(data, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode("utf-8")
                            ).hexdigest()}


def _phase_changes(data, checkpoint, events, when_us):
    phases = data["enemy"].get("phases", [])
    if not phases:
        return
    enemy_hp = checkpoint["hp"][ENEMY_ID]
    maximum = data["enemy"]["stats"]["max_hp"]
    extra_attacks = []
    while checkpoint["phase_index"] + 1 < len(phases):
        next_phase = phases[checkpoint["phase_index"] + 1]
        if enemy_hp * 10_000 > maximum * next_phase["threshold_bp"]:
            break
        checkpoint["phase_index"] += 1
        checkpoint["triggered_phases"].append(next_phase["phase_id"])
        _emit(data, checkpoint, events, when_us, "BossPhaseChanged", source=ENEMY_ID,
              target=ENEMY_ID, value=next_phase["phase_id"],
              phase_id=next_phase["phase_id"],
              atk_bp=next_phase["atk_bp"], speed_bp=next_phase["speed_bp"])
        if next_phase["extra_attack_bp"]:
            extra_attacks.append(next_phase["extra_attack_bp"])
    for scale_bp in extra_attacks:
        if checkpoint["status"] != "fighting":
            break
        _attack(data, checkpoint, events, when_us, ENEMY_ID,
                "phase_extra", scale_bp)


def _attack(data, checkpoint, events, when_us, actor_id,
            action_kind="normal", attack_scale_bp=10_000):
    target_id = ENEMY_ID if actor_id == PLAYER_ID else PLAYER_ID
    if checkpoint["hp"][actor_id] <= 0 or checkpoint["hp"][target_id] <= 0:
        return
    actor = _stats(data, checkpoint, actor_id)
    defender = _stats(data, checkpoint, target_id)
    _emit(data, checkpoint, events, when_us, "AttackStarted", source=actor_id,
          target=target_id, action_kind=action_kind)
    variance, counter = draw_uniform(
        data["seed_hex"], checkpoint["rng_counter"],
        data["combat"]["variance_min_bp"], data["combat"]["variance_max_bp"])
    crit_roll, counter = draw_uniform(data["seed_hex"], counter, 0, 9_999)
    checkpoint["rng_counter"] = counter
    is_critical = crit_roll < actor["crit_bp"]
    damage = calculate_damage(actor, defender, variance, is_critical, attack_scale_bp)
    hp_loss = min(checkpoint["hp"][target_id], damage)
    checkpoint["hp"][target_id] -= hp_loss
    total_key = "damage_dealt" if actor_id == PLAYER_ID else "damage_taken"
    checkpoint[total_key] += hp_loss
    _emit(data, checkpoint, events, when_us, "DamageApplied", source=actor_id,
          target=target_id, value=hp_loss, damage=damage, hp_loss=hp_loss,
          hp_after=checkpoint["hp"][target_id], is_critical=is_critical,
          variance_bp=variance, action_kind=action_kind)
    if checkpoint["hp"][target_id] == 0:
        _emit(data, checkpoint, events, when_us, "ActorDied", source=actor_id,
              target=target_id, value=target_id)
        _finish(data, checkpoint, events, when_us,
                "victory" if actor_id == PLAYER_ID else "defeat")
    elif actor_id == PLAYER_ID:
        _phase_changes(data, checkpoint, events, when_us)


def start(snapshot):
    data = _payload(snapshot)
    validate_base_stats(data["player_stats"])
    validate_base_stats(data["enemy"]["stats"])
    phases = data["enemy"].get("phases", [])
    combat = data["combat"]
    player_interval = attack_interval_us(data["player_stats"]["speed"], combat)
    enemy_interval = attack_interval_us(data["enemy"]["stats"]["speed"], combat)
    checkpoint = {"sim_time_us": 0,
                  "hp": {PLAYER_ID: data["player_stats"]["max_hp"],
                         ENEMY_ID: data["enemy"]["stats"]["max_hp"]},
                  "next_action_us": {
                      PLAYER_ID: _round_half_up(player_interval * combat["first_attack_ratio_bp"], 10_000),
                      ENEMY_ID: _round_half_up(enemy_interval * combat["first_attack_ratio_bp"], 10_000),
                  },
                  "phase_index": 0 if phases else None,
                  "triggered_phases": [phases[0]["phase_id"]] if phases else [],
                  "rng_counter": 0, "sequence_id": 0,
                  "damage_dealt": 0, "damage_taken": 0,
                  "status": "fighting", "outcome": None, "result": None}
    events = []
    _emit(data, checkpoint, events, 0, "BattleStarted", value=data["enemy"]["enemy_id"],
          player_hp=checkpoint["hp"][PLAYER_ID], enemy_hp=checkpoint["hp"][ENEMY_ID],
          phase_id=phases[0]["phase_id"] if phases else None)
    return CombatStep(checkpoint, events, None)


def advance(snapshot, checkpoint, target_time_us):
    """从检查点推进到目标模拟时间；同一快照和种子可任意分段复算。"""
    data = _payload(snapshot)
    if (isinstance(target_time_us, bool) or not isinstance(target_time_us, int)
            or target_time_us < checkpoint["sim_time_us"]):
        raise CombatError("invalid_target_time", "目标时间不能早于检查点")
    state = deepcopy(checkpoint)
    if state["status"] == "resolved":
        return CombatStep(state, [], deepcopy(state["result"]))
    if state["status"] != "fighting":
        raise CombatError("invalid_checkpoint", "检查点状态无效")
    events = []
    timeout = (data["combat"]["boss_timeout_us"] if data["enemy"]["type"] == "boss"
               else data["combat"]["normal_timeout_us"])
    target_time_us = min(target_time_us, timeout)
    while state["status"] == "fighting":
        next_action_time = min(state["next_action_us"].values())
        when_us = min(next_action_time, timeout)
        if when_us > target_time_us:
            break
        if when_us == timeout:
            _finish(data, state, events, when_us, "timeout")
            break
        eligible = [actor_id for actor_id, scheduled in state["next_action_us"].items()
                    if scheduled == when_us and state["hp"][actor_id] > 0]
        if not eligible:
            raise CombatError("invalid_checkpoint", "时间轴没有可执行的行动")
        actor_id = min(eligible, key=lambda actor: (-_stats(data, state, actor)["speed"], actor))
        _attack(data, state, events, when_us, actor_id)
        if state["status"] == "fighting":
            interval = attack_interval_us(_stats(data, state, actor_id)["speed"], data["combat"])
            state["next_action_us"][actor_id] = when_us + interval
    if state["status"] == "fighting":
        state["sim_time_us"] = target_time_us
    return CombatStep(state, events, deepcopy(state["result"]))


def simulate(snapshot):
    """开发工具和确定性测试使用；不会写入账号奖励。"""
    initial = start(snapshot)
    data = _payload(snapshot)
    timeout = (data["combat"]["boss_timeout_us"] if data["enemy"]["type"] == "boss"
               else data["combat"]["normal_timeout_us"])
    finished = advance(snapshot, initial.checkpoint, timeout)
    return CombatStep(finished.checkpoint, initial.events + finished.events, finished.result)
