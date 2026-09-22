from games.rating import TIERS, rating_info
from holdem_stats import load_holdem_stats, public_holdem_stats


class Ranking:
    def __init__(self, database, hub):
        self.database = database
        self.hub = hub

    def handlers(self):
        return {
            "get_rating_history": self.handle_get_rating_history,
            "get_rating_leaderboard": self.handle_get_rating_leaderboard,
            "get_asset_leaderboard": self.handle_get_asset_leaderboard,
        }

    def get_rating(self, username):
        with self.database() as conn:
            row = conn.execute("SELECT rating_score, rating_games FROM users WHERE username = ?",
                               (username,)).fetchone()
        return rating_info(*row) if row else None

    async def publish_ratings(self, room):
        for username in tuple(room.pending_rating_updates):
            room.pending_rating_updates.discard(username)
            info = self.get_rating(username)
            for client_state in self.hub.clients.values():
                user = client_state.get("user")
                if user and user["username"] == username:
                    user["rating"] = info
            await self.hub.broadcast({"type": "rating_update", "username": username, "rating": info})

    async def handle_get_rating_history(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        with self.database() as conn:
            rows = conn.execute(
                "SELECT game_type, room_name, hand_no, initial, final, delta, score, games, created_at "
                "FROM rating_history WHERE user_id = (SELECT id FROM users WHERE username = ?) "
                "ORDER BY id DESC LIMIT 20", (user["username"],),
            ).fetchall()
        await self.hub.send_json(websocket, {"type": "rating_history", "rating": self.get_rating(user["username"]),
            "entries": [{"game_type": r[0], "room_name": r[1], "hand_no": r[2],
                         "initial": r[3], "final": r[4], "delta": r[5],
                         "rating": rating_info(r[6], r[7]), "created_at": r[8]} for r in rows]})

    async def handle_get_rating_leaderboard(self, websocket, state, data):
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
        with self.database() as conn:
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
        await self.hub.send_json(websocket, {"type": "rating_leaderboard", "entries": entries,
            "self": own, "total": total, "limit": limit, "offset": offset,
            "request_id": request_id, "stats_since": stats_since,
            "tiers": [rating_info(floor) for floor, _ in TIERS]})

    async def handle_get_asset_leaderboard(self, websocket, state, data):
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
        with self.database() as conn:
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
        await self.hub.send_json(websocket, {"type": "asset_leaderboard", "entries": entries,
            "self": own, "total": total, "limit": limit, "offset": offset, "request_id": request_id})
