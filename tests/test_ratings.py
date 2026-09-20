#!/usr/bin/env python3
"""真实 SQLite + 游戏引擎/宿主回归：python3 tests/test_ratings.py。"""
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chat_server as server
from games.base import create_room
from games.rating import rating_change, rating_info


class FormulaTests(unittest.TestCase):
    def test_returns_caps_and_rounding(self):
        for initial, final, expected in [(100, 100, 0), (100, 120, 8),
                (100, 80, -4), (100, 60, -8), (100, 200, 40), (100, 900, 40),
                (100, 0, -20), (100, 101.25, 1), (100, 97.5, -1),
                (100, 100.01, 0), (2000, 2400, 8), (0.01, 0.02, 40)]:
            with self.subTest(initial=initial, final=final):
                self.assertEqual(rating_change(initial, final), expected)

    def test_invalid_money(self):
        for initial, final in [(0, 1), (-1, 1), (1, -1), (float("nan"), 1),
                               (1, float("inf"))]:
            with self.assertRaises(ValueError):
                rating_change(initial, final)

    def test_monotone_and_win_loss_factor_two_to_one(self):
        changes = [rating_change(100, final) for final in range(1000)]
        self.assertEqual(changes, sorted(changes))
        for percent in range(5, 101, 5):
            self.assertEqual(rating_change(100, 100 + percent),
                             -2 * rating_change(100, 100 - percent))

    def test_tier_boundaries(self):
        for floor, tier in [(0, "青铜"), (800, "白银"), (1200, "黄金"),
                            (1600, "铂金"), (2000, "钻石"), (2400, "大师")]:
            self.assertEqual(rating_info(floor)["tier"], tier)
            if floor:
                self.assertNotEqual(rating_info(floor - 1)["tier"], tier)
        self.assertIsNone(rating_info(3000)["next_score"])


class RatingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, "DB_FILE", str(Path(self.tmp.name) / "test.db"))
        self.db_patch.start()
        server.init_db()
        self.rooms = []
        server.clients.clear()
        server.game_rooms.clear()
        with server.database() as conn, conn:
            for name in ("alice", "bob", "carol"):
                conn.execute("INSERT INTO users (username, password_hash, salt, created_at, coins) "
                             "VALUES (?, '', '', 0, 900)", (name,))

    def tearDown(self):
        for room in self.rooms:
            room.close()
        server.clients.clear()
        server.game_rooms.clear()
        self.db_patch.stop()
        self.tmp.cleanup()

    def room(self, game="holdem", names=("alice", "bob")):
        room = create_room(game, room_id=len(self.rooms) + 1, name="段位测试",
                           owner=names[0], buy_in=100, blind=5)
        server.attach_host(room)
        for name in names:
            room.add_member(name, 100)
            server.set_escrow(name, room.id, 100)
        self.rooms.append(room)
        server.game_rooms[room.id] = room
        return room

    def count(self):
        with server.database() as conn:
            return conn.execute("SELECT count(*) FROM rating_history").fetchone()[0]

    async def test_defaults_auth_profile_and_no_coin_effect(self):
        password_hash, salt = server.hash_password("secret123")
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET password_hash = ?, salt = ?", (password_hash, salt))
            server.adjust_coins(conn, "alice", 500, "admin")
        user = server.authenticate_user("alice", "secret123")
        self.assertEqual(user["rating"], rating_info())
        self.assertEqual(server.resume_user(server.create_session("alice"))["rating"], rating_info())
        self.assertEqual(server.get_profile("alice")["rating"], rating_info())
        self.assertEqual(self.count(), 0)
        with patch.object(server, "send_json") as send:
            await server.handle_login(None, {"last_auth_attempt": -1000},
                                      {"username": "alice", "password": "secret123"})
            self.assertEqual(send.call_args.args[1]["rating"], rating_info())
            await server.handle_resume(None, {}, {"token": server.create_session("alice")})
            self.assertEqual(send.call_args.args[1]["rating"], rating_info())

    async def test_new_account_also_starts_at_1000(self):
        with server.database() as conn, conn:
            conn.execute("INSERT INTO invite_codes(code, created_at) VALUES ('new-user', 0)")
        self.assertTrue(server.register_user("newplayer", "secret123", "new-user")[0])
        self.assertEqual(server.get_rating("newplayer"), rating_info())
        self.assertEqual(self.count(), 0)

    async def test_failed_rating_write_can_retry_without_double_payout(self):
        for game in ("holdem", "uno"):
            room = self.room(game)
            await room.start()
            if game == "holdem":
                room.game["folded"].add("bob")
            before = {name: m["stack"] for name, m in room.members.items()}
            with patch.object(room, "record_ratings", side_effect=sqlite3.OperationalError("test")):
                with self.assertRaises(sqlite3.OperationalError):
                    await room.end_hand(False if game == "holdem" else "alice")
            self.assertEqual(before, {name: m["stack"] for name, m in room.members.items()})
            await room.end_hand(False if game == "holdem" else "alice")
            self.assertEqual(sum(m["stack"] for m in room.members.values()), 200)
            room.close()
        self.assertEqual(self.count(), 4)

    async def test_holdem_settlement_has_no_fallible_post_commit_escrow_write(self):
        room = self.room()
        await room.start()
        room.game["folded"].add("bob")
        with patch.object(room, "set_escrow", side_effect=sqlite3.OperationalError("escrow sync")) as sync:
            await room.end_hand(False)
            sync.assert_not_called()  # 最终托管只由积分/统计事务原子写入
        settled = {name: member["stack"] for name, member in room.members.items()}
        self.assertEqual(sum(settled.values()), 200)
        self.assertFalse(room.in_hand())
        await room.end_hand(False)
        self.assertEqual({name: member["stack"] for name, member in room.members.items()}, settled)
        self.assertEqual(self.count(), 2)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT SUM(hands) FROM holdem_player_stats").fetchone()[0], 2)
            self.assertEqual(dict(conn.execute("SELECT username, amount FROM game_escrows")), settled)
        await room.restart()
        self.assertEqual(room.rating_starts, settled)  # 已结算的盲注不能再退款

    async def test_mahjong_and_guandan_feed_shared_rating_pool(self):
        with server.database() as conn, conn:
            conn.execute("INSERT INTO users (username, password_hash, salt, created_at, coins) "
                         "VALUES ('dave', '', '', 0, 900)")
        names = ("alice", "bob", "carol", "dave")
        for game in ("mahjong", "guandan"):
            room = self.room(game, names)
            room.begin_rating_hand(names)
            self.assertEqual(set(room.rating_starts), set(names))
            for index, name in enumerate(names):
                room.members[name]["stack"] = 100 + (10 if index % 2 == 0 else -10)
            endings = {name: room.members[name]["stack"] for name in names}
            results = room.settle_ratings(endings)
            self.assertEqual(set(results), set(names))
            self.assertEqual(results["alice"]["delta"], 4)   # +10% → +4
            self.assertEqual(results["bob"]["delta"], -2)    # −10% → −2
            room.settle_ratings(endings)  # 同一手重复提交不重复计分
            room.close()
        self.assertEqual(self.count(), 8)
        with server.database() as conn:
            types = {row[0] for row in conn.execute(
                "SELECT DISTINCT game_type FROM rating_history")}
            scores = dict(conn.execute("SELECT username, rating_score FROM users"))
        self.assertLessEqual({"mahjong", "guandan"}, types)
        self.assertEqual(scores["alice"], 1008)  # 两局各 +4
        self.assertEqual(scores["bob"], 996)     # 两局各 −2
        self.assertEqual(scores["dave"], 996)

    async def test_holdem_before_blinds_and_idempotence(self):
        room = self.room()
        await room.start()
        self.assertEqual(room.rating_starts, {"alice": 100, "bob": 100})
        await room.perform_action("alice", "fold")
        entries = room.game["result"]["ratings"]
        self.assertEqual(entries["alice"]["final"], 95)
        self.assertEqual(entries["alice"]["delta"], -1)
        self.assertEqual(entries["bob"]["delta"], 2)
        self.assertEqual(room.view_for("alice")["players"][0]["rating"]["score"], 999)
        before = {name: m["stack"] for name, m in room.members.items()}
        await room.end_hand(False)
        replay = server.record_hand_ratings(room, room.rating_hand_id, room.rating_starts, before)
        self.assertEqual(replay, entries)
        self.assertEqual(before, {name: m["stack"] for name, m in room.members.items()})
        self.assertEqual(self.count(), 2)
        await server.dissolve_room(room, "结算解散")
        self.assertEqual(self.count(), 2)

    async def test_next_hand_uses_new_stacks(self):
        room = self.room()
        await room.start()
        first_id = room.rating_hand_id
        await room.perform_action("alice", "fold")
        await room.start_hand()
        self.assertNotEqual(first_id, room.rating_hand_id)
        self.assertEqual(room.rating_starts, {"alice": 95, "bob": 105})
        await room.perform_action(room.game["to_act"], "fold")
        self.assertEqual(self.count(), 4)
        self.assertEqual(server.get_rating("alice")["games"], 2)

    async def test_uno_result_and_replay(self):
        room = self.room("uno")
        await room.start()
        room.game["hands"]["bob"] = [{"c": "r", "v": "1"}] * 4
        await room.end_hand("alice")
        entries = room.game["result"]["ratings"]
        self.assertEqual(entries["alice"]["delta"], 8)
        self.assertEqual(entries["bob"]["delta"], -4)
        self.assertEqual(server.get_rating("alice")["score"], 1008)
        await room.end_hand("alice")
        self.assertEqual(room.members["alice"]["stack"], 120)
        self.assertEqual(self.count(), 2)

    async def test_departure_is_recorded_once_in_final_result(self):
        room = self.room(names=("alice", "bob", "carol"))
        await room.start()
        await server.leave_room_internal(room, "bob")
        self.assertEqual(server.get_rating("bob")["score"], 999)
        self.assertEqual(self.count(), 1)
        await room.perform_action(room.game["to_act"], "fold")
        self.assertEqual(set(room.game["result"]["ratings"]), {"alice", "bob", "carol"})
        self.assertEqual(self.count(), 3)
        self.assertEqual(server.get_rating("bob")["games"], 1)
        with server.database() as conn:
            self.assertIsNone(conn.execute("SELECT * FROM game_escrows WHERE username='bob'").fetchone())

    async def test_holdem_same_hand_rejoin_preserves_new_buyin(self):
        room = self.room(names=("alice", "bob", "carol"))
        await room.start()
        await server.leave_room_internal(room, "bob")
        self.assertEqual(room.rating_results["bob"]["final"], 95)
        with patch.object(server, "send_json"):
            await server.handle_join_room(None, {"user": {"username": "bob"}, "last_room_op": -1000}, {"room_id": room.id})
        self.assertEqual(room.members["bob"]["stack"], 100)
        await room.perform_action(room.game["to_act"], "fold")
        self.assertEqual(room.members["bob"]["stack"], 100)
        self.assertEqual(server.get_rating("bob")["games"], 1)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM game_escrows WHERE username='bob'").fetchone()[0], 100)
            self.assertEqual(conn.execute("SELECT hands FROM holdem_player_stats WHERE user_id = "
                                          "(SELECT id FROM users WHERE username='bob')").fetchone()[0], 1)

    async def test_uno_departure_keeps_existing_refund_rules(self):
        room = self.room("uno")
        await room.start()
        await server.leave_room_internal(room, "bob")
        self.assertEqual(room.game["result"]["ratings"]["bob"]["delta"], 0)
        self.assertEqual(server.get_rating("bob")["score"], 1000)
        self.assertEqual(self.count(), 2)

    async def test_waiting_cancel_and_restart_never_score(self):
        waiting = self.room()
        await server.dissolve_room(waiting, "等待中解散")
        room = self.room()
        await room.start()
        old_id = room.rating_hand_id
        await room.restart()
        self.assertNotEqual(room.rating_hand_id, old_id)
        self.assertEqual(room.rating_starts, {"alice": 100, "bob": 100})
        await server.dissolve_room(room, "流局")
        self.assertEqual(self.count(), 0)

    async def test_restart_refunds_do_not_rate_again(self):
        room = self.room("uno")
        await room.start()
        await room.end_hand("alice")
        score = server.get_rating("alice")
        server.refund_game_escrows()
        server.refund_game_escrows()
        self.assertEqual(server.get_rating("alice"), score)
        self.assertEqual(self.count(), 2)

    async def test_zero_floor_reports_actual_delta(self):
        room = self.room()
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET rating_score = 7 WHERE username='bob'")
        entries = server.record_hand_ratings(room, "floor", {"bob": 100}, {"bob": 0})
        self.assertEqual(entries["bob"]["delta"], -7)
        self.assertEqual(server.get_rating("bob")["score"], 0)

    async def test_transaction_rolls_back_all_players_and_escrows(self):
        room = self.room()
        with self.assertRaises(ValueError):
            server.record_hand_ratings(room, "bad", {"alice": 100, "bob": 0},
                                       {"alice": 120, "bob": 80})
        self.assertEqual(self.count(), 0)
        self.assertEqual(server.get_rating("alice")["score"], 1000)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM game_escrows WHERE username='alice'").fetchone()[0], 100)

    async def test_history_is_private_newest_first_and_capped(self):
        room = self.room()
        for i in range(22):
            server.record_hand_ratings(room, f"history-{i}", {"alice": 100, "bob": 100},
                                       {"alice": 120, "bob": 80})
        with patch.object(server, "send_json") as send:
            await server.handle_get_rating_history(None, {"user": {"username": "bob"}}, {"username": "alice"})
            data = send.call_args.args[1]
            self.assertEqual(len(data["entries"]), 20)
            self.assertEqual(data["entries"][0]["rating"]["games"], 22)
            self.assertTrue(all(e["delta"] == -4 for e in data["entries"]))
            send.reset_mock()
            await server.handle_get_rating_history(None, {}, {})
            send.assert_not_called()

    async def leaderboard(self, username="alice", **request):
        with patch.object(server, "send_json") as send:
            await server.handle_get_rating_leaderboard(None, {"user": {"username": username}}, request)
            return send.call_args.args[1]

    async def test_leaderboard_ties_public_fields_and_tier_ranges(self):
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET rating_score=2400, nickname='同名玩家' WHERE username!='carol'")
        data = await self.leaderboard(username="bob")
        self.assertEqual([(e["username"], e["rank"]) for e in data["entries"]],
                         [("alice", 1), ("bob", 1), ("carol", 3)])
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["self"], data["entries"][1])
        for entry in data["entries"]:
            self.assertEqual(set(entry), {"username", "nickname", "rating", "rank", "holdem_stats"})
            self.assertEqual(entry["holdem_stats"]["hands"], 0)
            self.assertIsNone(entry["holdem_stats"]["win_rate"])
        self.assertEqual([t["floor"] for t in data["tiers"]], [0, 800, 1200, 1600, 2000, 2400])
        self.assertEqual([t["next_score"] for t in data["tiers"]], [800, 1200, 1600, 2000, 2400, None])

    async def test_leaderboard_caps_players_and_keeps_own_rank_outside_top_100(self):
        with server.database() as conn, conn:
            conn.executemany("INSERT INTO users(username,password_hash,salt,created_at,rating_score) "
                             "VALUES (?, '', '', 0, ?)",
                             [(f"player{i:03d}", 2000) for i in range(105)])
        data = await self.leaderboard()
        self.assertEqual(data["limit"], 100)
        self.assertEqual(data["total"], 108)
        self.assertEqual(len(data["entries"]), 100)
        self.assertEqual(data["entries"][-1]["username"], "player099")
        self.assertTrue(all(e["rank"] == 1 for e in data["entries"]))
        self.assertEqual(data["self"]["rank"], 106)
        self.assertEqual(data["self"]["rating"], rating_info())
        # 全部初始分也只取 100 位；本人的并列名次不因榜单截断改变。
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET rating_score=1000")
        data = await self.leaderboard(username="player104")
        self.assertEqual(len(data["entries"]), 100)
        self.assertEqual(data["self"]["rank"], 1)

    async def test_leaderboard_requires_login_and_ignores_client_score_or_target(self):
        with patch.object(server, "send_json") as send:
            await server.handle_get_rating_leaderboard(None, {}, {})
            send.assert_not_called()
        with patch.object(server, "send_json") as send:
            await server.handle_get_rating_leaderboard(None, {"user": {"username": "alice"}},
                {"username": "bob", "rating_score": 99999, "limit": 99999})
            data = send.call_args.args[1]
        self.assertEqual(data["self"]["username"], "alice")
        self.assertEqual(data["self"]["rating"]["score"], 1000)
        self.assertEqual(data["limit"], 100)

    async def test_leaderboard_protocol_reads_new_settlement_and_persisted_scores(self):
        import websockets

        async def receive(ws, kind):
            async def read():
                while True:
                    message = json.loads(await ws.recv())
                    if message["type"] == kind:
                        return message
            return await asyncio.wait_for(read(), 5)

        token = server.create_session("alice")
        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"type": "resume", "token": token}))
                await receive(ws, "resume_success")
                await ws.send(json.dumps({"type": "get_rating_leaderboard"}))
                data = await receive(ws, "rating_leaderboard")
                self.assertEqual(data["self"]["rating"], rating_info())
                room = self.room("uno")
                await room.start()
                await room.end_hand("bob")
                await receive(ws, "rating_update")
                server.init_db()
                await ws.send(json.dumps({"type": "get_rating_leaderboard"}))
                data = await receive(ws, "rating_leaderboard")
                self.assertEqual(data["entries"][0]["username"], "bob")
                self.assertEqual(data["self"]["rating"], server.get_rating("alice"))
                self.assertEqual(data["self"]["rank"], 3)
                await server.dissolve_room(room, "测试结束")

    async def test_rating_update_reaches_all_connections(self):
        class Socket:
            def __init__(self):
                self.messages = []
            async def send(self, message):
                self.messages.append(json.loads(message))
        room = self.room("uno")
        sockets = [Socket(), Socket()]
        for socket in sockets:
            server.clients[socket] = {"user": {"username": "alice"}, "send_lock": asyncio.Lock()}
        await room.start()
        await room.end_hand("alice")
        for socket in sockets:
            updates = [m for m in socket.messages if m["type"] == "rating_update" and m["username"] == "alice"]
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0]["rating"], server.get_rating("alice"))


class MigrationTests(unittest.TestCase):
    def test_existing_accounts_receive_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "DB_FILE", str(Path(tmp) / "old.db")):
                with closing(sqlite3.connect(server.DB_FILE)) as conn, conn:
                    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
                                 "password_hash TEXT, salt TEXT, role TEXT, created_at INTEGER)")
                    conn.execute("INSERT INTO users VALUES (1, 'old', '', '', 'user', 0)")
                server.init_db()
                self.assertEqual(server.get_rating("old"), rating_info())
                with server.database() as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM rating_history").fetchone()[0], 0)
                with server.database() as conn, conn:
                    conn.execute("UPDATE users SET rating_score=1234, rating_games=3")
                server.init_db()
                self.assertEqual(server.get_rating("old"), rating_info(1234, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
