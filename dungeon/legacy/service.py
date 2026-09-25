"""地下城基础读模型和一次性开档；调用者持有写事务。"""

import json
from uuid import uuid4

from dungeon.legacy.catalog import CATALOG, SLOTS, prerequisite_key, public_catalog
from dungeon.legacy.effects import resolve_stats
from dungeon.legacy.crafting import CURRENCIES, probability_table, public_rules
from dungeon.domain.errors import DungeonError


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
    changed = not granted
    if not granted:
        starters = [item for item in catalog["items"] if item["template_id"].startswith("starter_")]
        for template in starters:
            item_id = uuid4().hex
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,display_name,visual_id,
                 slot,quality,stats_json,tags_json,effects_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, username, template["template_id"], catalog["config_version"],
                 template["name"], template.get("visual_id", template["template_id"]),
                 template["slot"], template["quality"], _json(template["stats"]),
                 _json(template["tags"]), _json(template["effects"]), int(now)))
            conn.execute("INSERT INTO dungeon_loadout(username,slot,item_id) VALUES (?,?,?)",
                         (username, template["slot"], item_id))
    # Freeze display fields on pre-upgrade items while their template still exists.
    missing_display = conn.execute("""SELECT item_id,template_id FROM dungeon_items
        WHERE owner=? AND (display_name IS NULL OR visual_id IS NULL)""", (username,)).fetchall()
    if missing_display:
        templates = {item["template_id"]: item for item in catalog["items"]}
        for item_id, template_id in missing_display:
            template = templates.get(template_id)
            if template is not None:
                conn.execute("""UPDATE dungeon_items SET display_name=COALESCE(display_name,?),
                    visual_id=COALESCE(visual_id,?) WHERE item_id=?""",
                    (template["name"], template.get("visual_id", template_id), item_id))
    progress = {(row[0], row[1]): (row[2], bool(row[3])) for row in conn.execute(
        """SELECT challenge_id,difficulty_id,clear_count,unlocked FROM dungeon_progress
        WHERE username=?""", (username,))}
    for challenge in catalog["challenges"]:
        key = challenge["challenge_id"], challenge["difficulty_id"]
        unlocked = all(progress.get(prerequisite_key(required, key[1]), (0, False))[0] > 0
                       for required in challenge["requires"])
        if key not in progress:
            conn.execute("""INSERT INTO dungeon_progress
                (username,challenge_id,difficulty_id,unlocked) VALUES (?,?,?,?)""",
                (username, *key, int(unlocked)))
            progress[key] = (0, unlocked)
            changed = True
        elif unlocked and not progress[key][1]:
            conn.execute("""UPDATE dungeon_progress SET unlocked=1
                WHERE username=? AND challenge_id=? AND difficulty_id=?""", (username, *key))
            progress[key] = (progress[key][0], True)
            changed = True
    if changed:
        conn.execute("""UPDATE dungeon_profiles
            SET starter_granted=1,version=version+1,updated_at=? WHERE username=?""",
            (int(now), username))


def dungeon_state(conn, username, now, catalog=CATALOG):
    ensure_dungeon(conn, username, now, catalog)
    profile = conn.execute("""SELECT bag_capacity,version FROM dungeon_profiles
        WHERE username=?""", (username,)).fetchone()
    templates = {item["template_id"]: item for item in catalog["items"]}
    rows = conn.execute("""SELECT item_id,template_id,template_version,slot,quality,
        stats_json,tags_json,effects_json,affixes_json,sell_coins,locked,location,version,
        display_name,visual_id,item_level FROM dungeon_items
        WHERE owner=? AND location<>'sold' ORDER BY created_at,item_id""", (username,)).fetchall()
    items = []
    for row in rows:
        template = templates.get(row[1])
        items.append({"item_id": row[0], "template_id": row[1],
                      "name": row[13] or (template["name"] if template else row[1]),
                      "visual_id": row[14] or (template.get("visual_id", row[1]) if template else row[1]),
                      "template_version": row[2], "slot": row[3], "quality": row[4],
                      "stats": json.loads(row[5]), "tags": json.loads(row[6]),
                      "effects": json.loads(row[7]), "affixes": json.loads(row[8]),
                      "sell_coins": row[9], "locked": bool(row[10]),
                      "location": row[11], "version": row[12], "item_level": row[15]})
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
    currencies = {currency_id: amount for currency_id, amount in conn.execute(
        "SELECT currency_id,amount FROM dungeon_currency WHERE username=? AND amount>0",
        (username,))}
    return {"phase": "battle" if active else "equipment", "profile_version": profile[1],
            "bag_capacity": profile[0], "catalog": public_catalog(catalog),
            "items": items, "loadout": loadout, "stats": panel,
            "currencies": currencies, "crafting_rules": public_rules(),
            "progress": progress, "pending_count": sum(item["location"] == "pending" for item in items),
            "active_job": {"kind": active[0], "id": active[1]} if active else None,
            "coins": round(coins or 0, 2),
            "available_actions": ["dungeon_equip", "dungeon_lock_item", "dungeon_sell_item",
                                  "dungeon_claim_items", "dungeon_use_currency",
                                  "dungeon_compare_item", "dungeon_affix_probabilities",
                                  "dungeon_start", "dungeon_sync", "dungeon_control",
                                  "dungeon_get_result"]}


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


def affix_probabilities(conn, username, item_id, now, catalog=CATALOG,
                        currency_id=None):
    state = dungeon_state(conn, username, now, catalog)
    item = next((entry for entry in state["items"] if entry["item_id"] == item_id), None)
    if item is None:
        raise DungeonError("not_found", "装备不存在")
    kind = None
    if currency_id is not None:
        if currency_id not in CURRENCIES:
            raise DungeonError("invalid_request", "通货编号无效")
        rarity, affixes = item["quality"], item["affixes"]
        if currency_id == "transmutation" and rarity == "normal" and not affixes:
            pass
        elif currency_id == "augmentation" and rarity == "excellent" and len(affixes) == 1:
            kind = "suffix" if affixes[0]["kind"] == "prefix" else "prefix"
        elif currency_id == "regal" and rarity == "excellent" and 1 <= len(affixes) <= 2:
            pass
        elif currency_id == "exalted" and rarity == "rare" and len(affixes) < 6:
            pass
        else:
            raise DungeonError("currency_unavailable", "该通货没有单次新增词条的概率表")
    pool = probability_table(item["slot"], item["item_level"], item["affixes"], kind=kind)
    return {"item_id": item_id, "item_level": item["item_level"],
            "affix_version": state["crafting_rules"]["version"],
            "probability_model": "game_config_weighted", "currency_id": currency_id,
            "probability_scope": "single_add" if currency_id else "generic_add",
            "entries": pool}
