"""共享数据库建表与兼容迁移；管理命令和 WebSocket 宿主共用。"""
from estate import init_estate
from dungeon.storage.legacy_schema import init_dungeon
from dungeon.storage.beta_schema import init_beta
from holdem_stats import init_holdem_stats
from rewards import init_rewards
from server.database import database


def init_db(database=database):
    with database() as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                used_by TEXT,
                used_at INTEGER,
                created_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        invite_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(invite_codes)")
        }
        if "created_by" not in invite_columns:
            conn.execute(
                "ALTER TABLE invite_codes ADD COLUMN created_by TEXT NOT NULL DEFAULT ''"
            )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "nickname" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''"
            )
        if "avatar" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''"
            )
        if "coins" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN coins REAL NOT NULL DEFAULT 100"
            )
        if "rating_score" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN rating_score INTEGER NOT NULL DEFAULT 1000")
        if "rating_games" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN rating_games INTEGER NOT NULL DEFAULT 0")
        init_rewards(conn)
        init_estate(conn)
        init_dungeon(conn)
        init_beta(conn)
        init_holdem_stats(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rating_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hand_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                game_type TEXT NOT NULL,
                room_name TEXT NOT NULL,
                hand_no INTEGER NOT NULL,
                initial REAL NOT NULL,
                final REAL NOT NULL,
                delta INTEGER NOT NULL,
                score INTEGER NOT NULL,
                games INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(hand_id, user_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rating_user ON rating_history(user_id, id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coin_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                amount REAL NOT NULL,
                balance REAL NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                ref TEXT NOT NULL DEFAULT ''
            )
            """
        )
        tx_columns = {row[1] for row in conn.execute("PRAGMA table_info(coin_transactions)")}
        if "ref" not in tx_columns:
            conn.execute(
                "ALTER TABLE coin_transactions ADD COLUMN ref TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coin_tx_ref "
            "ON coin_transactions(username, ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coin_tx_user "
            "ON coin_transactions(username, id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                options TEXT NOT NULL,
                creator TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                correct_index INTEGER,
                created_at INTEGER NOT NULL,
                settled_at INTEGER,
                close_delay INTEGER NOT NULL DEFAULT 0,
                closed_at INTEGER
            )
            """
        )
        bet_columns = {row[1] for row in conn.execute("PRAGMA table_info(bets)")}
        if "close_delay" not in bet_columns:
            conn.execute(
                "ALTER TABLE bets ADD COLUMN close_delay INTEGER NOT NULL DEFAULT 0"
            )
        if "closed_at" not in bet_columns:
            conn.execute("ALTER TABLE bets ADD COLUMN closed_at INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bet_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                option_index INTEGER NOT NULL,
                amount REAL NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(bet_id, username)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS game_escrows (
                username TEXT NOT NULL,
                room_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                PRIMARY KEY (username, room_id)
            )
            """
        )
