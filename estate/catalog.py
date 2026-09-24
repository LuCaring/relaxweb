"""休闲庄园服务端权威目录。

数值集中在这里，前端只消费 ``public_catalog`` 的输出，不能提交价格、
成熟时间、产量或经验。首版经济基线与复核方式见 docs/estate-economy.md。
"""
import math
from copy import deepcopy

from config import get
from estate.customization import COLLECTION_REWARD_ID, collection_reward


INITIAL_PLOTS = 4
MAX_PLOTS = 12
BALANCE_VERSION = "v1"
FISH_RARITY_WEIGHTS = {1: 100, 2: 22, 3: 4, 4: .6, 5: .08, 6: .006}

# 稳定 ID 写入账号存档，价格和解锁方式由服务端定义。
SKINS = {
    "berry": {"name": "小女孩", "description": "莓果色长发，陪你照料每一寸田野。"},
    "steve": {"name": "史蒂夫", "description": "方块风格的冒险家，准备探索庄园的每个角落。"},
    "dva": {"name": "DVA", "description": "粉蓝机甲风格，轻快地穿行在田野之间。"},
    "little_gwen": {"name": "小小格温", "description": "蓝发与蝴蝶结装束，带着优雅来到庄园。"},
    "jamie": {"name": "杰米", "description": "亮黄色运动装，活力满满地照料庄园。"},
    "xiaofei": {"name": "小菲", "description": "粉发小礼帽装束，把田园生活变得甜美。"},
    "weichong": {"name": "威虫", "description": "战术装甲造型，沉稳守护庄园的收获。"},
    "ryu": {"name": "隆", "description": "红色头带与白色道服，带着格斗家的坚定来到庄园。"},
    "malphite": {"name": "墨菲特", "description": "岩石铠甲坚不可摧，稳稳守护庄园的每一份收获。"},
    "nailong": {"name": "奶龙", "description": "圆滚滚的金黄色小恐龙，开心地陪你打理庄园。"},
}

for _skin_id, _skin in SKINS.items():
    _skin["price"] = 0 if _skin_id == "berry" else 5000
    _skin["unlock"] = "default" if _skin_id == "berry" else "purchase"


# --------------------------------------------------------------------------
# 钓鱼与矿场的规则常量
# --------------------------------------------------------------------------
# 这些数值同时被服务端结算、客户端表现和经济模拟使用，集中在此命名，
# 避免同一个数字在多个模块里各写一遍。改动需同步 docs/estate-economy.md。

FISHING_STEPS = 10              # 挣扎曲线采样数，每个采样点持续十次操作
FISHING_STEPS_PER_FRAME = 10    # 每 10 次操作推进一个张力采样点
FISHING_HOLD_STEPS = {1: 80, 2: 60, 3: 40}  # 持续按住时各级鱼竿的捕获步数
FISHING_TIMEOUT_SECONDS = 90    # 超过该秒数未收线即判过期
DAILY_FISH_RETAIN_LIMIT = 30   # 每日可保留并出售的普通鱼数量；收集品不计入
TRACE_MIN_STEPS = 20            # 客户端回传操作序列的长度下限
TRACE_MAX_STEPS = 400           # 客户端回传操作序列的长度上限

# 张力曲线初始值与每次操作的增减系数。
TENSION_START = .18
PROGRESS_START = .08
HOLD_TENSION_GAIN = .026        # 按住时张力上升
HOLD_TENSION_FORCE_BASE = .68   # 目标挣扎强度的影响
HOLD_PROGRESS_GAIN = .013       # 按住时进度上升
HOLD_PROGRESS_BASE = 1.12
HOLD_PROGRESS_FORCE_SCALE = .3
RELEASE_TENSION_DROP = .045     # 松开时张力下降
RELEASE_PROGRESS_DROP = .0035   # 松开时进度回落
RELEASE_PROGRESS_FORCE_BASE = .5
TENSION_SNAPPED_AT = 1.0        # 张力达到该值即断线
PROGRESS_CAUGHT_AT = 1.0        # 进度达到该值即入护

