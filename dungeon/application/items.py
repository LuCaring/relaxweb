"""Internal Beta equipment factory. The caller owns the write transaction."""

import json
import time
from uuid import uuid4

from dungeon.domain.errors import DungeonError
from dungeon.legacy.receipts import REQUEST_ID_PATTERN
from dungeon.storage.assets import bump_asset_revision


SOURCES = frozenset({"run_reward", "starter", "integration_fixture", "test"})


def create_item(conn, ruleset, owner_username, template_id, *, source,
                bound_reason=None, item_id=None, now=None):
    """Freeze one Ruleset weapon as an inventory item in the caller's transaction.

    This is an internal grant primitive, not a public client operation. A full bag
    routes the new item to pending. Trigger effects live in beta_effects_json;
    legacy effects_json remains empty because its parser expects passive effects.
    """
    if not conn.in_transaction:
        raise RuntimeError("create_item requires a caller-owned write transaction")
    if not isinstance(owner_username, str) or not owner_username:
        raise DungeonError("invalid_request", "装备所有者无效")
    if not isinstance(template_id, str) or not template_id:
        raise DungeonError("invalid_request", "装备模板无效")
    if not isinstance(source, str) or source not in SOURCES:
        raise DungeonError("invalid_request", "装备来源无效")
    if bound_reason is not None and (not isinstance(bound_reason, str) or not bound_reason or len(bound_reason) > 96):
        raise DungeonError("invalid_request", "绑定原因无效")
    item_id = uuid4().hex if item_id is None else item_id
    if not isinstance(item_id, str) or REQUEST_ID_PATTERN.fullmatch(item_id) is None:
        raise DungeonError("invalid_request", "装备编号无效")
    now = int(time.time()) if now is None else now
    if type(now) is not int or now < 0:
        raise DungeonError("invalid_request", "装备时间无效")
    user = conn.execute("SELECT id FROM users WHERE username=?", (owner_username,)).fetchone()
    if user is None:
        raise DungeonError("auth_required", "用户不存在")
    weapons = {weapon["id"]: weapon for weapon in ruleset.mutable_content("weapons")}
    weapon = weapons.get(template_id)
    if weapon is None:
        raise DungeonError("invalid_config", "装备模板不存在")
    effects = {effect["id"]: effect for effect in ruleset.mutable_content("effects")}
    try:
        frozen_effects = [effects[effect_id] for effect_id in weapon["effects"]]
    except KeyError as error:
        raise DungeonError("invalid_config", "装备效果不存在") from error
    trade_allowed = bool(weapon["trade"]["allowed"])
    if not trade_allowed and bound_reason is None:
        bound_reason = weapon["trade"].get("reason") or "template_policy"
    if source in ("starter", "test") and bound_reason is None:
        bound_reason = source
    conn.execute("""INSERT OR IGNORE INTO dungeon_profiles
        (username,starter_granted,bag_capacity,version,created_at,updated_at)
        VALUES (?,0,60,1,?,?)""", (owner_username, now, now))
    capacity = conn.execute("SELECT bag_capacity FROM dungeon_profiles WHERE username=?",
                            (owner_username,)).fetchone()[0]
    occupied = conn.execute("""SELECT COUNT(*) FROM dungeon_items
        WHERE owner=? AND location='bag'""", (owner_username,)).fetchone()[0]
    location = "bag" if occupied < capacity else "pending"
    encoded = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False)
    conn.execute("""INSERT INTO dungeon_items
        (item_id,owner,template_id,template_version,display_name,visual_id,slot,quality,
         stats_json,effects_json,location,created_at,beta_upgrade_level,beta_bound_reason,
         beta_source,beta_ruleset_id,beta_ruleset_hash,beta_effects_json,beta_trade_allowed)
        VALUES (?,?,?,?,?,?,?,?,?,'[]',?,?,0,?,?,?,?,?,?)""",
        (item_id, owner_username, template_id, ruleset.ruleset_id, weapon["name"],
         weapon["visual_id"], weapon["slot"], "normal", encoded(weapon["stats"]),
         location, now, bound_reason, source, ruleset.ruleset_id, ruleset.ruleset_hash,
         encoded(frozen_effects), int(trade_allowed)))
    revision = bump_asset_revision(conn, user[0])
    return {"item_id": item_id, "template_id": template_id,
            "template_version": ruleset.ruleset_id, "ruleset_id": ruleset.ruleset_id,
            "ruleset_hash": ruleset.ruleset_hash, "display_name": weapon["name"],
            "visual_id": weapon["visual_id"], "slot": weapon["slot"], "quality": "normal",
            "stats": dict(weapon["stats"]), "effects": frozen_effects, "upgrade_level": 0,
            "bound_reason": bound_reason, "source": source,
            "trade_allowed": trade_allowed, "location": location, "version": 1,
            "asset_revision": revision}
