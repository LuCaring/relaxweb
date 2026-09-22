"""休闲庄园 SQLite 表结构。"""


def init_estate(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_profiles (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            skin_id TEXT NOT NULL DEFAULT 'berry',
            level INTEGER NOT NULL DEFAULT 1 CHECK(level >= 1),
            xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
            warehouse_level INTEGER NOT NULL DEFAULT 1 CHECK(warehouse_level >= 1),
            plot_count INTEGER NOT NULL DEFAULT 0 CHECK(plot_count >= 0),
            reserved_capacity INTEGER NOT NULL DEFAULT 0 CHECK(reserved_capacity >= 0),
            pet_level INTEGER NOT NULL DEFAULT 0 CHECK(pet_level BETWEEN 0 AND 4),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.execute("""CREATE TABLE IF NOT EXISTS estate_owned_skins (
        username TEXT NOT NULL COLLATE NOCASE, skin_id TEXT NOT NULL,
        PRIMARY KEY(username, skin_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS estate_collections (
        username TEXT NOT NULL COLLATE NOCASE, item_id TEXT NOT NULL,
        PRIMARY KEY(username, item_id))""")
    profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(estate_profiles)")}
    if "skin_id" not in profile_columns:
        conn.execute("ALTER TABLE estate_profiles ADD COLUMN skin_id TEXT NOT NULL DEFAULT 'berry'")
    if "reserved_capacity" not in profile_columns:
        conn.execute("ALTER TABLE estate_profiles ADD COLUMN reserved_capacity INTEGER NOT NULL DEFAULT 0")
    if "pet_level" not in profile_columns:
        conn.execute("ALTER TABLE estate_profiles ADD COLUMN pet_level INTEGER NOT NULL DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_plots (
            username TEXT NOT NULL COLLATE NOCASE,
            plot_index INTEGER NOT NULL CHECK(plot_index >= 0),
            land_level INTEGER NOT NULL DEFAULT 1 CHECK(land_level >= 1),
            crop_id TEXT,
            planted_at INTEGER,
            ready_at INTEGER,
            PRIMARY KEY (username, plot_index),
            CHECK (
                (crop_id IS NULL AND planted_at IS NULL AND ready_at IS NULL) OR
                (crop_id IS NOT NULL AND planted_at IS NOT NULL AND ready_at IS NOT NULL
                 AND ready_at >= planted_at)
            )
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_inventory (
            username TEXT NOT NULL COLLATE NOCASE,
            item_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            PRIMARY KEY (username, item_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_actions (
            username TEXT NOT NULL COLLATE NOCASE,
            request_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (username, request_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_estate_actions_created "
        "ON estate_actions(username, created_at)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_tools (
            username TEXT NOT NULL COLLATE NOCASE,
            tool_type TEXT NOT NULL,
            level INTEGER NOT NULL CHECK(level >= 1),
            durability INTEGER NOT NULL CHECK(durability >= 0),
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (username, tool_type)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_tool_daily (
            username TEXT NOT NULL COLLATE NOCASE,
            tool_type TEXT NOT NULL,
            refill_day TEXT NOT NULL DEFAULT '',
            repair_day TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username, tool_type)
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS delete_estate_tool_daily
        AFTER DELETE ON estate_tools
        BEGIN
            DELETE FROM estate_tool_daily
            WHERE username=OLD.username AND tool_type=OLD.tool_type;
        END
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_fishing_sessions (
            session_id TEXT PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE,
            bait_id TEXT NOT NULL,
            rod_level INTEGER NOT NULL,
            fish_id TEXT NOT NULL,
            seed INTEGER NOT NULL,
            pattern_json TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            result_json TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_mining_runs (
            run_id TEXT PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE,
            mine_level INTEGER NOT NULL,
            pickaxe_level INTEGER NOT NULL,
            seed INTEGER NOT NULL,
            board_json TEXT NOT NULL,
            revealed_json TEXT NOT NULL DEFAULT '[]',
            loot_json TEXT NOT NULL DEFAULT '{}',
            strikes_left INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            result_json TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fishing_user ON estate_fishing_sessions(username,status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_fishing_daily (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            fishing_day TEXT NOT NULL,
            retained_count INTEGER NOT NULL DEFAULT 0 CHECK(retained_count >= 0),
            notice_shown INTEGER NOT NULL DEFAULT 0 CHECK(notice_shown IN (0,1))
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS delete_estate_fishing_daily
        AFTER DELETE ON estate_profiles
        BEGIN
            DELETE FROM estate_fishing_daily WHERE username=OLD.username;
        END
    """)
    mining_columns = {row[1] for row in conn.execute("PRAGMA table_info(estate_mining_runs)")}
    if "reserved_slots" not in mining_columns:
        conn.execute("ALTER TABLE estate_mining_runs ADD COLUMN reserved_slots INTEGER NOT NULL DEFAULT 12")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mining_user ON estate_mining_runs(username,status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS estate_thefts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL COLLATE NOCASE,
            visitor_username TEXT NOT NULL COLLATE NOCASE,
            plot_index INTEGER NOT NULL CHECK(plot_index >= 0),
            crop_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            steal_day TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            read_at INTEGER,
            request_id TEXT NOT NULL,
            UNIQUE(visitor_username, request_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_estate_thefts_owner_day "
        "ON estate_thefts(owner_username, steal_day)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_estate_thefts_pair_day "
        "ON estate_thefts(visitor_username, owner_username, steal_day)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_estate_thefts_unread "
        "ON estate_thefts(owner_username, read_at, created_at)"
    )
    theft_columns = {row[1] for row in conn.execute("PRAGMA table_info(estate_thefts)")}
    if "outcome" not in theft_columns:
        conn.execute("ALTER TABLE estate_thefts ADD COLUMN outcome TEXT NOT NULL DEFAULT 'stolen'")
    if "coins_dropped" not in theft_columns:
        conn.execute("ALTER TABLE estate_thefts ADD COLUMN coins_dropped REAL NOT NULL DEFAULT 0")
    if "visitor_read_at" not in theft_columns:
        conn.execute("ALTER TABLE estate_thefts ADD COLUMN visitor_read_at INTEGER")