# 每局生成的挣扎强度采样范围。
PATTERN_MIN = .08
PATTERN_MAX = .95
PATTERN_SPAN = .55              # 随机部分占比，其余由鱼的难度决定
PATTERN_DIFFICULTY_WEIGHT = .45

# 鱼获抽取：可达稀有度上限，以及鱼饵与鱼竿对稀有度和权重的加成。
CATCH_RARITY_BASE = 2
CATCH_TREASURE_RARITY_BASE = 4
CATCH_TREASURE_CHANCE = .006
CATCH_TREASURE_BAIT_BONUS = .012
CATCH_TREASURE_ROD_BONUS = .009
CATCH_BOOST_PER_ROD_LEVEL = .35

MINE_CELLS = 25                 # 矿壁总格数
MINE_BOARD_SIZE = 5             # 矿壁边长，下发客户端用于布局
MINE_EXTRA_CELLS = 2            # 每局固定的 +2 敲击格数量
MINE_EMPTY_WEIGHT = 24          # 空格在矿壁权重表中的权重
MINE_LEGACY_RESERVED_SLOTS = 12  # 旧存档矿局的预留仓位，仅供迁移默认值使用
FERTILIZER_ITEM = "supply:fertilizer"
FERTILIZER_SELL_PRICE = 100.0
FERTILIZER_SECONDS = 60 * 60
FERTILIZER_FISH_CHANCE = {"worm": .05, "glow_grub": .10}
FERTILIZER_MINE_CHANCE = .01
LAND_UPGRADE_TICKET = "supply:land_upgrade_ticket"
LOTTERY_PRICE = 3000.0
LOTTERY_PRIZES = ("thanks", "coins_250", "coins_1000", "coins_2000",
                  "legendary_seed", "missing_collectible", "fertilizer_5", "grand")
LOTTERY_GRAND_PRIZES = (
    ("coins_100000", 10), ("reroll", 40), ("land_ticket", 10),
    ("coins_5000", 20), ("coins_10000", 20),
)

def _crop(name, seed_price, sell_price, grow_minutes, xp, unlock_level, icon, color):
    """所有收益与成长规则集中定义，保留稳定存档 ID。"""
    return {
        "name": name, "seed_price": float(seed_price), "sell_price": float(sell_price),
        "grow_seconds": int(grow_minutes * 60), "yield": 1, "xp": xp,
        "unlock_level": unlock_level, "icon": icon, "color": color,
        "balance_status": BALANCE_VERSION,
    }


