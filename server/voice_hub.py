"""语音聊天室（VoiceHub）：全站唯一实例，六个常驻频道的成员、临时重命名与分频道文字聊天。

复用 VoiceService 的差分签发：本模块拼一个鸭子类型对象（id/seating/
spectators/voice_plan）交给 VoiceService.sync_room，成员换频道时旧
LiveKit 房间被踢出，token 过半衰期静默续签，与游戏房间同一套机制。

与游戏房间互斥：进任何游戏房间即退出频道——房间视图广播后由
RoomHost.broadcast_views 调 recheck() 把频道里的玩家拉出去。
频道名是临时的：空置超过 rename_ttl 秒后恢复默认名，有人时绝不恢复。
"""
import asyncio
import logging
import time
from collections import deque

from config import get_int
from server.transport import rate_limited

logger = logging.getLogger("live-chat")

DEFAULT_CHANNEL = "default"
CHANNEL_IDS = ("default", "1", "2", "3", "4", "5")
DEFAULT_NAMES = {
    "default": "默认频道",
    "1": "频道1",
    "2": "频道2",
    "3": "频道3",
    "4": "频道4",
    "5": "频道5",
}
CHAT_HISTORY_LIMIT = 30
CHAT_TEXT_LIMIT = 200
RENAME_COOLDOWN = 5.0
CHAT_COOLDOWN = 1.0


class _ChannelFacade:
    """VoiceService.sync_room 的鸭子类型房间：全部频道成员映射成一个语音视图。

    seating 只含频道成员，绝不包含单纯订阅者或游戏房内用户。
    """

    def __init__(self, hub):
        self._hub = hub

    @property
    def id(self):
        return "voice-hub"

    @property
    def spectators(self):
        return []

    @property
    def seating(self):
        return [name for channel in self._hub.channels.values()
                for name in channel["members"]]

    def voice_plan(self, username):
        for channel_id, channel in self._hub.channels.items():
            if username in channel["members"]:
                return {f"vh-{channel_id}": True}
        return {}


