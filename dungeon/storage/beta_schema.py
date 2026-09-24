"""Numbered Beta asset migrations. Call after the legacy dungeon schema."""

import hashlib
import sqlite3


_MIGRATIONS = ((1, """
CREATE TABLE dungeon_beta_receipts (
    user_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    receipt_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status = 'success'),
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, request_id)
);
CREATE TABLE dungeon_asset_reservations (
    asset_type TEXT NOT NULL CHECK(asset_type IN ('item')),
    asset_id TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('active_run','trade_offer')),
    reservation_ref TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(asset_type, asset_id),
    UNIQUE(purpose, reservation_ref, asset_type, asset_id)
);
CREATE INDEX dungeon_asset_reservations_owner ON dungeon_asset_reservations(owner_user_id);
CREATE TABLE dungeon_material_balances (
    user_id INTEGER NOT NULL,
    material_id TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0 CHECK(amount >= 0),
    PRIMARY KEY(user_id, material_id)
);
CREATE TABLE dungeon_beta_progress (
    user_id INTEGER NOT NULL,
    progress_id TEXT NOT NULL,
    PRIMARY KEY(user_id, progress_id)
);
CREATE TABLE dungeon_beta_asset_revisions (
    user_id INTEGER PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0)
);
ALTER TABLE dungeon_items ADD COLUMN beta_upgrade_level INTEGER NOT NULL DEFAULT 0 CHECK(beta_upgrade_level >= 0);
ALTER TABLE dungeon_items ADD COLUMN beta_bound_reason TEXT;
ALTER TABLE dungeon_items ADD COLUMN beta_source TEXT;
CREATE TRIGGER delete_user_dungeon_beta AFTER DELETE ON users BEGIN
    DELETE FROM dungeon_asset_reservations WHERE owner_user_id=OLD.id;
    DELETE FROM dungeon_beta_receipts WHERE user_id=OLD.id;
    DELETE FROM dungeon_material_balances WHERE user_id=OLD.id;
    DELETE FROM dungeon_beta_progress WHERE user_id=OLD.id;
    DELETE FROM dungeon_beta_asset_revisions WHERE user_id=OLD.id;
END;
"""),)


def init_beta(conn):
    """Apply each migration atomically inside the caller's transaction."""
    conn.execute("SAVEPOINT dungeon_beta_migration")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_migrations (
            version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""")
        for version, script in _MIGRATIONS:
            checksum = hashlib.sha256(script.encode()).hexdigest()
            row = conn.execute("SELECT checksum FROM dungeon_migrations WHERE version=?", (version,)).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"dungeon migration {version} checksum mismatch")
                continue
            # executescript commits implicitly; execute statements within the savepoint.
            # sqlite3.complete_statement preserves the trigger body as one statement.
            statement = ""
            for line in script.splitlines(keepends=True):
                statement += line
                if not sqlite3.complete_statement(statement):
                    continue
                if statement.strip():
                    conn.execute(statement)
                statement = ""
            conn.execute("INSERT INTO dungeon_migrations(version,checksum) VALUES (?,?)", (version, checksum))
    except BaseException:
        conn.execute("ROLLBACK TO dungeon_beta_migration")
        conn.execute("RELEASE dungeon_beta_migration")
        raise
    conn.execute("RELEASE dungeon_beta_migration")
