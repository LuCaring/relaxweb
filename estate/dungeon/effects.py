"""统一属性来源合同；后续装备、局内选择和环境规则共用此入口。"""

from collections import defaultdict


STAT_FIELDS = ("max_hp", "atk", "defense", "crit_bp", "crit_damage_bp", "speed")
SOURCE_KINDS = frozenset(("equipment", "roguelike", "environment"))
PASSIVE_OPERATIONS = frozenset(("stat_flat", "stat_add_bp"))
MAX_STAT = 1_000_000


class EffectError(ValueError):
    pass


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise EffectError(f"{label} 必须为整数")
    return value


def validate_effect(effect):
    """验证当前能执行的规则；未知触发器/操作不能静默变成无效果。"""
    if not isinstance(effect, dict):
        raise EffectError("效果必须为对象")
    if not isinstance(effect.get("rule_id"), str) or not effect["rule_id"]:
        raise EffectError("效果缺少 rule_id")
    if effect.get("trigger") != "passive":
        raise EffectError(f"尚未支持的效果触发器: {effect.get('trigger')}")
    if effect.get("operation") not in PASSIVE_OPERATIONS:
        raise EffectError(f"尚未支持的效果操作: {effect.get('operation')}")
    if effect.get("stat") not in STAT_FIELDS:
        raise EffectError(f"未知属性: {effect.get('stat')}")
    _integer(effect.get("value"), "效果数值")
    if abs(effect["value"]) > 100_000:
        raise EffectError("效果数值越界")
    if set(effect) != {"rule_id", "trigger", "operation", "stat", "value"}:
        raise EffectError("效果包含未声明字段")


def validate_base_stats(stats):
    if not isinstance(stats, dict) or set(stats) != set(STAT_FIELDS):
        raise EffectError("基础属性字段不完整")
    for name, value in stats.items():
        _integer(value, name)
        if value < 0 or value > MAX_STAT:
            raise EffectError(f"{name} 越界")
    if stats["max_hp"] == 0 or stats["speed"] <= 0:
        raise EffectError("生命和速度必须为正数")
    if stats["crit_bp"] > 10_000 or not 10_000 <= stats["crit_damage_bp"] <= 100_000:
        raise EffectError("暴击属性越界")


def resolve_stats(base, equipment=(), active_effects=()):
    """汇总固定装备数值与有明确来源的常驻效果。

    active_effects 的每项为 {source_kind, source_id, effect}。未来局内选择和
    环境系统可生成同一合同，但触发型效果必须另实现执行器后才能进入目录。
    """
    validate_base_stats(base)
    flat = defaultdict(int)
    bonus_bp = defaultdict(int)
    sources = []
    effects_to_apply = list(active_effects)
    for item in equipment:
        if not isinstance(item, dict) or not isinstance(item.get("stats"), dict):
            raise EffectError("装备属性无效")
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise EffectError("装备缺少 item_id")
        for stat, value in item["stats"].items():
            if stat not in STAT_FIELDS:
                raise EffectError(f"未知装备属性: {stat}")
            _integer(value, stat)
            if abs(value) > MAX_STAT:
                raise EffectError("装备属性越界")
            flat[stat] += value
            sources.append({"source_kind": "equipment", "source_id": item_id,
                            "rule_id": None, "stat": stat, "operation": "stat_flat", "value": value})
        for effect in item.get("effects", ()):
            effects_to_apply.append({"source_kind": "equipment",
                                     "source_id": item_id, "effect": effect})
    for entry in effects_to_apply:
        if not isinstance(entry, dict) or entry.get("source_kind") not in SOURCE_KINDS:
            raise EffectError("效果来源无效")
        if not isinstance(entry.get("source_id"), str) or not entry["source_id"]:
            raise EffectError("效果缺少来源 ID")
        effect = entry.get("effect")
        validate_effect(effect)
        target = flat if effect["operation"] == "stat_flat" else bonus_bp
        target[effect["stat"]] += effect["value"]
        sources.append({"source_kind": entry["source_kind"], "source_id": entry["source_id"],
                        "rule_id": effect["rule_id"], "stat": effect["stat"],
                        "operation": effect["operation"], "value": effect["value"]})
    values = {}
    for stat in STAT_FIELDS:
        factor = max(0, 10_000 + bonus_bp[stat])
        value = (base[stat] + flat[stat]) * factor // 10_000
        if stat == "max_hp":
            value = max(1, min(MAX_STAT, value))
        elif stat == "speed":
            value = max(25, min(500, value))
        elif stat == "crit_bp":
            value = max(0, min(10_000, value))
        elif stat == "crit_damage_bp":
            value = max(10_000, min(100_000, value))
        else:
            value = max(0, min(MAX_STAT, value))
        values[stat] = value
    return {"values": values, "sources": sources}
