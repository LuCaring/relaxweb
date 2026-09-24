"""地下城存储合同；建表可重入，不提交调用者的事务。"""


def init_dungeon(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_profiles (
        username TEXT PRIMARY KEY COLLATE NOCASE,
        starter_granted INTEGER NOT NULL DEFAULT 0 CHECK(starter_granted IN (0,1)),
        bag_capacity INTEGER NOT NULL DEFAULT 60 CHECK(bag_capacity > 0),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_items (
        item_id TEXT PRIMARY KEY,
        owner TEXT NOT NULL COLLATE NOCASE,
        template_id TEXT NOT NULL,
        template_version TEXT NOT NULL,
        display_name TEXT,
        visual_id TEXT,
        slot TEXT NOT NULL CHECK(slot IN ('weapon','helmet','chest','belt','boots','accessory')),
        quality TEXT NOT NULL CHECK(quality IN ('normal','excellent','rare','epic')),
        stats_json TEXT NOT NULL,
        tags_json TEXT NOT NULL DEFAULT '[]',
        effects_json TEXT NOT NULL DEFAULT '[]',
        affixes_json TEXT NOT NULL DEFAULT '[]',
        sell_coins INTEGER NOT NULL DEFAULT 0 CHECK(sell_coins >= 0),
        locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
        location TEXT NOT NULL DEFAULT 'bag' CHECK(location IN ('bag','pending','sold')),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
        created_at INTEGER NOT NULL
    )""")
    # Existing databases predate the frozen display fields.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dungeon_items)")}
    for column in ("display_name", "visual_id"):
        if column not in columns:
            conn.execute(f"ALTER TABLE dungeon_items ADD COLUMN {column} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dungeon_items_owner ON dungeon_items(owner,location)")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_loadout (
        username TEXT NOT NULL COLLATE NOCASE,
        slot TEXT NOT NULL CHECK(slot IN ('weapon','helmet','chest','belt','boots','accessory')),
        item_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY(username,slot)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_runs (
        battle_id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE,
        challenge_id TEXT NOT NULL,
        difficulty_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','paused','settled','abandoned','error')),
        snapshot_json TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        checkpoint_json TEXT NOT NULL,
        result_json TEXT,
        reward_token TEXT UNIQUE,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
        sim_anchor_us INTEGER NOT NULL DEFAULT 0 CHECK(sim_anchor_us >= 0),
        wall_anchor_ms INTEGER NOT NULL,
        playback_rate INTEGER NOT NULL DEFAULT 1 CHECK(playback_rate IN (1,2)),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dungeon_runs_owner ON dungeon_runs(username,created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_active_jobs (
        username TEXT PRIMARY KEY COLLATE NOCASE,
        job_kind TEXT NOT NULL CHECK(job_kind IN ('battle','sweep')),
        job_id TEXT NOT NULL UNIQUE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_events (
        battle_id TEXT NOT NULL,
        sequence_id INTEGER NOT NULL CHECK(sequence_id >= 0),
        battle_time_us INTEGER NOT NULL CHECK(battle_time_us >= 0),
        event_json TEXT NOT NULL,
        PRIMARY KEY(battle_id,sequence_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_rewards (
        reward_token TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE,
        battle_id TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_progress (
        username TEXT NOT NULL COLLATE NOCASE,
        challenge_id TEXT NOT NULL,
        difficulty_id TEXT NOT NULL,
        clear_count INTEGER NOT NULL DEFAULT 0 CHECK(clear_count >= 0),
        unlocked INTEGER NOT NULL DEFAULT 0 CHECK(unlocked IN (0,1)),
        first_clear_at INTEGER,
        PRIMARY KEY(username,challenge_id,difficulty_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dungeon_actions (
        username TEXT NOT NULL COLLATE NOCASE,
        request_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(username,request_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dungeon_actions_created ON dungeon_actions(username,created_at)")
    # server.database.database() does not enable SQLite foreign keys. Delete dependents
    # explicitly so account deletion is correct on existing installations too.
    conn.execute("""CREATE TRIGGER IF NOT EXISTS delete_user_dungeon
        AFTER DELETE ON users BEGIN
            DELETE FROM dungeon_events WHERE battle_id IN
                (SELECT battle_id FROM dungeon_runs WHERE username=OLD.username);
            DELETE FROM dungeon_rewards WHERE username=OLD.username;
            DELETE FROM dungeon_runs WHERE username=OLD.username;
            DELETE FROM dungeon_active_jobs WHERE username=OLD.username;
            DELETE FROM dungeon_actions WHERE username=OLD.username;
            DELETE FROM dungeon_progress WHERE username=OLD.username;
            DELETE FROM dungeon_loadout WHERE username=OLD.username;
            DELETE FROM dungeon_items WHERE owner=OLD.username;
            DELETE FROM dungeon_profiles WHERE username=OLD.username;
        END""")
