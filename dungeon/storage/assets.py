"""Shared asset gate for old and Beta inventory writes."""

import json

from dungeon.domain.errors import DungeonError


def _has_table(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def ensure_available(conn, username, item_id):
    """Reject a reservation or an active legacy battle snapshot using the item."""
    row = conn.execute("SELECT 1 FROM dungeon_items WHERE item_id=? AND owner=?", (item_id, username)).fetchone()
    if row is None:
        raise DungeonError("not_found", "装备不存在")
    if _has_table(conn, "dungeon_asset_reservations"):
        reservation = conn.execute("""SELECT purpose FROM dungeon_asset_reservations
            WHERE asset_type='item' AND asset_id=?""", (item_id,)).fetchone()
        if reservation:
            raise DungeonError("asset_reserved", "装备已被预留")
    active = conn.execute("SELECT job_kind,job_id FROM dungeon_active_jobs WHERE username=?", (username,)).fetchone()
    if active:
        if active[0] != "battle":
            raise DungeonError("asset_reserved", "活动挑战正在使用装备")
        snapshot = conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=? AND username=?",
                                (active[1], username)).fetchone()
        if snapshot is None:
            raise DungeonError("invalid_save", "活动挑战存档异常")
        try:
            equipment = json.loads(snapshot[0])["equipment"]
            in_use = any(entry.get("item_id") == item_id for entry in equipment)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise DungeonError("invalid_save", "活动挑战存档异常") from error
        if in_use:
            raise DungeonError("asset_reserved", "活动挑战正在使用装备")


def reserve_item(conn, username, item_id, purpose, ref, now):
    """Reserve inside the caller's BEGIN IMMEDIATE transaction."""
    if not conn.in_transaction:
        raise RuntimeError("reserve_item requires a write transaction")
    if purpose not in ("active_run", "trade_offer") or not isinstance(ref, str) or not ref:
        raise DungeonError("invalid_request", "预留参数无效")
    ensure_available(conn, username, item_id)
    item = conn.execute("""SELECT location,locked,template_id,beta_bound_reason,beta_source
        FROM dungeon_items WHERE item_id=? AND owner=?""", (item_id, username)).fetchone()
    if item[0] != "bag":
        raise DungeonError("asset_not_tradable" if purpose == "trade_offer" else "forbidden", "装备位置不可预留")
    if purpose == "trade_offer":
        if item[1] or item[3] or item[2].startswith("starter_") or item[4] in ("starter", "test"):
            raise DungeonError("asset_not_tradable", "该装备不能交易")
        if conn.execute("SELECT 1 FROM dungeon_loadout WHERE item_id=? AND username=?",
                        (item_id, username)).fetchone():
            raise DungeonError("asset_not_tradable", "已穿戴装备不能交易")
    user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        raise DungeonError("auth_required", "用户不存在")
    conn.execute("""INSERT INTO dungeon_asset_reservations
        (asset_type,asset_id,owner_user_id,purpose,reservation_ref,created_at)
        VALUES ('item',?,?,?,?,?)""", (item_id, user[0], purpose, ref, int(now)))
    bump_asset_revision(conn, user[0])


def release_item(conn, item_id, purpose, ref):
    """Release inside the caller's BEGIN IMMEDIATE transaction."""
    if not conn.in_transaction:
        raise RuntimeError("release_item requires a write transaction")
    row = conn.execute("""SELECT owner_user_id FROM dungeon_asset_reservations
        WHERE asset_type='item' AND asset_id=? AND purpose=? AND reservation_ref=?""",
        (item_id, purpose, ref)).fetchone()
    if row is None:
        raise DungeonError("not_found", "预留不存在")
    conn.execute("""DELETE FROM dungeon_asset_reservations
        WHERE asset_type='item' AND asset_id=? AND purpose=? AND reservation_ref=?""",
        (item_id, purpose, ref))
    bump_asset_revision(conn, row[0])


def bump_asset_revision(conn, user_id):
    if _has_table(conn, "dungeon_beta_asset_revisions"):
        conn.execute("""INSERT INTO dungeon_beta_asset_revisions(user_id,revision) VALUES (?,2)
            ON CONFLICT(user_id) DO UPDATE SET revision=revision+1""", (user_id,))
        return conn.execute("SELECT revision FROM dungeon_beta_asset_revisions WHERE user_id=?", (user_id,)).fetchone()[0]
    return None