CROPS = {
    "legendary_flower": _crop("传说花", 0, 36888, 72 * 60, 1440, 1, "🌟", "#f4cc5b"),
    "wheat": _crop("小麦", 20, 30, 5, 5, 1, "🌾", "#e6cb63"),
    "carrot": _crop("胡萝卜", 40, 65, 15, 8, 1, "🥕", "#f28b36"),
    "rice": _crop("水稻", 28, 44, 9, 6, 1, "🌾", "#ddd477"),
    "potato": _crop("土豆", 34, 54, 12, 7, 1, "🥔", "#c99b61"),
    "tomato": _crop("番茄", 52, 84, 20, 10, 1, "🍅", "#df5b45"),
    "cabbage": _crop("卷心菜", 64, 120, 28, 20, 2, "🥬", "#78b95d"),
    "cucumber": _crop("黄瓜", 72, 142, 35, 24, 2, "🥒", "#5da653"),
    "soybean": _crop("大豆", 82, 166, 42, 28, 2, "🫘", "#d0b65d"),
    "corn": _crop("玉米", 100, 220, 60, 40, 2, "🌽", "#f4cf46"),
    "peanut": _crop("花生", 116, 266, 75, 48, 2, "🥜", "#c78b50"),
    "sweet_potato": _crop("红薯", 132, 335, 90, 56, 3, "🍠", "#b75c49"),
    "eggplant": _crop("茄子", 150, 398, 110, 68, 3, "🍆", "#835c9f"),
    "pepper": _crop("辣椒", 172, 476, 135, 80, 3, "🌶️", "#d94b3d"),
    "pumpkin": _crop("南瓜", 260, 800, 240, 140, 3, "🎃", "#ee7a2d"),
    "strawberry": _crop("草莓", 290, 1040, 300, 175, 4, "🍓", "#e85e67"),
    "watermelon": _crop("西瓜", 340, 1315, 390, 225, 4, "🍉", "#52a95e"),
    "grape": _crop("葡萄", 410, 1610, 480, 280, 4, "🍇", "#8663a8"),
    "spinach": _crop("菠菜", 460, 2110, 600, 320, 5, "🥬", "#3e9652"),
    "onion": _crop("洋葱", 530, 2593, 750, 400, 5, "🧅", "#d3a5bb"),
    "garlic": _crop("大蒜", 620, 3095, 900, 480, 5, "🧄", "#e7d9b1"),
    "sunflower": _crop("向日葵", 760, 4000, 1080, 540, 6, "🌻", "#f3c84d"),
    "magic_grass": _crop("神奇草", 1100, 5420, 1440, 720, 6, "🌿", "#64c987"),
    "miracle_flower": _crop("奇迹花", 1800, 8820, 2160, 1080, 7, "🌸", "#f28fc2"),
    "starlight_berry": _crop("星露果", 3200, 13280, 2880, 1440, 8, "✨", "#8dd9df"),
}
CROPS["legendary_flower"]["lottery_only"] = True

LAND_LEVELS = {
    1: {"multiplier": 1.0, "upgrade_price": 500.0, "unlock_level": 2},
    2: {"multiplier": 0.85, "upgrade_price": 1500.0, "unlock_level": 4},
    3: {"multiplier": 0.65, "upgrade_price": None, "unlock_level": 6},
    4: {"multiplier": 0.45, "upgrade_price": None, "unlock_level": 6},
}

PLOT_UNLOCKS = {
    4: {"price": 500.0, "unlock_level": 2},
    5: {"price": 900.0, "unlock_level": 3},
    6: {"price": 1600.0, "unlock_level": 4},
    7: {"price": 2600.0, "unlock_level": 5},
    8: {"price": 5000.0, "unlock_level": 10},
    9: {"price": 8000.0, "unlock_level": 12},
    10: {"price": 12000.0, "unlock_level": 15},
    11: {"price": 20000.0, "unlock_level": 20},
}

WAREHOUSE_LEVELS = {
    1: {"capacity": 100, "upgrade_price": 1000.0, "unlock_level": 2},
    2: {"capacity": 200, "upgrade_price": 3000.0, "unlock_level": 4},
    3: {"capacity": 350, "upgrade_price": None, "unlock_level": 6},
}

TOOLS = {
    "rod": {
        1: {"name": "榛木鱼竿", "price": 300.0, "max_durability": 20,
            "repair_price": 90.0, "upgrade_price": 900.0, "unlock_level": 1,
            "tension_factor": 1.0},
        2: {"name": "碳素鱼竿", "price": None, "max_durability": 35,
            "repair_price": 220.0, "upgrade_price": 2400.0, "unlock_level": 3,
            "tension_factor": 0.82},
        3: {"name": "星纹鱼竿", "price": None, "max_durability": 55,
            "repair_price": 480.0, "upgrade_price": None, "unlock_level": 6,
            "tension_factor": 0.68},
    },
    "pickaxe": {
        1: {"name": "铜矿镐", "price": 350.0, "max_durability": 15,
            "repair_price": 200.0, "upgrade_price": 1100.0, "unlock_level": 1,
            "strikes": 8},
        2: {"name": "铁矿镐", "price": None, "max_durability": 20,
            "repair_price": 525.0, "upgrade_price": 2800.0, "unlock_level": 3,
            "strikes": 11},
        3: {"name": "秘银矿镐", "price": None, "max_durability": 25,
            "repair_price": 1100.0, "upgrade_price": None, "unlock_level": 6,
            "strikes": 14},
    },
}