class VoiceHub:
    """六个常驻频道的成员管理、临时重命名与分频道文字聊天。"""

    def __init__(self, hub, accounts, rooms, voice, *, rename_ttl=None,
                 disconnect_grace=30.0):
        self.hub = hub
        self.accounts = accounts
        self.rooms = rooms
        self.voice = voice
        if rename_ttl is None:
            rename_ttl = get_int("voice.hub_rename_ttl",
                                 env="VOICE_HUB_RENAME_TTL", default=600)
        self.rename_ttl = rename_ttl
        self.disconnect_grace = disconnect_grace
        self.channels = {
            channel_id: {"name": DEFAULT_NAMES[channel_id], "custom": False,
                         "members": {}, "chat": deque(maxlen=CHAT_HISTORY_LIMIT)}
            for channel_id in CHANNEL_IDS
        }
        self.subscribers = set()
        self._facade = _ChannelFacade(self)
        self._reset_timers = {}    # channel_id -> asyncio.TimerHandle
        self._expire_timers = {}   # username -> [asyncio.TimerHandle, ...]

    # ---- 协议 ----
    def handlers(self):
        return {
            "voice_hub_join": self.handle_join,
            "voice_hub_leave": self.handle_leave,
            "voice_hub_rename": self.handle_rename,
            "voice_hub_chat": self.handle_chat,
            "voice_hub_state": self.handle_state,
            "voice_hub_unsubscribe": self.handle_unsubscribe,
        }

    async def handle_join(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        channel_id = str(data.get("channel") or DEFAULT_CHANNEL)
        if channel_id not in self.channels:
            await self.hub.send_json(websocket,
                                     {"type": "voice_hub_error", "message": "频道不存在"})
            return
        username = user["username"]
        if self.rooms.user_in_room(username):
            await self.hub.send_json(
                websocket,
                {"type": "voice_hub_error", "message": "游戏中不能加入语音聊天室"})
            return
        emptied = self._remove_member(username)
        self.channels[channel_id]["members"][username] = {
            "nickname": user.get("nickname") or self.accounts.display_name(username),
            "avatar": user.get("avatar") or "",
            "joined_at": time.time(),
        }
        await self._after_change(emptied)
        # 加入/切换频道后补一份该频道聊天历史，客户端据此替换旧频道消息
        await self.hub.send_json(websocket, {
            "type": "voice_hub_chat_history",
            "channel": channel_id,
            "messages": list(self.channels[channel_id]["chat"]),
        })

    async def handle_leave(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        emptied = self._remove_member(user["username"])
        await self.voice.sync_room(self._facade)
        # 客户端收到无 token 的 voice_update 即断开，确保立即离开 LiveKit 房
        await self._send_voice_disconnect(user["username"])
        for channel_id in emptied:
            self._schedule_reset(channel_id)
        await self._broadcast_state()

    async def handle_rename(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        channel_id = self._member_channel(user["username"])
        if channel_id is None:
            await self.hub.send_json(
                websocket,
                {"type": "voice_hub_error", "message": "加入频道后才能重命名"})
            return
        if channel_id == DEFAULT_CHANNEL:
            await self.hub.send_json(
                websocket,
                {"type": "voice_hub_error", "message": "默认频道不能重命名"})
            return
        if rate_limited(state, "last_voice_hub_rename", RENAME_COOLDOWN):
            await self.hub.send_json(
                websocket,
                {"type": "voice_hub_error", "message": "操作太频繁，请稍后再试"})
            return
        name = str(data.get("name", "")).strip()
        if not 1 <= len(name) <= 12:
            await self.hub.send_json(
                websocket,
                {"type": "voice_hub_error", "message": "频道名需要 1~12 个字"})
            return
        channel = self.channels[channel_id]
        channel["name"] = name
        channel["custom"] = True
        self._schedule_reset(channel_id)
        await self._broadcast_state()

    async def handle_chat(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        channel_id = self._member_channel(user["username"])
        if channel_id is None:
            return
        if rate_limited(state, "last_voice_hub_chat", CHAT_COOLDOWN):
            await self.hub.send_json(websocket,
                                     {"type": "voice_hub_error", "message": "发送太快了"})
            return
        text = str(data.get("text", "")).strip()[:CHAT_TEXT_LIMIT]
        if not text:
            return
        message = {
            "username": user["username"],
            "nickname": user.get("nickname") or self.accounts.display_name(user["username"]),
            "text": text,
            "time": int(time.time()),
        }
        self.channels[channel_id]["chat"].append(message)
        payload = {"type": "voice_hub_chat", "channel": channel_id, **message}
        for username in tuple(self.channels[channel_id]["members"]):
            await self.hub.send_to_user(username, dict(payload))

    async def handle_state(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        self.subscribers.add(websocket)
        await self.hub.send_json(websocket, self._snapshot(user["username"]))
        channel_id = self._member_channel(user["username"])
        if channel_id is not None:
            await self.hub.send_json(websocket, {
                "type": "voice_hub_chat_history",
                "channel": channel_id,
                "messages": list(self.channels[channel_id]["chat"]),
            })

    async def handle_unsubscribe(self, websocket, state, data):
        if not state.get("user"):
            return
        self.subscribers.discard(websocket)

    # ---- 广播与活跃 ----
    async def _after_change(self, emptied=()):
        """成员变动收尾：差分签发、空频道安排恢复默认名、推送个性化快照。"""
        await self.voice.sync_room(self._facade)
        for channel_id in emptied:
            self._schedule_reset(channel_id)
        await self._broadcast_state()

    def _snapshot(self, username=None):
        return {
            "type": "voice_hub_state",
            "channels": [
                {
                    "id": channel_id,
                    "name": channel["name"],
                    "custom": channel["custom"],
                    "members": [
                        {"username": name, "nickname": info["nickname"],
                         "avatar": info["avatar"]}
                        for name, info in channel["members"].items()
                    ],
                }
                for channel_id, channel in self.channels.items()
            ],
            "my_channel": self._member_channel(username) if username else None,
        }

    async def _broadcast_state(self):
        """向订阅连接推送个性化快照；已关闭的连接顺手剔除。"""
        for websocket in list(self.subscribers):
            client_state = self.hub.clients.get(websocket)
            if client_state is None:
                self.subscribers.discard(websocket)
                continue
            user = client_state.get("user")
            await self.hub.send_json(
                websocket, self._snapshot(user["username"] if user else None))
            if websocket not in self.hub.clients:
                self.subscribers.discard(websocket)

    def _schedule_reset(self, channel_id):
        """频道空置后安排恢复默认名；到点时再次复核，有人则不动。"""
        handle = self._reset_timers.pop(channel_id, None)
        if handle:
            handle.cancel()
        loop = asyncio.get_running_loop()
        self._reset_timers[channel_id] = loop.call_later(
            self.rename_ttl, self._reset_channel, channel_id)

    def _reset_channel(self, channel_id):
        """空置超时复核：期间有人进频道、或名字本就是默认则不动。"""
        self._reset_timers.pop(channel_id, None)
        channel = self.channels.get(channel_id)
        if channel is None or channel["members"] or not channel["custom"]:
            return
        channel["name"] = DEFAULT_NAMES[channel_id]
        channel["custom"] = False
        logger.info("voice hub channel %s restored default name", channel_id)
        asyncio.ensure_future(self._broadcast_state())

    # ---- 与游戏互斥和断线 ----
    async def recheck(self):
        """游戏房间视图广播后调用：进了游戏房间的频道成员立即退出频道。"""
        removed = []
        emptied = []
        for channel_id, channel in self.channels.items():
            hit = [name for name in channel["members"] if self.rooms.user_in_room(name)]
            for username in hit:
                del channel["members"][username]
                removed.append(username)
            if hit and not channel["members"]:
                emptied.append(channel_id)
        if not removed:
            return
        await self._after_change(emptied)
        await asyncio.gather(*(self._send_voice_disconnect(name) for name in removed))

    async def _send_voice_disconnect(self, username):
        """显式下发无 token 的 voice_update；room-voice.js 收到后立即断开。"""
        await self.hub.send_to_user(username, {
            "type": "voice_update", "url": self.voice.url,
            "room": None, "can_publish": False, "token": None,
        })

    async def disconnect(self, websocket, state):
        """连接关闭（app.handler 的 finally）：退订推送，宽限期后确认移除成员。"""
        self.subscribers.discard(websocket)
        user = state.get("user")
        if not user:
            return
        loop = asyncio.get_running_loop()
        self._expire_timers.setdefault(user["username"], []).append(
            loop.call_later(self.disconnect_grace,
                            self._expire_member, user["username"]))

    def _expire_member(self, username):
        """宽限期到点：同账号仍有已认证连接（已重连）则跳过，否则移出频道。"""
        for handle in self._expire_timers.pop(username, []):
            handle.cancel()
        for client_state in self.hub.clients.values():
            other = client_state.get("user")
            if other and other["username"] == username:
                return
        if self._member_channel(username) is None:
            return
        emptied = self._remove_member(username)
        asyncio.ensure_future(self._after_change(emptied))

    async def refresh_loop(self):
        """后台续签：有频道成员时周期同步，token 剩余不足半衰期则静默换发。"""
        while True:
            await asyncio.sleep(60)
            if not any(channel["members"] for channel in self.channels.values()):
                continue
            try:
                await self.voice.sync_room(self._facade)
            except Exception:
                logger.warning("voice hub refresh failed", exc_info=True)

    async def aclose(self):
        """清掉全部计时器；后台续签任务由 Application 持有并在其 aclose 中取消。"""
        for handle in self._reset_timers.values():
            handle.cancel()
        self._reset_timers.clear()
        for handles in self._expire_timers.values():
            for handle in handles:
                handle.cancel()
        self._expire_timers.clear()

    # ---- 内部 ----
    def _member_channel(self, username):
        for channel_id, channel in self.channels.items():
            if username in channel["members"]:
                return channel_id
        return None

    def _remove_member(self, username):
        """把用户从所有频道移除，返回因此变空的频道。"""
        emptied = []
        for channel_id, channel in self.channels.items():
            if channel["members"].pop(username, None) is not None and not channel["members"]:
                emptied.append(channel_id)
        return emptied
