"""地下城 WebSocket 入口；读写协议共享账号事务与状态广播。"""

import asyncio
import logging
import sqlite3
import time

from dungeon.legacy.effects import EffectError
from dungeon.legacy.actions import ITEM_ID_PATTERN, REQUEST_ID_PATTERN, run_dungeon_action
from dungeon.legacy.runs import progress_run, read_run, start_run
from dungeon.legacy.service import (DungeonError, affix_probabilities,
                                    compare_item, dungeon_state)
from server.wallet import adjust_coins

logger = logging.getLogger("live-chat")


class DungeonProtocol:
    def __init__(self, *, database, hub, clock=None):
        self.database = database
        self.hub = hub
        self.clock = clock or time.time
        self._storage_slots = asyncio.Semaphore(4)

    async def _blocking(self, function, *args, **kwargs):
        async with self._storage_slots:
            return await asyncio.to_thread(function, *args, **kwargs)

    async def _advance(self, *args, **kwargs):
        return await self._blocking(progress_run, *args, **kwargs)

    def handlers(self):
        return {"get_dungeon": self.handle_get_dungeon,
                "dungeon_compare_item": self.handle_compare_item,
                "dungeon_affix_probabilities": self.handle_affix_probabilities,
                "dungeon_equip": self.handle_action,
                "dungeon_lock_item": self.handle_action,
                "dungeon_sell_item": self.handle_action,
                "dungeon_claim_items": self.handle_action,
                "dungeon_use_currency": self.handle_action,
                "dungeon_start": self.handle_start,
                "dungeon_sync": self.handle_sync,
                "dungeon_control": self.handle_control,
                "dungeon_get_result": self.handle_get_result}

    async def _error(self, websocket, request_id, code, message):
        await self.hub.send_json(websocket, {"type": "dungeon_error", "code": code,
                                              "message": message, "request_id": request_id,
                                              "retryable": code in ("storage_failed", "state_conflict",
                                                                    "version_conflict")})

    def _request_id(self, data):
        value = data.get("request_id")
        if not isinstance(value, str) or REQUEST_ID_PATTERN.fullmatch(value) is None:
            raise DungeonError("invalid_request", "请求编号无效")
        return value

    async def _broadcast_state(self, username, snapshot):
        for client_state in list(self.hub.clients.values()):
            account = client_state.get("user")
            if account and account.get("username") == username:
                account["coins"] = snapshot["coins"]
        await self.hub.send_to_user(username, {"type": "dungeon_state",
                                               "request_id": None, **snapshot})

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
            def read_state():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    return dungeon_state(conn, user["username"], int(self.clock()))
            snapshot = await self._blocking(read_state)
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
            def read_comparison():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    return compare_item(conn, user["username"], item_id, int(self.clock()))
            result = await self._blocking(read_comparison)
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

    async def handle_affix_probabilities(self, websocket, state, data):
        request_id = data.get("request_id")
        item_id = data.get("item_id")
        if (not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None
                or not isinstance(item_id, str) or ITEM_ID_PATTERN.fullmatch(item_id) is None):
            await self._error(websocket, None, "invalid_request", "请求编号或装备编号无效")
            return
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        try:
            def read_probabilities():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    return affix_probabilities(conn, user["username"], item_id,
                                               int(self.clock()), currency_id=data.get("currency_id"))
            result = await self._blocking(read_probabilities)
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon affix probabilities failed")
            await self._error(websocket, request_id, "invalid_save", "地下城存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon affix probability storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_affix_probabilities",
                                              "request_id": request_id, **result})

    async def handle_action(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        username = user["username"]
        try:
            def apply_action():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    now = int(self.clock())
                    result = run_dungeon_action(
                        conn, username, request_id, data.get("type"), data,
                        data.get("expected_version"), now, adjust_coins)
                    snapshot = dungeon_state(conn, username, now)
                    return result, snapshot
            result, snapshot = await self._blocking(apply_action)
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
        await self.hub.send_json(websocket, {"type": "dungeon_result",
                                              "request_id": request_id,
                                              "result_kind": "action",
                                              "result": result, "state": snapshot})
        await self._broadcast_state(username, snapshot)

    async def handle_start(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        username = user["username"]
        try:
            def apply_start():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    now_ms = int(self.clock() * 1000)
                    result = start_run(conn, username, request_id, data.get("challenge_id"),
                                       data.get("difficulty_id"), data.get("expected_version"),
                                       now_ms)
                    snapshot = dungeon_state(conn, username, now_ms // 1000)
                    return result, snapshot
            result, snapshot = await self._blocking(apply_start)
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon start failed on configuration or saved state")
            await self._error(websocket, request_id, "invalid_config", "地下城配置或存档异常")
            return
        except sqlite3.Error:
            logger.exception("dungeon start storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_result", "request_id": request_id,
                                              "result_kind": "action",
                                              "result": result, "state": snapshot})
        await self._broadcast_state(username, snapshot)

    async def handle_sync(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        username = user["username"]
        try:
            self._request_id(data)
            now_ms = int(self.clock() * 1000)
            result = await self._advance(self.database, username, data.get("battle_id"),
                                         now_ms, adjust_coins,
                                         after_sequence=data.get("after_sequence", 0))
            if result["changed"]:
                def read_updated_state():
                    with self.database() as conn, conn:
                        conn.execute("BEGIN IMMEDIATE")
                        return dungeon_state(conn, username, now_ms // 1000)
                snapshot = await self._blocking(read_updated_state)
            else:
                snapshot = None
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon sync failed on saved state")
            await self._error(websocket, request_id, "invalid_save", "挑战存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon sync storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_events", "request_id": request_id,
                                              **result})
        if snapshot is not None:
            await self._broadcast_state(username, snapshot)

    async def handle_control(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        username = user["username"]
        try:
            if data.get("command") is None:
                raise DungeonError("invalid_request", "挑战控制指令无效")
            result = await self._advance(self.database, username, data.get("battle_id"),
                                         int(self.clock() * 1000), adjust_coins,
                                         command=data.get("command"), request_id=request_id,
                                         expected_revision=data.get("expected_revision"),
                                         rate=data.get("rate"))
            def read_updated_state():
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    return dungeon_state(conn, username, int(self.clock()))
            snapshot = await self._blocking(read_updated_state)
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (EffectError, ValueError, KeyError, TypeError):
            logger.exception("dungeon control failed on saved state")
            await self._error(websocket, request_id, "invalid_save", "挑战存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon control storage failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_result", "request_id": request_id,
                                              "result_kind": "action",
                                              "result": result, "state": snapshot})
        if result["changed"]:
            await self._broadcast_state(username, snapshot)

    async def handle_get_result(self, websocket, state, data):
        request_id = data.get("request_id")
        user = state.get("user")
        if not user:
            await self._error(websocket, request_id, "auth_required", "请先登录")
            return
        try:
            self._request_id(data)
            def read_result():
                with self.database() as conn:
                    return read_run(conn, user["username"], data.get("battle_id"))
            battle = await self._blocking(read_result)
        except DungeonError as error:
            await self._error(websocket, request_id, error.code, str(error))
            return
        except (ValueError, KeyError, TypeError):
            logger.exception("dungeon result saved state is invalid")
            await self._error(websocket, request_id, "invalid_save", "挑战存档异常，请联系管理员")
            return
        except sqlite3.Error:
            logger.exception("dungeon result read failed")
            await self._error(websocket, request_id, "storage_failed", "地下城暂时不可用，请稍后重试")
            return
        await self.hub.send_json(websocket, {"type": "dungeon_result", "request_id": request_id,
                                              "result_kind": "lookup",
                                              "battle": battle, "result": battle["result"]})