BAITS = {
    "worm": {"name": "蚯蚓鱼饵", "price": 15.0, "unlock_level": 1, "rarity_bonus": 0},
    "glow_grub": {"name": "荧光虫饵", "price": 25.0, "unlock_level": 3, "rarity_bonus": 1},
}

def _fish(name, sell_price, rarity, xp, difficulty, habitat):
    return {
        "name": name, "sell_price": float(sell_price), "rarity": rarity, "xp": xp,
        "difficulty": difficulty, "habitat": habitat, "balance_status": BALANCE_VERSION,
    }


FISH = {
    "minnow": _fish("银鲦", 42, 1, 8, .18, "溪流"),
    "carp": _fish("湖鲤", 70, 1, 12, .25, "湖泊"),
    "crucian_carp": _fish("鲫鱼", 58, 1, 10, .22, "湖泊"),
    "tilapia": _fish("罗非鱼", 64, 1, 11, .24, "暖水湖"),
    "loach": _fish("泥鳅", 52, 1, 9, .21, "浅滩"),
    "perch": _fish("金鲈", 115, 2, 18, .36, "湖泊"),
    "catfish": _fish("鲶鱼", 128, 2, 20, .39, "河底"),
    "grass_carp": _fish("草鱼", 138, 2, 21, .38, "湖泊"),
    "silver_carp": _fish("鲢鱼", 146, 2, 22, .41, "河流"),
    "bream": _fish("鳊鱼", 152, 2, 23, .42, "湖泊"),
    "snakehead": _fish("黑鱼", 185, 3, 27, .47, "芦苇荡"),
    "bass": _fish("大口鲈", 205, 3, 30, .5, "深水湖"),
    "trout": _fish("虹鳟", 228, 3, 33, .53, "溪流"),
    "koi": _fish("锦鲤", 245, 3, 34, .5, "湖泊"),
    "pike": _fish("白斑狗鱼", 310, 4, 42, .59, "深水湖"),
    "salmon": _fish("鲑鱼", 340, 4, 45, .61, "河口"),
    "sardine": _fish("沙丁鱼", 285, 4, 39, .56, "近海"),
    "mackerel": _fish("鲭鱼", 390, 4, 50, .64, "近海"),
    "moon_eel": _fish("欧洲鳗鲡", 430, 4, 52, .66, "夜间河口"),
    "sturgeon": _fish("中华鲟", 590, 5, 65, .72, "大河"),
    "tuna": _fish("蓝鳍金枪鱼", 760, 5, 76, .78, "远海"),
    "crystal_fish": _fish("玻璃鱼", 680, 5, 70, .74, "清澈湖泊"),
    "cloudfin": _fish("云鳍鱼", 1200, 6, 105, .84, "雨后云影"),
    "mystery_fish": _fish("神秘鱼", 1680, 6, 135, .9, "静谧湖深处"),
}


FISHING_TREASURES = {
    "message_bottle": {
        "name": "神秘漂流瓶", "rarity": 5, "xp": 45, "difficulty": .68,
        "required_rod_level": 2, "weight": 5.0, "balance_status": BALANCE_VERSION,
    },
    "gold_button": {
        "name": "遗失的金纽扣", "rarity": 6, "xp": 70, "difficulty": .76,
        "required_rod_level": 2, "weight": 2.4, "balance_status": BALANCE_VERSION,
    },
    "antique_watch": {
        "name": "古旧怀表", "rarity": 7, "xp": 110, "difficulty": .84,
        "required_rod_level": 3, "weight": .8, "balance_status": BALANCE_VERSION,
    },
    "lost_underwear": {
        "name": "一条不知道是谁的内裤", "rarity": 8, "xp": 180, "difficulty": .92,
        "required_rod_level": 3, "weight": .18, "balance_status": BALANCE_VERSION,
    },
}

