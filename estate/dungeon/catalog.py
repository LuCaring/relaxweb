"""v0.1 联调目录。增加关卡或装备只改配置，不改协议/属性结算器。"""

from copy import deepcopy

from estate.dungeon.effects import (EffectError, MAX_STAT, STAT_FIELDS,
                                    validate_base_stats, validate_effect)


SLOTS = ("weapon", "helmet", "chest", "belt", "boots", "accessory")
QUALITIES = ("normal", "excellent", "rare", "epic")

CATALOG = {
    "config_version": "dungeon-v0.1.0-foundation",
    "simulation_version": 1,
    "rng_version": 1,
    "combat": {"base_interval_us": 2_000_000, "min_interval_us": 400_000,
               "max_interval_us": 5_000_000, "first_attack_ratio_bp": 5_000,
               "variance_min_bp": 9_500, "variance_max_bp": 10_500,
               "normal_timeout_us": 60_000_000, "boss_timeout_us": 180_000_000,
               "log_capacity": 100},
    "base_stats": {"max_hp": 200, "atk": 0, "defense": 0,
                   "crit_bp": 500, "crit_damage_bp": 15000, "speed": 100},
    "items": [
        {"template_id": "starter_blade", "name": "练习短刃", "slot": "weapon",
         "quality": "normal", "stats": {"atk": 30}, "tags": ["blade"], "effects": []},
        {"template_id": "starter_helm", "name": "练习头盔", "slot": "helmet",
         "quality": "normal", "stats": {"max_hp": 20}, "tags": ["guard"], "effects": []},
        {"template_id": "starter_chest", "name": "练习胸甲", "slot": "chest",
         "quality": "normal", "stats": {"defense": 12}, "tags": ["guard"], "effects": []},
        {"template_id": "starter_belt", "name": "练习腰带", "slot": "belt",
         "quality": "normal", "stats": {"max_hp": 50}, "tags": [], "effects": []},
        {"template_id": "starter_boots", "name": "练习短靴", "slot": "boots",
         "quality": "normal", "stats": {"defense": 8}, "tags": [], "effects": []},
        {"template_id": "starter_charm", "name": "练习护符", "slot": "accessory",
         "quality": "normal", "stats": {"max_hp": 30}, "tags": [], "effects": []},
    ],
    "enemies": [
        {"enemy_id": "ruins_slime", "name": "遗迹软泥", "type": "normal",
         "stats": {"max_hp": 100, "atk": 15, "defense": 5, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 80}, "visual_id": "ruins_slime"},
    ],
    "challenges": [
        {"challenge_id": "ruins_slime_01", "difficulty_id": "normal",
         "enemy_id": "ruins_slime", "requires": [], "reward_table_id": None},
    ],
}


class DungeonConfigError(ValueError):
    pass


