"""休闲庄园多人拜访、偷菜和离线记录领域服务。"""
from datetime import datetime
import secrets
from zoneinfo import ZoneInfo

from estate.catalog import (CROPS, PET_LEVELS, FERTILIZER_ITEM,
                            FERTILIZER_SECONDS, public_catalog)
from estate.farming import harvest_item
from estate.store import (
    CROP_DATA_BROKEN, bump_version, change_inventory,
    estate_error, load_profile, plot_index, require_capacity,
    run_action,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
VISIT_UNLOCK_LEVEL = 3
VISITOR_DAILY_LIMIT = 2
OWNER_DAILY_LIMIT = 6
NOTIFICATION_RETENTION_SECONDS = 30 * 24 * 60 * 60


def day_key(now):
    return datetime.fromtimestamp(int(now), SHANGHAI).date().isoformat()


def _require_visit_level(conn, username):
    profile = load_profile(conn, username)
    if profile["level"] < VISIT_UNLOCK_LEVEL:
        raise estate_error(("visit_level_locked", "庄园 3 级才能拜访和偷菜"))
    return profile


def list_estates(conn, username, query="", now=0):
    _require_visit_level(conn, username)
    query = str(query or "").strip()[:30]
    pattern = f"%{query}%"
    rows = conn.execute(
        "SELECT p.username, COUNT(CASE WHEN l.crop_id IS NOT NULL AND l.ready_at<=? "
        "THEN 1 END) AS mature FROM estate_profiles p "
        "LEFT JOIN estate_plots l ON l.username=p.username "
        "WHERE p.username<>? AND p.username LIKE ? "
        "GROUP BY p.username ORDER BY p.username COLLATE NOCASE LIMIT 100",
        (int(now), username, pattern),
    ).fetchall()
    return [{"username": row[0], "mature_plots": int(row[1] or 0)} for row in rows]


def public_estate_state(conn, visitor, owner, now, adjust_coins=None):
    _require_visit_level(conn, visitor)
    if not isinstance(owner, str) or not owner.strip() or owner.lower() == visitor.lower():
        raise estate_error(("invalid_visit", "不能拜访自己的庄园"))
    owner = owner.strip()
    from estate.farming import auto_harvest_penguin
    auto_harvest_penguin(conn, owner, now, adjust_coins)
    owner_profile = load_profile(conn, owner)
    rows = conn.execute(
        "SELECT plot_index,land_level,crop_id,planted_at,ready_at FROM estate_plots "
        "WHERE username=? ORDER BY plot_index", (owner,),
    ).fetchall()
    today = day_key(now)
    pair_used = conn.execute(
        "SELECT COUNT(*) FROM estate_thefts WHERE visitor_username=? "
        "AND owner_username=? AND steal_day=?", (visitor, owner, today),
    ).fetchone()[0]
    owner_used = conn.execute(
        "SELECT COUNT(*) FROM estate_thefts WHERE owner_username=? AND steal_day=? "
        "AND outcome='stolen'",
        (owner, today),
    ).fetchone()[0]
    plots = []
    for index, land_level, crop_id, planted_at, ready_at in rows:
        plots.append({
            "index": index, "land_level": land_level,
            "locked": index >= owner_profile["plot_count"], "crop_id": crop_id,
            "planted_at": planted_at, "ready_at": ready_at,
        })
    return {
        "owner_username": owner,
        "server_time": int(now),
        "profile": {"username": owner, "level": owner_profile["level"],
                    "plot_count": owner_profile["plot_count"],
                    "pet_level": owner_profile["pet_level"],
                    "penguin_level": owner_profile["penguin_level"],
                    "active_pet": owner_profile["active_pet"]},
        "plots": plots, "catalog": public_catalog(),
        "steal_limits": {
            "visitor_remaining": max(0, VISITOR_DAILY_LIMIT - pair_used),
            "owner_remaining": max(0, OWNER_DAILY_LIMIT - owner_used),
        },
    }


def fertilize(conn, username, request_id, owner, plot_id, now):
    """消耗自己的化肥，为本人或正在拜访的庄园缩短一小时成熟时间。"""
    owner = str(owner or username).strip()
    index = plot_index(plot_id)

    def mutate():
        if owner.lower() != username.lower():
            _require_visit_level(conn, username)
        profile = load_profile(conn, owner)
        if index >= profile["plot_count"]:
            raise estate_error(("plot_locked", "这块土地尚未解锁"))
        row = conn.execute(
            "SELECT crop_id,ready_at FROM estate_plots WHERE username=? AND plot_index=?",
            (owner, index),
        ).fetchone()
        if not row or not row[0]:
            raise estate_error(("plot_empty", "土地上没有作物"))
        if row[1] <= int(now):
            raise estate_error(("crop_mature", "作物已经成熟，无需施肥"))
        change_inventory(conn, username, FERTILIZER_ITEM, -1)
        ready_at = max(int(now), row[1] - FERTILIZER_SECONDS)
        conn.execute(
            "UPDATE estate_plots SET ready_at=? WHERE username=? AND plot_index=?",
            (ready_at, owner, index),
        )
        if owner.lower() != username.lower():
            bump_version(conn, owner, now)
        return {"action": "fertilize", "owner_username": owner,
                "plot_id": index, "crop_id": row[0], "ready_at": ready_at,
                "seconds_reduced": row[1] - ready_at}

    return run_action(conn, username, request_id, "fertilize",
                      {"owner_username": owner, "plot_id": index}, now, mutate)


def steal_crop(conn, visitor, request_id, owner, plot_id, now,
               adjust_coins=None, random_int=None):
    owner = str(owner or "").strip()
    index = plot_index(plot_id)
    payload = {"owner": owner, "plot_id": index}

    def mutate():
        visitor_profile = _require_visit_level(conn, visitor)
        if not owner or owner.lower() == visitor.lower():
            raise estate_error(("invalid_visit", "不能偷自己的作物"))
        owner_profile = load_profile(conn, owner)
        from estate.farming import auto_harvest_penguin
        auto_harvest_penguin(conn, owner, now, adjust_coins)
        if index >= owner_profile["plot_count"]:
            raise estate_error(("plot_locked", "土地尚未解锁"))
        row = conn.execute(
            "SELECT crop_id,ready_at FROM estate_plots WHERE username=? AND plot_index=?",
            (owner, index),
        ).fetchone()
        if not row or not row[0]:
            raise estate_error(("plot_empty", "土地上没有可偷的作物"))
        if int(row[1]) > int(now):
            raise estate_error(("crop_growing", "作物还没有成熟"))
        crop = CROPS.get(row[0])
        if not crop:
            raise estate_error(CROP_DATA_BROKEN)
        today = day_key(now)
        pair_used = conn.execute(
            "SELECT COUNT(*) FROM estate_thefts WHERE visitor_username=? "
            "AND owner_username=? AND steal_day=?", (visitor, owner, today),
        ).fetchone()[0]
        if pair_used >= VISITOR_DAILY_LIMIT:
            raise estate_error(("visitor_limit", "今日已从该庄园尝试偷取 2 块"))
        owner_used = conn.execute(
            "SELECT COUNT(*) FROM estate_thefts WHERE owner_username=? AND steal_day=? "
            "AND outcome='stolen'",
            (owner, today),
        ).fetchone()[0]
        if owner_used >= OWNER_DAILY_LIMIT:
            raise estate_error(("owner_protected", "该庄园今日已被偷满 6 块"))
        quantity = int(crop["yield"])
        pet_level = int(owner_profile.get("pet_level", 0)) if owner_profile["active_pet"] == "doudou" else 0
        roll = random_int or (lambda low, high: low + secrets.randbelow(high - low + 1))
        defended = pet_level > 0 and roll(1, 100) <= round(
            PET_LEVELS[pet_level]["defend_chance"] * 100)
        if defended:
            if adjust_coins is None:
                raise RuntimeError("pet defense requires coin adjustment")
            balance = conn.execute("SELECT coins FROM users WHERE username=?", (visitor,)).fetchone()
            available = max(0, int(float(balance[0] or 0))) if balance else 0
            dropped = min(available, roll(1, 1000))
            if dropped:
                adjust_coins(conn, visitor, -dropped, "estate_pet_defense",
                             f"偷菜失败，被{owner}的豆豆发现", ref=f"estate:{request_id}:drop")
                adjust_coins(conn, owner, dropped, "estate_pet_defense",
                             f"豆豆阻止{visitor}偷菜", ref=f"estate:{request_id}:guard")
            conn.execute(
                "INSERT INTO estate_thefts(owner_username,visitor_username,plot_index,crop_id,"
                "quantity,steal_day,created_at,request_id,outcome,coins_dropped) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (owner, visitor, index, row[0], quantity, today, int(now), request_id,
                 "defended", dropped),
            )
            bump_version(conn, owner, now)
            return {"action": "steal_crop", "outcome": "defended",
                    "owner_username": owner, "plot_id": index, "crop_id": row[0],
                    "crop_name": crop["name"], "quantity": 0, "coins_dropped": dropped,
                    "pet_level": pet_level,
                    "visitor_remaining": VISITOR_DAILY_LIMIT - pair_used - 1,
                    "owner_remaining": OWNER_DAILY_LIMIT - owner_used}
        require_capacity(conn, visitor, visitor_profile, quantity)
        changed = conn.execute(
            "UPDATE estate_plots SET crop_id=NULL,planted_at=NULL,ready_at=NULL "
            "WHERE username=? AND plot_index=? AND crop_id=? AND ready_at<=?",
            (owner, index, row[0], int(now)),
        ).rowcount
        if changed != 1:
            raise estate_error(("plot_changed", "庄园状态已变化，请刷新后重试"))
        change_inventory(conn, visitor, harvest_item(row[0]), quantity)
        conn.execute(
            "INSERT INTO estate_thefts(owner_username,visitor_username,plot_index,crop_id,"
            "quantity,steal_day,created_at,request_id,outcome,coins_dropped) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (owner, visitor, index, row[0], quantity, today, int(now), request_id,
             "stolen", 0),
        )
        bump_version(conn, owner, now)
        return {"action": "steal_crop", "owner_username": owner,
                "plot_id": index, "crop_id": row[0], "crop_name": crop["name"],
                "quantity": quantity, "outcome": "stolen", "coins_dropped": 0,
                "visitor_remaining": VISITOR_DAILY_LIMIT - pair_used - 1,
                "owner_remaining": OWNER_DAILY_LIMIT - owner_used - 1}

    return run_action(conn, visitor, request_id, "steal_crop", payload, now, mutate)