_reward = collection_reward(get("estate.collection_reward"), SKINS, FISHING_TREASURES)
if _reward is not None:
    SKINS[COLLECTION_REWARD_ID] = _reward


MINERALS = {
    "stone": {"name": "石料", "sell_price": 5.0, "rarity": 1, "xp": 2},
    "coal": {"name": "煤块", "sell_price": 10.0, "rarity": 1, "xp": 3},
    "copper": {"name": "铜矿", "sell_price": 20.0, "rarity": 2, "xp": 4},
    "iron": {"name": "铁矿", "sell_price": 38.0, "rarity": 3, "xp": 6},
    "amethyst": {"name": "紫晶", "sell_price": 85.0, "rarity": 4, "xp": 10},
    "star_gem": {"name": "星辉宝石", "sell_price": 200.0, "rarity": 5, "xp": 16},
}

MINING_LEVELS = {
    1: {"name": "青苔浅层", "unlock_level": 1, "bombs": 1, "risk": "低危",
        "weights": [42, 18, 20, 7, 2, 0]},
    2: {"name": "赤铜中层", "unlock_level": 3, "bombs": 2, "risk": "中危",
        "weights": [28, 16, 25, 15, 5, 1]},
    3: {"name": "星晶深层", "unlock_level": 6, "bombs": 3, "risk": "高危",
        "weights": [18, 12, 20, 24, 10, 3]},
}

PET_LEVELS = {
    1: {"name": "豆豆", "buy_price": 10000.0, "defend_chance": .20, "upgrade_price": 10000.0},
    2: {"name": "豆豆", "defend_chance": .30, "upgrade_price": 20000.0},
    3: {"name": "豆豆", "defend_chance": .40, "upgrade_price": 50000.0},
    4: {"name": "豆豆", "defend_chance": .50, "upgrade_price": None},
}


def item_id(kind, key):
    """物品 ID 统一为 ``<kind>:<key>``，解析见 :func:`item_info`。"""
    return f"{kind}:{key}"


# 具名包装让调用点更易读，同时保持测试直接导入的历史名字可用。
def seed_item(crop_id):
    return item_id("seed", crop_id)


def crop_item(crop_id):
    return item_id("crop", crop_id)


def bait_item(bait_id):
    return item_id("bait", bait_id)


def fish_item(fish_id):
    return item_id("fish", fish_id)


def collectible_item(collectible_id):
    return item_id("collectible", collectible_id)


def mineral_item(mineral_id):
    return item_id("mineral", mineral_id)


def xp_for_next(level):
    """当前等级升到下一级所需经验。"""
    return 100 * max(1, int(level))


def grow_seconds(crop_id, land_level):
    crop = CROPS[crop_id]
    land = LAND_LEVELS[land_level]
    return max(1, math.ceil(crop["grow_seconds"] * land["multiplier"]))


def item_info(item_id):
    if item_id == LAND_UPGRADE_TICKET:
        return {"id": item_id, "kind": "supply", "name": "4级农田升级券",
                "sellable": False, "sell_price": None}
    if item_id == FERTILIZER_ITEM:
        return {"id": item_id, "kind": "supply", "name": "化肥",
                "sellable": True, "sell_price": FERTILIZER_SELL_PRICE}
    if not isinstance(item_id, str) or ":" not in item_id:
        return None
    kind, crop_id = item_id.split(":", 1)
    if kind in ("seed", "crop") and crop_id in CROPS:
        crop = CROPS[crop_id]
        return {"id": item_id, "kind": kind, "crop_id": crop_id,
                "name": f"{crop['name']}种子" if kind == "seed" else crop["name"],
                "sellable": kind == "crop",
                "sell_price": crop["sell_price"] if kind == "crop" else None}
    groups = {"bait": BAITS, "fish": FISH, "mineral": MINERALS,
              "collectible": FISHING_TREASURES}
    group = groups.get(kind)
    if not group or crop_id not in group:
        return None
    entry = group[crop_id]
    return {"id": item_id, "kind": kind, f"{kind}_id": crop_id,
            "name": entry["name"], "sellable": kind in ("fish", "mineral"),
            "sell_price": entry.get("sell_price")}


