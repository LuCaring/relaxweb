"""庄园 WebSocket 协议与事务编排；对应前端 estate/protocol.js。"""
import asyncio
import json
import logging
import math
import sqlite3
import time

from server.wallet import adjust_coins
from estate.market import fetch_source_price, quote_refresh_due, refresh_market_quote
from server.estate.presence import ESTATE_PLOT_POSITIONS
from estate import (
    EstateError,
    buy as estate_buy,
    buy_tool as estate_buy_tool,
    buy_or_upgrade_pet as estate_buy_or_upgrade_pet,
    buy_or_upgrade_penguin as estate_buy_or_upgrade_penguin,
    estate_state,
    draw_lottery as estate_draw_lottery,
    market_snapshot as estate_market_snapshot,
    trade_market as estate_trade_market,
    fertilize as estate_fertilize,
    finish_fishing as estate_finish_fishing,
    finish_mining as estate_finish_mining,
    harvest as estate_harvest,
    plant as estate_plant,
    mine_cell as estate_mine_cell,
    repair_tool as estate_repair_tool,
    sell as estate_sell,
    sell_all as estate_sell_all,
    set_skin as estate_set_skin,
    buy_skin as estate_buy_skin,
    start_fishing as estate_start_fishing,
    start_mining as estate_start_mining,
    upgrade_tool as estate_upgrade_tool,
    use_land_upgrade_ticket as estate_use_land_upgrade_ticket,
    list_estates as estate_list_estates,
    mark_notifications_read as estate_mark_notifications_read,
    notifications as estate_notifications,
    public_estate_state,
    steal_crop as estate_steal_crop,
)

logger = logging.getLogger("live-chat")


