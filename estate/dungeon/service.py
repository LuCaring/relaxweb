"""地下城基础读模型和一次性开档；调用者持有写事务。"""

import json
from uuid import uuid4

from estate.dungeon.catalog import CATALOG, SLOTS, public_catalog
from estate.dungeon.effects import resolve_stats


class DungeonError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ensure_dungeon(conn, username, now, catalog=CATALOG):
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is None:
        raise DungeonError("auth_required", "请先登录")
    conn.execute("""INSERT OR IGNORE INTO dungeon_profiles
        (username,starter_granted,bag_capacity,version,created_at,updated_at)
        VALUES (?,0,60,1,?,?)""", (username, int(now), int(now)))
    granted = conn.execute("SELECT starter_granted FROM dungeon_profiles WHERE username=?",
                           (username,)).fetchone()[0]
    if granted:
        return
    starters = [item for item in catalog["items"] if item["template_id"].startswith("starter_")]
    for template in starters:
        item_id = uuid4().hex
        conn.execute("""INSERT INTO dungeon_items
            (item_id,owner,template_id,template_version,slot,quality,stats_json,tags_json,effects_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (item_id, username, template["template_id"], catalog["config_version"],
             template["slot"], template["quality"], _json(template["stats"]),
             _json(template["tags"]), _json(template["effects"]), int(now)))
        conn.execute("INSERT INTO dungeon_loadout(username,slot,item_id) VALUES (?,?,?)",
                     (username, template["slot"], item_id))
    for challenge in catalog["challenges"]:
        if not challenge["requires"]:
            conn.execute("""INSERT OR IGNORE INTO dungeon_progress
                (username,challenge_id,difficulty_id,unlocked) VALUES (?,?,?,1)""",
                (username, challenge["challenge_id"], challenge["difficulty_id"]))
    conn.execute("""UPDATE dungeon_profiles
        SET starter_granted=1,version=version+1,updated_at=? WHERE username=?""",
        (int(now), username))


def dungeon_state(conn, username, now, catalog=CATALOG):
    ensure_dungeon(conn, username, now, catalog)
    profile = conn.execute("""SELECT bag_capacity,version FROM dungeon_profiles
        WHERE username=?""", (username,)).fetchone()
    templates = {item["template_id"]: item for item in catalog["items"]}
    rows = conn.execute("""SELECT item_id,template_id,template_version,slot,quality,
        stats_json,tags_json,effects_json,affixes_json,sell_coins,locked,location,version FROM dungeon_items
        WHERE owner=? AND location<>'sold' ORDER BY created_at,item_id""", (username,)).fetchall()
    items = []
    for row in rows:
        template = templates.get(row[1])
        items.append({"item_id": row[0], "template_id": row[1], "name": template["name"] if template else row[1],
                      "template_version": row[2], "slot": row[3], "quality": row[4],
                      "stats": json.loads(row[5]), "tags": json.loads(row[6]),
                      "effects": json.loads(row[7]), "affixes": json.loads(row[8]),
                      "sell_coins": row[9], "locked": bool(row[10]),
                      "location": row[11], "version": row[12]})
    loadout = dict(conn.execute("SELECT slot,item_id FROM dungeon_loadout WHERE username=?",
                                (username,)).fetchall())
    by_id = {item["item_id"]: item for item in items}
    equipped = []
    for slot in SLOTS:
        item_id = loadout.get(slot)
        if item_id is None:
            continue
        item = by_id.get(item_id)
        if item is None or item["slot"] != slot or item["location"] != "bag":
            raise DungeonError("invalid_save", "穿戴存档异常")
        equipped.append(item)
    panel = resolve_stats(catalog["base_stats"], equipped)
    progress = [{"challenge_id": row[0], "difficulty_id": row[1],
                 "clear_count": row[2], "unlocked": bool(row[3])}
                for row in conn.execute("""SELECT challenge_id,difficulty_id,clear_count,unlocked
                    FROM dungeon_progress WHERE username=? ORDER BY challenge_id,difficulty_id""", (username,))]
    active = conn.execute("SELECT job_kind,job_id FROM dungeon_active_jobs WHERE username=?",
                          (username,)).fetchone()
    coins = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
    return {"phase": "equipment", "profile_version": profile[1],
            "bag_capacity": profile[0], "catalog": public_catalog(catalog),
            "items": items, "loadout": loadout, "stats": panel,
            "progress": progress, "pending_count": sum(item["location"] == "pending" for item in items),
            "active_job": {"kind": active[0], "id": active[1]} if active else None,
            "coins": round(coins or 0, 2),
            "available_actions": ["dungeon_equip", "dungeon_lock_item", "dungeon_sell_item",
                                  "dungeon_claim_items", "dungeon_compare_item"]}


def compare_item(conn, username, item_id, now, catalog=CATALOG):
    """服务器计算换装差值；不改变装备与版本。"""
    state = dungeon_state(conn, username, now, catalog)
    item = next((entry for entry in state["items"]
                 if entry["item_id"] == item_id and entry["location"] == "bag"), None)
    if item is None:
        raise DungeonError("not_found", "装备不存在")
    equipped = [entry for entry in state["items"]
                if entry["item_id"] in state["loadout"].values()
                and entry["slot"] != item["slot"]]
    equipped.append(item)
    preview = resolve_stats(catalog["base_stats"], equipped)
    current = state["stats"]["values"]
    return {"item_id": item_id, "slot": item["slot"],
            "profile_version": state["profile_version"],
            "current": current, "preview": preview,
            "delta": {stat: value - current[stat] for stat, value in preview["values"].items()}}
