"""装备写操作；调用者以 BEGIN IMMEDIATE 包裹动作和后续状态读取。"""

import json
import secrets

from dungeon.legacy.catalog import SLOTS
from dungeon.legacy.crafting import CURRENCIES, apply_currency
from dungeon.legacy.receipts import (REQUEST_ID_PATTERN, load_receipt,
                                     request_digest, save_receipt)
from dungeon.legacy.service import DungeonError, ensure_dungeon
from dungeon.storage.assets import ensure_available


ITEM_ID_PATTERN = REQUEST_ID_PATTERN
ACTION_TYPES = frozenset(("dungeon_equip", "dungeon_lock_item",
                          "dungeon_sell_item", "dungeon_claim_items",
                          "dungeon_use_currency"))


def _item_id(value):
    if not isinstance(value, str) or ITEM_ID_PATTERN.fullmatch(value) is None:
        raise DungeonError("invalid_request", "装备编号无效")
    return value


def normalize_payload(action_type, data):
    """只保留每种指令的业务字段，确定回执摘要与重试语义。"""
    if action_type == "dungeon_equip":
        slot = data.get("slot")
        if slot not in SLOTS:
            raise DungeonError("invalid_request", "装备部位无效")
        item_id = data.get("item_id")
        return {"slot": slot, "item_id": None if item_id is None else _item_id(item_id)}
    if action_type == "dungeon_lock_item":
        locked = data.get("locked")
        if not isinstance(locked, bool):
            raise DungeonError("invalid_request", "锁定状态无效")
        return {"item_id": _item_id(data.get("item_id")), "locked": locked}
    if action_type == "dungeon_sell_item":
        return {"item_id": _item_id(data.get("item_id"))}
    if action_type == "dungeon_claim_items":
        ids = data.get("item_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
            raise DungeonError("invalid_request", "领取清单无效")
        normalized = [_item_id(item_id) for item_id in ids]
        if len(normalized) != len(set(normalized)):
            raise DungeonError("invalid_request", "领取清单存在重复装备")
        return {"item_ids": sorted(normalized)}
    if action_type == "dungeon_use_currency":
        currency_id = data.get("currency_id")
        if currency_id not in CURRENCIES:
            raise DungeonError("invalid_request", "通货编号无效")
        return {"item_id": _item_id(data.get("item_id")),
                "currency_id": currency_id}
    raise DungeonError("invalid_request", "未知地下城操作")


def _owned_item(conn, username, item_id):
    row = conn.execute("""SELECT item_id,slot,location,locked,sell_coins
        FROM dungeon_items WHERE item_id=? AND owner=?""", (item_id, username)).fetchone()
    if row is None or row[2] == "sold":
        raise DungeonError("not_found", "装备不存在")
    return {"item_id": row[0], "slot": row[1], "location": row[2],
            "locked": bool(row[3]), "sell_coins": row[4]}


def _active_job(conn, username):
    return conn.execute("SELECT job_kind,job_id FROM dungeon_active_jobs WHERE username=?",
                        (username,)).fetchone()


def _equip(conn, username, payload):
    if _active_job(conn, username):
        raise DungeonError("active_job", "挑战或扫荡进行中，暂不能换装")
    slot, item_id = payload["slot"], payload["item_id"]
    current = conn.execute("SELECT item_id FROM dungeon_loadout WHERE username=? AND slot=?",
                           (username, slot)).fetchone()
    if item_id is not None:
        item = _owned_item(conn, username, item_id)
        if item["location"] != "bag" or item["slot"] != slot:
            raise DungeonError("item_unavailable", "装备部位或位置不适用")
        ensure_available(conn, username, item_id)
    if (current is not None and current[0] == item_id) or (current is None and item_id is None):
        return {"slot": slot, "item_id": item_id}, False
    if current is not None:
        ensure_available(conn, username, current[0])
    if item_id is None:
        conn.execute("DELETE FROM dungeon_loadout WHERE username=? AND slot=?", (username, slot))
    else:
        conn.execute("""INSERT INTO dungeon_loadout(username,slot,item_id) VALUES (?,?,?)
            ON CONFLICT(username,slot) DO UPDATE SET item_id=excluded.item_id""",
            (username, slot, item_id))
    return {"slot": slot, "item_id": item_id}, True


def _lock_item(conn, username, payload):
    item = _owned_item(conn, username, payload["item_id"])
    if item["locked"] == payload["locked"]:
        return {"item_id": item["item_id"], "locked": item["locked"]}, False
    ensure_available(conn, username, item["item_id"])
    conn.execute("""UPDATE dungeon_items SET locked=?,version=version+1
        WHERE item_id=? AND owner=?""", (int(payload["locked"]), item["item_id"], username))
    return {"item_id": item["item_id"], "locked": payload["locked"]}, True


def _snapshot_uses_item(conn, username, item_id):
    active = _active_job(conn, username)
    if not active:
        return False
    if active[0] != "battle":
        # Sweep content is introduced later; until its snapshot reader exists,
        # protect all owned gear during a sweep.
        return True
    row = conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=? AND username=?",
                       (active[1], username)).fetchone()
    if row is None:
        raise DungeonError("invalid_save", "活动挑战存档异常")
    try:
        equipment = json.loads(row[0])["equipment"]
        return any(entry.get("item_id") == item_id for entry in equipment)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise DungeonError("invalid_save", "活动挑战存档异常") from error