class EstateProtocol:
    def __init__(self, *, database, clients, send_json, send_encoded, presence):
        self.database = database
        self.clients = clients
        self.send_json = send_json
        self.send_encoded = send_encoded
        self.presence = presence
        self._market_quote_lock = asyncio.Lock()

    async def refresh_market_quote(self, now):
        async with self._market_quote_lock:
            with self.database() as conn:
                if not quote_refresh_due(conn, now):
                    return None
            source = await asyncio.to_thread(fetch_source_price)
            with self.database() as conn:
                refresh_market_quote(conn, now, source)
            return source

    def handlers(self):
        return {
            "get_estate": self.handle_get_estate,
            "estate_set_skin": self.handle_estate_set_skin,
            "estate_buy_skin": self.handle_estate_buy_skin,
            "estate_buy": self.handle_estate_buy,
            "estate_lottery_draw": self.handle_estate_lottery_draw,
            "estate_market_get": self.handle_estate_market_get,
            "estate_market_trade": self.handle_estate_market_trade,
            "estate_use_land_upgrade_ticket": self.handle_estate_use_land_upgrade_ticket,
            "estate_plant": self.handle_estate_plant,
            "estate_harvest": self.handle_estate_harvest,
            "estate_fertilize": self.handle_estate_fertilize,
            "estate_sell": self.handle_estate_sell,
            "estate_sell_all": self.handle_estate_sell_all,
            "estate_buy_tool": self.handle_estate_buy_tool,
            "estate_upgrade_tool": self.handle_estate_upgrade_tool,
            "estate_repair_tool": self.handle_estate_repair_tool,
            "estate_pet": self.handle_estate_pet,
            "estate_penguin": self.handle_estate_penguin,
            "estate_start_fishing": self.handle_estate_start_fishing,
            "estate_finish_fishing": self.handle_estate_finish_fishing,
            "estate_start_mining": self.handle_estate_start_mining,
            "estate_mine_cell": self.handle_estate_mine_cell,
            "estate_finish_mining": self.handle_estate_finish_mining,
            "estate_list_visits": self.handle_estate_list_visits,
            "estate_enter_visit": self.handle_estate_enter_visit,
            "estate_leave_visit": self.handle_estate_leave_visit,
            "estate_steal_crop": self.handle_estate_steal_crop,
            "estate_visit_fertilize": self.handle_estate_visit_fertilize,
            "estate_get_notifications": self.handle_estate_get_notifications,
            "estate_mark_notifications_read": self.handle_estate_mark_notifications_read,
            "estate_visit_move": self.presence.move,
        }

    async def publish_estate(self, username, snapshot, result=None, request_id=None):
        """把庄园完整快照同步到同账号的所有连接。"""
        payload = {
            "type": "estate_state",
            **snapshot,
            "request_id": request_id,
            "result": result,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for socket, client_state in list(self.clients.items()):
            user = client_state.get("user")
            if user and user["username"] == username:
                user["coins"] = snapshot["coins"]
                await self.send_encoded(socket, encoded)

    async def handle_estate_list_visits(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录"})
            return
        try:
            with self.database() as conn:
                entries = estate_list_estates(conn, user["username"], data.get("query"), int(time.time()))
            await self.send_json(websocket, {"type": "estate_visit_list", "estates": entries,
                                        "request_id": data.get("request_id")})
        except EstateError as error:
            await self.send_json(websocket, {"type": "estate_error", "code": error.code,
                                        "message": str(error), "request_id": data.get("request_id")})

    async def handle_estate_enter_visit(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录"})
            return
        try:
            with self.database() as conn:
                now = int(time.time())
                snapshot = public_estate_state(conn, user["username"], data.get("owner_username"), now, adjust_coins)
                visitor = estate_state(conn, user["username"], now, adjust_coins)
            players = await self.presence.join(websocket, state, snapshot["owner_username"],
                                               visitor["profile"]["skin_id"])
            await self.send_json(websocket, {"type": "estate_visit_state", **snapshot,
                                        "players": players, "request_id": data.get("request_id")})
        except EstateError as error:
            await self.send_json(websocket, {"type": "estate_error", "code": error.code,
                                        "message": str(error), "request_id": data.get("request_id")})

    async def handle_estate_leave_visit(self, websocket, state, data):
        await self.presence.leave(websocket, state)
        await self.send_json(websocket, {"type": "estate_visit_left_self", "request_id": data.get("request_id")})

    async def handle_estate_steal_crop(self, websocket, state, data):
        user = state.get("user")
        owner = state.get("estate_owner")
        request_id = data.get("request_id")
        if not user:
            await self.send_json(websocket, {"type": "estate_error", "code": "auth_required", "message": "请先登录", "request_id": request_id})
            return
        if not owner or owner.lower() != str(data.get("owner_username") or "").lower():
            await self.send_json(websocket, {"type": "estate_error", "code": "not_visiting", "message": "未在目标庄园内", "request_id": request_id})
            return
        try:
            plot_id = int(data.get("plot_id"))
            plot_x, plot_y = ESTATE_PLOT_POSITIONS[plot_id]
            position = state.get("estate_position", {})
            if math.hypot(float(position.get("x", -999)) - (plot_x + 41),
                          float(position.get("y", -999)) - (plot_y + 35)) > 82:
                raise ValueError
        except (TypeError, ValueError, IndexError):
            await self.send_json(websocket, {"type": "estate_error", "code": "too_far",
                                        "message": "请先走到农田附近", "request_id": request_id})
            return
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                result = estate_steal_crop(conn, user["username"], request_id, owner,
                                           plot_id, now, adjust_coins)
                snapshot = public_estate_state(conn, user["username"], owner, now, adjust_coins)
            await self.send_json(websocket, {"type": "estate_steal_result", "result": result,
                                        "state": snapshot, "request_id": request_id})
            if not result.get("replayed") and result.get("outcome") == "stolen":
                await self.presence.broadcast(owner, {"type": "estate_crop_stolen",
                    "plot_id": result["plot_id"], "visitor_username": user["username"]},
                    exclude=websocket)
        except (EstateError, sqlite3.Error) as error:
            code = error.code if isinstance(error, EstateError) else "estate_failed"
            message = str(error) if isinstance(error, EstateError) else "庄园暂时忙碌，请稍后重试"
            await self.send_json(websocket, {"type": "estate_error", "code": code,
                                        "message": message, "request_id": request_id})

    async def handle_estate_visit_fertilize(self, websocket, state, data):
        user = state.get("user")
        owner = state.get("estate_owner")
        request_id = data.get("request_id")
        if not user:
            await self.send_json(websocket, {"type": "estate_error", "code": "auth_required",
                                             "message": "请先登录", "request_id": request_id})
            return
        if not owner or owner.lower() != str(data.get("owner_username") or "").lower():
            await self.send_json(websocket, {"type": "estate_error", "code": "not_visiting",
                                             "message": "未在目标庄园内", "request_id": request_id})
            return
        try:
            plot_id = int(data.get("plot_id"))
            plot_x, plot_y = ESTATE_PLOT_POSITIONS[plot_id]
            position = state.get("estate_position", {})
            if math.hypot(float(position.get("x", -999)) - (plot_x + 41),
                          float(position.get("y", -999)) - (plot_y + 35)) > 82:
                raise ValueError
        except (TypeError, ValueError, IndexError):
            await self.send_json(websocket, {"type": "estate_error", "code": "too_far",
                                             "message": "请先走到农田附近", "request_id": request_id})
            return
        try:
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                result = estate_fertilize(conn, user["username"], request_id, owner, plot_id, now)
                visit_snapshot = public_estate_state(conn, user["username"], owner, now, adjust_coins)
                own_snapshot = estate_state(conn, user["username"], now, adjust_coins)
                owner_snapshot = estate_state(conn, owner, now, adjust_coins)
            await self.send_json(websocket, {"type": "estate_visit_fertilize_result",
                                             "result": result, "state": visit_snapshot,
                                             "home_state": own_snapshot,
                                             "request_id": request_id})
            if not result.get("replayed"):
                await self.publish_estate(owner, owner_snapshot)
                await self.presence.broadcast(owner, {"type": "estate_crop_fertilized",
                    "plot_id": plot_id, "ready_at": result["ready_at"]}, exclude=websocket)
        except (EstateError, sqlite3.Error) as error:
            code = error.code if isinstance(error, EstateError) else "estate_failed"
            message = str(error) if isinstance(error, EstateError) else "庄园暂时忙碌，请稍后重试"
            await self.send_json(websocket, {"type": "estate_error", "code": code,
                                             "message": message, "request_id": request_id})

    async def handle_estate_get_notifications(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        with self.database() as conn, conn:
            rows = estate_notifications(conn, user["username"], int(time.time()))
        await self.send_json(websocket, {"type": "estate_notifications", "notifications": rows,
                                    "request_id": data.get("request_id")})

    async def handle_estate_mark_notifications_read(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        with self.database() as conn, conn:
            result = estate_mark_notifications_read(conn, user["username"], data.get("ids"), int(time.time()))
        await self.send_json(websocket, {"type": "estate_notifications_read", "result": result,
                                    "request_id": data.get("request_id")})

    async def handle_estate_action(self, websocket, state, data, action,
                                   now_override=None, market_source=None, execution_source=None):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {
                "type": "estate_error", "code": "auth_required",
                "message": "请先登录", "request_id": data.get("request_id"),
            })
            return
        username = user["username"]
        request_id = data.get("request_id")
        try:
            with self.database() as conn, conn:
                now = int(time.time()) if now_override is None else now_override
                result = None
                if action != "get":
                    conn.execute("BEGIN IMMEDIATE")
                if action == "set_skin":
                    result = estate_set_skin(conn, username, request_id, data.get("skin_id"), now)
                elif action == "buy_skin":
                    result = estate_buy_skin(conn, username, request_id, data.get("skin_id"), now, adjust_coins)
                elif action == "buy":
                    result = estate_buy(
                        conn, username, request_id, data.get("kind"),
                        data.get("item_id"), data.get("quantity", 1), now,
                        adjust_coins,
                    )
                elif action == "lottery_draw":
                    result = estate_draw_lottery(conn, username, request_id, now, adjust_coins)
                elif action == "market_trade":
                    result = estate_trade_market(conn, username, request_id,
                                                 data.get("side"), data.get("quantity"), now,
                                                 adjust_coins, market_source, execution_source)
                elif action == "use_land_upgrade_ticket":
                    result = estate_use_land_upgrade_ticket(
                        conn, username, request_id, data.get("plot_id"), now)
                elif action == "plant":
                    result = estate_plant(
                        conn, username, request_id, data.get("plot_id"),
                        data.get("crop_id"), now,
                    )
                elif action == "harvest":
                    result = estate_harvest(
                        conn, username, request_id, data.get("plot_id"), now,
                    )
                elif action == "fertilize":
                    result = estate_fertilize(conn, username, request_id, username,
                                              data.get("plot_id"), now)
                elif action == "sell":
                    result = estate_sell(
                        conn, username, request_id, data.get("item_id"),
                        data.get("quantity"), now, adjust_coins,
                    )
                elif action == "sell_all":
                    result = estate_sell_all(
                        conn, username, request_id, now, adjust_coins,
                    )
                elif action == "buy_tool":
                    result = estate_buy_tool(conn, username, request_id,
                                             data.get("tool_type"), now, adjust_coins)
                elif action == "upgrade_tool":
                    result = estate_upgrade_tool(conn, username, request_id,
                                                 data.get("tool_type"), now, adjust_coins)
                elif action == "repair_tool":
                    result = estate_repair_tool(conn, username, request_id,
                                                data.get("tool_type"), now, adjust_coins)
                elif action == "pet":
                    result = estate_buy_or_upgrade_pet(conn, username, request_id, now, adjust_coins)
                elif action == "penguin":
                    result = estate_buy_or_upgrade_penguin(conn, username, request_id, now, adjust_coins)
                elif action == "start_fishing":
                    result = estate_start_fishing(conn, username, request_id,
                                                  data.get("bait_id"), now)
                elif action == "finish_fishing":
                    result = estate_finish_fishing(conn, username, request_id,
                                                   data.get("session_id"), data.get("trace"), now)
                elif action == "start_mining":
                    result = estate_start_mining(conn, username, request_id,
                                                 data.get("mine_level"), now)
                elif action == "mine_cell":
                    result = estate_mine_cell(conn, username, request_id,
                                              data.get("run_id"), data.get("cell"), now)
                elif action == "finish_mining":
                    result = estate_finish_mining(conn, username, request_id,
                                                  data.get("run_id"), now)
                snapshot = estate_state(conn, username, now, adjust_coins)
        except (EstateError, ValueError, sqlite3.Error) as error:
            code = error.code if isinstance(error, EstateError) else "estate_failed"
            if isinstance(error, sqlite3.Error):
                logger.exception("estate database failure for %s", username)
                message = "庄园暂时忙碌，请稍后重试"
            else:
                logger.info("estate action rejected for %s: %s (%s)", username, error, code)
                message = str(error)
            payload = {
                "type": "estate_error", "code": code, "message": message,
                "request_id": request_id,
            }
            try:
                with self.database() as conn:
                    payload["state"] = estate_state(conn, username, int(time.time()), adjust_coins)
            except (EstateError, sqlite3.Error):
                pass
            await self.send_json(websocket, payload)
            return
        if action == "get":
            user["coins"] = snapshot["coins"]
            players = await self.presence.join(websocket, state, username,
                                               snapshot["profile"]["skin_id"])
            await self.send_json(websocket, {
                "type": "estate_state", **snapshot, "players": players, "request_id": None,
                "result": None,
            })
        else:
            await self.publish_estate(username, snapshot, result, request_id)
            if action == "set_skin":
                await self.presence.update_skin(username, snapshot["profile"]["skin_id"])

    async def handle_get_estate(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "get")
        user = state.get("user")
        if user:
            with self.database() as conn, conn:
                rows = estate_notifications(conn, user["username"], int(time.time()))
            await self.send_json(websocket, {"type": "estate_notifications", "notifications": rows})

    async def handle_estate_set_skin(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "set_skin")

    async def handle_estate_buy_skin(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "buy_skin")

    async def handle_estate_buy(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "buy")

    async def handle_estate_lottery_draw(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "lottery_draw")

    async def handle_estate_market_get(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.send_json(websocket, {"type": "estate_error", "code": "auth_required",
                                            "message": "请先登录", "request_id": data.get("request_id")})
            return
        try:
            now = int(time.time())
            await self.refresh_market_quote(now)
            with self.database() as conn:
                snapshot = estate_market_snapshot(conn, user["username"], now, None)
            await self.send_json(websocket, {"type": "estate_market_state", "market": snapshot,
                                            "request_id": data.get("request_id")})
        except sqlite3.Error:
            logger.exception("estate market unavailable for %s", user["username"])
            await self.send_json(websocket, {"type": "estate_error", "code": "estate_failed",
                                            "message": "行情暂不可用，请稍后重试",
                                            "request_id": data.get("request_id")})

    async def handle_estate_market_trade(self, websocket, state, data):
        if not state.get("user"):
            await self.handle_estate_action(websocket, state, data, "market_trade")
            return
        now = int(time.time())
        try:
            execution_source = await self.refresh_market_quote(now)
        except sqlite3.Error:
            logger.exception("estate market lookup failed")
            await self.send_json(websocket, {"type": "estate_error", "code": "estate_failed",
                                            "message": "行情暂不可用，请稍后重试",
                                            "request_id": data.get("request_id")})
            return
        if execution_source is None:
            with self.database() as conn:
                row = conn.execute("SELECT source_kind FROM estate_market_index WHERE id=1").fetchone()
            if row and row[0] == "live":
                execution_source = await asyncio.to_thread(fetch_source_price)
        await self.handle_estate_action(websocket, state, data, "market_trade",
                                        now_override=now, market_source=None,
                                        execution_source=execution_source)

    async def handle_estate_use_land_upgrade_ticket(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "use_land_upgrade_ticket")

    async def handle_estate_plant(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "plant")

    async def handle_estate_harvest(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "harvest")

    async def handle_estate_fertilize(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "fertilize")

    async def handle_estate_sell(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "sell")

    async def handle_estate_sell_all(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "sell_all")

    async def handle_estate_buy_tool(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "buy_tool")

    async def handle_estate_upgrade_tool(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "upgrade_tool")

    async def handle_estate_repair_tool(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "repair_tool")

    async def handle_estate_pet(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "pet")

    async def handle_estate_penguin(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "penguin")

    async def handle_estate_start_fishing(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "start_fishing")

    async def handle_estate_finish_fishing(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "finish_fishing")

    async def handle_estate_start_mining(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "start_mining")

    async def handle_estate_mine_cell(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "mine_cell")

    async def handle_estate_finish_mining(self, websocket, state, data):
        await self.handle_estate_action(websocket, state, data, "finish_mining")
