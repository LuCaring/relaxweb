import logging
import time

from games.rating import rating_change, rating_info
from holdem_stats import record_holdem_hand
from rewards import record_holdem_turnover
from server.wallet import adjust_coins, merge_ref_coins

logger = logging.getLogger("live-chat")


class Settlement:
    """维护牌局的持久化与退款事务，不依赖在线连接或协议处理器。"""

    def __init__(self, database):
        self.database = database

    def set_escrow(self, username, room_id, amount):
        with self.database() as conn, conn:
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

    def record_hand_ratings(self, room, hand_id, starts, endings, stakes=None, statistics=None):
        """评分、统计、下注流水及结算筹码在同一事务落库，重试不会重复写入。"""
        results = {}
        with self.database() as conn, conn:
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

    def refund_game_escrows(self):
        """服务器重启后房间不再存在，把所有托管中的游戏筹码退还为金币。"""
        with self.database() as conn, conn:
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

    def settle_room_coins(self, room, username, refund, detail, paid=None):
        """离桌/解散时结算该房间的流水：买入（含重新买入）与退款合并为一条净额记录。

        净额为零（如未开局的流局退款）时只删除扣款条目，不留痕。
        """
        paid = room.buy_in if paid is None else paid
        with self.database() as conn, conn:
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
