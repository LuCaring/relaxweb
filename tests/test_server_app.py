"""应用装配、传输与生命周期回归；所有数据及监听端口均为本地临时资源。"""
import asyncio
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

from server.accounts import hash_password
from server.app import create_app
from server.database import database
from server.schema import init_db
from server.transport import ConnectionHub, connection_client


class Socket:
    remote_address = ("127.0.0.1", 12345)

    def __init__(self):
        self.messages = []
        self.sending = False

    async def send(self, payload):
        assert not self.sending, "同一连接不能并发发送"
        self.sending = True
        try:
            await asyncio.sleep(0)
            self.messages.append(json.loads(payload))
        finally:
            self.sending = False


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_broadcasts_and_direct_messages_share_connection_lock(self):
        hub = ConnectionHub()
        sockets = [Socket(), Socket()]
        for ws in sockets:
            hub.clients[ws] = {"send_lock": asyncio.Lock(), "user": {"username": "alice"}}
        await asyncio.gather(hub.broadcast({"type": "one"}), hub.broadcast({"type": "two"}),
                             hub.send_to_user("alice", {"type": "direct"}))
        for ws in sockets:
            self.assertEqual({m["type"] for m in ws.messages}, {"one", "two", "direct"})
        self.assertEqual(len(hub.clients), 2)

    async def test_failed_send_drops_only_failed_connection(self):
        hub = ConnectionHub()
        good, bad = Socket(), Socket()
        for ws in (good, bad):
            hub.clients[ws] = {"send_lock": asyncio.Lock()}
        with patch.object(bad, "send", side_effect=OSError("closed")):
            await hub.broadcast({"type": "test"})
        self.assertEqual(list(hub.clients), [good])
        self.assertEqual(good.messages, [{"type": "test"}])

    async def test_online_count_excludes_game_connections_and_users_are_deduplicated(self):
        hub = ConnectionHub()
        first, second = Socket(), Socket()
        for ws, client in ((first, ""), (second, "game")):
            hub.clients[ws] = {"client": client, "send_lock": asyncio.Lock(),
                               "user": {"username": "alice"}}
        await hub.broadcast_online_count()
        self.assertEqual(first.messages[-1], {"type": "online", "count": 1})
        await hub.handlers()["get_online"](first, {}, {})
        self.assertEqual(len(first.messages[-1]["users"]), 1)
        old = type("OldSocket", (), {"path": "/?client=game"})()
        new = type("NewSocket", (), {"request": type("Request", (), {"path": "/?client=game"})()})()
        self.assertEqual(connection_client(old), "game")
        self.assertEqual(connection_client(new), "game")
        self.assertEqual(connection_client(first), "")


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.app = self.make_app("first")

    def make_app(self, name):
        app = create_app(partial(database, str(Path(self.tmp) / (name + ".db"))))
        init_db(app.database)
        password, salt = hash_password("password123")
        with app.database() as conn, conn:
            conn.executemany(
                "INSERT INTO users(username,password_hash,salt,created_at,coins) VALUES (?,?,?,0,1000)",
                [(name, password, salt) for name in ("alice", "bob")],
            )
        self.addAsyncCleanup(app.aclose)
        return app

    def connect(self, name="alice"):
        ws = Socket()
        state = {"user": self.app.accounts.authenticate_user(name, "password123"),
                 "send_lock": asyncio.Lock()}
        self.app.hub.clients[ws] = state
        return ws, state

    async def test_factory_has_no_database_or_event_loop_side_effects(self):
        def forbidden():
            raise AssertionError("create_app 不应访问数据库")
        app = create_app(forbidden)
        self.assertFalse(app.hub.clients)
        self.assertFalse(app.rooms.game_rooms)
        self.assertIsNone(app._watcher)
        await app.aclose()

    async def test_route_contract_is_complete(self):
        expected = """register login resume logout update_profile get_profile delete_account get_online
            list_invites create_invite chat get_finance get_daily_rewards daily_checkin draw_lottery
            claim_holdem_reward get_rating_history get_rating_leaderboard get_asset_leaderboard
            transfer_coins admin_set_coins list_rooms get_room create_room join_room leave_room
            start_game poker_action pause_game restart_game settle_vote hand_continue room_chat
            watch_player list_users get_bet create_bet place_bet settle_bet cancel_bet close_bet
            get_estate estate_buy estate_plant estate_harvest estate_sell estate_sell_all estate_pet
            estate_set_skin estate_buy_skin estate_buy_tool estate_upgrade_tool estate_repair_tool
            estate_start_fishing estate_finish_fishing estate_start_mining estate_mine_cell
            estate_finish_mining estate_list_visits estate_enter_visit estate_leave_visit
            estate_visit_move estate_steal_crop estate_get_notifications estate_mark_notifications_read"""
        self.assertEqual(set(self.app.handlers), set(expected.split()))
        self.assertEqual(len(self.app.handlers), 65)

    async def test_two_apps_isolate_accounts_rooms_history_and_protocol_state(self):
        second = self.make_app("second")
        ws, state = self.connect()
        await self.app.handlers["chat"](ws, state, {"text": "hello"})
        await self.app.handlers["create_room"](ws, state, {"game": "uno", "buy_in": 100, "blind": 1})
        token = self.app.accounts.create_session("alice")
        self.assertIsNone(second.accounts.resume_user(token))
        self.assertEqual(len(self.app.rooms.game_rooms), 1)
        self.assertEqual(self.app.rooms.room_seq, 1)
        self.assertFalse(second.rooms.game_rooms)
        self.assertEqual(second.rooms.room_seq, 0)
        self.assertFalse(second.chat.history)
        self.assertFalse(second.hub.clients)
        self.app.auth.register_ip_times["127.0.0.1"] = [1]
        self.app.estate_presence.channels["alice"] = {ws}
        self.app.betting.active_bet = {"id": 1}
        self.assertFalse(second.auth.register_ip_times)
        self.assertFalse(second.estate_presence.channels)
        self.assertIsNone(second.betting.active_bet)
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 900)
        with second.database() as conn:
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 1000)

    async def test_transfer_finance_profile_and_account_deletion_through_routes(self):
        alice, state = self.connect()
        bob, _ = self.connect("bob")
        other, _ = self.connect()
        await self.app.handlers["update_profile"](alice, state, {"nickname": "爱丽丝"})
        await self.app.handlers["transfer_coins"](alice, state, {"to": "bob", "amount": 25})
        self.assertIn({"type": "transfer_success", "coins": 975}, alice.messages)
        self.assertIn({"type": "coins", "username": "bob", "coins": 1025}, bob.messages)
        await self.app.handlers["get_finance"](alice, state, {})
        self.assertEqual(alice.messages[-1]["transactions"][0]["amount"], -25)
        self.assertIn("爱丽丝", self.app.chat.history[-1]["text"])
        token = self.app.accounts.create_session("alice")
        await self.app.handlers["delete_account"](alice, state, {"password": "password123"})
        self.assertEqual(alice.messages[-1], {"type": "account_deleted"})
        self.assertEqual(other.messages[-1], {"type": "account_deleted"})
        self.assertIsNone(self.app.accounts.resume_user(token))
        self.assertIsNone(self.app.hub.clients[other]["user"])

    async def test_reconnect_cancels_leave_timer_and_close_cancels_owned_tasks(self):
        ws, state = self.connect()
        await self.app.handlers["create_room"](ws, state, {"buy_in": 100, "blind": 1})
        room = next(iter(self.app.rooms.game_rooms.values()))
        self.app.hub.clients.pop(ws)
        await self.app.rooms.cleanup_rooms_on_disconnect(state)
        handle = self.app.rooms.leave_timers[(room.id, "alice")]
        self.app.rooms.on_user_authenticated("alice")
        self.assertTrue(handle.cancelled())
        self.assertFalse(self.app.rooms.leave_timers)
        await self.app.rooms.cleanup_rooms_on_disconnect(state)
        pending_timer = self.app.rooms.leave_timers[(room.id, "alice")]
        self.app.rooms.fire_leave_timer(room.id, "bob")
        task = next(iter(self.app.rooms.cleanup_tasks))
        self.app._watcher = asyncio.create_task(self.app.betting.bet_close_watcher())
        watcher = self.app._watcher
        await asyncio.sleep(0)
        await self.app.aclose()
        self.assertTrue(pending_timer.cancelled())
        self.assertTrue(task.cancelled())
        self.assertTrue(watcher.cancelled())
        self.assertFalse(self.app.rooms.game_rooms)
        self.assertFalse(self.app.rooms.cleanup_tasks)
        # 关闭只清理内存，留给下一次启动退款，不能重复结算。
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM game_escrows").fetchone()[0], 100)

    async def test_real_websocket_ignores_malformed_messages_and_cleans_presence(self):
        token = self.app.accounts.create_session("alice")

        async def receive(ws, kind):
            async with asyncio.timeout(3):
                while True:
                    payload = json.loads(await ws.recv())
                    if payload["type"] == kind:
                        return payload

        async with websockets.serve(self.app.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}/?client=game"
            async with websockets.connect(uri) as ws:
                self.assertEqual(await receive(ws, "history"), {"type": "history", "messages": []})
                self.assertEqual(await receive(ws, "online"), {"type": "online", "count": 0})
                for raw in ("not-json", "null", "[]", "1", '{"type":"unknown"}'):
                    await ws.send(raw)
                await ws.send(json.dumps({"type": "resume", "token": token}))
                self.assertEqual((await receive(ws, "resume_success"))["username"], "alice")
                await ws.send(json.dumps({"type": "get_estate"}))
                await receive(ws, "estate_state")
                self.assertTrue(self.app.estate_presence.channels)
                await ws.send(json.dumps({"type": "logout", "token": token}))
                await receive(ws, "logout_success")
                self.assertIsNone(self.app.accounts.resume_user(token))
        self.assertFalse(self.app.hub.clients)
        self.assertFalse(self.app.estate_presence.channels)

    async def test_run_cancellation_closes_listener_then_background_tasks(self):
        entered = asyncio.Event()
        events = []

        class Listener:
            async def __aenter__(self):
                entered.set()

            async def __aexit__(self, *args):
                events.append("listener_closed")

        async def watcher():
            try:
                await asyncio.Future()
            finally:
                events.append("watcher_closed")

        with patch("server.app.websockets.serve", return_value=Listener()) as serve, \
             patch.object(self.app.betting, "bet_close_watcher", watcher):
            task = asyncio.create_task(self.app.run("127.0.0.1", 0))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.sleep(0)
                self.assertIsNotNone(self.app._watcher)
                serve.assert_called_once_with(
                    self.app.handler, "127.0.0.1", 0, max_size=300_000, max_queue=32,
                    ping_interval=20, ping_timeout=20, close_timeout=5,
                )
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(events, ["listener_closed", "watcher_closed"])
        self.assertIsNone(self.app._watcher)


if __name__ == "__main__":
    unittest.main()
