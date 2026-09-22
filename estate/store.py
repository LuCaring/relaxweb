"""休闲庄园领域内核：档案、库存、容量、金币、经验、幂等与读模型。

只依赖一个已打开的 SQLite 连接，不导入 ``farming`` 或 ``activities``
（依赖方向单向：catalog ← store ← 上层业务）。调用方负责开启与提交事务。
跨模块使用的原语一律用公开名称，下划线只表示模块内部实现细节。
"""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from estate.catalog import (
    DAILY_FISH_RETAIN_LIMIT, FISHING_STEPS, INITIAL_PLOTS, MAX_PLOTS, MINE_BOARD_SIZE, MINING_LEVELS, TOOLS,
    WAREHOUSE_LEVELS, FISHING_TREASURES, SKINS, collectible_item, item_id, item_info, public_catalog,
    xp_for_next,
)


COLLECTIBLE_ITEMS = frozenset(collectible_item(key) for key in FISHING_TREASURES)


class EstateError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def estate_error(spec, message=None):
    """按命名规则构造异常，供 ``raise estate_error(...)`` 使用。

    ``spec`` 是 ``(code, message)``；``message`` 会直接展示给玩家，测试也
    逐字断言，因此每个 code 在不同场景下的文案必须分别保留、不可合并。
    """
    code, default = spec
    return EstateError(code, message or default)


# 被多个模块引用的规则，集中在此避免文案漂移；只在一处使用的直接内联。
INVALID_REQUEST = ("invalid_request", "请求编号无效")
LEVEL_LOCKED = ("level_locked", "庄园等级不足")
PLOT_LOCKED = ("plot_locked", "土地尚未解锁")
TOOL_MISSING = ("tool_missing", "请先购买工具")
PLOT_INDEX_INVALID = ("invalid_plot", "土地编号无效")
PLOT_NOT_FOUND = ("invalid_plot", "土地不存在")
CROP_DATA_BROKEN = ("invalid_save", "作物数据异常")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def estate_day_key(now):
    """庄园每日规则统一按北京时间 0 点换日。"""
    return datetime.fromtimestamp(int(now), SHANGHAI).date().isoformat()


def refresh_daily_pickaxe(conn, username, now):
    """跨日后将矿镐补满；无矿镐或当天已刷新时保持不变。"""
    row = conn.execute(
        "SELECT t.level,COALESCE(d.refill_day,'') FROM estate_tools t "
        "LEFT JOIN estate_tool_daily d ON d.username=t.username AND d.tool_type=t.tool_type "
        "WHERE t.username=? AND t.tool_type='pickaxe'", (username,),
    ).fetchone()
    if not row:
        return False
    today = estate_day_key(now)
    if row[1] == today:
        return False
    maximum = TOOLS["pickaxe"].get(row[0], {}).get("max_durability")
    if maximum is None:
        return False
    conn.execute(
        "UPDATE estate_tools SET durability=?,updated_at=? "
        "WHERE username=? AND tool_type='pickaxe'",
        (maximum, int(now), username),
    )
    conn.execute(
        "INSERT INTO estate_tool_daily(username,tool_type,refill_day) VALUES (?,'pickaxe',?) "
        "ON CONFLICT(username,tool_type) DO UPDATE SET refill_day=excluded.refill_day",
        (username, today),
    )
    return True


def fishing_daily_state(conn, username, now):
    """读取当日普通鱼保留额度；跨日时原子重置计数和公告状态。"""
    today = estate_day_key(now)
    conn.execute(
        "INSERT INTO estate_fishing_daily(username,fishing_day) VALUES (?,?) "
        "ON CONFLICT(username) DO UPDATE SET "
        "fishing_day=excluded.fishing_day,retained_count=0,notice_shown=0 "
        "WHERE estate_fishing_daily.fishing_day<>excluded.fishing_day",
        (username, today),
    )
    retained, notice_shown = conn.execute(
        "SELECT retained_count,notice_shown FROM estate_fishing_daily WHERE username=?",
        (username,),
    ).fetchone()
    return {"day": today, "retained": int(retained),
            "remaining": max(0, DAILY_FISH_RETAIN_LIMIT - int(retained)),
            "limit": DAILY_FISH_RETAIN_LIMIT, "notice_shown": bool(notice_shown)}