def public_catalog():
    return {
        "balance_version": BALANCE_VERSION,
        "fertilizer": {"item_id": FERTILIZER_ITEM, "name": "化肥",
                       "sell_price": FERTILIZER_SELL_PRICE,
                       "seconds_reduced": FERTILIZER_SECONDS,
                       "fish_chance": FERTILIZER_FISH_CHANCE,
                       "mine_chance": FERTILIZER_MINE_CHANCE},
        "skins": {key: {"id": key, **deepcopy(value)} for key, value in SKINS.items()},
        "crops": {
            crop_id: {
                "id": crop_id,
                "name": crop["name"],
                "seed_item": seed_item(crop_id),
                "product_item": crop_item(crop_id),
                **crop,
            }
            for crop_id, crop in CROPS.items()
        },
        "land_levels": LAND_LEVELS,
        "lottery": {"price": LOTTERY_PRICE, "prizes": LOTTERY_PRIZES,
                    "grand_prizes": LOTTERY_GRAND_PRIZES,
                    "ticket_item": LAND_UPGRADE_TICKET},
        "plot_unlocks": PLOT_UNLOCKS,
        "warehouse_levels": WAREHOUSE_LEVELS,
        "tools": TOOLS,
        "baits": {key: {"id": key, "item_id": bait_item(key), **value}
                  for key, value in BAITS.items()},
        "fish": {key: {"id": key, "item_id": fish_item(key), **value}
                 for key, value in FISH.items()},
        "fishing_treasures": {
            key: {"id": key, "item_id": collectible_item(key), **value}
            for key, value in FISHING_TREASURES.items()
        },
        "minerals": {key: {"id": key, "item_id": mineral_item(key), **value}
                     for key, value in MINERALS.items()},
        "mining_levels": MINING_LEVELS,
        "pet_levels": PET_LEVELS,
        # 客户端要用它推进钓鱼进度条并预判结局；不下发就会各自硬编码一份。
        "fishing_rules": {
            "steps": FISHING_STEPS,
            "steps_per_frame": FISHING_STEPS_PER_FRAME,
            "hold_steps": FISHING_HOLD_STEPS,
            "tension_start": TENSION_START,
            "progress_start": PROGRESS_START,
            "hold_tension_gain": HOLD_TENSION_GAIN,
            "hold_tension_force_base": HOLD_TENSION_FORCE_BASE,
            "hold_progress_gain": HOLD_PROGRESS_GAIN,
            "hold_progress_base": HOLD_PROGRESS_BASE,
            "hold_progress_force_scale": HOLD_PROGRESS_FORCE_SCALE,
            "release_tension_drop": RELEASE_TENSION_DROP,
            "release_progress_drop": RELEASE_PROGRESS_DROP,
            "release_progress_force_base": RELEASE_PROGRESS_FORCE_BASE,
            "snapped_at": TENSION_SNAPPED_AT,
            "caught_at": PROGRESS_CAUGHT_AT,
        },
        # 矿场格数与预留格：避免客户端各自写死 25 与 +2。
        "mining_rules": {
            "cells": MINE_CELLS,
            "board_size": MINE_BOARD_SIZE,
            "extra_cells": MINE_EXTRA_CELLS,
        },
        "initial_plots": INITIAL_PLOTS,
        "max_plots": MAX_PLOTS,
    }
