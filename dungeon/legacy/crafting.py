"""Versioned, game-specific affix weights and PoE2-inspired currency actions.

PoE2DB publishes tiers and item-level gates, but warns that modifier weights
cannot be recovered from game files. These weights are deliberately our own.
"""

from copy import deepcopy
import hashlib

from dungeon.domain.errors import DungeonError


AFFIX_VERSION = "ruins-affixes-v1"
TIER_WEIGHTS = {3: 1000, 2: 450, 1: 120}
TIER_LEVELS = {3: 1, 2: 35, 1: 65}
AFFIX_GROUPS = (
    {"id": "life", "name": "最大生命", "kind": "prefix", "stat": "max_hp",
     "slots": ("helmet", "chest", "belt", "boots", "accessory", "weapon"),
     "ranges": {3: (6, 12), 2: (13, 22), 1: (23, 35)}},
    {"id": "attack", "name": "攻击", "kind": "prefix", "stat": "atk",
     "slots": ("weapon", "accessory", "belt"),
     "ranges": {3: (2, 4), 2: (5, 8), 1: (9, 13)}},
    {"id": "guard", "name": "防御", "kind": "prefix", "stat": "defense",
     "slots": ("helmet", "chest", "belt", "boots", "accessory", "weapon"),
     "ranges": {3: (1, 2), 2: (3, 5), 1: (6, 9)}},
    {"id": "vigor", "name": "活力", "kind": "prefix", "stat": "max_hp",
     "slots": ("helmet", "chest", "belt", "boots", "accessory", "weapon"),
     "ranges": {3: (3, 5), 2: (6, 10), 1: (11, 16)}},
    {"id": "swiftness", "name": "速度", "kind": "suffix", "stat": "speed",
     "slots": ("weapon", "helmet", "chest", "belt", "boots", "accessory"),
     "ranges": {3: (2, 4), 2: (5, 8), 1: (9, 12)}},
    {"id": "precision", "name": "暴击率", "kind": "suffix", "stat": "crit_bp",
     "slots": ("weapon", "helmet", "chest", "belt", "boots", "accessory"),
     "ranges": {3: (20, 40), 2: (41, 70), 1: (71, 110)}},
    {"id": "ferocity", "name": "暴击伤害", "kind": "suffix", "stat": "crit_damage_bp",
     "slots": ("weapon", "helmet", "chest", "belt", "boots", "accessory"),
     "ranges": {3: (150, 250), 2: (251, 400), 1: (401, 600)}},
)
GROUP_BY_ID = {group["id"]: group for group in AFFIX_GROUPS}
CURRENCIES = {
    "transmutation": {"name": "蜕变石", "stack_size": 40},
    "augmentation": {"name": "增幅石", "stack_size": 30},
    "regal": {"name": "富豪石", "stack_size": 20},
    "alchemy": {"name": "点金石", "stack_size": 20},
    "exalted": {"name": "崇高石", "stack_size": 20},
    "chaos": {"name": "混沌石", "stack_size": 20},
    "divine": {"name": "神圣石", "stack_size": 10},
    "annulment": {"name": "无效石", "stack_size": 20},
}


def public_rules():
    """Weights imply probabilities only after slot, level and exclusions filter."""
    return {"version": AFFIX_VERSION, "probability_model": "game_config_weighted",
            "tier_weights": deepcopy(TIER_WEIGHTS), "tier_min_item_levels": deepcopy(TIER_LEVELS),
            "groups": [{"id": group["id"], "name": group["name"],
                        "kind": group["kind"], "stat": group["stat"],
                        "slots": list(group["slots"]), "ranges": deepcopy(group["ranges"])}
                       for group in AFFIX_GROUPS],
            "currencies": deepcopy(CURRENCIES)}


def _tier_value(mapping, tier):
    return mapping.get(tier, mapping.get(str(tier)))


def eligible_affixes(slot, item_level, existing=(), *, kind=None, min_level=1,
                     rules=None):
    used = {affix["group"] for affix in existing}
    prefix_count = sum(affix["kind"] == "prefix" for affix in existing)
    suffix_count = sum(affix["kind"] == "suffix" for affix in existing)
    candidates = []
    groups = AFFIX_GROUPS if rules is None else rules["groups"]
    weights = TIER_WEIGHTS if rules is None else rules["tier_weights"]
    levels = TIER_LEVELS if rules is None else rules["tier_min_item_levels"]
    for group in groups:
        if (slot not in group["slots"] or group["id"] in used
                or (kind is not None and group["kind"] != kind)
                or (group["kind"] == "prefix" and prefix_count >= 3)
                or (group["kind"] == "suffix" and suffix_count >= 3)):
            continue
        for tier in (3, 2, 1):
            level = _tier_value(levels, tier)
            if level <= item_level and level >= min_level:
                candidates.append((group, tier, _tier_value(weights, tier)))
    return candidates


