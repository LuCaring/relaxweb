"""庄园在线频道、移动校验与连接清理；对应前端 estate/state.js。"""
import json
import math
import time

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


class EstatePresence:
    """每个宿主独立持有频道集合，发送仍通过宿主的连接锁。"""

    def __init__(self, *, clients, send_encoded, rate_limited):
        self.clients = clients
        self.send_encoded = send_encoded
        self.rate_limited = rate_limited
        self.channels = {}

    async def broadcast(self, owner, payload, exclude=None):
        scoped = {**payload, "owner_username": owner}
        encoded = json.dumps(scoped, ensure_ascii=False, separators=(",", ":"))
        for socket in list(self.channels.get(owner.lower(), set())):
            if socket is exclude or socket not in self.clients:
                continue
            await self.send_encoded(socket, encoded)

    async def leave(self, websocket, state):
        owner = state.pop("estate_owner", None)
        if not owner:
            return
        members = self.channels.get(owner.lower())
        if members:
            members.discard(websocket)
            if not members:
                self.channels.pop(owner.lower(), None)
        user = state.get("user")
        if user:
            await self.broadcast(owner, {
                "type": "estate_visit_left", "username": user["username"],
            }, exclude=websocket)

    @staticmethod
    def player_state(state):
        user = state.get("user")
        if not user:
            return None
        return {
            "username": user["username"],
            "skin_id": state.get("estate_skin_id", "berry"),
            **state.get("estate_position", {
                "x": 275, "y": 440, "direction": "down", "walking": False,
            }),
        }

    async def join(self, websocket, state, owner, skin_id="berry"):
        await self.leave(websocket, state)
        members = self.channels.setdefault(owner.lower(), set())
        players = []
        for socket in list(members):
            other_state = self.clients.get(socket, {})
            other = self.player_state(other_state)
            if other:
                players.append(other)
        members.add(websocket)
        state["estate_owner"] = owner
        state["estate_skin_id"] = skin_id
        state["estate_position"] = {"x": 275, "y": 440, "direction": "down", "walking": False}
        state["estate_position_at"] = time.monotonic()
        player = self.player_state(state)
        await self.broadcast(owner, {"type": "estate_visit_joined", **player},
                             exclude=websocket)
        return players

    async def move(self, websocket, state, data):
        user = state.get("user")
        owner = state.get("estate_owner")
        if not user or not owner or self.rate_limited(state, "last_estate_move", .06):
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
        await self.broadcast(owner, {"type": "estate_visit_moved",
            "username": user["username"], "skin_id": state.get("estate_skin_id", "berry"),
            **position}, exclude=websocket)

    async def update_skin(self, username, skin_id):
        """更新同账号各连接的频道状态，并立即让同场玩家看到新皮肤。"""
        for websocket, state in list(self.clients.items()):
            user = state.get("user")
            if not user or user["username"].lower() != username.lower():
                continue
            state["estate_skin_id"] = skin_id
            owner = state.get("estate_owner")
            player = self.player_state(state)
            if owner and player:
                await self.broadcast(owner, {"type": "estate_visit_moved", **player},
                                     exclude=websocket)