def retain_daily_fish(conn, username, now):
    """为普通鱼消耗一个当日保留名额，额度用尽后只首次返回公告标记。"""
    state = fishing_daily_state(conn, username, now)
    if state["retained"] < DAILY_FISH_RETAIN_LIMIT:
        conn.execute(
            "UPDATE estate_fishing_daily SET retained_count=retained_count+1 WHERE username=?",
            (username,),
        )
        return {**state, "retained": True, "remaining": state["remaining"] - 1,
                "retained_count": state["retained"] + 1, "show_notice": False}
    show_notice = not state["notice_shown"]
    if show_notice:
        conn.execute("UPDATE estate_fishing_daily SET notice_shown=1 WHERE username=?", (username,))
    return {**state, "retained": False, "retained_count": state["retained"],
            "show_notice": show_notice}


def positive_int(value, spec=("invalid_quantity", "数量无效"), maximum=9999):
    """接受整数或等值十进制字符串；拒绝布尔、越界与非法格式。"""
    if isinstance(value, bool):
        raise estate_error(spec)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise estate_error(spec) from None
    if number <= 0 or number > maximum or str(value).strip() not in (str(number), f"{number}.0"):
        raise estate_error(spec)
    return number


def plot_index(value):
    if isinstance(value, bool):
        raise estate_error(PLOT_INDEX_INVALID)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise estate_error(PLOT_INDEX_INVALID) from None
    if number < 0 or number >= MAX_PLOTS:
        raise estate_error(PLOT_INDEX_INVALID)
    return number


# --------------------------------------------------------------------------
# 档案
# --------------------------------------------------------------------------

PROFILE_COLUMNS = (
    "skin_id", "level", "xp", "warehouse_level", "plot_count",
    "reserved_capacity", "pet_level", "version", "created_at", "updated_at",
)


def ensure_estate(conn, username, now):
    now = int(now)
    conn.execute(
        "INSERT OR IGNORE INTO estate_profiles"
        "(username,level,xp,warehouse_level,plot_count,version,created_at,updated_at) "
        "VALUES (?,1,0,1,?,1,?,?)",
        (username, INITIAL_PLOTS, now, now),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO estate_plots(username,plot_index,land_level) "
        "VALUES (?,?,1)",
        ((username, index) for index in range(MAX_PLOTS)),
    )


