"""账号、密码与会话存储；不依赖 WebSocket 宿主。"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time

from config import get
from games.rating import rating_info
from server.wallet import adjust_coins

SESSION_TTL = 30 * 24 * 60 * 60
NEW_USER_COINS = float(get("economy.new_user_coins", env="NEW_USER_COINS", default=1000))


def hash_password(password, salt=None):
    salt_bytes = os.urandom(16) if salt is None else base64.b64decode(salt)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, 310_000
    )
    return base64.b64encode(digest).decode(), base64.b64encode(salt_bytes).decode()


def valid_username(username):
    return bool(re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9_-]{2,20}", username))


class Accounts:
    """账号、资料和会话持久化，使用调用方注入的数据库。"""

    def __init__(self, database):
        self.database = database

    def register_user(self, username, password, invite_code=""):
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
            with self.database() as conn, conn:
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

    def authenticate_user(self, username, password):
        with self.database() as conn:
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

    def get_profile(self, username):
        with self.database() as conn:
            row = conn.execute(
                "SELECT nickname, avatar, rating_score, rating_games FROM users WHERE username = ?", (username,)
            ).fetchone()
        if not row:
            return {"username": username, "nickname": "", "avatar": ""}
        return {"username": username, "nickname": row[0] or "", "avatar": row[1] or "",
                "rating": rating_info(row[2], row[3])}

    def create_session(self, username):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = int(time.time()) + SESSION_TTL
        with self.database() as conn, conn:
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()[0]
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
        return token

    def resume_user(self, token):
        if not token:
            return None
        token_hash = hashlib.sha256(str(token).encode()).hexdigest()
        with self.database() as conn:
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

    def display_name(self, username):
        profile = self.get_profile(username)
        return profile["nickname"] or username
