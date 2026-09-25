"""Consistent, read-only Beta camp snapshot."""

import json

from dungeon.application.wallet import SharedWalletPort
from dungeon.domain.errors import DungeonError


def _decoded(raw, expected, label):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise DungeonError("invalid_save", "%s存档异常" % label) from error
    if not isinstance(value, expected):
        raise DungeonError("invalid_save", "%s存档异常" % label)
    return value


class StateService:
    def __init__(self, database, wallet=None):
        self.database = database
        self.wallet = wallet or SharedWalletPort()

    def get_state(self, user_id):
        if type(user_id) is not int or user_id <= 0:
            raise DungeonError("invalid_request", "用户编号无效")
        with self.database() as conn:
            # BEGIN pins all queries to one SQLite snapshot. ROLLBACK releases the
            # read transaction without invoking legacy's mutating ensure_dungeon.
            conn.execute("BEGIN")
            try:
                result = self._read(conn, user_id)
            finally:
                conn.rollback()
            return result

    def _read(self, conn, user_id):
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            raise DungeonError("auth_required", "用户不存在")
        username = user[0]
        profile = conn.execute("""SELECT bag_capacity,version FROM dungeon_profiles
            WHERE username=?""", (username,)).fetchone()
        revision = conn.execute("""SELECT revision FROM dungeon_beta_asset_revisions
            WHERE user_id=?""", (user_id,)).fetchone()
        reservations = {row[0]: {"purpose": row[1], "ref": row[2]} for row in conn.execute(
            """SELECT asset_id,purpose,reservation_ref FROM dungeon_asset_reservations
               WHERE owner_user_id=? AND asset_type='item'""", (user_id,))}
        items = []
        for row in conn.execute("""SELECT item_id,template_id,template_version,display_name,
            visual_id,slot,quality,stats_json,tags_json,effects_json,affixes_json,sell_coins,
            locked,location,version,beta_upgrade_level,beta_bound_reason,beta_source,
            beta_ruleset_id,beta_ruleset_hash,beta_effects_json,beta_trade_allowed
            FROM dungeon_items WHERE owner=? AND location<>'sold'
            ORDER BY created_at,item_id""", (username,)):
            item = {"item_id": row[0], "template_id": row[1], "template_version": row[2],
                    "display_name": row[3] or row[1], "visual_id": row[4] or row[1],
                    "slot": row[5], "quality": row[6], "stats": _decoded(row[7], dict, "装备属性"),
                    "tags": _decoded(row[8], list, "装备标签"),
                    "legacy_effects": _decoded(row[9], list, "旧装备效果"),
                    "affixes": _decoded(row[10], list, "装备词缀"), "sell_coins": row[11],
                    "locked": bool(row[12]), "location": row[13], "version": row[14],
                    "upgrade_level": row[15], "bound_reason": row[16], "source": row[17],
                    "ruleset_id": row[18], "ruleset_hash": row[19],
                    "effects": _decoded(row[20], list, "Beta装备效果") if row[20] is not None else [],
                    "template_trade_allowed": bool(row[21]) if row[21] is not None else None,
                    "reservation": reservations.get(row[0])}
            items.append(item)
        loadout = dict(conn.execute("""SELECT slot,item_id FROM dungeon_loadout
            WHERE username=?""", (username,)).fetchall())
        equipped_ids = set(loadout.values())
        legacy_job = conn.execute("""SELECT job_kind,job_id FROM dungeon_active_jobs
            WHERE username=?""", (username,)).fetchone()
        legacy_in_use = set()
        legacy_blocks_all = bool(legacy_job and legacy_job[0] != "battle")
        if legacy_job and legacy_job[0] == "battle":
            legacy_run = conn.execute("""SELECT snapshot_json FROM dungeon_runs
                WHERE battle_id=? AND username=?""", (legacy_job[1], username)).fetchone()
            if legacy_run is None:
                raise DungeonError("invalid_save", "活动挑战存档异常")
            snapshot = _decoded(legacy_run[0], dict, "活动挑战")
            equipment = snapshot.get("equipment")
            if not isinstance(equipment, list) or any(not isinstance(entry, dict) for entry in equipment):
                raise DungeonError("invalid_save", "活动挑战存档异常")
            legacy_in_use = {entry.get("item_id") for entry in equipment}
        for item in items:
            item["can_trade"] = (item["location"] == "bag" and not item["locked"]
                and not item["bound_reason"] and item["source"] not in ("starter", "test")
                and item["template_trade_allowed"] is not False
                and not item["template_id"].startswith("starter_")
                and item["reservation"] is None and item["item_id"] not in equipped_ids
                and not legacy_blocks_all and item["item_id"] not in legacy_in_use)
        progress = [row[0] for row in conn.execute("""SELECT progress_id FROM dungeon_beta_progress
            WHERE user_id=? ORDER BY progress_id""", (user_id,))]
        materials = {row[0]: row[1] for row in conn.execute("""SELECT material_id,amount
            FROM dungeon_material_balances WHERE user_id=? ORDER BY material_id""", (user_id,))}
        active_run = None
        if conn.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
            AND name='dungeon_beta_runs'""").fetchone():
            row = conn.execute("""SELECT run_id,route_id,status,room_index,server_tick,
                durable_tick,run_revision,control_epoch,ruleset_id,ruleset_hash
                FROM dungeon_beta_runs WHERE user_id=? AND status IN ('ready','running','paused')
                ORDER BY created_at DESC LIMIT 1""", (user_id,)).fetchone()
            if row:
                active_run = {"run_id": row[0], "route_id": row[1], "status": row[2],
                              "room_index": row[3], "server_tick": row[4],
                              "durable_tick": row[5], "run_revision": row[6],
                              "control_epoch": row[7], "ruleset_id": row[8],
                              "ruleset_hash": row[9]}
        return {"wallet": {"coin_minor": self.wallet.balance(conn, username)},
                "bag_capacity": profile[0] if profile else 60,
                "profile_version": profile[1] if profile else None,
                "items": items, "pending_count": sum(item["location"] == "pending" for item in items),
                "loadout": loadout, "progress": progress, "materials": materials,
                "asset_revision": revision[0] if revision else 1,
                "active_run": active_run}
