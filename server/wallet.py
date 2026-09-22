"""金币与流水原语：使用调用方的连接，不开启或提交独立事务。"""
import json
import logging
import time

from games.base import parse_amount
from server.transport import rate_limited

FINANCE_HISTORY_LIMIT = 60
TRANSFER_COOLDOWN = 2.0
logger = logging.getLogger("live-chat")


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


class WalletProtocol:
    def __init__(self, database, hub, accounts, chat):
        self.database = database
        self.hub = hub
        self.accounts = accounts
        self.chat = chat

    def handlers(self):
        return {
            "get_finance": self.handle_get_finance,
            "transfer_coins": self.handle_transfer_coins,
            "list_users": self.handle_list_users,
        }

    async def push_balance(self, username, coins):
        payload = json.dumps(
            {"type": "coins", "username": username, "coins": round(coins, 2)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for socket, client_state in list(self.hub.clients.items()):
            user = client_state.get("user")
            if user and user["username"] == username:
                await self.hub.send_encoded(socket, payload)

    async def handle_get_finance(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        with self.database() as conn:
            row = conn.execute(
                "SELECT coins FROM users WHERE username = ?", (user["username"],)
            ).fetchone()
            rows = conn.execute(
                "SELECT amount, balance, kind, detail, created_at FROM coin_transactions "
                "WHERE username = ? ORDER BY id DESC LIMIT ?",
                (user["username"], FINANCE_HISTORY_LIMIT),
            ).fetchall()
        coins = round(row[0] or 0.0, 2) if row else 0.0
        await self.hub.send_json(
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

    async def handle_transfer_coins(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_transfer", TRANSFER_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "coins_error", "message": "操作太频繁，请稍后再试"})
            return
        target = str(data.get("to", "")).strip()
        amount = parse_amount(data.get("amount"))
        if not target or target == user["username"]:
            await self.hub.send_json(websocket, {"type": "coins_error", "message": "请输入正确的对方用户名"})
            return
        if amount is None:
            await self.hub.send_json(websocket, {"type": "coins_error", "message": "转账金额无效"})
            return
        sender = user["username"]
        try:
            with self.database() as conn, conn:
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
            await self.hub.send_json(websocket, {"type": "coins_error", "message": str(error)})
            return
        user["coins"] = sender_balance
        logger.info("transfer %.2f from %s to %s", amount, sender, row[0])
        await self.hub.send_json(websocket, {"type": "transfer_success", "coins": sender_balance})
        await self.push_balance(row[0], target_balance)
        await self.chat.broadcast_system(
            f"💰 {self.accounts.display_name(sender)} 转账 {amount:,.2f} 金币给 {self.accounts.display_name(row[0])}",
            danmaku=True,
        )

    async def handle_list_users(self, websocket, state, data):
        """转账目标候选：全部注册用户，按昵称（无昵称用用户名）首字母拼音排序。"""
        with self.database() as conn:
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
        await self.hub.send_json(websocket, {"type": "user_list", "users": users})
