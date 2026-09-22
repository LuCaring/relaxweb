import hashlib
import logging
import re
import time

from server.accounts import valid_username
from server.transport import rate_limited

MAX_AVATAR_LENGTH = 200_000
AVATAR_PATTERN = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$")
AUTH_COOLDOWN = 1.5
REGISTER_IP_LIMIT = 10
REGISTER_IP_WINDOW = 3600
logger = logging.getLogger("live-chat")


class AuthProtocol:
    def __init__(self, database, hub, accounts, rooms, betting):
        self.database = database
        self.hub = hub
        self.accounts = accounts
        self.rooms = rooms
        self.betting = betting
        self.register_ip_times = {}

    def handlers(self):
        return {
            "register": self.handle_register,
            "login": self.handle_login,
            "resume": self.handle_resume,
            "logout": self.handle_logout,
            "update_profile": self.handle_update_profile,
            "get_profile": self.handle_get_profile,
            "delete_account": self.handle_delete_account,
        }

    async def handle_register(self, websocket, state, data):
        ip = (websocket.remote_address or ("?", 0))[0]
        now = time.monotonic()
        recent = [t for t in self.register_ip_times.get(ip, []) if now - t < REGISTER_IP_WINDOW]
        self.register_ip_times[ip] = recent
        if len(recent) >= REGISTER_IP_LIMIT:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "注册太频繁，请稍后再试"})
            return
        if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
            return
        success, message = self.accounts.register_user(
            str(data.get("username", "")).strip(),
            str(data.get("password", "")),
            str(data.get("invite_code", "")),
        )
        if success:
            recent.append(now)
        await self.hub.send_json(
            websocket,
            {"type": "register_success" if success else "auth_error", "message": message},
        )

    async def handle_login(self, websocket, state, data):
        if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
            return
        user = self.accounts.authenticate_user(
            str(data.get("username", "")).strip(), str(data.get("password", ""))
        )
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "用户名或密码错误"})
            return
        state["user"] = user
        token = self.accounts.create_session(user["username"])
        self.rooms.on_user_authenticated(user["username"])
        logger.info("user login: %s", user["username"])
        profile = self.accounts.get_profile(user["username"])
        await self.hub.send_json(
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

    async def handle_resume(self, websocket, state, data):
        user = self.accounts.resume_user(data.get("token"))
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_expired"})
            return
        state["user"] = user
        self.rooms.on_user_authenticated(user["username"])
        await self.hub.send_json(
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

    async def handle_logout(self, websocket, state, data):
        if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "操作太频繁，请稍后再试"})
            return
        token = data.get("token")
        if token:
            token_hash = hashlib.sha256(str(token).encode()).hexdigest()
            with self.database() as conn, conn:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
                )
        state["user"] = None
        logger.info("user logout")
        await self.hub.send_json(websocket, {"type": "logout_success"})

    async def handle_update_profile(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_profile_update", 1.0):
            await self.hub.send_json(websocket, {"type": "profile_error", "message": "操作太频繁，请稍后再试"})
            return
        updates, params = [], []
        if "nickname" in data:
            nickname = str(data.get("nickname") or "").strip()
            if nickname and not valid_username(nickname):
                await self.hub.send_json(
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
                await self.hub.send_json(
                    websocket,
                    {"type": "profile_error", "message": "头像格式不支持或过大"},
                )
                return
            updates.append("avatar = ?")
            params.append(avatar)
        if updates:
            params.append(user["username"])
            with self.database() as conn, conn:
                conn.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params
                )
        profile = self.accounts.get_profile(user["username"])
        state["user"]["nickname"] = profile["nickname"]
        state["user"]["avatar"] = profile["avatar"]
        logger.info("profile updated: %s", user["username"])
        await self.hub.send_json(websocket, {"type": "profile_updated", **profile})
        await self.hub.broadcast({"type": "profile", **profile})

    async def handle_get_profile(self, websocket, state, data):
        await self.hub.send_json(
            websocket,
            {"type": "profile", **self.accounts.get_profile(str(data.get("username", "")))},
        )

    async def handle_delete_account(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_auth_attempt", AUTH_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "account_error", "message": "操作太频繁，请稍后再试"})
            return
        if not self.accounts.authenticate_user(user["username"], str(data.get("password", ""))):
            await self.hub.send_json(websocket, {"type": "account_error", "message": "密码错误"})
            return
        # 房间仍以用户名标识参局者；禁止删除后同名新账号继承未结算的旧手。
        # 此检查到同步删除之间不 await，避免另一连接在注销过程中重新入座。
        if self.rooms.find_user_room(user["username"]):
            await self.hub.send_json(websocket, {"type": "account_error", "message": "请先离开或解散游戏房间，再注销账号"})
            return
        if self.betting.active_bet and self.betting.active_bet["creator"] == user["username"]:
            await self.betting.cancel_active_bet("发起者已注销账号")
            if self.rooms.find_user_room(user["username"]):
                await self.hub.send_json(websocket, {"type": "account_error", "message": "请先离开或解散游戏房间，再注销账号"})
                return
        with self.database() as conn, conn:
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
        for socket, client_state in self.hub.clients.items():
            if (client_state.get("user") or {}).get("username") == user["username"]:
                client_state["user"] = None
                if socket is not websocket:
                    other_sockets.append(socket)
        logger.info("account deleted: %s", user["username"])
        await self.hub.send_json(websocket, {"type": "account_deleted"})
        for socket in other_sockets:
            await self.hub.send_json(socket, {"type": "account_deleted"})