def _unique(rows, key):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DungeonConfigError(f"{key} 目录必须为列表")
    ids = [row.get(key) for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise DungeonConfigError(f"{key} 必须是唯一的非空字符串")
    return set(ids)


def validate_catalog(catalog):
    """当前运行版本只接受已实现的效果；新触发器随执行器一起注册。"""
    if not isinstance(catalog, dict):
        raise DungeonConfigError("目录必须为对象")
    if not isinstance(catalog.get("config_version"), str) or not catalog["config_version"]:
        raise DungeonConfigError("缺少配置版本")
    if catalog.get("simulation_version") != 1 or catalog.get("rng_version") != 1:
        raise DungeonConfigError("未支持的规则版本")
    combat = catalog.get("combat")
    if not isinstance(combat, dict) or set(combat) != {
        "base_interval_us", "min_interval_us", "max_interval_us", "first_attack_ratio_bp",
        "variance_min_bp", "variance_max_bp", "normal_timeout_us", "boss_timeout_us",
        "log_capacity",
    } or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
             for value in combat.values()):
        raise DungeonConfigError("战斗常量无效")
    if not (combat["min_interval_us"] <= combat["base_interval_us"] <= combat["max_interval_us"]
            and 0 < combat["first_attack_ratio_bp"] <= 10_000
            and combat["variance_min_bp"] <= combat["variance_max_bp"]
            and combat["normal_timeout_us"] <= combat["boss_timeout_us"]):
        raise DungeonConfigError("战斗常量顺序无效")
    try:
        validate_base_stats(catalog["base_stats"])
        item_ids = _unique(catalog["items"], "template_id")
        enemy_ids = _unique(catalog["enemies"], "enemy_id")
        challenge_ids = _unique(catalog["challenges"], "challenge_id")
        for item in catalog["items"]:
            if item.get("slot") not in SLOTS or item.get("quality") not in QUALITIES:
                raise DungeonConfigError(f"装备部位或品质无效: {item['template_id']}")
            if not isinstance(item.get("name"), str) or not item["name"]:
                raise DungeonConfigError("装备名称无效")
            if not isinstance(item.get("stats"), dict) or not item["stats"]:
                raise DungeonConfigError("装备属性为空")
            for key, value in item["stats"].items():
                if (key not in STAT_FIELDS or isinstance(value, bool) or not isinstance(value, int)
                        or abs(value) > MAX_STAT):
                    raise DungeonConfigError(f"装备属性无效: {item['template_id']}")
            if not isinstance(item.get("tags"), list) or any(not isinstance(tag, str) for tag in item["tags"]):
                raise DungeonConfigError("装备标签无效")
            for effect in item.get("effects", ()):
                validate_effect(effect)
        starters = [item for item in catalog["items"] if item["template_id"].startswith("starter_")]
        if len(starters) != len(SLOTS) or {item["slot"] for item in starters} != set(SLOTS):
            raise DungeonConfigError("新手装备必须覆盖六个部位")
        for enemy in catalog["enemies"]:
            if enemy.get("type") not in ("normal", "elite", "boss"):
                raise DungeonConfigError("敌人类型无效")
            validate_base_stats(enemy["stats"])
            phases = enemy.get("phases", [])
            if not isinstance(phases, list):
                raise DungeonConfigError("首领阶段必须为列表")
            if enemy["type"] != "boss":
                if phases:
                    raise DungeonConfigError("普通敌人不能配置首领阶段")
                continue
            if not phases or any(not isinstance(phase, dict) for phase in phases) or phases[0].get("threshold_bp") != 10_000:
                raise DungeonConfigError("首领必须以完整生命阶段开始")
            _unique(phases, "phase_id")
            previous = 10_001
            for index, phase in enumerate(phases):
                if set(phase) != {"phase_id", "threshold_bp", "atk_bp", "speed_bp", "extra_attack_bp"}:
                    raise DungeonConfigError("首领阶段字段无效")
                values = [phase[key] for key in ("threshold_bp", "atk_bp", "speed_bp", "extra_attack_bp")]
                if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                    raise DungeonConfigError("首领阶段数值无效")
                if not 0 < phase["threshold_bp"] < previous:
                    raise DungeonConfigError("首领阈值必须严格降序")
                if not 0 < phase["atk_bp"] <= 100_000 or not 0 < phase["speed_bp"] <= 100_000:
                    raise DungeonConfigError("首领属性修正无效")
                if phase["extra_attack_bp"] < 0 or phase["extra_attack_bp"] > 100_000:
                    raise DungeonConfigError("首领追加攻击倍率无效")
                if index == 0 and phase["extra_attack_bp"]:
                    raise DungeonConfigError("开场阶段不能追加攻击")
                previous = phase["threshold_bp"]
        if not catalog["challenges"]:
            raise DungeonConfigError("至少配置一个挑战")
        for challenge in catalog["challenges"]:
            if challenge.get("enemy_id") not in enemy_ids:
                raise DungeonConfigError("挑战引用不存在的敌人")
            if challenge.get("difficulty_id") not in ("normal", "hard", "expert"):
                raise DungeonConfigError("挑战难度无效")
            if challenge.get("reward_table_id") is not None:
                raise DungeonConfigError("当前阶段尚未开放奖励表")
            if not isinstance(challenge.get("requires"), list) or any(
                required not in challenge_ids for required in challenge["requires"]
            ):
                raise DungeonConfigError("挑战前置无效")
        dependencies = {row["challenge_id"]: row["requires"] for row in catalog["challenges"]}
        visited = set()
        visiting = set()

        def visit(challenge_id):
            if challenge_id in visiting:
                raise DungeonConfigError("挑战前置存在环")
            if challenge_id in visited:
                return
            visiting.add(challenge_id)
            for parent in dependencies[challenge_id]:
                visit(parent)
            visiting.remove(challenge_id)
            visited.add(challenge_id)

        for challenge_id in challenge_ids:
            visit(challenge_id)
        if not item_ids:
            raise DungeonConfigError("装备目录为空")
    except (KeyError, TypeError, EffectError) as error:
        raise DungeonConfigError(str(error)) from error


def public_catalog(catalog=CATALOG):
    """返回可给客户端的副本；以后随机权重、种子和未解锁内容留在服务端。"""
    enemies = {enemy["enemy_id"]: enemy for enemy in catalog["enemies"]}
    return {"config_version": catalog["config_version"],
            "simulation_version": catalog["simulation_version"],
            "slots": list(SLOTS), "qualities": list(QUALITIES),
            "challenges": [{"challenge_id": row["challenge_id"],
                            "difficulty_id": row["difficulty_id"],
                            "requires": list(row["requires"]),
                            "enemy": {"enemy_id": row["enemy_id"],
                                      "name": enemies[row["enemy_id"]]["name"],
                                      "type": enemies[row["enemy_id"]]["type"],
                                      "stats": deepcopy(enemies[row["enemy_id"]]["stats"])}}
                           for row in catalog["challenges"]]}


validate_catalog(CATALOG)
