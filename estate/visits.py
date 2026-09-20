"""休闲庄园多人拜访、偷菜和离线记录领域服务。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from estate.catalog import CROPS, crop_item, public_catalog
from estate.store import (
    CROP_DATA_BROKEN, bump_version, change_inventory,
    estate_error, load_profile, plot_index, require_capacity,
    run_action,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
VISIT_UNLOCK_LEVEL = 3
VISITOR_DAILY_LIMIT = 3
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


def public_estate_state(conn, visitor, owner, now):
    _require_visit_level(conn, visitor)
    if not isinstance(owner, str) or not owner.strip() or owner.lower() == visitor.lower():
        raise estate_error(("invalid_visit", "不能拜访自己的庄园"))
    owner = owner.strip()
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
        "SELECT COUNT(*) FROM estate_thefts WHERE owner_username=? AND steal_day=?",
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
                    "plot_count": owner_profile["plot_count"]},
        "plots": plots, "catalog": public_catalog(),
        "steal_limits": {
            "visitor_remaining": max(0, VISITOR_DAILY_LIMIT - pair_used),
            "owner_remaining": max(0, OWNER_DAILY_LIMIT - owner_used),
        },
    }


def steal_crop(conn, visitor, request_id, owner, plot_id, now):
    owner = str(owner or "").strip()
    index = plot_index(plot_id)
    payload = {"owner": owner, "plot_id": index}

    def mutate():
        visitor_profile = _require_visit_level(conn, visitor)
        if not owner or owner.lower() == visitor.lower():
            raise estate_error(("invalid_visit", "不能偷自己的作物"))
        owner_profile = load_profile(conn, owner)
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
            raise estate_error(("visitor_limit", "今日已从该庄园偷满 3 块"))
        owner_used = conn.execute(
            "SELECT COUNT(*) FROM estate_thefts WHERE owner_username=? AND steal_day=?",
            (owner, today),
        ).fetchone()[0]
        if owner_used >= OWNER_DAILY_LIMIT:
            raise estate_error(("owner_protected", "该庄园今日已被偷满 6 块"))
        quantity = int(crop["yield"])
        require_capacity(conn, visitor, visitor_profile, quantity)
        changed = conn.execute(
            "UPDATE estate_plots SET crop_id=NULL,planted_at=NULL,ready_at=NULL "
            "WHERE username=? AND plot_index=? AND crop_id=? AND ready_at<=?",
            (owner, index, row[0], int(now)),
        ).rowcount
        if changed != 1:
            raise estate_error(("plot_changed", "庄园状态已变化，请刷新后重试"))
        change_inventory(conn, visitor, crop_item(row[0]), quantity)
        conn.execute(
            "INSERT INTO estate_thefts(owner_username,visitor_username,plot_index,crop_id,"
            "quantity,steal_day,created_at,request_id) VALUES (?,?,?,?,?,?,?,?)",
            (owner, visitor, index, row[0], quantity, today, int(now), request_id),
        )
        bump_version(conn, owner, now)
        return {"action": "steal_crop", "owner_username": owner,
                "plot_id": index, "crop_id": row[0], "crop_name": crop["name"],
                "quantity": quantity,
                "visitor_remaining": VISITOR_DAILY_LIMIT - pair_used - 1,
                "owner_remaining": OWNER_DAILY_LIMIT - owner_used - 1}

    return run_action(conn, visitor, request_id, "steal_crop", payload, now, mutate)


def notifications(conn, owner, now):
    cutoff = int(now) - NOTIFICATION_RETENTION_SECONDS
    conn.execute("DELETE FROM estate_thefts WHERE created_at<?", (cutoff,))
    rows = conn.execute(
        "SELECT id,visitor_username,crop_id,quantity,created_at,read_at "
        "FROM estate_thefts WHERE owner_username=? ORDER BY created_at DESC LIMIT 100",
        (owner,),
    ).fetchall()
    return [{"id": row[0], "visitor_username": row[1], "crop_id": row[2],
             "crop_name": CROPS.get(row[2], {}).get("name", row[2]),
             "quantity": row[3], "created_at": row[4], "read": row[5] is not None}
            for row in rows]


def mark_notifications_read(conn, owner, ids, now):
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
            (int(now), owner, *valid),
        )
    return {"marked": len(valid)}
