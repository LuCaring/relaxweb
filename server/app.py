"""显式装配协议模块；每个 Application 拥有独立在线状态和生命周期。"""
import asyncio
import json
import logging

import websockets

from server.accounts import Accounts
from server.admin import AdminProtocol
from server.auth import AuthProtocol
from server.betting import Betting
from server.chat import ChatProtocol
from server.database import database as default_database
from server.estate.presence import EstatePresence
from server.estate.protocol import EstateProtocol
from server.ranking import Ranking
from server.rewards import RewardsProtocol
from server.rooms.host import RoomHost
from server.rooms.protocol import RoomProtocol
from server.rooms.settlement import Settlement
from server.routing import merge_handlers
from server.schema import init_db
from server.transport import ConnectionHub, connection_client, rate_limited
from server.voice_livekit import VoiceService
from server.wallet import WalletProtocol

logger = logging.getLogger("live-chat")


class Application:
    def __init__(self, database, *, disconnect_grace=30.0):
        self.database = database
        self.hub = ConnectionHub()
        self.accounts = Accounts(database)
        self.chat = ChatProtocol(self.hub)
        self.wallet = WalletProtocol(database, self.hub, self.accounts, self.chat)
        self.ranking = Ranking(database, self.hub)
        self.rewards = RewardsProtocol(database, self.hub)
        self.settlement = Settlement(database)
        self.voice = VoiceService(self.hub)
        self.rooms = RoomHost(database, self.hub, self.accounts, self.settlement,
                              self.ranking, self.rewards, self.wallet,
                              disconnect_grace=disconnect_grace, voice=self.voice)
        self.room_protocol = RoomProtocol(database, self.hub, self.rooms,
                                          self.accounts, self.settlement)
        self.betting = Betting(
            database=database, send_json=self.hub.send_json, broadcast=self.hub.broadcast,
            broadcast_system=self.chat.broadcast_system, display_name=self.accounts.display_name,
            rate_limited=rate_limited,
        )
        self.estate_presence = EstatePresence(
            clients=self.hub.clients, send_encoded=self.hub.send_encoded,
            rate_limited=rate_limited,
        )
        self.estate_protocol = EstateProtocol(
            database=database, clients=self.hub.clients, send_json=self.hub.send_json,
            send_encoded=self.hub.send_encoded, presence=self.estate_presence,
        )
        self.auth = AuthProtocol(database, self.hub, self.accounts, self.rooms, self.betting)
        self.admin = AdminProtocol(database, self.hub, self.wallet)
        self.handlers = merge_handlers(
            self.hub.handlers(), self.auth.handlers(), self.chat.handlers(),
            self.wallet.handlers(), self.ranking.handlers(), self.rewards.handlers(),
            self.admin.handlers(), self.room_protocol.handlers(),
            self.estate_protocol.handlers(), self.betting.handlers(),
        )
        self._watcher = None

    async def aclose(self):
        """监听器关闭后释放本实例的后台任务，不触发额外的业务结算。"""
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None
        await self.rooms.aclose()
        await self.voice.aclose()

    async def run(self, host, port):
        init_db(self.database)
        self.settlement.refund_game_escrows()
        self.betting.active_bet = self.betting.load_open_bet()
        if self.betting.active_bet:
            logger.info("resumed open bet #%d from %s: %s",
                        self.betting.active_bet["id"], self.betting.active_bet["creator"],
                        self.betting.active_bet["question"])
        logger.info("chat server listening on ws://%s:%d", host, port)
        try:
            async with websockets.serve(
                self.handler, host, port, max_size=300_000, max_queue=32,
                ping_interval=20, ping_timeout=20, close_timeout=5,
            ):
                self._watcher = asyncio.create_task(self.betting.bet_close_watcher())
                await asyncio.Future()
        finally:
            await self.aclose()

    async def handler(self, websocket):
        state = {
            "user": None,
            "client": connection_client(websocket),
            "send_lock": asyncio.Lock(),
            "last_message": 0.0,
            "last_auth_attempt": 0.0,
            "last_profile_update": 0.0,
            "last_invite_create": 0.0,
            "last_transfer": 0.0,
            "last_bet_action": 0.0,
            "last_estate_move": 0.0,
        }
        self.hub.clients[websocket] = state
        logger.info("connection opened; online=%d", len(self.hub.clients))
        await self.hub.send_json(websocket, {"type": "history", "messages": list(self.chat.history)})
        await self.hub.broadcast_online_count()
        try:
            async for raw_message in websocket:
                try:
                    data = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                action = self.handlers.get(data.get("type"))
                if action:
                    await action(websocket, state, data)
        except websockets.ConnectionClosed:
            pass
        finally:
            await self.estate_presence.leave(websocket, state)
            self.hub.clients.pop(websocket, None)
            logger.info("connection closed; online=%d", len(self.hub.clients))
            await self.rooms.cleanup_rooms_on_disconnect(state)
            await self.hub.broadcast_online_count()


def create_app(database=None, *, disconnect_grace=30.0):
    """只装配，不建表或启动任务；测试可注入独立的临时库连接工厂。"""
    return Application(default_database if database is None else database,
                       disconnect_grace=disconnect_grace)
