import logging
import math
import secrets
import time

from server.transport import rate_limited
from server.wallet import record_coins

INVITE_UNUSED_LIMIT = 5
logger = logging.getLogger("live-chat")


class AdminProtocol:
    def __init__(self, database, hub, wallet):
        self.database = database
        self.hub = hub
        self.wallet = wallet

    def handlers(self):
        return {
            "admin_set_coins": self.handle_admin_set_coins,
            "list_invites": self.handle_list_invites,
            "create_invite": self.handle_create_invite,
        }

    async def handle_admin_set_coins(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if user.get("role") not in ("admin", "streamer"):
            await self.hub.send_json(websocket, {"type": "coins_error", "message": "没有权限执行此操作"})
            return
        target = str(data.get("username", "")).strip()
        try:
            coins = round(float(data.get("coins")), 2)
        except (TypeError, ValueError):
            coins = -1.0
        if not target:
            await self.hub.send_json(websocket, {"type": "coins_error", "message": "请输入用户名"})
            return
        if not math.isfinite(coins) or coins < 0:
            await self.hub.send_json(
                websocket,
                {"type": "coins_error", "message": "金币数量无效（不能低于 0）"},
            )
            return
        try:
            with self.database() as conn, conn:
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
            await self.hub.send_json(websocket, {"type": "coins_error", "message": str(error)})
            return
        logger.info("admin %s set coins of %s to %.2f", user["username"], target, coins)
        await self.hub.send_json(
            websocket, {"type": "admin_coins_done", "username": target, "coins": coins}
        )
        await self.wallet.push_balance(target, coins)

    async def handle_list_invites(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        with self.database() as conn:
            rows = conn.execute(
                "SELECT code, created_at FROM invite_codes "
                "WHERE created_by = ? AND used_by IS NULL ORDER BY created_at DESC",
                (user["username"],),
            ).fetchall()
        await self.hub.send_json(
            websocket,
            {
                "type": "invite_list",
                "codes": [
                    {"code": code, "created_at": created_at} for code, created_at in rows
                ],
            },
        )

    async def handle_create_invite(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_invite_create", 5.0):
            await self.hub.send_json(websocket, {"type": "invite_error", "message": "操作太频繁，请稍后再试"})
            return
        with self.database() as conn, conn:
            unused = conn.execute(
                "SELECT COUNT(*) FROM invite_codes "
                "WHERE created_by = ? AND used_by IS NULL",
                (user["username"],),
            ).fetchone()[0]
            if unused >= INVITE_UNUSED_LIMIT:
                await self.hub.send_json(
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
        await self.hub.send_json(websocket, {"type": "invite_created", "code": code})
