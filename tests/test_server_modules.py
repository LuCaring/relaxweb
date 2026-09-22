"""公共存储与独立协议模块回归：python3 tests/test_server_modules.py。"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from functools import partial
from unittest.mock import patch

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import accounts
import server.database as storage
from server.betting import Betting
from server.estate.presence import EstatePresence
from server.estate.protocol import EstateProtocol
from server.routing import merge_handlers
from server.schema import init_db
from server.wallet import adjust_coins, user_balance


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = str(Path(tmp) / "users.db")
        self.database = partial(storage.database, self.path)
        init_db(self.database)

    def test_wallet_changes_and_ledger_rollback_with_callers_transaction(self):
        with self.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                         "VALUES ('alice','','',0,100)")
        with self.assertRaises(ValueError):
            with self.database() as conn, conn:
                adjust_coins(conn, "alice", 20, "test")
                adjust_coins(conn, "alice", -200, "test")
        with self.database() as conn:
            self.assertEqual(user_balance(conn, "alice"), 100)
            self.assertEqual(conn.execute("SELECT count(*) FROM coin_transactions").fetchone()[0], 0)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_accounts_share_database_and_session_contract(self):
        with self.database() as conn, conn:
            conn.execute("INSERT INTO invite_codes(code,created_at) VALUES ('invite',0)")
        account_store = accounts.Accounts(self.database)
        with patch.object(storage, "database", side_effect=AssertionError("必须使用注入的库")):
            self.assertEqual(account_store.register_user("alice", "password123", "invite"),
                             (True, "注册成功"))
            self.assertIsNone(account_store.authenticate_user("alice", "wrong-password"))
            user = account_store.authenticate_user("alice", "password123")
            self.assertEqual(user["coins"], accounts.NEW_USER_COINS)
            token = account_store.create_session("alice")
            self.assertEqual(account_store.resume_user(token), user)
            self.assertEqual(account_store.get_profile("alice")["username"], "alice")
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT used_by FROM invite_codes").fetchone()[0], "alice")
            self.assertEqual(conn.execute("SELECT kind FROM coin_transactions").fetchone()[0], "register")

    def test_management_commands_use_same_explicit_database(self):
        env = {**os.environ, "LIVE_DB_FILE": self.path}
        invite = subprocess.run([sys.executable, "manage_invite.py", "gen", "1"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(invite.returncode, 0, invite.stderr)
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT code FROM invite_codes").fetchone()[0],
                             invite.stdout.strip())
        listing = subprocess.run([sys.executable, "admin.py", "list"],
                                 cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(listing.returncode, 0, listing.stderr)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def host(self, name):
        database = partial(storage.database, str(Path(self.tmp) / (name + ".db")))
        init_db(database)
        with database() as conn, conn:
            conn.executemany("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?,'','',0,10000)", [("alice",), ("bob",)])
        messages = []

        async def send(socket, payload):
            messages.append((socket, payload))

        async def encoded(socket, payload):
            await send(socket, json.loads(payload))

        async def broadcast(payload):
            messages.append((None, payload))

        async def system(text):
            pass

        return database, messages, send, encoded, broadcast, system

    async def test_betting_instances_isolate_state_and_restore_from_own_database(self):
        def make(name):
            database, messages, send, _, broadcast, system = self.host(name)
            deps = dict(database=database, send_json=send, broadcast=broadcast,
                        broadcast_system=system, display_name=str,
                        rate_limited=lambda *args: False)
            return Betting(**deps), deps, messages

        first, deps, messages = make("first")
        second, _, second_messages = make("second")
        alice = {"user": {"username": "alice"}}
        await first.handlers()["create_bet"]("alice", alice,
            {"question": "比赛结果", "options": ["主胜", "客胜"], "close_minutes": 1})
        await second.handlers()["get_bet"]("bob", {}, {})
        self.assertEqual(second_messages[-1][1], {"type": "bet_state", "bet": None})
        await first.handlers()["place_bet"]("bob", {"user": {"username": "bob"}},
                                             {"option_index": 1, "amount": 20})
        await first.handlers()["close_bet"]("alice", alice, {})
        restored = Betting(**deps)
        restored.active_bet = restored.load_open_bet()
        await restored.handlers()["get_bet"]("bob", {}, {})
        public = messages[-1][1]["bet"]
        self.assertEqual(public["question"], "比赛结果")
        self.assertEqual(public["totals"], [0, 20])
        self.assertIsNotNone(public["closed_at"])
        await restored.handlers()["place_bet"]("alice", alice,
                                                {"option_index": 0, "amount": 20})
        self.assertEqual(messages[-1][1]["message"], "竞猜已封盘，无法参与")

    async def test_estate_instances_isolate_channels_and_preserve_same_account_sync(self):
        def make(name):
            database, messages, send, encoded, _, _ = self.host(name)
            clients = {"a": {"user": {"username": "alice"}},
                       "b": {"user": {"username": "alice"}},
                       "other": {"user": {"username": "bob"}}}
            presence = EstatePresence(clients=clients, send_encoded=encoded,
                                      rate_limited=lambda *args: False)
            protocol = EstateProtocol(database=database, clients=clients,
                                      send_json=send, send_encoded=encoded, presence=presence)
            return protocol, presence, clients, messages

        first, presence, clients, messages = make("first")
        second, _, other_clients, other_messages = make("second")
        for socket in ("a", "b"):
            await first.handlers()["get_estate"](socket, clients[socket], {})
        await second.handlers()["get_estate"]("a", other_clients["a"], {})
        self.assertFalse(any(p["type"] == "estate_visit_joined" for _, p in other_messages))
        messages.clear()
        other_messages.clear()
        await first.handlers()["estate_buy"]("a", clients["a"],
            {"request_id": "isolated-buy-001", "kind": "seed", "item_id": "wheat", "quantity": 1})
        self.assertEqual({socket for socket, p in messages if p["type"] == "estate_state"}, {"a", "b"})
        self.assertEqual(clients["a"]["user"]["coins"], 9980)
        self.assertEqual(clients["b"]["user"]["coins"], 9980)
        self.assertEqual(other_clients["a"]["user"]["coins"], 10000)
        self.assertEqual(other_messages, [])
        messages.clear()
        await presence.leave("a", clients["a"])
        self.assertEqual(messages, [("b", {"type": "estate_visit_left", "username": "alice"})])
        self.assertNotIn("estate_owner", clients["a"])


class RoutingTests(unittest.TestCase):
    def test_duplicate_message_registration_fails_instead_of_overwriting(self):
        async def handle(*args):
            pass

        with self.assertRaisesRegex(ValueError, "get_estate"):
            merge_handlers({"get_estate": handle}, {"get_estate": handle})
        self.assertEqual(set(merge_handlers({"get_estate": handle}, {"get_bet": handle})),
                         {"get_estate", "get_bet"})


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_entrypoint_refunds_escrow_and_resumes_bet_watcher(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        path = str(Path(tmp) / "startup.db")
        database = partial(storage.database, path)
        init_db(database)
        with database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                         "VALUES ('alice','','',0,50)")
            conn.execute("INSERT INTO game_escrows VALUES ('alice',1,100)")
            conn.execute("INSERT INTO bets(question,options,creator,status,created_at,close_delay) "
                         "VALUES ('恢复竞猜','[\"赢\",\"输\"]','alice','open',?,1)",
                         (int(time.time()) - 120,))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, "chat_server.py"], cwd=ROOT,
            env={**os.environ, "LIVE_DB_FILE": path, "LIVE_CHAT_HOST": "127.0.0.1",
                 "LIVE_CHAT_PORT": str(port)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)

        async def receive(ws, kind):
            async def read():
                while True:
                    data = json.loads(await ws.recv())
                    if data["type"] == kind:
                        return data
            return await asyncio.wait_for(read(), 5)

        try:
            ws = None
            for _ in range(100):
                if process.poll() is not None:
                    self.fail(process.communicate()[1])
                try:
                    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
                    break
                except OSError:
                    await asyncio.sleep(.05)
            self.assertIsNotNone(ws, "宿主未在启动期限内监听")
            try:
                await ws.send(json.dumps({"type": "get_bet"}))
                current = (await receive(ws, "bet_state"))["bet"]
                self.assertEqual(current["question"], "恢复竞猜")
                if not current["closed_at"]:
                    current = (await receive(ws, "bet_update"))["bet"]
                self.assertIsNotNone(current["closed_at"])
                await ws.send(json.dumps({"type": "get_estate"}))
                self.assertEqual((await receive(ws, "estate_error"))["code"], "auth_required")
                with database() as conn:
                    self.assertEqual(user_balance(conn, "alice"), 150)
                    self.assertEqual(conn.execute("SELECT count(*) FROM game_escrows").fetchone()[0], 0)
            finally:
                await ws.close()
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
