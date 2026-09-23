"""地下城 WebSocket 入口。基础阶段只开放可安全读取的状态协议。"""

import logging
import sqlite3
import time

from estate.dungeon.effects import EffectError
from estate.dungeon.service import DungeonError, dungeon_state

logger = logging.getLogger("live-chat")


class DungeonProtocol:
    def __init__(self, *, database, hub, clock=None):
        self.database = database
        self.hub = hub
        self.clock = clock or time.time

    def handlers(self):
        return {"get_dungeon": self.handle_get_dungeon}

    async def handle_get_dungeon(self, websocket, state, data):
        request_id = data.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 96):
            await self.hub.send_json(websocket, {"type": "dungeon_error", "code": "invalid_request",
                                                  "message": "请求编号无效", "request_id": None})
            return
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "dungeon_error", "code": "auth_required",
                                                  "message": "请先登录", "request_id": request_id})
            return
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                snapshot = dungeon_state(conn, user["username"], int(self.clock()))
        except DungeonError as error:
            await self.hub.send_json(websocket, {"type": "dungeon_error", "code": error.code,
                                                  "message": str(error), "request_id": request_id})
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon saved state is invalid")
            await self.hub.send_json(websocket, {"type": "dungeon_error", "code": "invalid_save",
                                                  "message": "地下城存档异常，请联系管理员",
                                                  "request_id": request_id})
            return
        except sqlite3.Error:
            logger.exception("dungeon state read failed")
            await self.hub.send_json(websocket, {"type": "dungeon_error", "code": "storage_failed",
                                                  "message": "地下城暂时不可用，请稍后重试",
                                                  "request_id": request_id})
            return
        await self.hub.send_json(websocket, {"type": "dungeon_state", "request_id": request_id,
                                              **snapshot})
