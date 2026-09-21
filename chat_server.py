import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from collections import deque
from contextlib import closing

import websockets

from config import get, get_int
from games.base import ROOM_TYPES, create_room, parse_amount
from games.holdem import BLIND_PRESETS as GAME_BLIND_PRESETS
from games.rating import TIERS, rating_change, rating_info
from holdem_stats import init_holdem_stats, load_holdem_stats, public_holdem_stats, record_holdem_hand
from rewards import (
    claim_checkin,
    claim_holdem_reward,
    draw_lottery,
    init_rewards,
    record_holdem_turnover,
    rewards_state,
)
from estate import (
    EstateError,
    buy as estate_buy,
    buy_tool as estate_buy_tool,
    buy_or_upgrade_pet as estate_buy_or_upgrade_pet,
    estate_state,
    finish_fishing as estate_finish_fishing,
    finish_mining as estate_finish_mining,
    harvest as estate_harvest,
    init_estate,
    plant as estate_plant,
    mine_cell as estate_mine_cell,
    repair_tool as estate_repair_tool,
    sell as estate_sell,
    sell_all as estate_sell_all,
    start_fishing as estate_start_fishing,
    start_mining as estate_start_mining,
    upgrade_tool as estate_upgrade_tool,
    list_estates as estate_list_estates,
    mark_notifications_read as estate_mark_notifications_read,
    notifications as estate_notifications,
    public_estate_state,
    steal_crop as estate_steal_crop,
)


HOST = str(get("servers.chat_host", env="LIVE_CHAT_HOST", default="0.0.0.0"))
PORT = get_int("servers.chat_port", env="LIVE_CHAT_PORT", default=8765)
DB_FILE = str(get("database.file", env="LIVE_DB_FILE", default="users.db"))
MAX_CHAT_LENGTH = 200
MAX_AVATAR_LENGTH = 200_000
AVATAR_PATTERN = re.compile(
    r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$"
)
AUTH_COOLDOWN = 1.5
CHAT_COOLDOWN = 1.0
REGISTER_IP_LIMIT = 10
REGISTER_IP_WINDOW = 3600
INVITE_UNUSED_LIMIT = 5
SESSION_TTL = 30 * 24 * 60 * 60
NEW_USER_COINS = float(get("economy.new_user_coins", env="NEW_USER_COINS", default=1000))
BET_MIN_STAKE = 10.0
BET_MAX_OPTIONS = 6
BET_QUESTION_LIMIT = 60
BET_OPTION_LIMIT = 20
BET_CLOSE_MAX_MINUTES = 1440
FINANCE_HISTORY_LIMIT = 60
TRANSFER_COOLDOWN = 2.0
GAME_MAX_PLAYERS = 9
GAME_DISCONNECT_GRACE = 30.0

clients = {}
history = deque(maxlen=50)
register_ip_times = {}
active_bet = None
room_leave_timers = {}
estate_channels = {}
ESTATE_PLOT_POSITIONS = (
    (330, 108), (430, 108), (530, 108), (330, 204),
    (430, 204), (530, 204), (330, 300), (430, 300),
    (530, 300), (330, 396), (430, 396), (530, 396),
)
ESTATE_BLOCKS = ((32, 24, 250, 168), (675, 30, 245, 160),
                 (24, 326, 230, 155), (680, 302, 280, 298),
                 (982, 42, 266, 198))


def valid_estate_position(x, y):
    if not (18 <= x <= 1262 and 24 <= y <= 702):
        return False
    return not any(x + 12 > bx and x - 12 < bx + width
                   and y + 12 > by and y - 12 < by + height
                   for bx, by, width, height in ESTATE_BLOCKS)
logger = logging.getLogger("live-chat")


def database():
    return closing(sqlite3.connect(DB_FILE, timeout=10))


def init_db():
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


def set_escrow(username, room_id, amount):
    with database() as conn, conn:
        if amount is None:
            conn.execute(
                "DELETE FROM game_escrows WHERE username = ? AND room_id = ?",
                (username, room_id),
            )
        else:
            conn.execute(
                "INSERT INTO game_escrows (username, room_id, amount) VALUES (?, ?, ?) "
                "ON CONFLICT(username, room_id) DO UPDATE SET amount = excluded.amount",
                (username, room_id, round(amount, 2)),
            )


def get_rating(username):
    with database() as conn:
        row = conn.execute("SELECT rating_score, rating_games FROM users WHERE username = ?",
                           (username,)).fetchone()
    return rating_info(*row) if row else None


def record_hand_ratings(room, hand_id, starts, endings, stakes=None, statistics=None):
    """评分、统计、下注流水及结算筹码在同一事务落库，重试不会重复写入。"""
    results = {}
    with database() as conn, conn:
        # 提前取写锁，读分数到更新的整个过程只有一个写者。
        conn.execute("BEGIN IMMEDIATE")
        for username, final in endings.items():
            user = conn.execute(
                "SELECT id, rating_score, rating_games FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not user:
                continue
            saved = conn.execute(
                "SELECT initial, final, delta, score, games FROM rating_history "
                "WHERE hand_id = ? AND user_id = ?", (hand_id, user[0]),
            ).fetchone()
            if saved:
                initial, final, delta, score, games = saved
            else:
                initial = starts[username]
                score = max(0, user[1] + rating_change(initial, final))
                delta, games = score - user[1], user[2] + 1
                conn.execute("UPDATE users SET rating_score = ?, rating_games = ? WHERE id = ?",
                             (score, games, user[0]))
                conn.execute(
                    "INSERT INTO rating_history "
                    "(hand_id, user_id, game_type, room_name, hand_no, initial, final, "
                    "delta, score, games, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (hand_id, user[0], room.game_type, room.name, room.hand_seq,
                     initial, final, delta, score, games, int(time.time())),
                )
                if room.game_type == "holdem" and statistics is not None:
                    record_holdem_hand(conn, hand_id, user[0], initial, final, delta,
                                       statistics[username])
                # 离桌者的托管由离桌流程清除，不能在重试时重新创建。
                if room.has_member(username):
                    conn.execute(
                        "UPDATE game_escrows SET amount = ? WHERE username = ? AND room_id = ?",
                        (final, username, room.id),
                    )
            results[username] = {"initial": initial, "final": final,
                                 "return_rate": round((final - initial) / initial, 6),
                                 "delta": delta, "rating": rating_info(score, games)}
        if stakes is not None and room.game_type == "holdem":
            # 只认本手结算的稳定用户 ID；离桌后注销/同名重注册不能继承旧手流水。
            eligible = {row[0] for row in conn.execute(
                "SELECT u.username FROM users u JOIN rating_history h ON h.user_id = u.id "
                "WHERE h.hand_id = ?", (hand_id,))}
            record_holdem_turnover(conn, hand_id,
                {name: amount for name, amount in stakes.items() if name in eligible}, time.time())
    return results


async def publish_ratings(room):
    for username in tuple(room.pending_rating_updates):
        room.pending_rating_updates.discard(username)
        info = get_rating(username)
        for client_state in clients.values():
            user = client_state.get("user")
            if user and user["username"] == username:
                user["rating"] = info
        await broadcast({"type": "rating_update", "username": username, "rating": info})