def notifications(conn, username, now):
    cutoff = int(now) - NOTIFICATION_RETENTION_SECONDS
    conn.execute("DELETE FROM estate_thefts WHERE created_at<?", (cutoff,))
    rows = conn.execute(
        "SELECT id,owner_username,visitor_username,crop_id,quantity,created_at,read_at,"
        "outcome,coins_dropped,visitor_read_at FROM estate_thefts "
        "WHERE owner_username=? OR visitor_username=? ORDER BY created_at DESC LIMIT 100",
        (username, username),
    ).fetchall()
    return [{"id": row[0], "owner_username": row[1], "visitor_username": row[2],
             "crop_id": row[3], "crop_name": CROPS.get(row[3], {}).get("name", row[3]),
             "quantity": row[4], "created_at": row[5], "outcome": row[7],
             "coins_dropped": row[8], "role": "owner" if row[1].lower() == username.lower() else "visitor",
             "read": (row[6] if row[1].lower() == username.lower() else row[9]) is not None}
            for row in rows]


def mark_notifications_read(conn, username, ids, now):
    valid = []
    for value in ids if isinstance(ids, list) else []:
        try:
            valid.append(int(value))
        except (TypeError, ValueError):
            continue
    if valid:
        placeholders = ",".join("?" for _ in valid)
        conn.execute(
            f"UPDATE estate_thefts SET read_at=? WHERE owner_username=? "
            f"AND id IN ({placeholders}) AND read_at IS NULL",
            (int(now), username, *valid),
        )
        conn.execute(
            f"UPDATE estate_thefts SET visitor_read_at=? WHERE visitor_username=? "
            f"AND id IN ({placeholders}) AND visitor_read_at IS NULL",
            (int(now), username, *valid),
        )
    return {"marked": len(valid)}
