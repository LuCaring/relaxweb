import logging
import time

from games.base import ROOM_TYPES, create_room, parse_amount
from games.holdem import BLIND_PRESETS as GAME_BLIND_PRESETS
from server.chat import MAX_CHAT_LENGTH
from server.transport import rate_limited
from server.wallet import adjust_coins

GAME_MAX_PLAYERS = 9
logger = logging.getLogger("live-chat")


class RoomProtocol:
    """游戏厅消息的权限与参数校验；玩法动作仍委托给房间引擎。"""

    def __init__(self, database, hub, rooms, accounts, settlement):
        self.database = database
        self.hub = hub
        self.rooms = rooms
        self.accounts = accounts
        self.settlement = settlement

    async def _refresh_voice(self, room, username):
        voice_hub = getattr(self.rooms, "voice_hub", None)
        if voice_hub is not None:
            try:
                await voice_hub.recheck()
            except Exception:
                logger.warning("voice hub recheck failed before game voice refresh",
                               exc_info=True)
        voice = getattr(self.rooms, "voice", None)
        if voice is None:
            return
        try:
            await voice.sync_user(room, username, force=True)
        except Exception:
            logger.warning("voice refresh failed for room %s user %s",
                           room.id, username, exc_info=True)

    def handlers(self):
        return {
            "list_rooms": self.handle_list_rooms,
            "get_room": self.handle_get_room,
            "create_room": self.handle_create_room,
            "join_room": self.handle_join_room,
            "leave_room": self.handle_leave_room,
            "start_game": self.handle_start_game,
            "poker_action": self.handle_poker_action,
            "pause_game": self.handle_pause_game,
            "settle_vote": self.handle_settle_vote,
            "hand_continue": self.handle_hand_continue,
            "restart_game": self.handle_restart_game,
            "watch_player": self.handle_watch_player,
            "room_chat": self.handle_room_chat,
        }

    async def handle_list_rooms(self, websocket, state, data):
        await self.hub.send_json(
            websocket,
            {"type": "room_list", "rooms": [room.summary() for room in self.rooms.game_rooms.values()]},
        )

    async def handle_get_room(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        if rate_limited(state, "last_get_room", 0.2):
            return
        room = self.rooms.find_user_room(user["username"])
        if room:
            if room.has_spectator(user["username"]):
                await self.hub.send_json(websocket, room.spectator_view(user["username"]))
            else:
                await self.hub.send_json(websocket, room.view_for(user["username"]))
            await self.hub.send_json(
                websocket,
                {
                    "type": "room_chat_history",
                    "room_id": room.id,
                    "messages": room.visible_chat(user["username"]),
                },
            )
            await self._refresh_voice(room, user["username"])
        else:
            await self.hub.send_json(websocket, {"type": "room_closed", "reason": ""})

    async def handle_create_room(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_room_op", 3.0):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "操作太频繁，请稍后再试"})
            return
        username = user["username"]
        if self.rooms.find_user_room(username):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "你已在一个房间中，请先退出"})
            return
        name = str(data.get("name", "")).strip()[:20]
        game = str(data.get("game") or "holdem")
        if game not in ROOM_TYPES:
            game = "holdem"
        buy_in = parse_amount(data.get("buy_in"))
        blind = data.get("blind")
        blind = blind if blind in GAME_BLIND_PRESETS else 5
        if buy_in is None or buy_in < blind * 20:
            await self.hub.send_json(
                websocket,
                {"type": "game_error", "message": f"买入至少需要 {blind * 20:,.0f} 金币（20 倍小盲注）"},
            )
            return
        # 玩法自定义规则原样交给房间类清洗（各引擎自己 sanitize）
        rules = data.get("rules")
        if not isinstance(rules, dict):
            rules = {}
        self.rooms.room_seq += 1
        room_id = int(time.time() * 1000) % 1_000_000_000 + self.rooms.room_seq
        room_name = name or f"{self.accounts.display_name(username)}的房间"
        try:
            with self.database() as conn, conn:
                balance = conn.execute(
                    "SELECT coins FROM users WHERE username = ?", (username,)
                ).fetchone()[0] or 0.0
                if balance < buy_in:
                    raise ValueError("金币不足，无法买入")
                adjust_coins(
                    conn, username, -buy_in, "game_buyin", f"游戏厅买入：{room_name}",
                    ref=f"room:{room_id}:{username}",
                )
        except ValueError as error:
            await self.hub.send_json(websocket, {"type": "game_error", "message": str(error)})
            return
        room = create_room(
            game,
            room_id=room_id,
            name=room_name,
            owner=username,
            buy_in=buy_in,
            blind=blind,
            rules=rules,
        )
        self.rooms.attach_host(room)
        room.add_member(username, buy_in)
        self.rooms.game_rooms[room.id] = room
        self.settlement.set_escrow(username, room.id, buy_in)
        logger.info("game room %s created by %s", room.id, username)
        await self.hub.send_json(websocket, {"type": "game_joined", "room": room.view_for(username)})
        await self._refresh_voice(room, username)
        await self.rooms.broadcast_room_list()

    async def handle_join_room(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        if rate_limited(state, "last_room_op", 1.0):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "操作太频繁，请稍后再试"})
            return
        username = user["username"]
        if self.rooms.find_user_room(username):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "你已在一个房间中，请先退出"})
            return
        room = self.rooms.game_rooms.get(data.get("room_id"))
        if not room:
            await self.hub.send_json(websocket, {"type": "game_error", "message": "房间不存在或已解散"})
            return
        if data.get("spectate"):
            # 开局后进入一律观战：不买入、不占座，随时可退出。
            if room.status not in ("playing", "settled"):
                await self.hub.send_json(websocket, {"type": "game_error", "message": "对局尚未开始，暂不能观战"})
                return
            watched = data.get("watch")
            room.add_spectator(username, watched if isinstance(watched, str) else None)
            logger.info("%s watches game room %s", username, room.id)
            await self.hub.send_json(websocket, {"type": "game_joined", "room": room.spectator_view(username)})
            await self.hub.send_json(
                websocket,
                {
                    "type": "room_chat_history",
                    "room_id": room.id,
                    "messages": room.visible_chat(username),
                },
            )
            await self._refresh_voice(room, username)
            await self.rooms.broadcast_spectator_notice(room, username, joined=True)
            return
        if room.status != "waiting":
            await self.hub.send_json(websocket, {"type": "game_error", "message": "游戏已开始，请以观战身份进入"})
            return
        if len(room.seating) >= getattr(room, "max_seats", GAME_MAX_PLAYERS):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "房间已满"})
            return
        buy_in = room.buy_in
        try:
            with self.database() as conn, conn:
                balance = conn.execute(
                    "SELECT coins FROM users WHERE username = ?", (username,)
                ).fetchone()[0] or 0.0
                if balance < buy_in:
                    raise ValueError(f"金币不足，进入该房间需要买入 {buy_in:,.2f} 金币")
                adjust_coins(
                    conn, username, -buy_in, "game_buyin", f"游戏厅买入：{room.name}",
                    ref=f"room:{room.id}:{username}",
                )
        except ValueError as error:
            await self.hub.send_json(websocket, {"type": "game_error", "message": str(error)})
            return
        room.add_member(username, buy_in)
        self.settlement.set_escrow(username, room.id, buy_in)
        await self.hub.send_json(websocket, {"type": "game_joined", "room": room.view_for(username)})
        await self.hub.send_json(
            websocket,
            {
                "type": "room_chat_history",
                "room_id": room.id,
                "messages": room.visible_chat(username),
            },
        )
        await room.broadcast_views()
        await self.rooms.broadcast_room_list()

    async def handle_leave_room(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        room = self.rooms.find_user_room(user["username"])
        if not room:
            await self.hub.send_json(websocket, {"type": "game_error", "message": "你不在任何房间中"})
            return
        if room.owner == user["username"]:
            reason = "房主流局" if room.status == "playing" else "房主解散了房间"
            await self.rooms.dissolve_room(room, reason)
            return
        await self.rooms.leave_room_internal(room, user["username"])

    async def handle_start_game(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or room.owner != user["username"]:
            await self.hub.send_json(websocket, {"type": "game_error", "message": "只有房主可以开始游戏"})
            return
        if room.status == "playing":
            await self.hub.send_json(websocket, {"type": "game_error", "message": "游戏已在进行中"})
            return
        try:
            logger.info("game room %s started by %s", room.id, user["username"])
            await room.start()
        except ValueError as error:
            await self.hub.send_json(websocket, {"type": "game_error", "message": str(error)})
            return
        await self.rooms.broadcast_room_list()

    async def handle_poker_action(self, websocket, state, data):
        user = state.get("user")
        if not user:
            return
        action = str(data.get("action", ""))
        room = self.rooms.find_user_room(user["username"])
        # 观战者只能看：所有对局动作仅对成员生效
        if not room or not room.has_member(user["username"]) or not room.in_hand():
            return
        # 不做限流：出牌/摸牌/补喊是毫秒级连招（UNO 摸到可出牌立刻出、
        # 快节奏下转眼又轮到自己），且引擎按回合校验，垃圾动作无效且廉价
        await room.perform_action(user["username"], action, data)

    async def handle_pause_game(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or room.owner != user["username"] or not room.in_hand():
            await self.hub.send_json(websocket, {"type": "game_error", "message": "只有房主可以在进行中的牌局里暂停"})
            return
        if rate_limited(state, "last_game_admin", 0.5):
            return
        paused = bool(data.get("paused"))
        if paused == room.paused:
            return
        if paused:
            room.pause()
            logger.info("game room %s paused by %s", room.id, user["username"])
        else:
            room.resume()
            logger.info("game room %s resumed by %s", room.id, user["username"])
        await room.broadcast_views()
        await self.rooms.broadcast_room_list()

    async def handle_settle_vote(self, websocket, state, data):
        """对局结束投票：过半数生效；再来一局会结清当前轮并按买入额重置筹码。"""
        user = state.get("user")
        if not user:
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or not room.has_member(user["username"]) or room.status not in ("playing", "settled"):
            return
        choice = str(data.get("choice") or "")
        if choice not in ("next", "dissolve"):
            return
        blind = data.get("blind")
        blind = blind if blind in GAME_BLIND_PRESETS else room.blind
        # 冷却刻意很短：投票本身幂等（同一个人只留最后一票），而连打多局时
        # 上一局的票和下一局的票可能只隔几十毫秒，卡太久会把合法投票丢掉
        if rate_limited(state, "last_settle_vote", 0.1):
            return
        executed = await room.cast_vote(user["username"], choice, blind)
        if executed == "dissolve":
            return
        await room.broadcast_views()

    async def handle_hand_continue(self, websocket, state, data):
        """手牌结果确认：全员确认或倒计时结束后开下一手或进入整局结算。"""
        user = state.get("user")
        if not user:
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or not room.has_member(user["username"]) or room.status != "playing":
            return
        # 不限流：连得快时两手之间可能只隔几十毫秒，而 mark_ready 自身幂等
        await room.mark_ready(user["username"])

    async def handle_restart_game(self, websocket, state, data):
        user = state.get("user")
        if not user:
            await self.hub.send_json(websocket, {"type": "auth_error", "message": "请先登录"})
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or room.owner != user["username"]:
            await self.hub.send_json(websocket, {"type": "game_error", "message": "只有房主可以重新开始"})
            return
        if room.status != "playing":
            await self.hub.send_json(websocket, {"type": "game_error", "message": "游戏尚未开始"})
            return
        if rate_limited(state, "last_game_admin", 0.5):
            return
        logger.info("game room %s restarted by %s", room.id, user["username"])
        await room.restart()

    async def handle_watch_player(self, websocket, state, data):
        """观战者切换第一视角：换发被看玩家的私有视图（不含可操作字段）。"""
        user = state.get("user")
        if not user:
            return
        room = self.rooms.find_user_room(user["username"])
        if not room or not room.has_spectator(user["username"]):
            await self.hub.send_json(websocket, {"type": "game_error", "message": "你不在观战中"})
            return
        if rate_limited(state, "last_watch_switch", 0.2):
            return
        watched = str(data.get("username") or "")
        if watched not in room.members:
            await self.hub.send_json(websocket, {"type": "game_error", "message": "该玩家已不在本房间"})
            return
        room.spectators[user["username"]] = watched
        await self.hub.send_json(websocket, room.spectator_view(user["username"]))

    async def handle_room_chat(self, websocket, state, data):
        """房间内聊天：只广播给房间成员，记录在内存里随房间销毁。"""
        user = state.get("user")
        if not user:
            return
        room = self.rooms.find_user_room(user["username"])
        if not room:
            return
        if rate_limited(state, "last_room_message", 1.0):
            await self.hub.send_json(websocket, {"type": "error", "message": "发送太快了"})
            return
        text = str(data.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        message = {
            "type": "room_chat",
            "room_id": room.id,
            "username": user["username"],
            "nickname": user.get("nickname") or "",
            "role": user["role"],
            "spectator": room.has_spectator(user["username"]),
            "text": text,
            "time": time.strftime("%m/%d %H:%M"),
        }
        # 子类可按阶段定向（狼人杀夜晚狼频道/死者频道），返回 "" 表示拦截
        channel = room.chat_route(user["username"], message)
        if channel == "":
            await self.hub.send_json(websocket, {"type": "error", "message": "当前阶段不能发言"})
            return
        if channel:
            message["channel"] = channel
        room.chat.append(message)
        audience = room.chat_audience(channel) if channel else None
        if audience is None:
            await room.broadcast_payload(dict(message))
        else:
            await self.rooms.send_to_members(room, audience, dict(message))