async def handle_get_rating_history(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    with database() as conn:
        rows = conn.execute(
            "SELECT game_type, room_name, hand_no, initial, final, delta, score, games, created_at "
            "FROM rating_history WHERE user_id = (SELECT id FROM users WHERE username = ?) "
            "ORDER BY id DESC LIMIT 20", (user["username"],),
        ).fetchall()
    await send_json(websocket, {"type": "rating_history", "rating": get_rating(user["username"]),
        "entries": [{"game_type": r[0], "room_name": r[1], "hand_no": r[2],
                     "initial": r[3], "final": r[4], "delta": r[5],
                     "rating": rating_info(r[6], r[7]), "created_at": r[8]} for r in rows]})


async def handle_get_rating_leaderboard(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    limit = 100
    requested_offset = data.get("offset", 0)
    if type(requested_offset) is not int:
        requested_offset = 0
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or len(request_id) > 128:
        request_id = None
    with database() as conn:
        # 显式读事务让页码、排名、本人和统计来自同一快照；只批量读取本页汇总。
        conn.execute("BEGIN")
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        last_offset = max(0, (total - 1) // limit * limit)
        offset = min(max(0, requested_offset // limit * limit), last_offset)
        rows = conn.execute("""
            WITH ranked AS (
                SELECT id, username, nickname, rating_score, rating_games,
                       RANK() OVER (ORDER BY rating_score DESC) AS rank,
                       ROW_NUMBER() OVER (ORDER BY rating_score DESC, username) AS position
                FROM users
            )
            SELECT * FROM ranked
            WHERE (position > ? AND position <= ?) OR username = ? ORDER BY position
        """, (offset, offset + limit, user["username"])).fetchall()
        stats = load_holdem_stats(conn, [row[0] for row in rows])
        stats_since = conn.execute("SELECT started_at FROM holdem_stats_metadata WHERE id = 1").fetchone()[0]
    entries, own = [], None
    for user_id, username, nickname, score, games, rank, position in rows:
        entry = {"username": username, "nickname": nickname, "rank": rank,
                 "rating": rating_info(score, games),
                 "holdem_stats": stats.get(user_id) or public_holdem_stats()}
        if offset < position <= offset + limit:
            entries.append(entry)
        if username == user["username"]:
            own = entry
    await send_json(websocket, {"type": "rating_leaderboard", "entries": entries,
        "self": own, "total": total, "limit": limit, "offset": offset,
        "request_id": request_id, "stats_since": stats_since,
        "tiers": [rating_info(floor) for floor, _ in TIERS]})


async def handle_get_asset_leaderboard(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    limit = 100
    requested_offset = data.get("offset", 0)
    if type(requested_offset) is not int:
        requested_offset = 0
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or len(request_id) > 128:
        request_id = None
    with database() as conn:
        # 页码、全站排名和本人余额必须来自同一快照，仅公开钱包金币。
        conn.execute("BEGIN")
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        last_offset = max(0, (total - 1) // limit * limit)
        offset = min(max(0, requested_offset // limit * limit), last_offset)
        rows = conn.execute("""
            WITH balances AS (
                SELECT username, nickname, ROUND(COALESCE(coins, 0), 2) AS coins FROM users
            ), ranked AS (
                SELECT username, nickname, coins,
                       RANK() OVER (ORDER BY coins DESC) AS rank,
                       ROW_NUMBER() OVER (ORDER BY coins DESC, username) AS position
                FROM balances
            )
            SELECT * FROM ranked
            WHERE (position > ? AND position <= ?) OR username = ? ORDER BY position
        """, (offset, offset + limit, user["username"])).fetchall()
    entries, own = [], None
    for username, nickname, coins, rank, position in rows:
        entry = {"username": username, "nickname": nickname, "coins": coins, "rank": rank}
        if offset < position <= offset + limit:
            entries.append(entry)
        if username == user["username"]:
            own = entry
    await send_json(websocket, {"type": "asset_leaderboard", "entries": entries,
        "self": own, "total": total, "limit": limit, "offset": offset, "request_id": request_id})


def refund_game_escrows():
    """服务器重启后房间不再存在，把所有托管中的游戏筹码退还为金币。"""
    with database() as conn, conn:
        rows = conn.execute(
            "SELECT username, amount FROM game_escrows"
        ).fetchall()
        for username, amount in rows:
            try:
                adjust_coins(
                    conn, username, amount, "game_settle", "游戏厅退款（服务重启）"
                )
            except (KeyError, ValueError):
                continue
        conn.execute("DELETE FROM game_escrows")
    if rows:
        logger.info("refunded %d game escrows on startup", len(rows))


def record_coins(conn, username, amount, balance, kind, detail="", ref=""):
    conn.execute(
        "INSERT INTO coin_transactions (username, amount, balance, kind, detail, created_at, ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            round(amount, 2),
            round(balance, 2),
            kind,
            str(detail or "")[:120],
            int(time.time()),
            ref,
        ),
    )


def adjust_coins(conn, username, delta, kind, detail="", ref=""):
    """在已打开的事务中调整用户金币并记录明细，返回新余额。"""
    row = conn.execute(
        "SELECT coins FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        raise KeyError(username)
    balance = round((row[0] or 0.0) + delta, 2)
    if balance < 0:
        raise ValueError("金币不能低于 0")
    conn.execute(
        "UPDATE users SET coins = ? WHERE username = ?", (balance, username)
    )
    record_coins(conn, username, delta, balance, kind, detail, ref)
    return balance


def user_balance(conn, username):
    row = conn.execute(
        "SELECT coins FROM users WHERE username = ?", (username,)
    ).fetchone()
    return round(row[0] or 0.0, 2) if row else None


def merge_ref_coins(conn, username, ref, delta, kind="", detail="",
                    stake_kind="", stake_detail=""):
    """把 ref 名下的流水合并为一条净额记录并返回当前余额。

    delta 为 0 表示全额冲销（流局），只删除原条目不留痕。
    stake_kind/stake_detail 用于兼容 ref 字段上线前的旧流水：
    按 kind+detail 匹配的投注/买入条目一并删除。
    """
    balance = user_balance(conn, username)
    conn.execute(
        "DELETE FROM coin_transactions WHERE username = ? AND ref = ?",
        (username, ref),
    )
    if stake_kind:
        conn.execute(
            "DELETE FROM coin_transactions WHERE username = ? AND ref = '' "
            "AND kind = ? AND detail = ?",
            (username, stake_kind, stake_detail),
        )
    if delta and balance is not None:
        record_coins(conn, username, delta, balance, kind, detail, ref)
    return balance


def settle_room_coins(room, username, refund, detail, paid=None):
    """离桌/解散时结算该房间的流水：买入（含重新买入）与退款合并为一条净额记录。

    净额为零（如未开局的流局退款）时只删除扣款条目，不留痕。
    """
    paid = room.buy_in if paid is None else paid
    with database() as conn, conn:
        if refund > 0:
            conn.execute(
                "UPDATE users SET coins = round(coins + ?, 2) WHERE username = ?",
                (refund, username),
            )
        merge_ref_coins(
            conn, username, f"room:{room.id}:{username}",
            round(refund - paid, 2), "game_result", detail,
            stake_kind="game_buyin", stake_detail=f"游戏厅买入：{room.name}",
        )


def hash_password(password, salt=None):
    salt_bytes = os.urandom(16) if salt is None else base64.b64decode(salt)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, 310_000
    )
    return base64.b64encode(digest).decode(), base64.b64encode(salt_bytes).decode()


def valid_username(username):
    return bool(re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9_-]{2,20}", username))


def register_user(username, password, invite_code=""):
    username = username.strip()
    if not valid_username(username):
        return False, "用户名需为2-20位中文、英文、数字、_ 或 -"
    if len(password) < 6:
        return False, "密码至少需要6位"
    if len(password) > 128:
        return False, "密码过长"

    code = str(invite_code or "").strip()
    if not code:
        return False, "注册需要邀请码"

    password_hash, salt = hash_password(password)
    try:
        with database() as conn, conn:
            row = conn.execute(
                "SELECT code FROM invite_codes WHERE code = ? AND used_by IS NULL",
                (code,),
            ).fetchone()
            if not row:
                return False, "邀请码无效或已被使用"
            conn.execute(
                """
                INSERT INTO users (username, password_hash, salt, role, created_at, coins)
                VALUES (?, ?, ?, 'user', ?, 0)
                """,
                (username, password_hash, salt, int(time.time())),
            )
            adjust_coins(
                conn, username, NEW_USER_COINS, "register", "新用户注册奖励"
            )
            conn.execute(
                "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE code = ?",
                (username, int(time.time()), code),
            )
    except sqlite3.IntegrityError:
        return False, "这个用户名已经被注册"
    return True, "注册成功"


def authenticate_user(username, password):
    with database() as conn:
        row = conn.execute(
            """
            SELECT username, password_hash, salt, role, nickname, avatar, coins, rating_score, rating_games
            FROM users WHERE username = ?
            """,
            (username.strip(),),
        ).fetchone()
    if not row:
        return None
    real_username, saved_hash, salt, role, nickname, avatar, coins, score, games = row
    calculated_hash, _ = hash_password(password, salt)
    if not hmac.compare_digest(calculated_hash, saved_hash):
        return None
    return {
        "username": real_username,
        "role": role,
        "nickname": nickname or "",
        "avatar": avatar or "",
        "coins": round(coins or 0.0, 2),
        "rating": rating_info(score, games),
    }


def get_profile(username):
    with database() as conn:
        row = conn.execute(
            "SELECT nickname, avatar, rating_score, rating_games FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row:
        return {"username": username, "nickname": "", "avatar": ""}
    return {"username": username, "nickname": row[0] or "", "avatar": row[1] or "",
            "rating": rating_info(row[2], row[3])}


def create_session(username):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = int(time.time()) + SESSION_TTL
    with database() as conn, conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()[0]
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),))
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )
    return token


def resume_user(token):
    if not token:
        return None
    token_hash = hashlib.sha256(str(token).encode()).hexdigest()
    with database() as conn:
        row = conn.execute(
            """
            SELECT users.username, users.role, users.nickname, users.avatar, users.coins,
                   users.rating_score, users.rating_games
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
            """,
            (token_hash, int(time.time())),
        ).fetchone()
    return {
        "username": row[0],
        "role": row[1],
        "nickname": row[2] or "",
        "avatar": row[3] or "",
        "coins": round(row[4] or 0.0, 2),
        "rating": rating_info(row[5], row[6]),
    } if row else None


async def _ws_send(socket, payload):
    """带锁发送：两个广播并发打到同一连接会触发 ConcurrencyError，导致连接被静默剔除。"""
    state = clients.get(socket)
    if not state:
        return
    async with state["send_lock"]:
        try:
            await socket.send(payload)
        except Exception as error:
            clients.pop(socket, None)
            logger.warning("send failed (%s), dropped connection", error)


async def send_json(websocket, data):
    await _ws_send(
        websocket, json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )


async def broadcast(data):
    if not clients:
        return
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    sockets = tuple(clients)
    await asyncio.gather(*(_ws_send(socket, payload) for socket in sockets))


async def broadcast_online_count():
    # 游戏厅独立页的连接不算直播间在线观众
    count = sum(
        1 for state in clients.values() if state.get("client") != "game"
    )
    await broadcast({"type": "online", "count": count})


def rate_limited(state, key, cooldown):
    now = time.monotonic()
    if now - state.get(key, 0.0) < cooldown:
        return True
    state[key] = now
    return False


async def handle_register(websocket, state, data):
    ip = (websocket.remote_address or ("?", 0))[0]
    now = time.monotonic()
    recent = [t for t in register_ip_times.get(ip, []) if now - t < REGISTER_IP_WINDOW]
    register_ip_times[ip] = recent
    if len(recent) >= REGISTER_IP_LIMIT:
        await send_json(websocket, {"type": "auth_error", "message": "注册太频繁，请稍后再试"})
        return
    if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
        await send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
        return
    success, message = register_user(
        str(data.get("username", "")).strip(),
        str(data.get("password", "")),
        str(data.get("invite_code", "")),
    )
    if success:
        recent.append(now)
    await send_json(
        websocket,
        {"type": "register_success" if success else "auth_error", "message": message},
    )


async def handle_login(websocket, state, data):
    if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
        await send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
        return
    user = authenticate_user(
        str(data.get("username", "")).strip(), str(data.get("password", ""))
    )
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "用户名或密码错误"})
        return
    state["user"] = user
    token = create_session(user["username"])
    on_user_authenticated(user["username"])
    logger.info("user login: %s", user["username"])
    profile = get_profile(user["username"])
    await send_json(
        websocket,
        {
            "type": "login_success",
            "username": user["username"],
            "role": user["role"],
            "token": token,
            "nickname": profile["nickname"],
            "avatar": profile["avatar"],
            "coins": user["coins"],
            "rating": user["rating"],
        },
    )


async def handle_resume(websocket, state, data):
    user = resume_user(data.get("token"))
    if not user:
        await send_json(websocket, {"type": "auth_expired"})
        return
    state["user"] = user
    on_user_authenticated(user["username"])
    await send_json(
        websocket,
        {
            "type": "resume_success",
            "username": user["username"],
            "role": user["role"],
            "nickname": user.get("nickname", ""),
            "avatar": user.get("avatar", ""),
            "coins": user.get("coins", 0),
            "rating": user["rating"],
        },
    )


async def handle_logout(websocket, state, data):
    if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
        await send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
        return
    token = data.get("token")
    if token:
        token_hash = hashlib.sha256(str(token).encode()).hexdigest()
        with database() as conn, conn:
            conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
            )
    state["user"] = None
    logger.info("user logout")
    await send_json(websocket, {"type": "logout_success"})


async def handle_update_profile(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_profile_update", 1.0):
        await send_json(websocket, {"type": "profile_error", "message": "操作太频繁，请稍后再试"})
        return
    updates, params = [], []
    if "nickname" in data:
        nickname = str(data.get("nickname") or "").strip()
        if nickname and not valid_username(nickname):
            await send_json(
                websocket,
                {"type": "profile_error", "message": "昵称需为2-20位中文、英文、数字、_ 或 -"},
            )
            return
        updates.append("nickname = ?")
        params.append(nickname)
    if "avatar" in data:
        avatar = str(data.get("avatar") or "")
        if avatar and (
            len(avatar) > MAX_AVATAR_LENGTH or not AVATAR_PATTERN.match(avatar)
        ):
            await send_json(
                websocket,
                {"type": "profile_error", "message": "头像格式不支持或过大"},
            )
            return
        updates.append("avatar = ?")
        params.append(avatar)
    if updates:
        params.append(user["username"])
        with database() as conn, conn:
            conn.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params
            )
    profile = get_profile(user["username"])
    state["user"]["nickname"] = profile["nickname"]
    state["user"]["avatar"] = profile["avatar"]
    logger.info("profile updated: %s", user["username"])
    await send_json(websocket, {"type": "profile_updated", **profile})
    await broadcast({"type": "profile", **profile})


async def handle_get_profile(websocket, state, data):
    await send_json(
        websocket,
        {"type": "profile", **get_profile(str(data.get("username", "")))},
    )


async def handle_delete_account(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
        await send_json(websocket, {"type": "account_error", "message": "操作太频繁，请稍后再试"})
        return
    if not authenticate_user(user["username"], str(data.get("password", ""))):
        await send_json(websocket, {"type": "account_error", "message": "密码错误"})
        return
    # 房间仍以用户名标识参局者；禁止删除后同名新账号继承未结算的旧手。
    # 此检查到同步删除之间不 await，避免另一连接在注销过程中重新入座。
    if find_user_room(user["username"]):
        await send_json(websocket, {"type": "account_error", "message": "请先离开或解散游戏房间，再注销账号"})
        return
    if active_bet and active_bet["creator"] == user["username"]:
        await cancel_active_bet("发起者已注销账号")
        if find_user_room(user["username"]):
            await send_json(websocket, {"type": "account_error", "message": "请先离开或解散游戏房间，再注销账号"})
            return
    with database() as conn, conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE username = ?)",
            (user["username"],),
        )
        conn.execute("DELETE FROM rating_history WHERE user_id = "
                     "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        for table in ("holdem_hand_stats", "holdem_player_stats"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = "
                         "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        conn.execute("DELETE FROM daily_checkins WHERE user_id = "
                     "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        conn.execute("DELETE FROM lottery_draws WHERE user_id = "
                     "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        conn.execute("DELETE FROM estate_thefts WHERE owner_username=? OR visitor_username=?",
                     (user["username"], user["username"]))
        for table in ("estate_actions", "estate_fishing_sessions", "estate_mining_runs",
                      "estate_tools", "estate_inventory", "estate_plots", "estate_profiles"):
            conn.execute(f"DELETE FROM {table} WHERE username = ?", (user["username"],))
        conn.execute("DELETE FROM holdem_turnover WHERE user_id = "
                     "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        conn.execute("DELETE FROM holdem_reward_claims WHERE user_id = "
                     "(SELECT id FROM users WHERE username = ?)", (user["username"],))
        conn.execute("DELETE FROM users WHERE username = ?", (user["username"],))
    state["user"] = None
    other_sockets = []
    for socket, client_state in clients.items():
        if (client_state.get("user") or {}).get("username") == user["username"]:
            client_state["user"] = None
            if socket is not websocket:
                other_sockets.append(socket)
    logger.info("account deleted: %s", user["username"])
    await send_json(websocket, {"type": "account_deleted"})
    for socket in other_sockets:
        await send_json(socket, {"type": "account_deleted"})


async def handle_get_online(websocket, state, data):
    users = {}
    for client_state in list(clients.values()):
        user = client_state.get("user")
        if not user:
            continue
        users[user["username"]] = {
            "username": user["username"],
            "nickname": user.get("nickname") or "",
            "avatar": user.get("avatar") or "",
            "role": user.get("role") or "user",
        }
    await send_json(websocket, {"type": "online_users", "users": list(users.values())})


def display_name(username):
    profile = get_profile(username)
    return profile["nickname"] or username



async def push_balance(username, coins):
    payload = json.dumps(
        {"type": "coins", "username": username, "coins": round(coins, 2)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    for socket, client_state in list(clients.items()):
        user = client_state.get("user")
        if user and user["username"] == username:
            await _ws_send(socket, payload)


async def broadcast_system(text, danmaku=False):
    message = {"type": "system", "text": text, "time": time.strftime("%m/%d %H:%M")}
    if danmaku:
        message["danmaku"] = True
    history.append(message)
    logger.info("system message: %s", text)
    await broadcast(message)


async def handle_get_finance(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    with database() as conn:
        row = conn.execute(
            "SELECT coins FROM users WHERE username = ?", (user["username"],)
        ).fetchone()
        rows = conn.execute(
            "SELECT amount, balance, kind, detail, created_at FROM coin_transactions "
            "WHERE username = ? ORDER BY id DESC LIMIT ?",
            (user["username"], FINANCE_HISTORY_LIMIT),
        ).fetchall()
    coins = round(row[0] or 0.0, 2) if row else 0.0
    await send_json(
        websocket,
        {
            "type": "finance",
            "coins": coins,
            "transactions": [
                {
                    "amount": round(amount, 2),
                    "balance": round(balance, 2),
                    "kind": kind,
                    "detail": detail,
                    "created_at": created_at,
                }
                for amount, balance, kind, detail, created_at in rows
            ],
        },
    )


async def publish_daily_rewards(username):
    for socket, client_state in list(clients.items()):
        user = client_state.get("user")
        if user and user["username"] == username:
            with database() as conn:
                # 玩家可在该手牌结束前注销；旧连接不能阻断其他玩家的结算广播。
                if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                    return
                payload = rewards_state(conn, username, time.time())
            user["coins"] = payload["coins"]
            await send_json(socket, {"type": "daily_rewards", **payload})


async def handle_rewards_action(websocket, state, data, action):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "rewards_error", "message": "请先登录"})
        return
    username = user["username"]
    try:
        with database() as conn, conn:
            # 多标签页、多连接的每日奖励领取必须串行读改写。
            if action != "get":
                conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            extra = {}
            if action == "checkin":
                extra["awarded"] = claim_checkin(conn, username, now)
            elif action == "draw":
                amount, replayed = draw_lottery(conn, username, data.get("request_id"), now, adjust_coins)
                extra = {"amount": amount, "replayed": replayed, "request_id": data["request_id"]}
            elif action == "holdem":
                amount, replayed = claim_holdem_reward(conn, username, data.get("threshold"),
                    data.get("day"), now, adjust_coins)
                extra = {"amount": amount, "replayed": replayed, "threshold": data["threshold"]}
            payload = rewards_state(conn, username, now)
    except ValueError as error:
        await send_json(websocket, {"type": "rewards_error", "message": str(error),
                                    "request_id": data.get("request_id"), "action": action})
        return
    kind = {"get": "daily_rewards", "checkin": "checkin_result", "draw": "lottery_result",
            "holdem": "holdem_reward_result"}[action]
    await send_json(websocket, {"type": kind, **payload, **extra})
    if action != "get":
        await publish_daily_rewards(username)


async def handle_get_daily_rewards(websocket, state, data):
    await handle_rewards_action(websocket, state, data, "get")


async def handle_daily_checkin(websocket, state, data):
    await handle_rewards_action(websocket, state, data, "checkin")


async def handle_draw_lottery(websocket, state, data):
    await handle_rewards_action(websocket, state, data, "draw")


async def publish_estate(username, snapshot, result=None, request_id=None):
    """把庄园完整快照同步到同账号的所有连接。"""
    payload = {
        "type": "estate_state",
        **snapshot,
        "request_id": request_id,
        "result": result,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for socket, client_state in list(clients.items()):
        user = client_state.get("user")
        if user and user["username"] == username:
            user["coins"] = snapshot["coins"]
            await _ws_send(socket, encoded)


async def broadcast_estate_channel(owner, payload, exclude=None):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for socket in list(estate_channels.get(owner.lower(), set())):
        if socket is exclude or socket not in clients:
            continue
        await _ws_send(socket, encoded)


async def leave_estate_channel(websocket, state):
    owner = state.pop("estate_owner", None)
    if not owner:
        return
    members = estate_channels.get(owner.lower())
    if members:
        members.discard(websocket)
        if not members:
            estate_channels.pop(owner.lower(), None)
    user = state.get("user")
    if user:
        await broadcast_estate_channel(owner, {
            "type": "estate_visit_left", "username": user["username"],
        }, exclude=websocket)


async def join_estate_channel(websocket, state, owner):
    await leave_estate_channel(websocket, state)
    members = estate_channels.setdefault(owner.lower(), set())
    players = []
    for socket in list(members):
        other_state = clients.get(socket, {})
        other = other_state.get("user")
        if other:
            players.append({"username": other["username"],
                            **other_state.get("estate_position", {"x": 275, "y": 440,
                              "direction": "down", "walking": False})})
    members.add(websocket)
    state["estate_owner"] = owner
    state["estate_position"] = {"x": 275, "y": 440, "direction": "down", "walking": False}
    state["estate_position_at"] = time.monotonic()
    user = state.get("user")
    await broadcast_estate_channel(owner, {
        "type": "estate_visit_joined", "username": user["username"],
        **state["estate_position"],
    }, exclude=websocket)
    return players


async def handle_estate_list_visits(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录"})
        return
    try:
        with database() as conn:
            entries = estate_list_estates(conn, user["username"], data.get("query"), int(time.time()))
        await send_json(websocket, {"type": "estate_visit_list", "estates": entries,
                                    "request_id": data.get("request_id")})
    except EstateError as error:
        await send_json(websocket, {"type": "estate_error", "code": error.code,
                                    "message": str(error), "request_id": data.get("request_id")})


async def handle_estate_enter_visit(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录"})
        return
    try:
        with database() as conn:
            snapshot = public_estate_state(conn, user["username"], data.get("owner_username"), int(time.time()))
        players = await join_estate_channel(websocket, state, snapshot["owner_username"])
        await send_json(websocket, {"type": "estate_visit_state", **snapshot,
                                    "players": players, "request_id": data.get("request_id")})
    except EstateError as error:
        await send_json(websocket, {"type": "estate_error", "code": error.code,
                                    "message": str(error), "request_id": data.get("request_id")})


async def handle_estate_leave_visit(websocket, state, data):
    await leave_estate_channel(websocket, state)
    await send_json(websocket, {"type": "estate_visit_left_self", "request_id": data.get("request_id")})


async def handle_estate_visit_move(websocket, state, data):
    user = state.get("user")
    owner = state.get("estate_owner")
    if not user or not owner or rate_limited(state, "last_estate_move", .06):
        return
    try:
        x, y = float(data.get("x")), float(data.get("y"))
    except (TypeError, ValueError):
        return
    if not valid_estate_position(x, y):
        return
    previous = state.get("estate_position", {"x": x, "y": y})
    moved = math.hypot(x - previous["x"], y - previous["y"])
    now_mono = time.monotonic()
    elapsed = max(.06, now_mono - state.get("estate_position_at", now_mono))
    if moved > 220 * elapsed + 28:
        return
    direction = data.get("direction") if data.get("direction") in ("up", "down", "left", "right") else "down"
    position = {"x": round(x, 1), "y": round(y, 1), "direction": direction,
                "walking": bool(data.get("walking"))}
    state["estate_position"] = position
    state["estate_position_at"] = now_mono
    await broadcast_estate_channel(owner, {"type": "estate_visit_moved",
        "username": user["username"], **position}, exclude=websocket)


async def handle_estate_steal_crop(websocket, state, data):
    user = state.get("user")
    owner = state.get("estate_owner")
    request_id = data.get("request_id")
    if not user:
        await send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录", "request_id": request_id})
        return
    if not owner or owner.lower() != str(data.get("owner_username") or "").lower():
        await send_json(websocket, {"type": "estate_error", "code": "not_visiting", "message": "未在目标庄园内", "request_id": request_id})
        return
    try:
        plot_id = int(data.get("plot_id"))
        plot_x, plot_y = ESTATE_PLOT_POSITIONS[plot_id]
        position = state.get("estate_position", {})
        if math.hypot(float(position.get("x", -999)) - (plot_x + 41),
                      float(position.get("y", -999)) - (plot_y + 35)) > 82:
            raise ValueError
    except (TypeError, ValueError, IndexError):
        await send_json(websocket, {"type": "estate_error", "code": "too_far",
                                    "message": "请先走到农田附近", "request_id": request_id})
        return
    try:
        with database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            result = estate_steal_crop(conn, user["username"], request_id, owner,
                                       plot_id, now, adjust_coins)
            snapshot = public_estate_state(conn, user["username"], owner, now)
        await send_json(websocket, {"type": "estate_steal_result", "result": result,
                                    "state": snapshot, "request_id": request_id})
        if not result.get("replayed") and result.get("outcome") == "stolen":
            await broadcast_estate_channel(owner, {"type": "estate_crop_stolen",
                "plot_id": result["plot_id"], "visitor_username": user["username"]},
                exclude=websocket)
    except (EstateError, sqlite3.Error) as error:
        code = error.code if isinstance(error, EstateError) else "estate_failed"
        message = str(error) if isinstance(error, EstateError) else "庄园暂时忙碌，请稍后重试"
        await send_json(websocket, {"type": "estate_error", "code": code,
                                    "message": message, "request_id": request_id})


async def handle_estate_get_notifications(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    with database() as conn, conn:
        rows = estate_notifications(conn, user["username"], int(time.time()))
    await send_json(websocket, {"type": "estate_notifications", "notifications": rows,
                                "request_id": data.get("request_id")})


async def handle_estate_mark_notifications_read(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    with database() as conn, conn:
        result = estate_mark_notifications_read(conn, user["username"], data.get("ids"), int(time.time()))
    await send_json(websocket, {"type": "estate_notifications_read", "result": result,
                                "request_id": data.get("request_id")})


async def handle_estate_action(websocket, state, data, action):
    user = state.get("user")
    if not user:
        await send_json(websocket, {
            "type": "estate_error", "code": "auth_required",
            "message": "请先登录", "request_id": data.get("request_id"),
        })
        return
    username = user["username"]
    request_id = data.get("request_id")
    try:
        with database() as conn, conn:
            now = int(time.time())
            result = None
            if action != "get":
                conn.execute("BEGIN IMMEDIATE")
            if action == "buy":
                result = estate_buy(
                    conn, username, request_id, data.get("kind"),
                    data.get("item_id"), data.get("quantity", 1), now,
                    adjust_coins,
                )
            elif action == "plant":
                result = estate_plant(
                    conn, username, request_id, data.get("plot_id"),
                    data.get("crop_id"), now,
                )
            elif action == "harvest":
                result = estate_harvest(
                    conn, username, request_id, data.get("plot_id"), now,
                )
            elif action == "sell":
                result = estate_sell(
                    conn, username, request_id, data.get("item_id"),
                    data.get("quantity"), now, adjust_coins,
                )
            elif action == "sell_all":
                result = estate_sell_all(
                    conn, username, request_id, now, adjust_coins,
                )
            elif action == "buy_tool":
                result = estate_buy_tool(conn, username, request_id,
                                         data.get("tool_type"), now, adjust_coins)
            elif action == "upgrade_tool":
                result = estate_upgrade_tool(conn, username, request_id,
                                             data.get("tool_type"), now, adjust_coins)
            elif action == "repair_tool":
                result = estate_repair_tool(conn, username, request_id,
                                            data.get("tool_type"), now, adjust_coins)
            elif action == "pet":
                result = estate_buy_or_upgrade_pet(conn, username, request_id, now, adjust_coins)
            elif action == "start_fishing":
                result = estate_start_fishing(conn, username, request_id,
                                              data.get("bait_id"), now)
            elif action == "finish_fishing":
                result = estate_finish_fishing(conn, username, request_id,
                                               data.get("session_id"), data.get("trace"), now)
            elif action == "start_mining":
                result = estate_start_mining(conn, username, request_id,
                                             data.get("mine_level"), now)
            elif action == "mine_cell":
                result = estate_mine_cell(conn, username, request_id,
                                          data.get("run_id"), data.get("cell"), now)
            elif action == "finish_mining":
                result = estate_finish_mining(conn, username, request_id,
                                              data.get("run_id"), now)
            snapshot = estate_state(conn, username, now)
    except (EstateError, ValueError, sqlite3.Error) as error:
        code = error.code if isinstance(error, EstateError) else "estate_failed"
        if isinstance(error, sqlite3.Error):
            logger.exception("estate database failure for %s", username)
            message = "庄园暂时忙碌，请稍后重试"
        else:
            logger.info("estate action rejected for %s: %s (%s)", username, error, code)
            message = str(error)
        payload = {
            "type": "estate_error", "code": code, "message": message,
            "request_id": request_id,
        }
        try:
            with database() as conn:
                payload["state"] = estate_state(conn, username, int(time.time()))
        except (EstateError, sqlite3.Error):
            pass
        await send_json(websocket, payload)
        return
    if action == "get":
        user["coins"] = snapshot["coins"]
        await send_json(websocket, {
            "type": "estate_state", **snapshot, "request_id": None,
            "result": None,
        })
    else:
        await publish_estate(username, snapshot, result, request_id)


async def handle_get_estate(websocket, state, data):
    await handle_estate_action(websocket, state, data, "get")
    user = state.get("user")
    if user:
        await join_estate_channel(websocket, state, user["username"])
        with database() as conn, conn:
            rows = estate_notifications(conn, user["username"], int(time.time()))
        await send_json(websocket, {"type": "estate_notifications", "notifications": rows})


async def handle_estate_buy(websocket, state, data):
    await handle_estate_action(websocket, state, data, "buy")


async def handle_estate_plant(websocket, state, data):
    await handle_estate_action(websocket, state, data, "plant")


async def handle_estate_harvest(websocket, state, data):
    await handle_estate_action(websocket, state, data, "harvest")


async def handle_estate_sell(websocket, state, data):
    await handle_estate_action(websocket, state, data, "sell")


async def handle_estate_sell_all(websocket, state, data):
    await handle_estate_action(websocket, state, data, "sell_all")


async def handle_estate_buy_tool(websocket, state, data):
    await handle_estate_action(websocket, state, data, "buy_tool")


async def handle_estate_upgrade_tool(websocket, state, data):
    await handle_estate_action(websocket, state, data, "upgrade_tool")


async def handle_estate_repair_tool(websocket, state, data):
    await handle_estate_action(websocket, state, data, "repair_tool")


async def handle_estate_pet(websocket, state, data):
    await handle_estate_action(websocket, state, data, "pet")


async def handle_estate_start_fishing(websocket, state, data):
    await handle_estate_action(websocket, state, data, "start_fishing")


async def handle_estate_finish_fishing(websocket, state, data):
    await handle_estate_action(websocket, state, data, "finish_fishing")


async def handle_estate_start_mining(websocket, state, data):
    await handle_estate_action(websocket, state, data, "start_mining")


async def handle_estate_mine_cell(websocket, state, data):
    await handle_estate_action(websocket, state, data, "mine_cell")


async def handle_estate_finish_mining(websocket, state, data):
    await handle_estate_action(websocket, state, data, "finish_mining")


async def handle_claim_holdem_reward(websocket, state, data):
    await handle_rewards_action(websocket, state, data, "holdem")


async def handle_transfer_coins(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_transfer", TRANSFER_COOLDOWN):
        await send_json(websocket, {"type": "coins_error", "message": "操作太频繁，请稍后再试"})
        return
    target = str(data.get("to", "")).strip()
    amount = parse_amount(data.get("amount"))
    if not target or target == user["username"]:
        await send_json(websocket, {"type": "coins_error", "message": "请输入正确的对方用户名"})
        return
    if amount is None:
        await send_json(websocket, {"type": "coins_error", "message": "转账金额无效"})
        return
    sender = user["username"]
    try:
        with database() as conn, conn:
            row = conn.execute(
                "SELECT username FROM users WHERE username = ?", (target,)
            ).fetchone()
            if not row:
                raise ValueError("用户不存在")
            balance_row = conn.execute(
                "SELECT coins FROM users WHERE username = ?", (sender,)
            ).fetchone()
            if (balance_row[0] or 0.0) < amount:
                raise ValueError("金币不足")
            sender_balance = adjust_coins(
                conn, sender, -amount, "transfer_out", f"转账给 {row[0]}"
            )
            target_balance = adjust_coins(
                conn, row[0], amount, "transfer_in", f"来自 {sender} 的转账"
            )
    except ValueError as error:
        await send_json(websocket, {"type": "coins_error", "message": str(error)})
        return
    user["coins"] = sender_balance
    logger.info("transfer %.2f from %s to %s", amount, sender, row[0])
    await send_json(websocket, {"type": "transfer_success", "coins": sender_balance})
    await push_balance(row[0], target_balance)
    await broadcast_system(
        f"💰 {display_name(sender)} 转账 {amount:,.2f} 金币给 {display_name(row[0])}",
        danmaku=True,
    )


def load_open_bet():
    with database() as conn:
        row = conn.execute(
            "SELECT id, question, options, creator, created_at, close_delay, closed_at "
            "FROM bets WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        entries = conn.execute(
            "SELECT username, option_index, amount FROM bet_entries "
            "WHERE bet_id = ? ORDER BY id",
            (row[0],),
        ).fetchall()
    return {
        "id": row[0],
        "question": row[1],
        "options": json.loads(row[2]),
        "creator": row[3],
        "created_at": row[4],
        "close_delay": row[5] or 0,
        "closed_at": row[6],
        "entries": [
            {"username": name, "option_index": index, "amount": round(amount, 2)}
            for name, index, amount in entries
        ],
    }


def bet_closes_at(bet):
    delay = int(bet.get("close_delay") or 0)
    if delay <= 0:
        return None
    return bet["created_at"] + delay * 60


def bet_closed(bet, now=None):
    if not bet:
        return False
    if bet.get("closed_at"):
        return True
    deadline = bet_closes_at(bet)
    if deadline is None:
        return False
    return (int(time.time()) if now is None else now) >= deadline


async def close_betting(bet, reason="到时封盘"):
    """封盘：停止接受新投注，已投注的保持不变，等发起者结账。"""
    if bet.get("closed_at"):
        return
    closed_at = int(time.time())
    with database() as conn, conn:
        conn.execute(
            "UPDATE bets SET closed_at = ? WHERE id = ?", (closed_at, bet["id"])
        )
    bet["closed_at"] = closed_at
    logger.info("bet closed (%s): %s", reason, bet["question"])
    await broadcast({"type": "bet_update", "bet": bet_public_state(bet)})
    await broadcast_system(f"🔒 竞猜已封盘：{bet['question']}｜等待发起者结账")


async def bet_close_watcher():
    """每秒检查一次进行中的竞猜是否到了封盘时间（也覆盖重启后补封盘）。"""
    while True:
        await asyncio.sleep(1)
        bet = active_bet
        if bet and not bet.get("closed_at"):
            deadline = bet_closes_at(bet)
            if deadline is not None and time.time() >= deadline:
                try:
                    await close_betting(bet, "到时自动封盘")
                except Exception:
                    logger.exception("auto close bet failed")


def bet_public_state(bet):
    if not bet:
        return None
    totals = [0.0] * len(bet["options"])
    for entry in bet["entries"]:
        if 0 <= entry["option_index"] < len(totals):
            totals[entry["option_index"]] = round(
                totals[entry["option_index"]] + entry["amount"], 2
            )
    return {
        "id": bet["id"],
        "question": bet["question"],
        "options": bet["options"],
        "creator": bet["creator"],
        "created_at": bet["created_at"],
        "entries": bet["entries"],
        "totals": totals,
        "pot": round(sum(totals), 2),
        "close_delay": int(bet.get("close_delay") or 0),
        "closed_at": bet.get("closed_at"),
        "closes_at": bet_closes_at(bet),
    }


def find_entry(bet, username):
    for entry in bet["entries"]:
        if entry["username"] == username:
            return entry
    return None


async def handle_get_bet(websocket, state, data):
    await send_json(
        websocket, {"type": "bet_state", "bet": bet_public_state(active_bet)}
    )


async def handle_create_bet(websocket, state, data):
    global active_bet
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_bet_action", 2.0):
        await send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
        return
    if active_bet:
        await send_json(
            websocket,
            {"type": "bet_error", "message": "已有进行中的竞猜，请等待它结账"},
        )
        return
    question = str(data.get("question", "")).strip()[:BET_QUESTION_LIMIT]
    raw_options = data.get("options")
    options = []
    if isinstance(raw_options, list):
        for item in raw_options:
            text = str(item).strip()[:BET_OPTION_LIMIT]
            if text and text not in options:
                options.append(text)
    if not question:
        await send_json(websocket, {"type": "bet_error", "message": "请输入竞猜问题"})
        return
    if len(options) < 2:
        await send_json(websocket, {"type": "bet_error", "message": "至少需要两个选项"})
        return
    if len(options) > BET_MAX_OPTIONS:
        await send_json(
            websocket,
            {"type": "bet_error", "message": f"选项最多 {BET_MAX_OPTIONS} 个"},
        )
        return
    try:
        close_minutes = int(data.get("close_minutes") or 0)
    except (TypeError, ValueError):
        close_minutes = 0
    close_minutes = max(0, min(close_minutes, BET_CLOSE_MAX_MINUTES))
    now = int(time.time())
    with database() as conn, conn:
        cursor = conn.execute(
            "INSERT INTO bets (question, options, creator, status, created_at, close_delay) "
            "VALUES (?, ?, ?, 'open', ?, ?)",
            (question, json.dumps(options, ensure_ascii=False), user["username"], now,
             close_minutes),
        )
        bet_id = cursor.lastrowid
    active_bet = {
        "id": bet_id,
        "question": question,
        "options": options,
        "creator": user["username"],
        "created_at": now,
        "close_delay": close_minutes,
        "closed_at": None,
        "entries": [],
    }
    logger.info(
        "bet created by %s: %s (close in %d min)",
        user["username"], question, close_minutes,
    )
    await send_json(websocket, {"type": "bet_created"})
    await broadcast({"type": "bet_update", "bet": bet_public_state(active_bet)})
    closing_note = (
        f"，{close_minutes} 分钟后自动封盘" if close_minutes else ""
    )
    await broadcast_system(
        f"🎲 {display_name(user['username'])} 发起了竞猜：{question}{closing_note}"
    )


async def handle_place_bet(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_bet_action", 1.0):
        await send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
        return
    bet = active_bet
    if not bet:
        await send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
        return
    if bet_closed(bet):
        await send_json(websocket, {"type": "bet_error", "message": "竞猜已封盘，无法参与"})
        return
    try:
        option_index = int(data.get("option_index"))
    except (TypeError, ValueError):
        option_index = -1
    if not 0 <= option_index < len(bet["options"]):
        await send_json(websocket, {"type": "bet_error", "message": "请选择一个选项"})
        return
    amount = parse_amount(data.get("amount"))
    if amount is None:
        await send_json(websocket, {"type": "bet_error", "message": "投注金额无效"})
        return
    username = user["username"]
    try:
        with database() as conn, conn:
            if find_entry(bet, username):
                raise ValueError("你已经参与过这个竞猜")
            balance = round(
                conn.execute(
                    "SELECT coins FROM users WHERE username = ?", (username,)
                ).fetchone()[0] or 0.0,
                2,
            )
            if amount > balance:
                raise ValueError("金币不足")
            if balance >= BET_MIN_STAKE and amount < BET_MIN_STAKE:
                raise ValueError(f"最低投注 {BET_MIN_STAKE:,.0f} 金币")
            if balance < BET_MIN_STAKE and amount < balance:
                raise ValueError("金币不足 10 时只能全部投上")
            new_balance = adjust_coins(
                conn, username, -amount, "bet_stake",
                f"竞猜投注：{bet['question']}", ref=f"bet:{bet['id']}:{username}",
            )
            conn.execute(
                "INSERT INTO bet_entries (bet_id, username, option_index, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (bet["id"], username, option_index, amount, int(time.time())),
            )
    except ValueError as error:
        await send_json(websocket, {"type": "bet_error", "message": str(error)})
        return
    bet["entries"].append(
        {"username": username, "option_index": option_index, "amount": amount}
    )
    user["coins"] = new_balance
    await send_json(
        websocket,
        {
            "type": "bet_placed",
            "coins": new_balance,
            "option_index": option_index,
            "amount": amount,
        },
    )
    await broadcast({"type": "bet_update", "bet": bet_public_state(bet)})


async def cancel_active_bet(reason, message=None, refund_prefix="竞猜取消"):
    global active_bet
    bet = active_bet
    if not bet:
        return
    with database() as conn, conn:
        rows = conn.execute(
            "SELECT username, amount FROM bet_entries WHERE bet_id = ? ORDER BY id",
            (bet["id"],),
        ).fetchall()
        for name, amount in rows:
            try:
                merge_ref_coins(
                    conn, name, f"bet:{bet['id']}:{name}", 0,
                    stake_kind="bet_stake",
                    stake_detail=f"竞猜投注：{bet['question']}",
                )
            except KeyError:
                continue
        conn.execute(
            "UPDATE bets SET status = 'cancelled', settled_at = ? WHERE id = ?",
            (int(time.time()), bet["id"]),
        )
    active_bet = None
    logger.info("bet cancelled: %s (%s)", bet["question"], reason)
    await broadcast({"type": "bet_update", "bet": None})
    await broadcast(
        {"type": "bet_cancelled", "question": bet["question"], "reason": reason}
    )
    await broadcast_system(message or f"🎲 竞猜已取消（{reason}），投注已退还")


async def handle_settle_bet(websocket, state, data):
    global active_bet
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    bet = active_bet
    if not bet:
        await send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
        return
    if user["username"] != bet["creator"]:
        await send_json(websocket, {"type": "bet_error", "message": "只有发起者可以结账"})
        return
    try:
        correct_index = int(data.get("correct_index"))
    except (TypeError, ValueError):
        correct_index = -1
    if not 0 <= correct_index < len(bet["options"]):
        await send_json(websocket, {"type": "bet_error", "message": "请选择正确选项"})
        return
    question = bet["question"]
    answer = bet["options"][correct_index]
    results = []
    winner_texts = []
    loser_texts = []
    refunded = False
    with database() as conn, conn:
        rows = conn.execute(
            "SELECT username, option_index, amount FROM bet_entries "
            "WHERE bet_id = ? ORDER BY id",
            (bet["id"],),
        ).fetchall()
        winners = [(name, amount) for name, index, amount in rows if index == correct_index]
        losers = [(name, amount) for name, index, amount in rows if index != correct_index]
        pot = round(sum(amount for _, amount in losers), 2)
        win_stake = round(sum(amount for _, amount in winners), 2)
        if winners and pot > 0:
            shares = [round(pot * amount / win_stake, 2) for _, amount in winners]
            shares[-1] = round(shares[-1] + pot - sum(shares), 2)
            for (name, amount), share in zip(winners, shares):
                ref = f"bet:{bet['id']}:{name}"
                conn.execute(
                    "UPDATE users SET coins = round(coins + ?, 2) WHERE username = ?",
                    (round(amount + share, 2), name),
                )
                balance = merge_ref_coins(
                    conn, name, ref, round(share, 2), "bet_result",
                    f"竞猜猜中：{question}",
                    stake_kind="bet_stake", stake_detail=f"竞猜投注：{question}",
                )
                results.append(
                    {
                        "username": name,
                        "change": round(share, 2),
                        "coins": balance if balance is not None else 0.0,
                    }
                )
                winner_texts.append(f"{display_name(name)} +{share:.2f}")
            for name, amount in losers:
                ref = f"bet:{bet['id']}:{name}"
                balance = merge_ref_coins(
                    conn, name, ref, -round(amount, 2), "bet_result",
                    f"竞猜未中：{question}",
                    stake_kind="bet_stake", stake_detail=f"竞猜投注：{question}",
                )
                results.append(
                    {
                        "username": name,
                        "change": round(-amount, 2),
                        "coins": balance if balance is not None else 0.0,
                    }
                )
                loser_texts.append(f"{display_name(name)} -{amount:.2f}")
        else:
            refunded = True
            for name, _, amount in rows:
                balance = merge_ref_coins(
                    conn, name, f"bet:{bet['id']}:{name}", 0,
                    stake_kind="bet_stake", stake_detail=f"竞猜投注：{question}",
                )
                results.append(
                    {
                        "username": name,
                        "change": round(amount, 2),
                        "coins": balance if balance is not None else 0.0,
                    }
                )
        conn.execute(
            "UPDATE bets SET status = 'settled', correct_index = ?, settled_at = ? "
            "WHERE id = ?",
            (correct_index, int(time.time()), bet["id"]),
        )
    active_bet = None
    logger.info("bet settled: %s answer=%s", question, answer)
    await broadcast(
        {
            "type": "bet_settled",
            "question": question,
            "answer": answer,
            "refunded": refunded,
            "results": results,
        }
    )
    if refunded:
        await broadcast_system(f"🎲 竞猜结账：{question}｜无人猜对，投注已退还")
    else:
        await broadcast_system(
            f"🎲 竞猜结账：{question}｜答案：{answer}｜"
            f"赢家：{'、'.join(winner_texts) or '无'}｜"
            f"输家：{'、'.join(loser_texts) or '无'}"
        )


async def handle_cancel_bet(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_bet_action", 2.0):
        await send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
        return
    bet = active_bet
    if not bet:
        await send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
        return
    if user["username"] != bet["creator"]:
        await send_json(websocket, {"type": "bet_error", "message": "只有发起者可以流局"})
        return
    logger.info("bet drawn by %s: %s", user["username"], bet["question"])
    await cancel_active_bet(
        "发起者流局",
        message=f"🎲 竞猜流局：{bet['question']}｜投注已全部退还",
        refund_prefix="竞猜流局",
    )


async def handle_close_bet(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_bet_action", 2.0):
        await send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
        return
    bet = active_bet
    if not bet:
        await send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
        return
    if user["username"] != bet["creator"]:
        await send_json(websocket, {"type": "bet_error", "message": "只有发起者可以封盘"})
        return
    await close_betting(bet, "发起者提前封盘")


# =========================================================
# 游戏厅：房间宿主（玩法引擎在 games/ 包内，协议保持不变）
# =========================================================

game_rooms = {}
room_seq = 0


def find_user_room(username):
    for room in game_rooms.values():
        if room.has_member(username) or room.has_spectator(username):
            return room
    return None


def attach_host(room):
    """把宿主能力注入房间：成员广播、视图分发、列表变更通知与托管同步。"""
    pending_rewards = set()
    def member_sockets():
        for socket, client_state in list(clients.items()):
            user = client_state.get("user")
            if user and (room.has_member(user["username"])
                         or room.has_spectator(user["username"])):
                yield socket

    async def broadcast_payload(payload):
        await asyncio.gather(*(send_json(socket, payload) for socket in member_sockets()))

    async def broadcast_views():
        await publish_ratings(room)
        for username in tuple(pending_rewards):
            pending_rewards.discard(username)
            await publish_daily_rewards(username)
        targets = []
        for socket, client_state in list(clients.items()):
            user = client_state.get("user")
            if not user:
                continue
            username = user["username"]
            if room.has_member(username):
                targets.append((socket, room.view_for(username)))
            elif room.has_spectator(username):
                targets.append((socket, room.spectator_view(username)))
        await asyncio.gather(*(send_json(socket, view) for socket, view in targets))

    async def on_rooms_changed():
        await broadcast_room_list()

    def sync_escrow(username, amount):
        set_escrow(username, room.id, amount)

    async def on_dissolve_requested(reason):
        await dissolve_room(room, reason)

    async def on_rebuy_requested():
        await rebuy_members(room)

    room.broadcast_payload = broadcast_payload
    room.broadcast_views = broadcast_views
    room.on_rooms_changed = on_rooms_changed
    room.on_dissolve_requested = on_dissolve_requested
    room.on_rebuy_requested = on_rebuy_requested
    room.display_name = display_name
    room.player_avatar = lambda username: get_profile(username)["avatar"]
    room.set_escrow = sync_escrow
    room.player_rating = get_rating
    room.pending_rating_updates = set()

    def record_ratings(hand_id, starts, endings, stakes=None, statistics=None):
        results = record_hand_ratings(room, hand_id, starts, endings,
                                      stakes=stakes, statistics=statistics)
        room.pending_rating_updates.update(results)
        if stakes is not None and room.game_type == "holdem":
            pending_rewards.update(stakes)
        return results

    room.record_ratings = record_ratings


async def rebuy_members(room):
    """对局结束「再来一局」：每人按买入额重新买入，余额不足者自动离桌退币。

    筹码与累计买入（paid）同步增加，最终离桌结算仍只留一条净额流水。
    """
    for username in list(room.seating):
        try:
            with database() as conn, conn:
                balance = user_balance(conn, username)
                if balance is None or balance < room.buy_in:
                    raise ValueError("金币不足")
                balance = adjust_coins(
                    conn, username, -room.buy_in, "game_buyin",
                    f"游戏厅重新买入：{room.name}", ref=f"room:{room.id}:{username}",
                )
        except ValueError:
            logger.info("%s 金币不足，未能重新买入 %s", username, room.id)
            await send_to_user(username, {"type": "game_error",
                                          "message": f"金币不足 {room.buy_in:,.2f}，已离桌"})
            await leave_room_internal(room, username)
            continue
        room.add_chips(username, room.buy_in)
        await push_balance(username, balance)   # 客户端金币牌要立刻反映扣款
    room.stacks_changed()
    await room.broadcast_views()
    await broadcast_room_list()


def send_to_user(username, payload):
    """给某个用户的所有连接发一条定向消息（如重新买入失败的通知）。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return asyncio.gather(*(
        _ws_send(socket, text)
        for socket, client_state in list(clients.items())
        if (client_state.get("user") or {}).get("username") == username
    ))


async def broadcast_room_list():
    await broadcast(
        {"type": "room_list", "rooms": [room.summary() for room in game_rooms.values()]}
    )


async def dissolve_room(room, reason):
    await room.finish_pending_settlement()
    room.close()
    game_rooms.pop(room.id, None)
    # 手牌进行中解散才是「流局」；打完后的正常解散按「结算」入账
    detail = (
        f"游戏厅流局：{room.name}" if room.in_hand()
        else f"游戏厅结算：{room.name}"
    )
    for username, refund in room.pending_refunds().items():
        settle_room_coins(room, username, refund, detail, room.member_paid(username))
    with database() as conn, conn:
        conn.execute("DELETE FROM game_escrows WHERE room_id = ?", (room.id,))
    logger.info("game room %s dissolved: %s", room.id, reason)
    await room.broadcast_payload({"type": "room_closed", "reason": reason})
    await broadcast_room_list()


async def dissolve_empty_room(room):
    """成员走光后没有可看的对局：移除房间并让残留观战者返回大厅。"""
    room.close()
    game_rooms.pop(room.id, None)
    await room.broadcast_payload({"type": "room_closed", "reason": "对局已结束"})
    await broadcast_room_list()


async def leave_room_internal(room, username):
    if room.has_spectator(username):
        room.remove_spectator(username)
        logger.info("%s stopped watching game room %s", username, room.id)
        await send_to_user(username, {"type": "room_closed", "reason": "已退出观战"})
        if not room.members:
            await dissolve_empty_room(room)
        return
    await room.finish_pending_settlement()
    if room.in_hand() and room.has_member(username):
        room.settle_leaving_rating(username)
    member = room.remove_member(username)
    if not member:
        return
    mid_hand = room.note_leave(username)
    set_escrow(username, room.id, None)
    settle_room_coins(room, username, member["stack"],
                      f"游戏厅离桌：{room.name}", member.get("paid"))
    await publish_ratings(room)
    logger.info("%s left game room %s", username, room.id)
    await send_to_user(username, {"type": "room_closed", "reason": "已离桌"})
    if not room.members:
        await dissolve_empty_room(room)
        return
    if mid_hand:
        await room.progress_game()
    else:
        await room.broadcast_views()
    await broadcast_room_list()


async def handle_list_rooms(websocket, state, data):
    await send_json(
        websocket,
        {"type": "room_list", "rooms": [room.summary() for room in game_rooms.values()]},
    )


async def handle_get_room(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    if rate_limited(state, "last_get_room", 0.2):
        return
    room = find_user_room(user["username"])
    if room:
        if room.has_spectator(user["username"]):
            await send_json(websocket, room.spectator_view(user["username"]))
        else:
            await send_json(websocket, room.view_for(user["username"]))
        await send_json(
            websocket,
            {
                "type": "room_chat_history",
                "room_id": room.id,
                "messages": list(room.chat),
            },
        )
    else:
        await send_json(websocket, {"type": "room_closed", "reason": ""})


async def handle_create_room(websocket, state, data):
    global room_seq
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_room_op", 3.0):
        await send_json(websocket, {"type": "game_error", "message": "操作太频繁，请稍后再试"})
        return
    username = user["username"]
    if find_user_room(username):
        await send_json(websocket, {"type": "game_error", "message": "你已在一个房间中，请先退出"})
        return
    name = str(data.get("name", "")).strip()[:20]
    game = str(data.get("game") or "holdem")
    if game not in ROOM_TYPES:
        game = "holdem"
    buy_in = parse_amount(data.get("buy_in"))
    blind = data.get("blind")
    blind = blind if blind in GAME_BLIND_PRESETS else 5
    if buy_in is None or buy_in < blind * 20:
        await send_json(
            websocket,
            {"type": "game_error", "message": f"买入至少需要 {blind * 20:,.0f} 金币（20 倍小盲注）"},
        )
        return
    # 玩法自定义规则原样交给房间类清洗（各引擎自己 sanitize）
    rules = data.get("rules")
    if not isinstance(rules, dict):
        rules = {}
    room_seq += 1
    room_id = int(time.time() * 1000) % 1_000_000_000 + room_seq
    room_name = name or f"{display_name(username)}的房间"
    try:
        with database() as conn, conn:
            balance = conn.execute(
                "SELECT coins FROM users WHERE username = ?", (username,)
            ).fetchone()[0] or 0.0
            if balance < buy_in:
                raise ValueError("金币不足，无法买入")
            adjust_coins(
                conn, username, -buy_in, "game_buyin", f"游戏厅买入：{room_name}",
                ref=f"room:{room_id}:{username}",
            )
    except ValueError as error:
        await send_json(websocket, {"type": "game_error", "message": str(error)})
        return
    room = create_room(
        game,
        room_id=room_id,
        name=room_name,
        owner=username,
        buy_in=buy_in,
        blind=blind,
        rules=rules,
    )
    attach_host(room)
    room.add_member(username, buy_in)
    game_rooms[room.id] = room
    set_escrow(username, room.id, buy_in)
    logger.info("game room %s created by %s", room.id, username)
    await send_json(websocket, {"type": "game_joined", "room": room.view_for(username)})
    await broadcast_room_list()


async def handle_join_room(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_room_op", 1.0):
        await send_json(websocket, {"type": "game_error", "message": "操作太频繁，请稍后再试"})
        return
    username = user["username"]
    if find_user_room(username):
        await send_json(websocket, {"type": "game_error", "message": "你已在一个房间中，请先退出"})
        return
    room = game_rooms.get(data.get("room_id"))
    if not room:
        await send_json(websocket, {"type": "game_error", "message": "房间不存在或已解散"})
        return
    if data.get("spectate"):
        # 开局后进入一律观战：不买入、不占座，随时可退出。
        if room.status not in ("playing", "settled"):
            await send_json(websocket, {"type": "game_error", "message": "对局尚未开始，暂不能观战"})
            return
        watched = data.get("watch")
        room.add_spectator(username, watched if isinstance(watched, str) else None)
        logger.info("%s watches game room %s", username, room.id)
        await send_json(websocket, {"type": "game_joined", "room": room.spectator_view(username)})
        await send_json(
            websocket,
            {
                "type": "room_chat_history",
                "room_id": room.id,
                "messages": list(room.chat),
            },
        )
        return
    if room.status != "waiting":
        await send_json(websocket, {"type": "game_error", "message": "游戏已开始，请以观战身份进入"})
        return
    if len(room.seating) >= getattr(room, "max_seats", GAME_MAX_PLAYERS):
        await send_json(websocket, {"type": "game_error", "message": "房间已满"})
        return
    buy_in = room.buy_in
    try:
        with database() as conn, conn:
            balance = conn.execute(
                "SELECT coins FROM users WHERE username = ?", (username,)
            ).fetchone()[0] or 0.0
            if balance < buy_in:
                raise ValueError(f"金币不足，进入该房间需要买入 {buy_in:,.2f} 金币")
            adjust_coins(
                conn, username, -buy_in, "game_buyin", f"游戏厅买入：{room.name}",
                ref=f"room:{room.id}:{username}",
            )
    except ValueError as error:
        await send_json(websocket, {"type": "game_error", "message": str(error)})
        return
    room.add_member(username, buy_in)
    set_escrow(username, room.id, buy_in)
    await send_json(websocket, {"type": "game_joined", "room": room.view_for(username)})
    await send_json(
        websocket,
        {
            "type": "room_chat_history",
            "room_id": room.id,
            "messages": list(room.chat),
        },
    )
    await room.broadcast_views()
    await broadcast_room_list()


async def handle_leave_room(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    room = find_user_room(user["username"])
    if not room:
        await send_json(websocket, {"type": "game_error", "message": "你不在任何房间中"})
        return
    if room.owner == user["username"]:
        reason = "房主流局" if room.status == "playing" else "房主解散了房间"
        await dissolve_room(room, reason)
        return
    await leave_room_internal(room, user["username"])


async def handle_start_game(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    room = find_user_room(user["username"])
    if not room or room.owner != user["username"]:
        await send_json(websocket, {"type": "game_error", "message": "只有房主可以开始游戏"})
        return
    if room.status == "playing":
        await send_json(websocket, {"type": "game_error", "message": "游戏已在进行中"})
        return
    try:
        logger.info("game room %s started by %s", room.id, user["username"])
        await room.start()
    except ValueError as error:
        await send_json(websocket, {"type": "game_error", "message": str(error)})
        return
    await broadcast_room_list()


async def handle_poker_action(websocket, state, data):
    user = state.get("user")
    if not user:
        return
    action = str(data.get("action", ""))
    room = find_user_room(user["username"])
    # 观战者只能看：所有对局动作仅对成员生效
    if not room or not room.has_member(user["username"]) or not room.in_hand():
        return
    # 不做限流：出牌/摸牌/补喊是毫秒级连招（UNO 摸到可出牌立刻出、
    # 快节奏下转眼又轮到自己），且引擎按回合校验，垃圾动作无效且廉价
    await room.perform_action(user["username"], action, data)


async def handle_pause_game(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    room = find_user_room(user["username"])
    if not room or room.owner != user["username"] or not room.in_hand():
        await send_json(websocket, {"type": "game_error", "message": "只有房主可以在进行中的牌局里暂停"})
        return
    if rate_limited(state, "last_game_admin", 0.5):
        return
    paused = bool(data.get("paused"))
    if paused == room.paused:
        return
    if paused:
        room.pause()
        logger.info("game room %s paused by %s", room.id, user["username"])
    else:
        room.resume()
        logger.info("game room %s resumed by %s", room.id, user["username"])
    await room.broadcast_views()
    await broadcast_room_list()


async def handle_settle_vote(websocket, state, data):
    """对局结束投票：过半数生效；票中可携带下一局盲注偏好，再来一局会按买入额重新买入。"""
    user = state.get("user")
    if not user:
        return
    room = find_user_room(user["username"])
    if not room or not room.has_member(user["username"]) or room.status not in ("playing", "settled"):
        return
    choice = str(data.get("choice") or "")
    if choice not in ("next", "dissolve"):
        return
    blind = data.get("blind")
    blind = blind if blind in GAME_BLIND_PRESETS else room.blind
    # 冷却刻意很短：投票本身幂等（同一个人只留最后一票），而连打多局时
    # 上一局的票和下一局的票可能只隔几十毫秒，卡太久会把合法投票丢掉
    if rate_limited(state, "last_settle_vote", 0.1):
        return
    executed = await room.cast_vote(user["username"], choice, blind)
    if executed == "dissolve":
        return
    await room.broadcast_views()


async def handle_hand_continue(websocket, state, data):
    """每手结束后的「继续下一手」：全员确认或 10 秒倒计时到点即开下一手。"""
    user = state.get("user")
    if not user:
        return
    room = find_user_room(user["username"])
    if not room or not room.has_member(user["username"]) or room.status != "playing":
        return
    # 不限流：连得快时两手之间可能只隔几十毫秒，而 mark_ready 自身幂等
    await room.mark_ready(user["username"])


async def handle_restart_game(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    room = find_user_room(user["username"])
    if not room or room.owner != user["username"]:
        await send_json(websocket, {"type": "game_error", "message": "只有房主可以重新开始"})
        return
    if room.status != "playing":
        await send_json(websocket, {"type": "game_error", "message": "游戏尚未开始"})
        return
    if rate_limited(state, "last_game_admin", 0.5):
        return
    logger.info("game room %s restarted by %s", room.id, user["username"])
    await room.restart()


async def handle_watch_player(websocket, state, data):
    """观战者切换第一视角：换发被看玩家的私有视图（不含可操作字段）。"""
    user = state.get("user")
    if not user:
        return
    room = find_user_room(user["username"])
    if not room or not room.has_spectator(user["username"]):
        await send_json(websocket, {"type": "game_error", "message": "你不在观战中"})
        return
    if rate_limited(state, "last_watch_switch", 0.2):
        return
    watched = str(data.get("username") or "")
    if watched not in room.members:
        await send_json(websocket, {"type": "game_error", "message": "该玩家已不在本房间"})
        return
    room.spectators[user["username"]] = watched
    await send_json(websocket, room.spectator_view(user["username"]))


# 常用汉字拼音首字母的 GB2312 区位上界（覆盖全部 6763 个一级/二级汉字）
_PINYIN_BOUNDS = (
    (0xB0C5, "A"), (0xB2C1, "B"), (0xB4EE, "C"), (0xB6EA, "D"), (0xB7A2, "E"),
    (0xB8C1, "F"), (0xB9FE, "G"), (0xBBF7, "H"), (0xBFA6, "J"), (0xC0AC, "K"),
    (0xC2E8, "L"), (0xC4C3, "M"), (0xC5B6, "N"), (0xC5BE, "O"), (0xC6DA, "P"),
    (0xC8BB, "Q"), (0xC8F6, "R"), (0xCBFA, "S"), (0xCDDA, "T"), (0xCEF4, "W"),
    (0xD1B9, "X"), (0xD4D1, "Y"), (0xD7FA, "Z"),
)


def pinyin_initial(text):
    """取文本首个可排序字符的字母：常用汉字查 GB2312 区位，英文取首字母。"""
    for ch in text:
        try:
            code = ch.encode("gb2312")
        except UnicodeEncodeError:
            continue
        if len(code) == 2:
            value = (code[0] << 8) | code[1]
            if 0xB0A1 <= value <= 0xD7F9:
                for boundary, letter in _PINYIN_BOUNDS:
                    if value < boundary:
                        return letter
                return "Z"
        if ch.isascii():
            return ch.upper()
        return "#"
    return "#"


async def handle_list_users(websocket, state, data):
    """转账目标候选：全部注册用户，按昵称（无昵称用用户名）首字母拼音排序。"""
    with database() as conn:
        rows = conn.execute(
            "SELECT username, nickname, avatar FROM users ORDER BY username"
        ).fetchall()
    users = [
        {"username": u, "nickname": n or "", "avatar": a or ""}
        for u, n, a in rows
    ]
    users.sort(key=lambda u: (
        pinyin_initial(u["nickname"] or u["username"]),
        u["nickname"] or u["username"],
        u["username"],
    ))
    await send_json(websocket, {"type": "user_list", "users": users})


async def handle_room_chat(websocket, state, data):
    """房间内聊天：只广播给房间成员，记录在内存里随房间销毁。"""
    user = state.get("user")
    if not user:
        return
    room = find_user_room(user["username"])
    if not room:
        return
    if rate_limited(state, "last_room_message", 1.0):
        await send_json(websocket, {"type": "error", "message": "发送太快了"})
        return
    text = str(data.get("text", "")).strip()[:MAX_CHAT_LENGTH]
    if not text:
        return
    message = {
        "type": "room_chat",
        "room_id": room.id,
        "username": user["username"],
        "nickname": user.get("nickname") or "",
        "role": user["role"],
        "spectator": room.has_spectator(user["username"]),
        "text": text,
        "time": time.strftime("%m/%d %H:%M"),
    }
    room.chat.append(message)
    await room.broadcast_payload(dict(message))


def cancel_leave_timer(room_id, username):
    handle = room_leave_timers.pop((room_id, username), None)
    if handle:
        handle.cancel()


def fire_leave_timer(room_id, username):
    room_leave_timers.pop((room_id, username), None)
    asyncio.ensure_future(delayed_room_cleanup(room_id, username))


async def delayed_room_cleanup(room_id, username):
    """断线宽限期内没有回到游戏厅（或直播间），再结算房间去留。"""
    await asyncio.sleep(GAME_DISCONNECT_GRACE)
    room = game_rooms.get(room_id)
    if not room:
        return
    if not room.has_member(username) and not room.has_spectator(username):
        return
    for client_state in clients.values():
        other = client_state.get("user")
        if other and other["username"] == username:
            return
    if room.has_spectator(username):
        # 观战者断线不牵动对局，直接移除记录即可
        room.remove_spectator(username)
        return
    if room.owner == username:
        await dissolve_room(room, "房主离开游戏厅较久")
    else:
        await leave_room_internal(room, username)


async def cleanup_rooms_on_disconnect(state):
    user = state.get("user")
    if not user:
        return
    username = user["username"]
    for client_state in clients.values():
        other = client_state.get("user")
        if other and other["username"] == username:
            return
    room = find_user_room(username)
    if not room:
        return
    # 页面间跳转（游戏厅 <-> 直播间）会短暂断线，延迟再结算，回到页面即取消
    cancel_leave_timer(room.id, username)
    room_leave_timers[(room.id, username)] = (
        asyncio.get_running_loop().call_later(
            GAME_DISCONNECT_GRACE,
            fire_leave_timer,
            room.id,
            username,
        )
    )


def on_user_authenticated(username):
    """登录/恢复会话时取消该用户的离桌倒计时。"""
    room = find_user_room(username)
    if room:
        cancel_leave_timer(room.id, username)


async def handle_admin_set_coins(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if user.get("role") not in ("admin", "streamer"):
        await send_json(websocket, {"type": "coins_error", "message": "没有权限执行此操作"})
        return
    target = str(data.get("username", "")).strip()
    try:
        coins = round(float(data.get("coins")), 2)
    except (TypeError, ValueError):
        coins = -1.0
    if not target:
        await send_json(websocket, {"type": "coins_error", "message": "请输入用户名"})
        return
    if not math.isfinite(coins) or coins < 0:
        await send_json(
            websocket,
            {"type": "coins_error", "message": "金币数量无效（不能低于 0）"},
        )
        return
    try:
        with database() as conn, conn:
            old = conn.execute(
                "SELECT coins FROM users WHERE username = ?", (target,)
            ).fetchone()
            if old is None:
                raise ValueError("用户不存在")
            delta = round(coins - (old[0] or 0.0), 2)
            conn.execute(
                "UPDATE users SET coins = ? WHERE username = ?", (coins, target)
            )
            record_coins(conn, target, delta, coins, "admin", "管理员调整")
    except ValueError as error:
        await send_json(websocket, {"type": "coins_error", "message": str(error)})
        return
    logger.info("admin %s set coins of %s to %.2f", user["username"], target, coins)
    await send_json(
        websocket, {"type": "admin_coins_done", "username": target, "coins": coins}
    )
    await push_balance(target, coins)


async def handle_list_invites(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    with database() as conn:
        rows = conn.execute(
            "SELECT code, created_at FROM invite_codes "
            "WHERE created_by = ? AND used_by IS NULL ORDER BY created_at DESC",
            (user["username"],),
        ).fetchall()
    await send_json(
        websocket,
        {
            "type": "invite_list",
            "codes": [
                {"code": code, "created_at": created_at} for code, created_at in rows
            ],
        },
    )


async def handle_create_invite(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    if rate_limited(state, "last_invite_create", 5.0):
        await send_json(websocket, {"type": "invite_error", "message": "操作太频繁，请稍后再试"})
        return
    with database() as conn, conn:
        unused = conn.execute(
            "SELECT COUNT(*) FROM invite_codes "
            "WHERE created_by = ? AND used_by IS NULL",
            (user["username"],),
        ).fetchone()[0]
        if unused >= INVITE_UNUSED_LIMIT:
            await send_json(
                websocket,
                {"type": "invite_error", "message": "未使用的邀请码已达上限（5 个），请先用掉一些"},
            )
            return
        code = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO invite_codes (code, created_at, created_by) VALUES (?, ?, ?)",
            (code, int(time.time()), user["username"]),
        )
    logger.info("invite created by %s", user["username"])
    await send_json(websocket, {"type": "invite_created", "code": code})


async def handle_chat(websocket, state, data):
    user = state.get("user")
    if not user:
        await send_json(websocket, {"type": "auth_error", "message": "请先登录"})
        return
    text = str(data.get("text", "")).strip()[:MAX_CHAT_LENGTH]
    if not text:
        return
    if rate_limited(state, "last_message", CHAT_COOLDOWN):
        await send_json(websocket, {"type": "error", "message": "发送太快了"})
        return
    message = {
        "type": "chat",
        "username": user["username"],
        "nickname": user.get("nickname") or "",
        "role": user["role"],
        "text": text,
        "time": time.strftime("%m/%d %H:%M"),
    }
    history.append(message)
    logger.info("chat message from %s (%d chars)", user["username"], len(text))
    await broadcast(message)


handlers = {
    "register": handle_register,
    "login": handle_login,
    "resume": handle_resume,
    "logout": handle_logout,
    "update_profile": handle_update_profile,
    "get_profile": handle_get_profile,
    "delete_account": handle_delete_account,
    "get_online": handle_get_online,
    "list_invites": handle_list_invites,
    "create_invite": handle_create_invite,
    "chat": handle_chat,
    "get_finance": handle_get_finance,
    "get_daily_rewards": handle_get_daily_rewards,
    "daily_checkin": handle_daily_checkin,
    "draw_lottery": handle_draw_lottery,
    "get_estate": handle_get_estate,
    "estate_buy": handle_estate_buy,
    "estate_plant": handle_estate_plant,
    "estate_harvest": handle_estate_harvest,
    "estate_sell": handle_estate_sell,
    "estate_sell_all": handle_estate_sell_all,
    "estate_buy_tool": handle_estate_buy_tool,
    "estate_upgrade_tool": handle_estate_upgrade_tool,
    "estate_repair_tool": handle_estate_repair_tool,
    "estate_pet": handle_estate_pet,
    "estate_start_fishing": handle_estate_start_fishing,
    "estate_finish_fishing": handle_estate_finish_fishing,
    "estate_start_mining": handle_estate_start_mining,
    "estate_mine_cell": handle_estate_mine_cell,
    "estate_finish_mining": handle_estate_finish_mining,
    "estate_list_visits": handle_estate_list_visits,
    "estate_enter_visit": handle_estate_enter_visit,
    "estate_leave_visit": handle_estate_leave_visit,
    "estate_visit_move": handle_estate_visit_move,
    "estate_steal_crop": handle_estate_steal_crop,
    "estate_get_notifications": handle_estate_get_notifications,
    "estate_mark_notifications_read": handle_estate_mark_notifications_read,
    "claim_holdem_reward": handle_claim_holdem_reward,
    "get_rating_history": handle_get_rating_history,
    "get_rating_leaderboard": handle_get_rating_leaderboard,
    "get_asset_leaderboard": handle_get_asset_leaderboard,
    "transfer_coins": handle_transfer_coins,
    "get_bet": handle_get_bet,
    "create_bet": handle_create_bet,
    "place_bet": handle_place_bet,
    "settle_bet": handle_settle_bet,
    "cancel_bet": handle_cancel_bet,
    "close_bet": handle_close_bet,
    "admin_set_coins": handle_admin_set_coins,
    "list_rooms": handle_list_rooms,
    "get_room": handle_get_room,
    "create_room": handle_create_room,
    "join_room": handle_join_room,
    "leave_room": handle_leave_room,
    "start_game": handle_start_game,
    "poker_action": handle_poker_action,
    "pause_game": handle_pause_game,
    "restart_game": handle_restart_game,
    "settle_vote": handle_settle_vote,
    "hand_continue": handle_hand_continue,
    "room_chat": handle_room_chat,
    "watch_player": handle_watch_player,
    "list_users": handle_list_users,
}


def connection_client(websocket):
    """兼容新旧 websockets 实现取连接路径，识别游戏厅独立页。"""
    request = getattr(websocket, "request", None)
    path = getattr(request, "path", None) or getattr(websocket, "path", "")
    return "game" if "client=game" in str(path) else ""


async def handler(websocket):
    state = {
        "user": None,
        "client": connection_client(websocket),
        "send_lock": asyncio.Lock(),
        "last_message": 0.0,
        "last_auth_attempt": 0.0,
        "last_profile_update": 0.0,
        "last_invite_create": 0.0,
        "last_transfer": 0.0,
        "last_bet_action": 0.0,
        "last_estate_move": 0.0,
    }
    clients[websocket] = state
    logger.info("connection opened; online=%d", len(clients))
    await send_json(websocket, {"type": "history", "messages": list(history)})
    await broadcast_online_count()
    try:
        async for raw_message in websocket:
            try:
                data = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            action = handlers.get(data.get("type"))
            if action:
                await action(websocket, state, data)
    except websockets.ConnectionClosed:
        pass
    finally:
        await leave_estate_channel(websocket, state)
        clients.pop(websocket, None)
        logger.info("connection closed; online=%d", len(clients))
        await cleanup_rooms_on_disconnect(state)
        await broadcast_online_count()


async def main():
    global active_bet
    init_db()
    refund_game_escrows()
    active_bet = load_open_bet()
    if active_bet:
        logger.info(
            "resumed open bet #%d from %s: %s",
            active_bet["id"],
            active_bet["creator"],
            active_bet["question"],
        )
    logger.info("chat server listening on ws://%s:%d", HOST, PORT)
    async with websockets.serve(
        handler,
        HOST,
        PORT,
        max_size=300_000,
        max_queue=32,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
    ):
        watcher = asyncio.create_task(bet_close_watcher())
        try:
            await asyncio.Future()
        finally:
            watcher.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
