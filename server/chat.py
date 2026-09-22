import logging
import time
from collections import deque

from server.transport import rate_limited

MAX_CHAT_LENGTH = 200
CHAT_COOLDOWN = 1.0
logger = logging.getLogger("live-chat")


class ChatProtocol:
    def __init__(self, hub):
        self.hub = hub
        self.history = deque(maxlen=50)

    def handlers(self):
        return {
            "chat": self.handle_chat,
        }

    async def broadcast_system(self, text, danmaku=False):
        message = {"type": "system", "text": text, "time": time.strftime("%m/%d %H:%M")}
        if danmaku:
            message["danmaku"] = True
        self.history.append(message)
        logger.info("system message: %s", text)
        await self.hub.broadcast(message)

    async def handle_chat(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        text = str(data.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        if rate_limited(state, "last_message", CHAT_COOLDOWN):
            await self.hub.send_json(websocket, {"type": "error", "message": "发送太快了"})
            return
        message = {
            "type": "chat",
            "username": user["username"],
            "nickname": user.get("nickname") or "",
            "role": user["role"],
            "text": text,
            "time": time.strftime("%m/%d %H:%M"),
        }
        self.history.append(message)
        logger.info("chat message from %s (%d chars)", user["username"], len(text))
        await self.hub.broadcast(message)
