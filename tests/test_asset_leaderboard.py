"""资产榜：临时 SQLite 数据库与真实 WebSocket，不修改用户数据。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.app import create_app
from server.schema import init_db
from server import wallet as wallet_module

import server.database as storage
import websockets

server = create_app()


class AssetLeaderboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_patch = patch.object(storage, "DB_FILE", str(Path(self.tmp.name) / "users.db"))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        init_db(server.database)
        server.hub.clients.clear()
        with server.database() as conn, conn:
            conn.executemany(
                "INSERT INTO users(username,password_hash,salt,created_at,coins,nickname) "
                "VALUES (?, '', '', 0, ?, ?)",
                [("alice", 12.34, "同名"), ("bob", 12.34, "同名"), ("carol", 0, "")])

    async def board(self, viewer="alice", **request):
        with patch.object(server.hub, "send_json") as send:
            await server.ranking.handle_get_asset_leaderboard(None, {"user": {"username": viewer}}, request)
            return send.call_args.args[1]

    async def test_balances_ties_all_accounts_and_public_fields(self):
        server.settlement.set_escrow("carol", 1, 9999)
        data = await self.board(viewer="bob", request_id="assets-1")
        self.assertEqual(data["request_id"], "assets-1")
        self.assertEqual([(e["username"], e["coins"], e["rank"]) for e in data["entries"]],
                         [("alice", 12.34, 1), ("bob", 12.34, 1), ("carol", 0, 3)])
        self.assertEqual(data["self"], data["entries"][1])
        for entry in data["entries"]:
            self.assertEqual(set(entry), {"username", "nickname", "coins", "rank"})

    async def test_pagination_cross_page_ties_and_own_rank(self):
        with server.database() as conn, conn:
            conn.executemany("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?, '', '', 0, 500)", [(f"player{i:03}",) for i in range(105)])
        first = await self.board()
        self.assertEqual((first["total"], first["limit"], len(first["entries"])), (108, 100, 100))
        self.assertEqual(first["self"]["rank"], 106)
        tail = await self.board(offset=100)
        self.assertEqual(len(tail["entries"]), 8)
        self.assertEqual(tail["entries"][0]["rank"], 1)
        self.assertEqual(tail["entries"][-1]["rank"], 108)
        self.assertEqual(len({e["username"] for e in first["entries"] + tail["entries"]}), 108)
        self.assertEqual((await self.board(offset=10**100))["offset"], 100)
        self.assertEqual((await self.board(offset=199))["offset"], 100)

    async def test_authorization_and_untrusted_request(self):
        with patch.object(server.hub, "send_json") as send:
            await server.ranking.handle_get_asset_leaderboard(None, {}, {})
            send.assert_not_called()
        data = await self.board(coins=9999999, limit=1, username="bob")
        self.assertEqual(data["self"]["username"], "alice")
        self.assertEqual(data["self"]["coins"], 12.34)
        self.assertEqual(data["limit"], 100)
        for offset in (None, "100", True, [], {}, -100, 1.5):
            self.assertEqual((await self.board(offset=offset))["offset"], 0)
        for request_id in (None, 123, [], "x" * 129):
            self.assertIsNone((await self.board(request_id=request_id))["request_id"])

    async def test_empty_database(self):
        with server.database() as conn, conn:
            conn.execute("DELETE FROM users")
        data = await self.board(offset=100)
        self.assertEqual((data["total"], data["offset"], data["self"], data["entries"]), (0, 0, None, []))

    async def test_protocol_refresh_reads_persisted_changes(self):
        async def receive(socket, kind):
            async def read():
                while True:
                    data = json.loads(await socket.recv())
                    if data["type"] == kind:
                        return data
            return await asyncio.wait_for(read(), 5)

        token = server.accounts.create_session("alice")
        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            async with websockets.connect(f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}") as ws:
                await ws.send(json.dumps({"type": "resume", "token": token}))
                await receive(ws, "resume_success")
                await ws.send(json.dumps({"type": "get_asset_leaderboard", "request_id": "first"}))
                self.assertEqual((await receive(ws, "asset_leaderboard"))["self"]["rank"], 1)
                with server.database() as conn, conn:
                    wallet_module.adjust_coins(conn, "carol", 20, "admin")
                init_db(server.database)
                await ws.send(json.dumps({"type": "get_asset_leaderboard", "request_id": "refresh"}))
                result = await receive(ws, "asset_leaderboard")
                self.assertEqual(result["request_id"], "refresh")
                self.assertEqual(result["entries"][0]["username"], "carol")
                self.assertEqual(result["entries"][0]["coins"], 20)
                self.assertEqual(result["self"]["rank"], 2)


if __name__ == "__main__":
    unittest.main()
