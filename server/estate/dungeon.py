"""地下城 WebSocket 入口；读写协议共享账号事务与状态广播。"""

import logging
import sqlite3
import time

from estate.dungeon.effects import EffectError
from estate.dungeon.actions import ITEM_ID_PATTERN, REQUEST_ID_PATTERN, run_dungeon_action
from estate.dungeon.service import DungeonError, compare_item, dungeon_state
from server.wallet import adjust_coins

logger = logging.getLogger("live-chat")


class DungeonProtocol:
    def __init__(self, *, database, hub, clock=None):
        self.database = database
        self.hub = hub
        self.clock = clock or time.time

    def handlers(self):
        return {"get_dungeon": self.handle_get_dungeon,
                "dungeon_compare_item": self.handle_compare_item,
                "dungeon_equip": self.handle_action,
                "dungeon_lock_item": self.handle_action,
                "dungeon_sell_item": self.handle_action,
                "dungeon_claim_items": self.handle_action}

    async def _error(self, websocket, request_id, code, message):
        await self.hub.send_json(websocket, {"type": "dungeon_error", "code": code,
                                              "message": message, "request_id": request_id})

    async def handle_get_dungeon(self, websocket, state, data):
        request_id = data.get("request_id")
        if request_id is not None and (
                not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None):
            await self._error(websocket, None, "invalid_request", "请求编号无效")
            return
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                snapshot = dungeon_state(conn, user["username"], int(self.clock()))
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon saved state is invalid")
            await self._error(websocket, request_id, "invalid_save", "地下城存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon state read failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_state", "request_id": request_id,
                                              **snapshot})

    async def handle_compare_item(self, websocket, state, data):
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            await self._error(websocket, None, "invalid_request", "请求编号无效")
            return
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        item_id = data.get("item_id")
        if not isinstance(item_id, str) or ITEM_ID_PATTERN.fullmatch(item_id) is None:
            await self._error(websocket, request_id, "invalid_request", "装备编号无效")
            return
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                result = compare_item(conn, user["username"], item_id, int(self.clock()))
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon comparison failed on saved state")
            await self._error(websocket, request_id, "invalid_save", "地下城存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon comparison storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_comparison",
                                              "request_id": request_id, **result})

    async def handle_action(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        username = user["username"]
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                now = int(self.clock())
                result = run_dungeon_action(
                    conn, username, request_id, data.get("type"), data,
                    data.get("expected_version"), now, adjust_coins)
                snapshot = dungeon_state(conn, username, now)
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon action failed on saved state")
            await self._error(websocket, request_id, "invalid_save", "地下城存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon action storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        for client_state in list(self.hub.clients.values()):
            account = client_state.get("user")
            if account and account.get("username") == username:
                account["coins"] = snapshot["coins"]
        await self.hub.send_json(websocket, {"type": "dungeon_result",
                                              "request_id": request_id,
                                              "result": result, "state": snapshot})
        await self.hub.send_to_user(username, {"type": "dungeon_state", **snapshot})