def _draw(rng, upper):
    if upper <= 0:
        raise DungeonError("no_affix", "没有可用词条")
    return rng(upper)


def roll_affix(slot, item_level, existing, rng, *, kind=None, min_level=1,
               rules=None):
    candidates = eligible_affixes(slot, item_level, existing, kind=kind,
                                  min_level=min_level, rules=rules)
    total = sum(weight for _, _, weight in candidates)
    draw = _draw(rng, total)
    for group, tier, weight in candidates:
        if draw < weight:
            low, high = _tier_value(group["ranges"], tier)
            value = low + _draw(rng, high - low + 1)
            return {"group": group["id"], "name": group["name"],
                    "kind": group["kind"], "tier": tier,
                    "stat": group["stat"], "value": value}
        draw -= weight
    raise AssertionError("weighted roll overflow")


def probability_table(slot, item_level, existing=(), *, kind=None):
    candidates = eligible_affixes(slot, item_level, existing, kind=kind)
    total = sum(weight for _, _, weight in candidates)
    return [{"group": group["id"], "tier": tier, "weight": weight,
             "total_weight": total, "probability_bp": weight * 10000 // total}
            for group, tier, weight in candidates] if total else []


def deterministic_rng(seed, label):
    counter = 0

    def draw(upper):
        nonlocal counter
        limit = 2**256 - (2**256 % upper)
        while True:
            digest = hashlib.sha256(seed + label + counter.to_bytes(4, "big")).digest()
            counter += 1
            value = int.from_bytes(digest, "big")
            if value < limit:
                return value % upper
    return draw


def _remove(item, affix):
    item["stats"][affix["stat"]] -= affix["value"]
    if item["stats"][affix["stat"]] == 0:
        del item["stats"][affix["stat"]]
    item["affixes"].remove(affix)


def _add(item, rng, *, kind=None):
    affix = roll_affix(item["slot"], item["item_level"], item["affixes"], rng,
                       kind=kind)
    item["affixes"].append(affix)
    item["stats"][affix["stat"]] = item["stats"].get(affix["stat"], 0) + affix["value"]


def apply_currency(source, currency_id, rng):
    """Return a new item or raise; callers debit currency only after success."""
    if currency_id not in CURRENCIES:
        raise DungeonError("invalid_request", "未知通货")
    item = deepcopy(source)
    rarity = item["quality"]
    if rarity not in ("normal", "excellent", "rare"):
        raise DungeonError("currency_unavailable", "当前品质暂不能使用基础通货")
    affixes = item["affixes"]
    prefixes = sum(a["kind"] == "prefix" for a in affixes)
    suffixes = sum(a["kind"] == "suffix" for a in affixes)
    if currency_id == "transmutation" and rarity == "normal" and not affixes:
        item["quality"] = "excellent"
        _add(item, rng)
    elif currency_id == "augmentation" and rarity == "excellent" and len(affixes) == 1:
        _add(item, rng, kind="suffix" if prefixes else "prefix")
    elif currency_id == "regal" and rarity == "excellent" and 1 <= len(affixes) <= 2:
        item["quality"] = "rare"
        _add(item, rng)
    elif currency_id == "alchemy" and rarity in ("normal", "excellent"):
        for affix in list(item["affixes"]):
            _remove(item, affix)
        item["quality"] = "rare"
        for kind in ("prefix", "suffix", "prefix", "suffix"):
            _add(item, rng, kind=kind)
    elif currency_id == "exalted" and rarity == "rare" and len(affixes) < 6:
        _add(item, rng)
    elif currency_id == "chaos" and rarity == "rare" and affixes:
        _remove(item, affixes[_draw(rng, len(affixes))])
        _add(item, rng)
    elif currency_id == "divine" and affixes:
        for affix in item["affixes"]:
            group = GROUP_BY_ID[affix["group"]]
            low, high = group["ranges"][affix["tier"]]
            value = low + _draw(rng, high - low + 1)
            item["stats"][affix["stat"]] += value - affix["value"]
            affix["value"] = value
    elif currency_id == "annulment" and affixes:
        _remove(item, affixes[_draw(rng, len(affixes))])
    else:
        raise DungeonError("currency_unavailable", "当前装备不能使用这枚通货")
    return item
