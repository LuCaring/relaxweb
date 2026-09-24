import asyncio
import json
import logging
import time

logger = logging.getLogger("live-chat")


def rate_limited(state, key, cooldown):
    now = time.monotonic()
    if now - state.get(key, 0.0) < cooldown:
        return True
    state[key] = now
    return False


def connection_client(websocket):
    """兼容新旧 websockets 实现取连接路径，识别游戏厅独立页。"""
    request = getattr(websocket, "request", None)
    path = getattr(request, "path", None) or getattr(websocket, "path", "")
    return "game" if "client=game" in str(path) else ""


def connection_ip(websocket):
    """取客户端真实 IP：nginx 反代场景读 X-Real-IP，直连场景退回 socket 地址。

    仅当服务绑定 loopback、公网流量全部经 nginx 时才可信（外部无法伪造该头）。
    """
    request = getattr(websocket, "request", None)
    headers = getattr(request, "headers", None) or getattr(websocket, "request_headers", None)
    forwarded = headers.get("X-Real-IP") if headers else None
    if forwarded:
        return forwarded.strip()
    return (websocket.remote_address or ("?", 0))[0]


class ConnectionHub:
    def __init__(self):
        self.clients = {}

    def handlers(self):
        return {
            "get_online": self.handle_get_online,
        }

    async def send_encoded(self, socket, payload):
        """带锁发送：两个广播并发打到同一连接会触发 ConcurrencyError，导致连接被静默剔除。"""
        state = self.clients.get(socket)
        if not state:
            return
        async with state["send_lock"]:
            try:
                await socket.send(payload)
            except Exception as error:
                self.clients.pop(socket, None)
                logger.warning("send failed (%s), dropped connection", error)

    async def send_json(self, websocket, data):
        await self.send_encoded(
            websocket, json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        )

    async def broadcast(self, data):
        if not self.clients:
            return
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        sockets = tuple(self.clients)
        await asyncio.gather(*(self.send_encoded(socket, payload) for socket in sockets))

    async def broadcast_online_count(self):
        # 游戏厅独立页的连接不算直播间在线观众
        count = sum(
            1 for state in self.clients.values() if state.get("client") != "game"
        )
        await self.broadcast({"type": "online", "count": count})

    def send_to_user(self, username, payload):
        """给某个用户的所有连接发一条定向消息（如重新买入失败的通知）。"""
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return asyncio.gather(*(
            self.send_encoded(socket, text)
            for socket, client_state in list(self.clients.items())
            if (client_state.get("user") or {}).get("username") == username
        ))

    async def handle_get_online(self, websocket, state, data):
        users = {}
        for client_state in list(self.clients.values()):
            user = client_state.get("user")
            if not user:
                continue
            users[user["username"]] = {
                "username": user["username"],
                "nickname": user.get("nickname") or "",
                "avatar": user.get("avatar") or "",
                "role": user.get("role") or "user",
            }
        await self.send_json(websocket, {"type": "online_users", "users": list(users.values())})