def load_profile(conn, username):
    """SELECT 列表由 ``PROFILE_COLUMNS`` 派生，列顺序不会与映射漂移。"""
    row = conn.execute(
        f"SELECT {','.join(PROFILE_COLUMNS)} FROM estate_profiles WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        raise estate_error(("estate_missing", "庄园存档不存在"))
    return dict(zip(PROFILE_COLUMNS, row))


def bump_version(conn, username, now):
    conn.execute(
        "UPDATE estate_profiles SET version=version+1,updated_at=? WHERE username=?",
        (int(now), username),
    )


# --------------------------------------------------------------------------
# 库存与容量
# --------------------------------------------------------------------------


def inventory_rows(conn, username):
    return conn.execute(
        "SELECT item_id,quantity FROM estate_inventory "
        "WHERE username = ? ORDER BY item_id", (username,),
    ).fetchall()


def inventory_used(conn, username):
    """仓库占用格数。

    普通物品按数量计格；收藏品不可出售（见 ``catalog.item_info``），
    重复获得时只计一格。否则同一种收藏品的副本既卖不掉也丢不掉，
    会永久占满仓库，最终连收获、挖矿和钓鱼都无法进行。
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN item_id LIKE ? THEN 1 ELSE quantity END),0) "
        "FROM estate_inventory WHERE username = ?",
        (f"{item_id('collectible', '')}%", username),
    ).fetchone()
    return int(row[0] or 0)


def normalize_collectibles(conn, username):
    """旧存档中的同类收藏品只保留一件，立即释放重复占用的仓位。"""
    if not COLLECTIBLE_ITEMS:
        return
    placeholders = ",".join("?" for _ in COLLECTIBLE_ITEMS)
    conn.execute(
        f"UPDATE estate_inventory SET quantity=1 "
        f"WHERE username=? AND quantity>1 AND item_id IN ({placeholders})",
        (username, *sorted(COLLECTIBLE_ITEMS)),
    )


def capacity(profile):
    try:
        return WAREHOUSE_LEVELS[profile["warehouse_level"]]["capacity"]
    except KeyError:
        raise estate_error(("invalid_save", "仓库等级数据异常")) from None


def require_capacity(conn, username, profile, extra):
    """容量校验把活动已预留的仓位一并计入，避免产物挤爆仓库。"""
    reserved = profile.get("reserved_capacity", 0)
    if inventory_used(conn, username) + reserved + extra > capacity(profile):
        raise estate_error(("warehouse_full", "仓库空间不足"))


def change_inventory(conn, username, item_id, delta):
    row = conn.execute(
        "SELECT quantity FROM estate_inventory WHERE username = ? AND item_id = ?",
        (username, item_id),
    ).fetchone()
    current = int(row[0]) if row else 0
    if item_id in COLLECTIBLE_ITEMS:
        current = min(current, 1)
    updated = current + int(delta)
    if item_id in COLLECTIBLE_ITEMS and delta > 0:
        updated = 1
    if updated < 0:
        raise estate_error(("inventory_short", "库存数量不足"))
    if updated == 0:
        conn.execute(
            "DELETE FROM estate_inventory WHERE username = ? AND item_id = ?",
            (username, item_id),
        )
    else:
        conn.execute(
            "INSERT INTO estate_inventory(username,item_id,quantity) VALUES (?,?,?) "
            "ON CONFLICT(username,item_id) DO UPDATE SET quantity=excluded.quantity",
            (username, item_id, updated),
        )
    if delta > 0 and item_id in COLLECTIBLE_ITEMS:
        conn.execute("INSERT OR IGNORE INTO estate_collections VALUES (?,?)", (username, item_id))
    return updated


# --------------------------------------------------------------------------
# 金币与经验
# --------------------------------------------------------------------------


def debit(adjust_coins, conn, username, amount, detail, request_id):
    """庄园支出统一记 ``estate_purchase`` 流水，余额不足转为领域错误。"""
    try:
        return adjust_coins(
            conn, username, -round(amount, 2), "estate_purchase", detail,
            ref=f"estate:{request_id}",
        )
    except ValueError as error:
        raise estate_error(("insufficient_coins", "金币不足"), str(error)) from None


def credit(adjust_coins, conn, username, amount, detail, request_id):
    return adjust_coins(
        conn, username, amount, "estate_sale", detail, ref=f"estate:{request_id}",
    )


def award_xp(conn, username, amount):
    """发放经验并处理连续升级；全庄园唯一的升级循环。"""
    profile = load_profile(conn, username)
    level, xp = profile["level"], profile["xp"] + int(amount)
    while xp >= xp_for_next(level):
        xp -= xp_for_next(level)
        level += 1
    conn.execute("UPDATE estate_profiles SET level=?,xp=? WHERE username=?",
                 (level, xp, username))
    return level


# --------------------------------------------------------------------------
# 幂等动作
# --------------------------------------------------------------------------


def request_hash(action_type, payload):
    raw = json.dumps(
        {"action": action_type, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_request_id(request_id):
    if not isinstance(request_id, str):
        raise estate_error(INVALID_REQUEST)
    request_id = request_id.strip()
    if not 8 <= len(request_id) <= 80 or any(ord(char) < 33 for char in request_id):
        raise estate_error(INVALID_REQUEST)
    return request_id


def run_action(conn, username, request_id, action_type, payload, now, mutate):
    """在幂等保护下执行一次写操作。

    同用户 + 同请求编号 + 同摘要返回已保存结果并标记 ``replayed``；
    摘要不同报 ``request_conflict``。动作记录与业务写入同事务提交。
    """
    request_id = validate_request_id(request_id)
    ensure_estate(conn, username, now)
    digest = request_hash(action_type, payload)
    existing = conn.execute(
        "SELECT action_type,request_hash,result_json FROM estate_actions "
        "WHERE username=? AND request_id=?", (username, request_id),
    ).fetchone()
    if existing:
        if existing[0] != action_type or existing[1] != digest:
            raise estate_error(("request_conflict", "请求编号已被其他操作使用"))
        return {**json.loads(existing[2]), "replayed": True, "request_id": request_id}

    result = mutate()
    bump_version(conn, username, now)
    stored = {**result, "replayed": False, "request_id": request_id}
    conn.execute(
        "INSERT INTO estate_actions"
        "(username,request_id,action_type,request_hash,result_json,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (username, request_id, action_type, digest,
         json.dumps(stored, ensure_ascii=False, separators=(",", ":")), int(now)),
    )
    return stored


# --------------------------------------------------------------------------
# 读模型
# --------------------------------------------------------------------------


def sweep_expired_fishing(conn, username, now):
    """把超时未收线的钓鱼局结算为过期，并释放其预留仓位。"""
    expired = conn.execute(
        "SELECT COUNT(*) FROM estate_fishing_sessions "
        "WHERE username=? AND status='active' AND expires_at<?", (username, now),
    ).fetchone()[0]
    if not expired:
        return
    result = json.dumps({"action": "finish_fishing", "outcome": "expired",
                         "progress": 0, "peak_tension": 0}, ensure_ascii=False)
    conn.execute(
        "UPDATE estate_fishing_sessions SET status='finished',result_json=? "
        "WHERE username=? AND status='active' AND expires_at<?",
        (result, username, now),
    )
    conn.execute(
        "UPDATE estate_profiles SET reserved_capacity=max(0,reserved_capacity-?) "
        "WHERE username=?", (expired, username),
    )


def _plot_views(conn, username, plot_count, now):
    views = []
    for index, land_level, crop_id, planted_at, ready_at in conn.execute(
        "SELECT plot_index,land_level,crop_id,planted_at,ready_at "
        "FROM estate_plots WHERE username = ? ORDER BY plot_index", (username,),
    ):
        views.append({
            "index": index,
            "locked": index >= plot_count,
            "land_level": land_level,
            "crop_id": crop_id,
            "planted_at": planted_at,
            "ready_at": ready_at,
            "remaining_seconds": max(0, int(ready_at - now)) if ready_at is not None else None,
            "mature": bool(crop_id is not None and ready_at <= now),
        })
    return views


def _inventory_views(conn, username):
    views = []
    for item_id, quantity in inventory_rows(conn, username):
        info = item_info(item_id) or {"id": item_id, "name": item_id, "kind": "unknown",
                                      "sellable": False, "sell_price": None}
        views.append({**info, "quantity": quantity})
    return views


def _tool_views(conn, username, now):
    views = {}
    today = estate_day_key(now)
    for tool_type, level, durability, repair_day in conn.execute(
        "SELECT t.tool_type,t.level,t.durability,COALESCE(d.repair_day,'') "
        "FROM estate_tools t LEFT JOIN estate_tool_daily d "
        "ON d.username=t.username AND d.tool_type=t.tool_type WHERE t.username=?",
        (username,),
    ):
        rule = TOOLS.get(tool_type, {}).get(level, {})
        views[tool_type] = {"type": tool_type, "level": level,
                            "durability": durability,
                            "max_durability": rule.get("max_durability", durability),
                            "name": rule.get("name", tool_type),
                            "repair_available": tool_type != "pickaxe" or repair_day != today}
    return views


def _fishing_view(conn, username):
    row = conn.execute(
        "SELECT session_id,bait_id,rod_level,fish_id,pattern_json,started_at,expires_at "
        "FROM estate_fishing_sessions WHERE username=? AND status='active' "
        "ORDER BY started_at DESC LIMIT 1", (username,),
    ).fetchone()
    if not row:
        return None
    return {"session_id": row[0], "bait_id": row[1], "rod_level": row[2],
            "fish_name": "水下的鱼影", "pattern": json.loads(row[4]),
            "started_at": row[5], "expires_at": row[6],
            "duration_limit": FISHING_STEPS}


def _mining_view(conn, username):
    row = conn.execute(
        "SELECT run_id,mine_level,strikes_left,revealed_json,loot_json,board_json "
        "FROM estate_mining_runs WHERE username=? AND status='active' "
        "ORDER BY started_at DESC LIMIT 1", (username,),
    ).fetchone()
    if not row:
        return None
    revealed = json.loads(row[3])
    board = json.loads(row[5])
    return {"run_id": row[0], "mine_level": row[1],
            "mine_name": MINING_LEVELS[row[1]]["name"],
            "strikes_left": row[2], "revealed": revealed,
            "revealed_cells": {str(index): board[index] for index in revealed},
            "loot": json.loads(row[4]), "size": MINE_BOARD_SIZE}


def skin_state(conn, username):
    # Backfill collections already present in old saves. Collection credit is permanent.
    conn.execute("INSERT OR IGNORE INTO estate_collections SELECT username,item_id "
                 "FROM estate_inventory WHERE username=? AND item_id LIKE 'collectible:%'", (username,))
    owned = {"berry"} | {row[0] for row in conn.execute(
        "SELECT skin_id FROM estate_owned_skins WHERE username=?", (username,)) if row[0] in SKINS}
    collected = {row[0] for row in conn.execute(
        "SELECT item_id FROM estate_collections WHERE username=?", (username,))}
    reward = next(((key, rule) for key, rule in SKINS.items() if rule["unlock"] == "collection"), None)
    missing_skins, missing_collectibles = [], []
    if reward is not None:
        reward_id, rule = reward
        missing_skins = [key for key in rule["required_skins"] if key not in owned]
        missing_collectibles = [key for key in rule["required_collectibles"]
                                if collectible_item(key) not in collected]
        if not missing_skins and not missing_collectibles:
            conn.execute("INSERT OR IGNORE INTO estate_owned_skins VALUES (?,?)", (username, reward_id))
            owned.add(reward_id)
    return {"owned": sorted(owned), "missing_skins": missing_skins,
            "missing_collectibles": missing_collectibles}


def estate_state(conn, username, now):
    """组装客户端所需的完整快照；读取本身也幂等建档并清扫过期钓鱼局。"""
    now = int(now)
    ensure_estate(conn, username, now)
    normalize_collectibles(conn, username)
    sweep_expired_fishing(conn, username, now)
    refresh_daily_pickaxe(conn, username, now)
    fishing_daily = fishing_daily_state(conn, username, now)

    skins = skin_state(conn, username)
    profile = load_profile(conn, username)
    if profile["skin_id"] not in skins["owned"]:
        conn.execute("UPDATE estate_profiles SET skin_id='berry' WHERE username=?", (username,))
        profile["skin_id"] = "berry"
    used = inventory_used(conn, username)
    balance = conn.execute(
        "SELECT coins FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not balance:
        raise estate_error(("user_missing", "账号不存在"))

    return {
        "version": profile["version"],
        "server_time": now,
        "coins": round(float(balance[0] or 0), 2),
        "profile": {
            **profile,
            "username": username,
            "xp_next": xp_for_next(profile["level"]),
            "warehouse_capacity": capacity(profile),
            "warehouse_used": used + profile["reserved_capacity"],
            "warehouse_items": used,
            "warehouse_reserved": profile["reserved_capacity"],
        },
        "plots": _plot_views(conn, username, profile["plot_count"], now),
        "inventory": _inventory_views(conn, username),
        "tools": _tool_views(conn, username, now),
        "fishing_session": _fishing_view(conn, username),
        "fishing_daily": {key: fishing_daily[key] for key in ("retained", "remaining", "limit")},
        "mining_run": _mining_view(conn, username),
        "catalog": public_catalog(),
        "skins": skins,
    }
