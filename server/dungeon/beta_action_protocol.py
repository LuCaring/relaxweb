"""Authenticated ``dungeon_beta_*`` run action adapter over the Beta run host.

High-frequency combat path: input messages carry no receipt and are answered
only on failure; authoritative state reaches clients as server-pushed frames.
Every durable handler resolves the stable user id from the authenticated session
per call and verifies run ownership before touching the host. When no simulator
is assembled yet, every action reports ``simulator_unavailable`` instead of
taking partial state.
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from dungeon.contracts.json_validation import validation_errors
from dungeon.domain.errors import DungeonError
from dungeon.legacy.receipts import REQUEST_ID_PATTERN
from server.transport import rate_limited

logger = logging.getLogger("live-chat")

MESSAGE_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "dungeon" /
                             "schemas" / "beta_messages.schema.json").read_text())
REQUEST_DEFINITIONS = {
    "dungeon_beta_start_run": "start_run_request",
    "dungeon_beta_take_control": "run_control_request",
    "dungeon_beta_resume": "run_control_request",
    "dungeon_beta_pause": "run_control_request",
    "dungeon_beta_abandon": "run_control_request",
    "dungeon_beta_input": "run_input_request",
    "dungeon_beta_sync": "run_sync_request",
}
RETRYABLE_CODES = frozenset({"storage_busy", "storage_failed"})
INPUT_COOLDOWN = 1.0 / 60


class DungeonBetaActionProtocol:
    def __init__(self, *, database, hub, run_service=None, host=None, scheduler=None,
                 disconnect_grace=30.0):
        if (host is None) != (scheduler is None):
            raise ValueError("host and scheduler must be assembled together")
        if host is not None and run_service is None:
            raise ValueError("host requires its run service")
        self.database = database
        self.hub = hub
        self.run_service = run_service
        self.host = host
        self.scheduler = scheduler
        self.disconnect_grace = disconnect_grace
        # run_id -> controller websocket / owner username; owner cache feeds the
        # 30Hz input path and frame pushes without a per-message user lookup.
        self._controllers = {}
        self._owners = {}
        self._grace_tasks = {}
        self._last_frames = {}

    def handlers(self):
        return {"dungeon_beta_start_run": self.handle_start_run,
                "dungeon_beta_take_control": self.handle_take_control,
                "dungeon_beta_resume": self.handle_resume,
                "dungeon_beta_pause": self.handle_pause,
                "dungeon_beta_abandon": self.handle_abandon,
                "dungeon_beta_input": self.handle_input,
                "dungeon_beta_sync": self.handle_sync}

    # ---------------------------------------------------------------- plumbing

    async def _fail(self, websocket, request_id, error):
        if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            request_id = None
        if isinstance(error, DungeonError):
            code, message = error.code, str(error)
        elif isinstance(error, sqlite3.OperationalError):
            code, message = "storage_busy", "存储繁忙，请稍后重试"
        else:
            code, message = "storage_failed", "存储暂时不可用，请稍后重试"
        await self.hub.send_json(websocket, {
            "type": "dungeon_beta_error", "protocol_version": 1, "request_id": request_id,
            "code": code, "message": message, "retryable": code in RETRYABLE_CODES,
            "details": {}})

    def _user_id(self, username):
        with self.database() as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            raise DungeonError("auth_required", "用户不存在")
        return row[0]

    async def _guard(self, websocket, state, data):
        """Return (request_id, user_id, username) or None after replying."""
        request_id = data.get("request_id") if isinstance(data, dict) else None
        user = state.get("user")
        if not user:
            await self._fail(websocket, request_id, DungeonError("auth_required", "请先登录"))
            return None
        if self.host is None:
            await self._fail(websocket, request_id,
                             DungeonError("simulator_unavailable", "战斗玩法尚未开放"))
            return None
        definition = REQUEST_DEFINITIONS.get(data.get("type"))
        if definition is None or validation_errors(
                {"$ref": "#/$defs/" + definition, "$defs": MESSAGE_SCHEMA["$defs"]}, data):
            await self._fail(websocket, request_id,
                             DungeonError("invalid_request", "地下城请求格式无效"))
            return None
        try:
            user_id = await asyncio.to_thread(self._user_id, user["username"])
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return None
        return request_id, user_id, user["username"]

    async def _require_owner(self, websocket, request_id, user_id, username, run_id):
        """Verify the run exists and belongs to this user; reply on failure."""
        try:
            await asyncio.to_thread(self.run_service.get, run_id, user_id)
            return True
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return False

    def _read_result(self, request_id, kind, result):
        return {"type": "dungeon_beta_result", "protocol_version": 1,
                "request_id": request_id, "result_kind": kind, "result": result}

    def _claim(self, run_id, websocket, username):
        self._controllers[run_id] = websocket
        self._owners[run_id] = username
        grace = self._grace_tasks.pop(run_id, None)
        if grace is not None:
            grace.cancel()

    def _forget(self, run_id):
        self._controllers.pop(run_id, None)
        self._owners.pop(run_id, None)
        self._last_frames.pop(run_id, None)
        grace = self._grace_tasks.pop(run_id, None)
        if grace is not None:
            grace.cancel()

    # ------------------------------------------------------------ host pushes

    def _on_frame(self, run_id, frame):
        username = self._owners.get(run_id)
        if username is None:
            return
        self._last_frames[run_id] = frame
        self.hub.send_to_user(username, {"type": "dungeon_beta_frame",
                                         "protocol_version": 1, **frame})

    def _on_room_cleared(self, run_id, result):
        self._last_frames.pop(run_id, None)
        username = self._owners.get(run_id)
        if username is None:
            return
        outcome = result["reward"]
        self.hub.send_to_user(username, {
            "type": "dungeon_beta_room_cleared", "protocol_version": 1, "run_id": run_id,
            "status": result["status"], "room_index": outcome["room_index"],
            "server_tick": result["server_tick"], "durable_tick": result["durable_tick"],
            "reward": outcome["reward"]})

    def _on_stopped(self, run_id, error):
        self._last_frames.pop(run_id, None)
        username = self._owners.get(run_id)
        if username is None:
            return
        code = error.code if isinstance(error, DungeonError) else "run_paused"
        message = str(error) if isinstance(error, DungeonError) else "挑战已暂停"
        self.hub.send_to_user(username, {
            "type": "dungeon_beta_error", "protocol_version": 1, "request_id": None,
            "code": code, "message": message, "retryable": False,
            "details": {"run_id": run_id}})

    # ----------------------------------------------------------- run lifecycle

    async def handle_start_run(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        try:
            result = await asyncio.to_thread(
                self.run_service.start, user_id, request_id, data.get("route_id"),
                expected_asset_revision=data.get("expected_asset_revision"))
            run_id = result["run_id"]
            if not result.get("replayed"):
                await self.scheduler.run_io(self.host.take_control, run_id)
                await self.scheduler.run_io(self.host.resume, run_id)
                await self.scheduler.attach(run_id)
                self._claim(run_id, websocket, username)
            status = await self.scheduler.run_io(self.host.status, run_id)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        logger.info("dungeon run started: user_id=%s run_id=%s route=%s replayed=%s",
                    user_id, run_id, data.get("route_id"), result.get("replayed"))
        await self.hub.send_json(websocket, self._read_result(request_id, "start_run", {
            **result, "status": status["status"], "control_epoch": status["control_epoch"]}))

    async def handle_take_control(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        run_id = data.get("run_id")
        if not await self._require_owner(websocket, request_id, user_id, username, run_id):
            return
        try:
            control = await self.scheduler.run_io(self.host.take_control, run_id)
            self._claim(run_id, websocket, username)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        logger.info("dungeon control taken: user_id=%s run_id=%s epoch=%s",
                    user_id, run_id, control.get("control_epoch"))
        await self.hub.send_json(websocket, self._read_result(
            request_id, "take_control", control))

    async def handle_resume(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        run_id = data.get("run_id")
        if not await self._require_owner(websocket, request_id, user_id, username, run_id):
            return
        try:
            await self.scheduler.run_io(self.host.resume, run_id)
            await self.scheduler.attach(run_id)
            self._claim(run_id, websocket, username)
            status = await self.scheduler.run_io(self.host.status, run_id)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        await self.hub.send_json(websocket, self._read_result(
            request_id, "resume", status))

    async def handle_pause(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        run_id = data.get("run_id")
        if not await self._require_owner(websocket, request_id, user_id, username, run_id):
            return
        try:
            await self.scheduler.stop(run_id)
            status = await self.scheduler.run_io(self.host.status, run_id)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        await self.hub.send_json(websocket, self._read_result(request_id, "pause", status))

    async def handle_abandon(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        run_id = data.get("run_id")
        if not await self._require_owner(websocket, request_id, user_id, username, run_id):
            return
        try:
            await self.scheduler.detach(run_id)
            result = await self.scheduler.run_io(self.host.abandon, run_id)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        self._forget(run_id)
        logger.info("dungeon run abandoned: user_id=%s run_id=%s", user_id, run_id)
        await self.hub.send_json(websocket, self._read_result(request_id, "abandon", result))

    # ------------------------------------------------------- realtime run path

    async def handle_input(self, websocket, state, data):
        if self.host is None:
            await self._fail(websocket, None,
                             DungeonError("simulator_unavailable", "战斗玩法尚未开放"))
            return
        if validation_errors({"$ref": "#/$defs/run_input_request",
                              "$defs": MESSAGE_SCHEMA["$defs"]}, data):
            await self._fail(websocket, None,
                             DungeonError("invalid_request", "地下城请求格式无效"))
            return
        # Rate limit before ownership checks: malformed spam must not reach the
        # serialization thread. Excess valid inputs are dropped without a reply.
        if rate_limited(state, "last_dungeon_beta_input", INPUT_COOLDOWN):
            return
        user = state.get("user") or {}
        run_id = data["run_id"]
        if (self._owners.get(run_id) != user.get("username")
                or self._controllers.get(run_id) is not websocket):
            await self._fail(websocket, None,
                             DungeonError("control_lost", "当前连接不持有该挑战控制权"))
            return
        try:
            await self.scheduler.run_io(self.host.input, run_id, data["control_epoch"],
                                        data["input_seq"], data["value"])
        except DungeonError as error:
            await self._fail(websocket, None, error)
        except sqlite3.OperationalError as error:
            await self._fail(websocket, None, error)

    async def handle_sync(self, websocket, state, data):
        guard = await self._guard(websocket, state, data)
        if guard is None:
            return
        request_id, user_id, username = guard
        run_id = data.get("run_id")
        if not await self._require_owner(websocket, request_id, user_id, username, run_id):
            return
        try:
            status = await self.scheduler.run_io(self.host.status, run_id)
        except (DungeonError, sqlite3.OperationalError) as error:
            await self._fail(websocket, request_id, error)
            return
        frame = self._last_frames.get(run_id)
        await self.hub.send_json(websocket, self._read_result(request_id, "sync", {
            "status": status, "frame": frame}))

    # ------------------------------------------------------------- lifecycle

    async def on_disconnect(self, websocket):
        """Release control of runs held by a closed connection after a grace window."""
        for run_id, held in list(self._controllers.items()):
            if held is not websocket:
                continue
            username = self._owners.get(run_id)
            self._grace_tasks[run_id] = asyncio.create_task(
                self._grace_pause(run_id, websocket, username))

    async def _grace_pause(self, run_id, websocket, username):
        await asyncio.sleep(self.disconnect_grace)
        if self._controllers.get(run_id) is not websocket:
            return
        try:
            status = await self.scheduler.run_io(self.host.status, run_id)
            if status["status"] == "running":
                await self.scheduler.stop(run_id)
        except DungeonError:
            pass
        self._controllers.pop(run_id, None)
        logger.info("dungeon run paused after disconnect: run_id=%s", run_id)
        if username:
            self.hub.send_to_user(username, {
                "type": "dungeon_beta_error", "protocol_version": 1, "request_id": None,
                "code": "run_paused", "message": "连接断开，挑战已暂停", "retryable": False,
                "details": {"run_id": run_id}})

    async def aclose(self):
        for grace in self._grace_tasks.values():
            grace.cancel()
        self._grace_tasks.clear()
        self._controllers.clear()
        self._owners.clear()
        self._last_frames.clear()
        if self.scheduler is not None:
            await self.scheduler.aclose()
