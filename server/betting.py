"""竞猜协议、持久化与自动封盘；当前竞猜归实例所有。"""
import asyncio
import json
import logging
import time

from games.base import parse_amount
from server.wallet import adjust_coins, merge_ref_coins

logger = logging.getLogger("live-chat")

BET_MIN_STAKE = 10.0
BET_MAX_OPTIONS = 6
BET_QUESTION_LIMIT = 60
BET_OPTION_LIMIT = 20
BET_CLOSE_MAX_MINUTES = 1440


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


class Betting:
    def __init__(self, *, database, send_json, broadcast, broadcast_system,
                 display_name, rate_limited):
        self.database = database
        self.send_json = send_json
        self.broadcast = broadcast
        self.broadcast_system = broadcast_system
        self.display_name = display_name
        self.rate_limited = rate_limited
        self.active_bet = None

    def handlers(self):
        return {
            "get_bet": self.handle_get_bet,
            "create_bet": self.handle_create_bet,
            "place_bet": self.handle_place_bet,
            "settle_bet": self.handle_settle_bet,
            "cancel_bet": self.handle_cancel_bet,
            "close_bet": self.handle_close_bet,
        }

    def load_open_bet(self):
        with self.database() as conn:
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

    async def close_betting(self, bet, reason="到时封盘"):
        """封盘：停止接受新投注，已投注的保持不变，等发起者结账。"""
        if bet.get("closed_at"):
            return
        closed_at = int(time.time())
        with self.database() as conn, conn:
            conn.execute(
                "UPDATE bets SET closed_at = ? WHERE id = ?", (closed_at, bet["id"])
            )
        bet["closed_at"] = closed_at
        logger.info("bet closed (%s): %s", reason, bet["question"])
        await self.broadcast({"type": "bet_update", "bet": bet_public_state(bet)})
        await self.broadcast_system(f"🔒 竞猜已封盘：{bet['question']}｜等待发起者结账")

    async def bet_close_watcher(self):
        """每秒检查一次进行中的竞猜是否到了封盘时间（也覆盖重启后补封盘）。"""
        while True:
            await asyncio.sleep(1)
            bet = self.active_bet
            if bet and not bet.get("closed_at"):
                deadline = bet_closes_at(bet)
                if deadline is not None and time.time() >= deadline:
                    try:
                        await self.close_betting(bet, "到时自动封盘")
                    except Exception:
                        logger.exception("auto close bet failed")

    async def handle_get_bet(self, websocket, state, data):
        await self.send_json(
            websocket, {"type": "bet_state", "bet": bet_public_state(self.active_bet)}
        )

    async def handle_create_bet(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if self.rate_limited(state, "last_bet_action", 2.0):
            await self.send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
            return
        if self.active_bet:
            await self.send_json(
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
            await self.send_json(websocket, {"type": "bet_error", "message": "请输入竞猜问题"})
            return
        if len(options) < 2:
            await self.send_json(websocket, {"type": "bet_error", "message": "至少需要两个选项"})
            return
        if len(options) > BET_MAX_OPTIONS:
            await self.send_json(
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
        with self.database() as conn, conn:
            cursor = conn.execute(
                "INSERT INTO bets (question, options, creator, status, created_at, close_delay) "
                "VALUES (?, ?, ?, 'open', ?, ?)",
                (question, json.dumps(options, ensure_ascii=False), user["username"], now,
                 close_minutes),
            )
            bet_id = cursor.lastrowid
        self.active_bet = {
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
        await self.send_json(websocket, {"type": "bet_created"})
        await self.broadcast({"type": "bet_update", "bet": bet_public_state(self.active_bet)})
        closing_note = (
            f"，{close_minutes} 分钟后自动封盘" if close_minutes else ""
        )
        await self.broadcast_system(
            f"🎲 {self.display_name(user['username'])} 发起了竞猜：{question}{closing_note}"
        )

    async def handle_place_bet(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if self.rate_limited(state, "last_bet_action", 1.0):
            await self.send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
            return
        bet = self.active_bet
        if not bet:
            await self.send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
            return
        if bet_closed(bet):
            await self.send_json(websocket, {"type": "bet_error", "message": "竞猜已封盘，无法参与"})
            return
        try:
            option_index = int(data.get("option_index"))
        except (TypeError, ValueError):
            option_index = -1
        if not 0 <= option_index < len(bet["options"]):
            await self.send_json(websocket, {"type": "bet_error", "message": "请选择一个选项"})
            return
        amount = parse_amount(data.get("amount"))
        if amount is None:
            await self.send_json(websocket, {"type": "bet_error", "message": "投注金额无效"})
            return
        username = user["username"]
        try:
            with self.database() as conn, conn:
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
            await self.send_json(websocket, {"type": "bet_error", "message": str(error)})
            return
        bet["entries"].append(
            {"username": username, "option_index": option_index, "amount": amount}
        )
        user["coins"] = new_balance
        await self.send_json(
            websocket,
            {
                "type": "bet_placed",
                "coins": new_balance,
                "option_index": option_index,
                "amount": amount,
            },
        )
        await self.broadcast({"type": "bet_update", "bet": bet_public_state(bet)})

    async def cancel_active_bet(self, reason, message=None, refund_prefix="竞猜取消"):
        bet = self.active_bet
        if not bet:
            return
        with self.database() as conn, conn:
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
        self.active_bet = None
        logger.info("bet cancelled: %s (%s)", bet["question"], reason)
        await self.broadcast({"type": "bet_update", "bet": None})
        await self.broadcast(
            {"type": "bet_cancelled", "question": bet["question"], "reason": reason}
        )
        await self.broadcast_system(message or f"🎲 竞猜已取消（{reason}），投注已退还")

    async def handle_settle_bet(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        bet = self.active_bet
        if not bet:
            await self.send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
            return
        if user["username"] != bet["creator"]:
            await self.send_json(websocket, {"type": "bet_error", "message": "只有发起者可以结账"})
            return
        try:
            correct_index = int(data.get("correct_index"))
        except (TypeError, ValueError):
            correct_index = -1
        if not 0 <= correct_index < len(bet["options"]):
            await self.send_json(websocket, {"type": "bet_error", "message": "请选择正确选项"})
            return
        question = bet["question"]
        answer = bet["options"][correct_index]
        results = []
        winner_texts = []
        loser_texts = []
        refunded = False
        with self.database() as conn, conn:
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
                    winner_texts.append(f"{self.display_name(name)} +{share:.2f}")
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
                    loser_texts.append(f"{self.display_name(name)} -{amount:.2f}")
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
        self.active_bet = None
        logger.info("bet settled: %s answer=%s", question, answer)
        await self.broadcast(
            {
                "type": "bet_settled",
                "question": question,
                "answer": answer,
                "refunded": refunded,
                "results": results,
            }
        )
        if refunded:
            await self.broadcast_system(f"🎲 竞猜结账：{question}｜无人猜对，投注已退还")
        else:
            await self.broadcast_system(
                f"🎲 竞猜结账：{question}｜答案：{answer}｜"
                f"赢家：{'、'.join(winner_texts) or '无'}｜"
                f"输家：{'、'.join(loser_texts) or '无'}"
            )

    async def handle_cancel_bet(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if self.rate_limited(state, "last_bet_action", 2.0):
            await self.send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
            return
        bet = self.active_bet
        if not bet:
            await self.send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
            return
        if user["username"] != bet["creator"]:
            await self.send_json(websocket, {"type": "bet_error", "message": "只有发起者可以流局"})
            return
        logger.info("bet drawn by %s: %s", user["username"], bet["question"])
        await self.cancel_active_bet(
            "发起者流局",
            message=f"🎲 竞猜流局：{bet['question']}｜投注已全部退还",
            refund_prefix="竞猜流局",
        )

    async def handle_close_bet(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if self.rate_limited(state, "last_bet_action", 2.0):
            await self.send_json(websocket, {"type": "bet_error", "message": "操作太频繁，请稍后再试"})
            return
        bet = self.active_bet
        if not bet:
            await self.send_json(websocket, {"type": "bet_error", "message": "当前没有进行中的竞猜"})
            return
        if user["username"] != bet["creator"]:
            await self.send_json(websocket, {"type": "bet_error", "message": "只有发起者可以封盘"})
            return
        await self.close_betting(bet, "发起者提前封盘")
