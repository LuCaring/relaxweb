"""LiveKit 语音适配层：把房间的语音计划变成 token 签发与订阅授权。

权限模型（docs/werewolf-voice-design.md）：
  - 频道 = LiveKit 房间（werewolf: ww{room_id}-day / ww{room_id}-wolf）；
  - 用户能否进某个 LiveKit 房间只由游戏进程签发的短时 JWT 决定，
    改版客户端拿不到 token 就进不去；
  - 离桌/解散由 remove_participant 尽力踢出，token 短时效兜底。

引擎不感知 LiveKit：VoiceService 只消费 BaseRoom.voice_plan(username)
（默认 {} 表示无语音；werewolf 把 voice 视图的 channel 翻译成房间名）。
配置 voice.enabled=false 或未安装 livekit-api 时整体为空操作。
"""
import asyncio
import logging
import time
from datetime import timedelta

from config import get, get_int

logger = logging.getLogger("live-chat.voice")


def voice_config():
    """读取语音配置；未配置 api_secret 视为未启用。"""
    return {
        "enabled": bool(get("voice.enabled", env="VOICE_ENABLED", default=False)),
        "url": str(get("voice.url", env="VOICE_URL", default="ws://127.0.0.1:7880")),
        "api_url": str(get("voice.api_url", env="VOICE_API_URL",
                           default="http://127.0.0.1:7880")),
        "api_key": str(get("voice.api_key", env="VOICE_API_KEY", default="devkey")),
        "api_secret": str(get("voice.api_secret", env="VOICE_API_SECRET", default="")),
        "token_ttl": get_int("voice.token_ttl", env="VOICE_TOKEN_TTL", default=600),
    }


def plan_key(plan):
    """计划的稳定签名：用于比较「是否需要换发 token」。"""
    return tuple(sorted((name, bool(publish)) for name, publish in plan.items()))


class VoiceService:
    """按房间维护「已签发计划」，在广播视图后同步语音授权。

    同步规则：
      - 计划变化 → 重新签发并下发 voice_update（客户端据此连接/换房/断开）；
      - 计划未变但 token 剩余有效期不足一半 → 静默换发同一计划；
      - 用户离桌/房间消失 → 尽力 remove_participant，token 短时效兜底。
    """

    def __init__(self, hub):
        self.hub = hub
        conf = voice_config()
        self.enabled = conf["enabled"] and bool(conf["api_secret"])
        self.url = conf["url"]
        self.api_url = conf.get("api_url", "http://127.0.0.1:7880")
        self.ttl = max(60, conf["token_ttl"])
        self.api_key = conf["api_key"]
        self.api_secret = conf["api_secret"]
        self.issued = {}          # room_id -> {username: (plan签名, 过期时刻)}
        self.rooms_seen = {}      # room_id -> 本局曾签发的 LiveKit 房间名
        self._api = None
        self._client = None
        if not self.enabled:
            logger.info("voice disabled (voice.enabled=false or no api_secret)")
        elif self._load_api() is None:
            self.enabled = False
            logger.warning("voice disabled: livekit-api 未安装（uv sync 后重试）")
        else:
            logger.info("voice enabled -> %s (ttl %ss)", self.url, self.ttl)

    def _load_api(self):
        if self._api is not None:
            return self._api
        try:
            from livekit import api
        except ImportError:
            return None
        self._api = api
        return self._api

    def _room_service(self):
        api = self._load_api()
        if api is None or not self.enabled:
            return None
        if self._client is None:
            self._client = api.LiveKitAPI(
                url=self.api_url, api_key=self.api_key, api_secret=self.api_secret)
        return self._client.room

    # ---- token ----
    def mint_token(self, username, plan):
        """为单个用户按计划签发 JWT；plan 为空时返回 None（断开语音）。"""
        if not plan:
            return None
        api = self._load_api()
        if api is None or not self.enabled:
            return None
        (room_name, can_publish), = plan.items()      # 当前频道模型：一人同时只有一个频道
        token = (api.AccessToken(self.api_key, self.api_secret)
                 .with_identity(username)
                 .with_name(username)
                 .with_ttl(timedelta(seconds=self.ttl))
                 .with_grants(api.VideoGrants(
                     room_join=True,
                     room=room_name,
                     can_publish=can_publish,
                     can_subscribe=True,
                 )))
        return token.to_jwt()

    # ---- 同步 ----
    async def sync_room(self, room):
        """房间广播视图后调用：按各成员的 voice_plan 差分签发。"""
        if not self.enabled:
            return
        usernames = list(room.seating) + list(room.spectators)
        state = self.issued.setdefault(room.id, {})
        now = time.time()
        removals = []
        for name in [n for n in state if n not in usernames]:
            previous = state.pop(name, None)
            if previous and previous[0]:
                removals.append(self._kick_room(next(iter(previous[0]))[0], name))
        if removals:
            await asyncio.gather(*removals)
        await asyncio.gather(*(self.sync_user(room, name, now=now)
                               for name in usernames))

    async def sync_user(self, room, username, *, force=False, now=None):
        """单用户补发授权；重新进入页面或观战时不等待下一次阶段广播。"""
        if not self.enabled:
            return
        now = time.time() if now is None else now
        state = self.issued.setdefault(room.id, {})
        plan = room.voice_plan(username)
        key = plan_key(plan)
        prev = state.get(username)
        if (not force and prev and prev[0] == key
                and prev[1] > now + self.ttl / 2):
            return
        if prev and prev[0] != key and prev[0]:
            await self._kick_room(next(iter(prev[0]))[0], username)
        token = self.mint_token(username, plan)
        if plan:
            self.rooms_seen.setdefault(room.id, set()).update(plan)
        state[username] = (key, now + self.ttl)
        await self.hub.send_to_user(username, {
            "type": "voice_update",
            "url": self.url,
            "room": next(iter(plan), None),
            "can_publish": any(plan.values()),
            "token": token,
        })

    async def _kick_room(self, room_name, username):
        """立即撤销已连接的旧频道；旧令牌只对应旧阶段房间。"""
        api = self._load_api()
        service = self._room_service()
        if service is None:
            return
        try:
            await asyncio.wait_for(service.remove_participant(
                api.RoomParticipantIdentity(room=room_name, identity=username)), 5)
        except api.TwirpError as error:
            if error.status != 404:
                logger.warning("voice kick failed: %s %s: %s", room_name, username, error)
        except Exception:
            logger.warning("voice kick failed: %s %s", room_name, username,
                           exc_info=True)

    async def close_room(self, room_id):
        """房间解散：清签发记录并删除对应 LiveKit 房间（踢出所有连接）。"""
        state = self.issued.pop(room_id, {})
        names = self.rooms_seen.pop(room_id, set())
        for key, _expires in state.values():
            names.update(name for name, _publish in key)
        if not names:
            return
        api = self._load_api()
        service = self._room_service()
        if service is None:
            return
        async def delete(name):
            try:
                await asyncio.wait_for(service.delete_room(
                    api.DeleteRoomRequest(room=name)), 5)
            except api.TwirpError as error:
                if error.status != 404:
                    logger.warning("voice room delete failed: %s: %s", name, error)
            except Exception:
                logger.warning("voice room delete failed: %s", name, exc_info=True)
        await asyncio.gather(*(delete(name) for name in names))

    async def aclose(self):
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
