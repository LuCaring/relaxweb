import time

from rewards import claim_checkin, claim_holdem_reward, draw_lottery, rewards_state
from server.wallet import adjust_coins


class RewardsProtocol:
    def __init__(self, database, hub):
        self.database = database
        self.hub = hub

    def handlers(self):
        return {
            "get_daily_rewards": self.handle_get_daily_rewards,
            "daily_checkin": self.handle_daily_checkin,
            "draw_lottery": self.handle_draw_lottery,
            "claim_holdem_reward": self.handle_claim_holdem_reward,
        }

    async def publish_daily_rewards(self, username):
        for socket, client_state in list(self.hub.clients.items()):
            user = client_state.get("user")
            if user and user["username"] == username:
                with self.database() as conn:
                    # 玩家可在该手牌结束前注销；旧连接不能阻断其他玩家的结算广播。
                    if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                        return
                    payload = rewards_state(conn, username, time.time())
                user["coins"] = payload["coins"]
                await self.hub.send_json(socket, {"type": "daily_rewards", **payload})

    async def handle_rewards_action(self, websocket, state, data, action):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "rewards_error", "message": "请先登录"})
            return
        username = user["username"]
        try:
            with self.database() as conn, conn:
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
            await self.hub.send_json(websocket, {"type": "rewards_error", "message": str(error),
                                        "request_id": data.get("request_id"), "action": action})
            return
        kind = {"get": "daily_rewards", "checkin": "checkin_result", "draw": "lottery_result",
                "holdem": "holdem_reward_result"}[action]
        await self.hub.send_json(websocket, {"type": kind, **payload, **extra})
        if action != "get":
            await self.publish_daily_rewards(username)

    async def handle_get_daily_rewards(self, websocket, state, data):
        await self.handle_rewards_action(websocket, state, data, "get")

    async def handle_daily_checkin(self, websocket, state, data):
        await self.handle_rewards_action(websocket, state, data, "checkin")

    async def handle_draw_lottery(self, websocket, state, data):
        await self.handle_rewards_action(websocket, state, data, "draw")

    async def handle_claim_holdem_reward(self, websocket, state, data):
        await self.handle_rewards_action(websocket, state, data, "holdem")