def _sell_item(conn, username, payload, adjust_coins):
    item = _owned_item(conn, username, payload["item_id"])
    if item["location"] != "bag" or item["locked"]:
        raise DungeonError("item_protected", "装备已锁定或尚未领取")
    if conn.execute("SELECT 1 FROM dungeon_loadout WHERE username=? AND item_id=?",
                    (username, item["item_id"])).fetchone():
        raise DungeonError("item_protected", "已穿戴装备不能出售")
    if _snapshot_uses_item(conn, username, item["item_id"]):
        raise DungeonError("item_protected", "活动挑战正在使用此装备")
    ensure_available(conn, username, item["item_id"])
    conn.execute("""UPDATE dungeon_items SET location='sold',version=version+1
        WHERE item_id=? AND owner=? AND location='bag'""", (item["item_id"], username))
    if item["sell_coins"]:
        balance = adjust_coins(conn, username, item["sell_coins"], "dungeon_sale",
                               "地下城装备出售", ref=f"dungeon:sale:{item['item_id']}")
    else:
        balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
    return {"item_id": item["item_id"], "coins_gained": item["sell_coins"],
            "coins": round(balance or 0, 2)}, True


def _claim_items(conn, username, payload):
    items = [_owned_item(conn, username, item_id) for item_id in payload["item_ids"]]
    if any(item["location"] != "pending" for item in items):
        raise DungeonError("item_unavailable", "领取清单包含非待领取装备")
    for item in items:
        ensure_available(conn, username, item["item_id"])
    capacity = conn.execute("SELECT bag_capacity FROM dungeon_profiles WHERE username=?",
                            (username,)).fetchone()[0]
    occupied = conn.execute("SELECT COUNT(*) FROM dungeon_items WHERE owner=? AND location='bag'",
                            (username,)).fetchone()[0]
    if occupied + len(items) > capacity:
        raise DungeonError("inventory_full", "装备背包空间不足")
    conn.executemany("""UPDATE dungeon_items SET location='bag',version=version+1
        WHERE item_id=? AND owner=? AND location='pending'""",
                     ((item["item_id"], username) for item in items))
    return {"item_ids": payload["item_ids"]}, True


def _use_currency(conn, username, payload):
    item_id, currency_id = payload["item_id"], payload["currency_id"]
    owned = _owned_item(conn, username, item_id)
    if owned["location"] != "bag" or owned["locked"]:
        raise DungeonError("item_protected", "装备已锁定或尚未领取")
    if _active_job(conn, username):
        raise DungeonError("active_job", "挑战或扫荡进行中，暂不能改造装备")
    ensure_available(conn, username, item_id)
    row = conn.execute("""SELECT quality,stats_json,affixes_json,item_level,slot
        FROM dungeon_items WHERE owner=? AND item_id=?""", (username, item_id)).fetchone()
    balance = conn.execute("""SELECT amount FROM dungeon_currency
        WHERE username=? AND currency_id=?""", (username, currency_id)).fetchone()
    if balance is None or balance[0] < 1:
        raise DungeonError("insufficient_currency", "通货数量不足")
    item = {"quality": row[0], "stats": json.loads(row[1]),
            "affixes": json.loads(row[2]), "item_level": row[3], "slot": row[4]}
    crafted = apply_currency(item, currency_id, secrets.randbelow)
    conn.execute("""UPDATE dungeon_currency SET amount=amount-1
        WHERE username=? AND currency_id=?""", (username, currency_id))
    conn.execute("""UPDATE dungeon_items SET quality=?,stats_json=?,affixes_json=?,
        version=version+1 WHERE item_id=? AND owner=?""",
        (crafted["quality"], json.dumps(crafted["stats"], ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":")),
         json.dumps(crafted["affixes"], ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")), item_id, username))
    return {"item_id": item_id, "currency_id": currency_id,
            "quality": crafted["quality"], "affixes": crafted["affixes"],
            "stats": crafted["stats"], "currency_remaining": balance[0] - 1}, True


def run_dungeon_action(conn, username, request_id, action_type, payload,
                       expected_version, now, adjust_coins):
    """请求回执、版本校验、装备与金币写入在同一调用方事务中完成。"""
    if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise DungeonError("invalid_request", "请求编号无效")
    if action_type not in ACTION_TYPES:
        raise DungeonError("invalid_request", "未知地下城操作")
    payload = normalize_payload(action_type, payload)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise DungeonError("invalid_request", "存档版本无效")
    ensure_dungeon(conn, username, now)
    digest = request_digest(action_type, payload, expected_version=expected_version)
    existing = load_receipt(conn, username, request_id, action_type, digest)
    if existing is not None:
        return existing
    version = conn.execute("SELECT version FROM dungeon_profiles WHERE username=?",
                           (username,)).fetchone()[0]
    if expected_version != version:
        raise DungeonError("version_conflict", "地下城存档已更新，请刷新后重试")
    if action_type == "dungeon_equip":
        result, changed = _equip(conn, username, payload)
    elif action_type == "dungeon_lock_item":
        result, changed = _lock_item(conn, username, payload)
    elif action_type == "dungeon_sell_item":
        result, changed = _sell_item(conn, username, payload, adjust_coins)
    elif action_type == "dungeon_use_currency":
        result, changed = _use_currency(conn, username, payload)
    else:
        result, changed = _claim_items(conn, username, payload)
    if changed:
        conn.execute("""UPDATE dungeon_profiles SET version=version+1,updated_at=?
            WHERE username=?""", (int(now), username))
        version += 1
    stored = {**result, "request_id": request_id, "profile_version": version,
              "changed": changed, "replayed": False}
    save_receipt(conn, username, request_id, action_type, digest, stored, now)
    return stored
