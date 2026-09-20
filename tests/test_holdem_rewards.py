#!/usr/bin/env python3
"""每日德扑流水：真实 SQLite、游戏引擎和 WebSocket 回归，不使用实际账号数据。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chat_server as server
from games.base import create_room
import rewards

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc).timestamp()
DAY = rewards.checkin_day(NOW)
MIDNIGHT = datetime(2026, 9, 19, 16, tzinfo=timezone.utc).timestamp()


class DatabaseFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, "DB_FILE", str(Path(self.tmp.name) / "test.db"))
        self.db_patch.start()
        server.init_db()
        server.clients.clear()
        server.game_rooms.clear()
        self.rooms = []
        with server.database() as conn, conn:
            for name in ("alice", "bob", "carol"):
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?, '', '', 0, 1000)", (name,))

    def tearDown(self):
        for room in [*self.rooms, *server.game_rooms.values()]:
            room.close()
        for timer in server.room_leave_timers.values():
            timer.cancel()
        server.room_leave_timers.clear()
        server.game_rooms.clear()
        server.clients.clear()
        self.db_patch.stop()
        self.tmp.cleanup()

    def transaction(self, fn, *args):
        with server.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return fn(conn, *args)

    def record(self, amount, hand="one", now=NOW, name="alice"):
        self.transaction(rewards.record_holdem_turnover, hand, {name: amount}, now)

    def status(self, name="alice", now=NOW):
        with server.database() as conn:
            return rewards.rewards_state(conn, name, now)

    def claim(self, threshold, name="alice", now=NOW, day=DAY, credit=server.adjust_coins):
        return self.transaction(rewards.claim_holdem_reward, name, threshold, day, now, credit)

    def room(self, game="holdem", names=("alice", "bob"), buy_in=100):
        room = create_room(game, room_id=len(self.rooms) + 1, name="每日流水测试",
                           owner=names[0], buy_in=buy_in, blind=5)
        server.attach_host(room)
        for name in names:
            room.add_member(name, buy_in)
            server.set_escrow(name, room.id, buy_in)
        self.rooms.append(room)
        server.game_rooms[room.id] = room
        return room


class RewardRulesTests(DatabaseFixture, unittest.TestCase):
    def test_exact_cent_thresholds_and_cumulative_tiers(self):
        for index, (increment, total, eligible) in enumerate([
            (99.99, 99.99, []), (0.01, 100, [100]), (100, 200, [100, 200]),
            (299.99, 499.99, [100, 200]), (0.01, 500, [100, 200, 500]),
            (500, 1000, [100, 200, 500, 1000]),
        ]):
            self.record(increment, hand=str(index))
            status = self.status()["holdem_turnover"]
            self.assertEqual(status["amount"], total)
            self.assertEqual([t["threshold"] for t in status["tiers"] if t["claimable"]], eligible)
        self.assertEqual(self.status("bob")["holdem_turnover"]["amount"], 0)

    def test_four_independent_rewards_credit_360_once_and_survive_restart(self):
        self.record(1000)
        for threshold in (1000, 100, 500, 200):
            self.assertEqual(self.claim(threshold), (dict(rewards.HOLDEM_REWARDS)[threshold], False))
        server.init_db()
        for threshold, amount in rewards.HOLDEM_REWARDS:
            self.assertEqual(self.claim(threshold), (amount, True))
        status = self.status()
        self.assertEqual(status["coins"], 1360)
        self.assertEqual(status["tickets"], 0)
        self.assertEqual(server.get_rating("alice")["score"], 1000)
        self.assertTrue(all(t["claimed"] and not t["claimable"] for t in status["holdem_turnover"]["tiers"]))
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*), SUM(amount) FROM coin_transactions "
                                          "WHERE kind='holdem_daily_reward'").fetchone(), (4, 360))

    def test_invalid_unearned_and_stale_claims_do_not_credit(self):
        self.record(99.99)
        for threshold in (None, True, 100.0, "100", {}, [], 0, -100, 101, 2000):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                self.claim(threshold)
        with self.assertRaisesRegex(ValueError, "尚未达到"):
            self.claim(100)
        self.assertEqual(self.status()["coins"], 1000)
        self.record(1000, hand="two")
        for day in (None, "2030-01-01", "2026-09-18", {}):
            with self.assertRaisesRegex(ValueError, "日期已切换"):
                self.claim(100, day=day)
        self.assertEqual(self.status()["coins"], 1000)

    def test_midnight_resets_progress_claims_and_expires_unclaimed_rewards(self):
        self.record(1000, now=MIDNIGHT - 1)
        self.claim(100, now=MIDNIGHT - 1)
        after = self.status(now=MIDNIGHT)
        self.assertEqual(after["day"], "2026-09-20")
        self.assertEqual(after["holdem_turnover"]["amount"], 0)
        self.assertTrue(all(not t["claimed"] and not t["claimable"] for t in after["holdem_turnover"]["tiers"]))
        with self.assertRaises(ValueError):
            self.claim(200, now=MIDNIGHT)
        # 跨天重放旧手牌不能把流水记入新的一天。
        self.record(1000, now=MIDNIGHT)
        self.assertEqual(self.status(now=MIDNIGHT)["holdem_turnover"]["amount"], 0)
        self.record(100, hand="next-day", now=MIDNIGHT)
        self.assertEqual(self.claim(100, now=MIDNIGHT, day="2026-09-20"), (20, False))
        self.assertEqual(self.status()["coins"], 1040)

    def test_concurrent_claims_pay_each_tier_once(self):
        self.record(1000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.claim, [100, 200, 500, 1000] * 5))
        self.assertEqual(sum(amount for amount, replayed in results if not replayed), 360)
        self.assertEqual(self.status()["coins"], 1360)

    def test_credit_failure_rolls_back_claim_balance_and_ledger(self):
        self.record(100)
        def fail(*args, **kwargs):
            server.adjust_coins(*args, **kwargs)
            raise sqlite3.OperationalError("test failure")
        with self.assertRaises(sqlite3.OperationalError):
            self.claim(100, credit=fail)
        self.assertTrue(self.status()["holdem_turnover"]["tiers"][0]["claimable"])
        self.assertEqual(self.status()["coins"], 1000)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0], 0)
        self.assertEqual(self.claim(100), (20, False))


class GameIntegrationTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_blinds_calls_raises_and_allin_are_actual_incremental_stakes(self):
        room = self.room(buy_in=200)
        with patch.object(server.time, "time", return_value=NOW):
            await room.start()
            await room.perform_action("alice", "raise", {"raise_to": 50})
            await room.perform_action("bob", "call")
            self.assertEqual(self.status()["holdem_turnover"]["amount"], 0)
            # 在下一条街加注到 150，全手累计 200，而非逐次把 raise_to 重复累加。
            await room.perform_action("bob", "raise", {"raise_to": 150})
            await room.perform_action("alice", "call")
        for name in ("alice", "bob"):
            self.assertEqual(self.status(name)["holdem_turnover"]["amount"], 200)
        await room.end_hand(True)
        self.assertEqual(self.status()["holdem_turnover"]["amount"], 200)

    async def test_folded_and_departed_player_only_count_at_final_settlement(self):
        room = self.room(names=("alice", "bob", "carol"))
        with patch.object(server.time, "time", return_value=NOW):
            await room.start()
            await room.perform_action("alice", "call")
            await room.perform_action("bob", "call")
            await room.perform_action("carol", "check")
            await server.leave_room_internal(room, "alice")
            self.assertEqual(self.status()["holdem_turnover"]["amount"], 0)
            await room.perform_action(room.game["to_act"], "fold")
        self.assertEqual(self.status()["holdem_turnover"]["amount"], 10)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdem_turnover").fetchone()[0], 3)

    async def test_buyin_restart_draw_restart_refunds_and_uno_do_not_count(self):
        room = self.room()
        with patch.object(server.time, "time", return_value=NOW):
            await room.start()
            await room.perform_action("alice", "raise", {"raise_to": 50})
            await room.restart()
            await server.dissolve_room(room, "流局")
            another = self.room()
            await another.start()
            server.refund_game_escrows()
            uno = self.room(game="uno")
            await uno.start()
            await uno.end_hand("alice")
        self.assertEqual(self.status()["holdem_turnover"]["amount"], 0)

    async def test_failed_settlement_rolls_back_turnover_rating_and_payout_then_retries(self):
        room = self.room()
        await room.start()
        stacks = {name: member["stack"] for name, member in room.members.items()}
        def fail(*args):
            rewards.record_holdem_turnover(*args)
            raise sqlite3.OperationalError("test failure")
        with patch.object(server.time, "time", return_value=NOW):
            with patch.object(server, "record_holdem_turnover", side_effect=fail):
                with self.assertRaises(sqlite3.OperationalError):
                    await room.perform_action("alice", "fold")
            self.assertEqual(self.status()["holdem_turnover"]["amount"], 0)
            self.assertEqual(server.get_rating("alice")["games"], 0)
            self.assertEqual(stacks, {name: member["stack"] for name, member in room.members.items()})
            await room.end_hand(False)
        self.assertEqual(self.status()["holdem_turnover"]["amount"], 5)
        self.assertEqual(self.status("bob")["holdem_turnover"]["amount"], 10)
        self.assertEqual(sum(m["stack"] for m in room.members.values()), 200)
        self.assertEqual(server.get_rating("alice")["games"], 1)

    async def test_hand_crossing_midnight_belongs_to_settlement_day(self):
        room = self.room()
        with patch.object(server.time, "time", return_value=MIDNIGHT - 1):
            await room.start()
            await room.perform_action("alice", "raise", {"raise_to": 100})
        with patch.object(server.time, "time", return_value=MIDNIGHT):
            await room.perform_action("bob", "call")
        self.assertEqual(self.status(now=MIDNIGHT - 1)["holdem_turnover"]["amount"], 0)
        self.assertEqual(self.status(now=MIDNIGHT)["holdem_turnover"]["amount"], 100)

    async def test_claim_handler_uses_authenticated_account_and_server_amount(self):
        self.record(100)
        with patch.object(server, "send_json") as send, patch.object(server.time, "time", return_value=NOW):
            await server.handle_claim_holdem_reward(None, {}, {"threshold": 100, "day": DAY})
            self.assertEqual(send.call_args.args[1]["type"], "rewards_error")
            await server.handle_claim_holdem_reward(None, {"user": {"username": "alice"}},
                {"threshold": 100, "day": DAY, "amount": 999999, "username": "bob", "turnover": 999999})
            self.assertEqual(send.call_args.args[1]["type"], "holdem_reward_result")
            self.assertEqual(send.call_args.args[1]["amount"], 20)
            await server.handle_claim_holdem_reward(None, {"user": {"username": "alice"}},
                {"threshold": 1000, "day": DAY, "turnover": 999999})
            self.assertEqual(send.call_args.args[1]["type"], "rewards_error")
        self.assertEqual(self.status()["coins"], 1020)
        self.assertEqual(self.status("bob")["coins"], 1000)

    async def test_deleted_account_rewards_are_removed(self):
        self.record(100)
        self.claim(100)
        with patch.object(server, "authenticate_user", return_value={"username": "alice"}), patch.object(server, "send_json"):
            await server.handle_delete_account(None, {"user": {"username": "alice"}, "last_auth_attempt": -1000}, {"password": "test"})
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdem_turnover").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdem_reward_claims").fetchone()[0], 0)
        server.clients["old-tab"] = {"user": {"username": "alice"}}
        with patch.object(server, "send_json") as send:
            await server.publish_daily_rewards("alice")
            send.assert_not_called()

    async def test_real_protocol_create_play_claim_multitab_and_reconnect(self):
        import websockets

        async def send(ws, **data):
            await ws.send(json.dumps(data))

        async def receive(ws, kind):
            async def read():
                while True:
                    data = json.loads(await ws.recv())
                    if data["type"] == kind:
                        return data
                    if data["type"].endswith("_error"):
                        raise AssertionError(data)
            return await asyncio.wait_for(read(), 5)

        tokens = {name: server.create_session(name) for name in ("alice", "bob")}
        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}/?client=game"
            async with websockets.connect(uri) as alice, websockets.connect(uri) as bob, websockets.connect(uri) as tab:
                for ws, name in ((alice, "alice"), (bob, "bob"), (tab, "alice")):
                    await send(ws, type="resume", token=tokens[name])
                    await receive(ws, "resume_success")
                # 测试环境的单调时钟可能从 0 起步，明确没有近期房间操作。
                for state in server.clients.values():
                    state["last_room_op"] = -1000
                await send(alice, type="create_room", game="holdem", buy_in=100, blind=5)
                room_id = (await receive(alice, "game_joined"))["room"]["room_id"]
                await send(bob, type="join_room", room_id=room_id)
                await receive(bob, "game_joined")
                await send(alice, type="start_game")
                while (await receive(alice, "game_update"))["status"] != "playing":
                    pass
                await send(alice, type="poker_action", action="raise", raise_to=100)
                while (await receive(bob, "game_update")).get("to_act") != "bob":
                    pass
                await send(bob, type="poker_action", action="call")
                for ws in (alice, bob, tab):
                    status = await receive(ws, "daily_rewards")
                    self.assertEqual(status["holdem_turnover"]["amount"], 100)
                day = status["day"]
                await asyncio.gather(*(send(ws, type="claim_holdem_reward", threshold=100, day=day)
                                       for ws in (alice, tab)))
                results = await asyncio.gather(*(receive(ws, "holdem_reward_result") for ws in (alice, tab)))
                self.assertEqual(sorted(r["replayed"] for r in results), [False, True])
                self.assertTrue(all(r["coins"] == 920 for r in results))
                await send(alice, type="get_finance")
                ledger = (await receive(alice, "finance"))["transactions"]
                self.assertEqual(sum(t["amount"] for t in ledger if t["kind"] == "holdem_daily_reward"), 20)
                await server.dissolve_room(server.game_rooms[room_id], "测试结束")
            async with websockets.connect(uri) as again:
                await send(again, type="resume", token=tokens["alice"])
                await receive(again, "resume_success")
                await send(again, type="get_daily_rewards")
                self.assertTrue((await receive(again, "daily_rewards"))["holdem_turnover"]["tiers"][0]["claimed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
