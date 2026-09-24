"""v0.1 联调目录。增加关卡或装备只改配置，不改协议/属性结算器。"""

from copy import deepcopy
import re

from estate.dungeon.effects import (EffectError, MAX_STAT, STAT_FIELDS,
                                    validate_base_stats, validate_effect)


SLOTS = ("weapon", "helmet", "chest", "belt", "boots", "accessory")
QUALITIES = ("normal", "excellent", "rare", "epic")
VISUAL_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")

CATALOG = {
    "config_version": "dungeon-v0.2.0-relic-depths",
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
        {"template_id": "ruins_blade", "name": "遗迹短刃", "slot": "weapon",
         "quality": "excellent", "stats": {"atk": 38}, "tags": ["blade"],
         "effects": [], "sell_coins": 12},
        # —— 第一档（普通）：浅层遗迹常规掉落 ——
        {"template_id": "t1_blade", "name": "破损的铁刃", "slot": "weapon",
         "quality": "normal", "stats": {"atk": 34}, "tags": ["blade"], "effects": [],
         "visual_id": "t1_blade", "sell_coins": 8},
        {"template_id": "t1_helm", "name": "铆钉轻盔", "slot": "helmet",
         "quality": "normal", "stats": {"max_hp": 32}, "tags": ["guard"], "effects": [],
         "visual_id": "t1_helm", "sell_coins": 6},
        {"template_id": "t1_chest", "name": "铁叶胸甲", "slot": "chest",
         "quality": "normal", "stats": {"defense": 15}, "tags": ["guard"], "effects": [],
         "visual_id": "t1_chest", "sell_coins": 6},
        {"template_id": "t1_belt", "name": "行者腰带", "slot": "belt",
         "quality": "normal", "stats": {"max_hp": 72}, "tags": [], "effects": [],
         "visual_id": "t1_belt", "sell_coins": 6},
        {"template_id": "t1_boots", "name": "疾行便靴", "slot": "boots",
         "quality": "normal", "stats": {"defense": 10, "speed": 105}, "tags": [], "effects": [],
         "visual_id": "t1_boots", "sell_coins": 8},
        {"template_id": "t1_charm", "name": "幸运硬币", "slot": "accessory",
         "quality": "normal", "stats": {"crit_bp": 300}, "tags": ["trinket"], "effects": [],
         "visual_id": "t1_charm", "sell_coins": 8},
        # —— 第二档（优良）：中层掉落，开始带暴击词条 ——
        {"template_id": "t2_blade", "name": "遗迹短剑", "slot": "weapon",
         "quality": "excellent", "stats": {"atk": 46}, "tags": ["blade"], "effects": [],
         "visual_id": "t2_blade", "sell_coins": 14},
        {"template_id": "t2_helm", "name": "游侠风帽", "slot": "helmet",
         "quality": "excellent", "stats": {"max_hp": 42}, "tags": ["guard"], "effects": [],
         "visual_id": "t2_helm", "sell_coins": 12},
        {"template_id": "t2_chest", "name": "硬革护胸", "slot": "chest",
         "quality": "excellent", "stats": {"defense": 20}, "tags": ["guard"], "effects": [],
         "visual_id": "t2_chest", "sell_coins": 12},
        {"template_id": "t2_belt", "name": "巡林者束带", "slot": "belt",
         "quality": "excellent", "stats": {"max_hp": 88}, "tags": [], "effects": [],
         "visual_id": "t2_belt", "sell_coins": 12},
        {"template_id": "t2_boots", "name": "影袭之靴", "slot": "boots",
         "quality": "excellent", "stats": {"defense": 13, "speed": 112}, "tags": [], "effects": [],
         "visual_id": "t2_boots", "sell_coins": 14},
        {"template_id": "t2_charm", "name": "守誓徽记", "slot": "accessory",
         "quality": "excellent", "stats": {"crit_bp": 500, "crit_damage_bp": 2000},
         "tags": ["trinket"], "effects": [], "visual_id": "t2_charm", "sell_coins": 14},
        # —— 第三档（稀有）：精英线核心掉落 ——
        {"template_id": "t3_blade", "name": "符文铁剑", "slot": "weapon",
         "quality": "rare", "stats": {"atk": 60}, "tags": ["blade"], "effects": [],
         "visual_id": "t3_blade", "sell_coins": 24},
        {"template_id": "t3_chest", "name": "秘银锁甲", "slot": "chest",
         "quality": "rare", "stats": {"defense": 28}, "tags": ["guard"], "effects": [],
         "visual_id": "t3_chest", "sell_coins": 22},
        {"template_id": "t3_boots", "name": "追风之靴", "slot": "boots",
         "quality": "rare", "stats": {"defense": 16, "speed": 120}, "tags": [], "effects": [],
         "visual_id": "t3_boots", "sell_coins": 26},
        {"template_id": "t3_charm", "name": "鹰眼坠饰", "slot": "accessory",
         "quality": "rare", "stats": {"crit_bp": 800, "crit_damage_bp": 3000},
         "tags": ["trinket"], "effects": [], "visual_id": "t3_charm", "sell_coins": 24},
        # —— 第四档（史诗）：首领专属 ——
        {"template_id": "t4_blade", "name": "守护者巨刃", "slot": "weapon",
         "quality": "epic", "stats": {"atk": 76}, "tags": ["blade"], "effects": [],
         "visual_id": "t4_blade", "sell_coins": 40},
        {"template_id": "t4_helm", "name": "龙冠", "slot": "helmet",
         "quality": "epic", "stats": {"max_hp": 60, "crit_bp": 400}, "tags": ["guard"], "effects": [],
         "visual_id": "t4_helm", "sell_coins": 44},
        {"template_id": "t4_chest", "name": "龙鳞重铠", "slot": "chest",
         "quality": "epic", "stats": {"defense": 36}, "tags": ["guard"], "effects": [],
         "visual_id": "t4_chest", "sell_coins": 36},
    ],
    "enemies": [
        {"enemy_id": "ruins_slime", "name": "遗迹软泥", "type": "normal",
         "stats": {"max_hp": 100, "atk": 15, "defense": 5, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 80}, "visual_id": "ruins_slime"},
        {"enemy_id": "ruins_rat", "name": "遗迹窃鼠", "type": "normal",
         "stats": {"max_hp": 200, "atk": 24, "defense": 3, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 115}, "visual_id": "ruins_rat"},
        {"enemy_id": "ruins_bat", "name": "遗迹毒蝠", "type": "normal",
         "stats": {"max_hp": 220, "atk": 28, "defense": 4, "crit_bp": 1000,
                   "crit_damage_bp": 15000, "speed": 125}, "visual_id": "ruins_bat"},
        {"enemy_id": "ruins_skeleton", "name": "遗迹骷髅", "type": "normal",
         "stats": {"max_hp": 300, "atk": 34, "defense": 14, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 95}, "visual_id": "ruins_skeleton"},
        {"enemy_id": "ruins_golem", "name": "遗迹石像卫", "type": "normal",
         "stats": {"max_hp": 420, "atk": 36, "defense": 24, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 60}, "visual_id": "ruins_golem"},
        {"enemy_id": "ruins_cultist", "name": "遗迹祭司", "type": "normal",
         "stats": {"max_hp": 420, "atk": 48, "defense": 10, "crit_bp": 2000,
                   "crit_damage_bp": 15000, "speed": 105}, "visual_id": "ruins_cultist"},
        {"enemy_id": "ruins_knight", "name": "遗迹叛骑士", "type": "elite",
         "stats": {"max_hp": 560, "atk": 60, "defense": 22, "crit_bp": 2000,
                   "crit_damage_bp": 15000, "speed": 90}, "visual_id": "ruins_knight"},
        {"enemy_id": "ruins_wyrm", "name": "遗迹幼龙", "type": "elite",
         "stats": {"max_hp": 650, "atk": 64, "defense": 20, "crit_bp": 1500,
                   "crit_damage_bp": 15000, "speed": 110}, "visual_id": "ruins_wyrm"},
        {"enemy_id": "ruins_guardian", "name": "遗迹守卫", "type": "boss",
         "stats": {"max_hp": 820, "atk": 58, "defense": 28, "crit_bp": 0,
                   "crit_damage_bp": 15000, "speed": 85}, "visual_id": "ruins_guardian",
         "phases": [
             {"phase_id": "guardian_solid", "threshold_bp": 10000,
              "atk_bp": 10000, "speed_bp": 10000, "extra_attack_bp": 0},
             {"phase_id": "guardian_berserk", "threshold_bp": 4500,
              "atk_bp": 13500, "speed_bp": 11000, "extra_attack_bp": 3000},
         ]},
        {"enemy_id": "ruins_overlord", "name": "遗迹主宰", "type": "boss",
         "stats": {"max_hp": 900, "atk": 58, "defense": 28, "crit_bp": 1000,
                   "crit_damage_bp": 15000, "speed": 95}, "visual_id": "ruins_overlord",
         "phases": [
             {"phase_id": "overlord_calm", "threshold_bp": 10000,
              "atk_bp": 10000, "speed_bp": 10000, "extra_attack_bp": 0},
             {"phase_id": "overlord_wrath", "threshold_bp": 5500,
              "atk_bp": 12200, "speed_bp": 10500, "extra_attack_bp": 2500},
             {"phase_id": "overlord_ruin", "threshold_bp": 2500,
              "atk_bp": 14000, "speed_bp": 12000, "extra_attack_bp": 5000},
         ]},
    ],
    "challenges": [
        {"challenge_id": "ruins_slime_01", "difficulty_id": "normal",
         "enemy_id": "ruins_slime", "requires": [], "reward_table_id": "slime_clear"},
        {"challenge_id": "ruins_rat_01", "difficulty_id": "normal",
         "enemy_id": "ruins_rat", "requires": ["ruins_slime_01"],
         "reward_table_id": "rat_clear"},
        {"challenge_id": "ruins_bat_01", "difficulty_id": "normal",
         "enemy_id": "ruins_bat", "requires": ["ruins_rat_01"],
         "reward_table_id": "bat_clear"},
        {"challenge_id": "ruins_skeleton_01", "difficulty_id": "normal",
         "enemy_id": "ruins_skeleton", "requires": ["ruins_bat_01"],
         "reward_table_id": "skeleton_clear"},
        {"challenge_id": "ruins_golem_01", "difficulty_id": "normal",
         "enemy_id": "ruins_golem", "requires": ["ruins_skeleton_01"],
         "reward_table_id": "golem_clear"},
        {"challenge_id": "ruins_cultist_01", "difficulty_id": "normal",
         "enemy_id": "ruins_cultist", "requires": ["ruins_golem_01"],
         "reward_table_id": "cultist_clear"},
        {"challenge_id": "ruins_knight_01", "difficulty_id": "normal",
         "enemy_id": "ruins_knight", "requires": ["ruins_cultist_01"],
         "reward_table_id": "knight_clear"},
        {"challenge_id": "ruins_wyrm_01", "difficulty_id": "normal",
         "enemy_id": "ruins_wyrm", "requires": ["ruins_knight_01"],
         "reward_table_id": "wyrm_clear"},
        {"challenge_id": "ruins_guardian_01", "difficulty_id": "normal",
         "enemy_id": "ruins_guardian", "requires": ["ruins_wyrm_01"],
         "reward_table_id": "guardian_clear"},
        {"challenge_id": "ruins_overlord_01", "difficulty_id": "normal",
         "enemy_id": "ruins_overlord", "requires": ["ruins_guardian_01"],
         "reward_table_id": "overlord_clear"},
    ],
    "reward_tables": [
        {"reward_table_id": "slime_clear", "coins": 20, "rolls": 1,
         "entries": [{"template_id": "ruins_blade", "weight": 1}]},
        {"reward_table_id": "rat_clear", "coins": 26, "rolls": 1,
         "entries": [{"template_id": "t1_blade", "weight": 3},
                     {"template_id": "t1_helm", "weight": 2},
                     {"template_id": "t1_belt", "weight": 2},
                     {"template_id": "t1_boots", "weight": 2},
                     {"template_id": "t1_charm", "weight": 2}]},
        {"reward_table_id": "bat_clear", "coins": 32, "rolls": 1,
         "entries": [{"template_id": "t1_blade", "weight": 2},
                     {"template_id": "t1_helm", "weight": 2},
                     {"template_id": "t1_belt", "weight": 2},
                     {"template_id": "t1_boots", "weight": 2},
                     {"template_id": "t1_charm", "weight": 2}]},
        {"reward_table_id": "skeleton_clear", "coins": 40, "rolls": 1,
         "entries": [{"template_id": "t1_chest", "weight": 2},
                     {"template_id": "t2_blade", "weight": 2},
                     {"template_id": "t2_helm", "weight": 2},
                     {"template_id": "t2_belt", "weight": 2},
                     {"template_id": "t1_charm", "weight": 1}]},
        {"reward_table_id": "golem_clear", "coins": 52, "rolls": 1,
         "entries": [{"template_id": "t2_blade", "weight": 3},
                     {"template_id": "t2_chest", "weight": 3},
                     {"template_id": "t2_boots", "weight": 2},
                     {"template_id": "t2_charm", "weight": 2}]},
        {"reward_table_id": "cultist_clear", "coins": 66, "rolls": 1,
         "entries": [{"template_id": "t2_blade", "weight": 2},
                     {"template_id": "t2_helm", "weight": 2},
                     {"template_id": "t2_chest", "weight": 2},
                     {"template_id": "t2_belt", "weight": 2},
                     {"template_id": "t2_charm", "weight": 2}]},
        {"reward_table_id": "knight_clear", "coins": 85, "rolls": 2,
         "entries": [{"template_id": "t2_blade", "weight": 2},
                     {"template_id": "t2_chest", "weight": 2},
                     {"template_id": "t3_blade", "weight": 2},
                     {"template_id": "t3_chest", "weight": 2},
                     {"template_id": "t2_charm", "weight": 1},
                     {"template_id": "t3_charm", "weight": 1}]},
        {"reward_table_id": "wyrm_clear", "coins": 110, "rolls": 2,
         "entries": [{"template_id": "t3_blade", "weight": 3},
                     {"template_id": "t3_chest", "weight": 3},
                     {"template_id": "t3_boots", "weight": 2},
                     {"template_id": "t3_charm", "weight": 2}]},
        {"reward_table_id": "guardian_clear", "coins": 150, "rolls": 2,
         "entries": [{"template_id": "t3_blade", "weight": 3},
                     {"template_id": "t3_chest", "weight": 3},
                     {"template_id": "t4_blade", "weight": 2},
                     {"template_id": "t4_chest", "weight": 2},
                     {"template_id": "t3_charm", "weight": 2}]},
        {"reward_table_id": "overlord_clear", "coins": 220, "rolls": 2,
         "entries": [{"template_id": "t4_blade", "weight": 3},
                     {"template_id": "t4_helm", "weight": 3},
                     {"template_id": "t4_chest", "weight": 3},
                     {"template_id": "t3_boots", "weight": 1},
                     {"template_id": "t3_charm", "weight": 1}]},
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


def prerequisite_key(required, difficulty_id):
    """旧字符串表示同难度；对象可明确指定跨难度前置。"""
    if isinstance(required, str):
        return required, difficulty_id
    if (isinstance(required, dict) and set(required) == {"challenge_id", "difficulty_id"}
            and isinstance(required["challenge_id"], str)
            and isinstance(required["difficulty_id"], str)):
        return required["challenge_id"], required["difficulty_id"]
    return None


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
        if not isinstance(catalog["challenges"], list) or any(
            not isinstance(row, dict) for row in catalog["challenges"]
        ):
            raise DungeonConfigError("挑战目录必须为列表")
        challenge_keys = [(row.get("challenge_id"), row.get("difficulty_id"))
                          for row in catalog["challenges"]]
        if any(not isinstance(challenge_id, str) or not challenge_id
               or not isinstance(difficulty_id, str) or not difficulty_id
               for challenge_id, difficulty_id in challenge_keys) or len(challenge_keys) != len(set(challenge_keys)):
            raise DungeonConfigError("挑战编号与难度组合必须唯一")
        challenge_keys = set(challenge_keys)
        for item in catalog["items"]:
            if item.get("slot") not in SLOTS or item.get("quality") not in QUALITIES:
                raise DungeonConfigError(f"装备部位或品质无效: {item['template_id']}")
            if not isinstance(item.get("name"), str) or not item["name"]:
                raise DungeonConfigError("装备名称无效")
            visual_id = item.get("visual_id", item["template_id"])
            if not isinstance(visual_id, str) or VISUAL_ID.fullmatch(visual_id) is None:
                raise DungeonConfigError("装备资源编号无效")
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
            sell_coins = item.get("sell_coins", 0)
            if (isinstance(sell_coins, bool) or not isinstance(sell_coins, int)
                    or not 0 <= sell_coins <= 1_000_000):
                raise DungeonConfigError("装备售价无效")
        starters = [item for item in catalog["items"] if item["template_id"].startswith("starter_")]
        if len(starters) != len(SLOTS) or {item["slot"] for item in starters} != set(SLOTS):
            raise DungeonConfigError("新手装备必须覆盖六个部位")
        for enemy in catalog["enemies"]:
            if enemy.get("type") not in ("normal", "elite", "boss"):
                raise DungeonConfigError("敌人类型无效")
            if not isinstance(enemy.get("name"), str) or not enemy["name"]:
                raise DungeonConfigError("敌人名称无效")
            if (not isinstance(enemy.get("visual_id"), str)
                    or VISUAL_ID.fullmatch(enemy["visual_id"]) is None):
                raise DungeonConfigError("敌人资源编号无效")
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
        reward_ids = _unique(catalog["reward_tables"], "reward_table_id")
        for table in catalog["reward_tables"]:
            if set(table) != {"reward_table_id", "coins", "rolls", "entries"}:
                raise DungeonConfigError("奖励表字段无效")
            if (isinstance(table["coins"], bool) or not isinstance(table["coins"], int)
                    or table["coins"] < 0 or table["coins"] > 1_000_000):
                raise DungeonConfigError("奖励金币无效")
            if (isinstance(table["rolls"], bool) or not isinstance(table["rolls"], int)
                    or not 0 <= table["rolls"] <= 20):
                raise DungeonConfigError("奖励数量无效")
            entries = table["entries"]
            if not isinstance(entries, list) or (table["rolls"] > 0 and not entries):
                raise DungeonConfigError("奖励池为空")
            for entry in entries:
                if (not isinstance(entry, dict) or set(entry) != {"template_id", "weight"}
                        or entry["template_id"] not in item_ids
                        or isinstance(entry["weight"], bool)
                        or not isinstance(entry["weight"], int) or entry["weight"] <= 0):
                    raise DungeonConfigError("奖励池条目无效")
        for challenge in catalog["challenges"]:
            if challenge.get("enemy_id") not in enemy_ids:
                raise DungeonConfigError("挑战引用不存在的敌人")
            if challenge.get("difficulty_id") not in ("normal", "hard", "expert"):
                raise DungeonConfigError("挑战难度无效")
            if challenge.get("reward_table_id") not in reward_ids:
                raise DungeonConfigError("挑战奖励表不存在")
            if not isinstance(challenge.get("requires"), list) or any(
                prerequisite_key(required, challenge["difficulty_id"]) not in challenge_keys
                for required in challenge["requires"]
            ):
                raise DungeonConfigError("挑战前置无效")
        dependencies = {(row["challenge_id"], row["difficulty_id"]): [
            prerequisite_key(required, row["difficulty_id"]) for required in row["requires"]
        ] for row in catalog["challenges"]}
        visited = set()
        visiting = set()

        def visit(challenge_key):
            if challenge_key in visiting:
                raise DungeonConfigError("挑战前置存在环")
            if challenge_key in visited:
                return
            visiting.add(challenge_key)
            for parent in dependencies[challenge_key]:
                visit(parent)
            visiting.remove(challenge_key)
            visited.add(challenge_key)

        for challenge_key in challenge_keys:
            visit(challenge_key)
        if not item_ids:
            raise DungeonConfigError("装备目录为空")
    except (KeyError, TypeError, EffectError) as error:
        raise DungeonConfigError(str(error)) from error


def public_catalog(catalog=CATALOG):
    """返回可给客户端的副本；以后随机权重、种子和未解锁内容留在服务端。"""
    enemies = {enemy["enemy_id"]: enemy for enemy in catalog["enemies"]}
    reward_tables = {table["reward_table_id"]: table for table in catalog["reward_tables"]}
    visible_items = {entry["template_id"] for challenge in catalog["challenges"]
                     for entry in reward_tables[challenge["reward_table_id"]]["entries"]}
    visible_items.update(item["template_id"] for item in catalog["items"]
                         if item["template_id"].startswith("starter_"))
    return {"config_version": catalog["config_version"],
            "simulation_version": catalog["simulation_version"],
            "slots": list(SLOTS), "qualities": list(QUALITIES),
            "item_templates": {item["template_id"]: {
                "name": item["name"], "slot": item["slot"], "quality": item["quality"],
                "visual_id": item.get("visual_id", item["template_id"]),
            } for item in catalog["items"] if item["template_id"] in visible_items},
            "challenges": [{"challenge_id": row["challenge_id"],
                            "difficulty_id": row["difficulty_id"],
                            "requires": deepcopy(row["requires"]),
                            "reward_preview": {
                                "coins": reward_tables[row["reward_table_id"]]["coins"],
                                "rolls": reward_tables[row["reward_table_id"]]["rolls"],
                                "possible_items": [entry["template_id"] for entry in
                                                   reward_tables[row["reward_table_id"]]["entries"]],
                            },
                            "enemy": {"enemy_id": row["enemy_id"],
                                      "name": enemies[row["enemy_id"]]["name"],
                                      "type": enemies[row["enemy_id"]]["type"],
                                      "visual_id": enemies[row["enemy_id"]]["visual_id"],
                                      "phases": deepcopy(enemies[row["enemy_id"]].get("phases", [])),
                                      "stats": deepcopy(enemies[row["enemy_id"]]["stats"])}}
                           for row in catalog["challenges"]]}


validate_catalog(CATALOG)
