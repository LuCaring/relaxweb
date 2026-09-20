#!/usr/bin/env python3
"""签到/抽奖规则、真实 SQLite 并发与宿主消息回归。"""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chat_server as server
import rewards

NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc).timestamp()


class PrizeTests(unittest.TestCase):
    def test_exact_bucket_weights_and_inclusive_bounds(self):
        buckets = Counter()
        for roll in range(100):
            low, high = (20, 100) if roll < 88 else (101, 200) if roll < 98 else (201, 500)
            for offset in (0, high - low):
                with patch.object(rewards.secrets, "randbelow", side_effect=[roll, offset]):
                    self.assertEqual(rewards.pick_prize(), low + offset)
            buckets[low] += 1
        self.assertEqual(buckets, {20: 88, 101: 10, 201: 2})

    def test_beijing_midnight(self):
        before = datetime(2026, 9, 18, 15, 59, 59, tzinfo=timezone.utc).timestamp()
        self.assertEqual(rewards.checkin_day(before), "2026-09-18")
        self.assertEqual(rewards.checkin_day(before + 1), "2026-09-19")


class RewardsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, "DB_FILE", str(Path(self.tmp.name) / "test.db"))
        self.db_patch.start()
        server.init_db()
        server.clients.clear()
        with server.database() as conn, conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?, '', '', 0, 1000)", (name,))

    def tearDown(self):
        server.clients.clear()
        self.db_patch.stop()
        self.tmp.cleanup()

    def transaction(self, function, *args):
        with server.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return function(conn, *args)

    def checkin(self, name="alice", now=NOW):
        return self.transaction(rewards.claim_checkin, name, now)

    def draw(self, request_id="draw-0001", name="alice", now=NOW):
        return self.transaction(rewards.draw_lottery, name, request_id, now, server.adjust_coins)

    def status(self, name="alice", now=NOW):
        with server.database() as conn:
            return rewards.rewards_state(conn, name, now)

    def test_zero_initial_tickets_and_one_checkin_daily(self):
        self.assertEqual(self.status()["tickets"], 0)
        self.assertFalse(self.status()["checked_in"])
        self.assertEqual(self.checkin(), 5)
        self.assertEqual(self.checkin(), 0)
        self.assertEqual(self.status()["tickets"], 5)
        self.assertTrue(self.status()["checked_in"])
        self.assertEqual(self.status()["coins"], 1000)
        self.assertEqual(server.get_rating("alice")["score"], 1000)

    def test_accumulate_across_days_and_restart_without_expiry(self):
        self.checkin()
        self.checkin(now=NOW + 86400)
        self.checkin(now=NOW + 86400 * 100)
        server.init_db()
        self.assertEqual(self.status(now=NOW + 86400 * 200)["tickets"], 15)
        self.assertFalse(self.status(now=NOW + 86400 * 200)["checked_in"])

    def test_midnight_can_claim_again(self):
        midnight = datetime(2026, 9, 18, 16, tzinfo=timezone.utc).timestamp()
        self.assertEqual(self.checkin(now=midnight - 1), 5)
        before = self.status(now=midnight - 1)
        self.assertEqual(before["next_reset_at"], midnight)
        self.assertEqual(self.checkin(now=midnight), 5)
        self.assertEqual(self.status(now=midnight)["tickets"], 10)

    def test_no_tickets_no_credit(self):
        with self.assertRaisesRegex(ValueError, "机会不足"):
            self.draw()
        self.assertEqual(self.status()["coins"], 1000)
        self.assertEqual(self.status()["history"], [])

    def test_draw_credits_ledger_and_does_not_change_rating(self):
        self.checkin()
        with patch.object(rewards, "pick_prize", return_value=500):
            self.assertEqual(self.draw(), (500, False))
        self.assertEqual(self.status()["tickets"], 4)
        self.assertEqual(self.status()["coins"], 1500)
        self.assertEqual(server.get_rating("alice")["score"], 1000)
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT amount, balance, kind FROM coin_transactions").fetchall(),
                             [(500, 1500, "lottery_win")])

    def test_retry_returns_same_prize_even_after_tickets_exhausted(self):
        self.checkin()
        with patch.object(rewards, "pick_prize", return_value=20):
            for i in range(5):
                self.draw(f"draw-{i:04d}")
        server.init_db()
        with patch.object(rewards, "pick_prize", side_effect=AssertionError("must not reroll")):
            self.assertEqual(self.draw("draw-0000"), (20, True))
        self.assertEqual(self.status()["tickets"], 0)
        self.assertEqual(self.status()["coins"], 1100)
        self.assertEqual(len(self.status()["history"]), 5)

    def test_request_id_is_scoped_to_account(self):
        self.checkin("alice")
        self.checkin("bob")
        self.assertFalse(self.draw(name="alice")[1])
        self.assertFalse(self.draw(name="bob")[1])
        self.assertEqual(self.status("bob")["tickets"], 4)

    def test_invalid_request_ids_do_not_consume_chances(self):
        self.checkin()
        for request_id in (None, {}, 1234, "", "short", "a" * 81, "bad <script>"):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                self.draw(request_id)
        self.assertEqual(self.status()["tickets"], 5)

    def test_award_failure_rolls_back_ticket_draw_and_ledger(self):
        self.checkin()
        def fail_after_credit(*args, **kwargs):
            server.adjust_coins(*args, **kwargs)
            raise sqlite3.OperationalError("test write failure")
        with self.assertRaises(sqlite3.OperationalError):
            self.transaction(rewards.draw_lottery, "alice", "draw-0001", NOW, fail_after_credit)
        self.assertEqual(self.status()["tickets"], 5)
        self.assertEqual(self.status()["coins"], 1000)
        self.assertEqual(self.status()["history"], [])
        with server.database() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM coin_transactions").fetchone()[0], 0)
        self.assertFalse(self.draw()[1])

    def test_concurrent_checkins_only_grant_five(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            awards = list(pool.map(lambda _: self.checkin(), range(12)))
        self.assertEqual(sum(awards), 5)
        self.assertEqual(self.status()["tickets"], 5)

    def test_concurrent_same_draw_consumes_one(self):
        self.checkin()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.draw(), range(12)))
        self.assertEqual(sum(not replayed for _, replayed in results), 1)
        self.assertEqual(len({amount for amount, _ in results}), 1)
        self.assertEqual(self.status()["tickets"], 4)
        self.assertEqual(self.status()["coins"], 1000 + results[0][0])

    def test_concurrent_draws_cannot_overspend(self):
        self.checkin()
        def attempt(i):
            try:
                return self.draw(f"draw-{i:04d}")[0]
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            awards = [amount for amount in pool.map(attempt, range(12)) if amount is not None]
        self.assertEqual(len(awards), 5)
        self.assertEqual(self.status()["tickets"], 0)
        self.assertEqual(self.status()["coins"], 1000 + sum(awards))

    def test_recent_history_is_private_and_capped(self):
        for i in range(3):
            self.checkin(now=NOW + 86400 * i)
        for i in range(12):
            with patch.object(rewards, "pick_prize", return_value=20 + i):
                self.draw(f"draw-{i:04d}")
        self.assertEqual([e["amount"] for e in self.status()["history"]], list(range(31, 21, -1)))
        self.assertEqual(self.status("bob")["history"], [])

    def test_handlers_ignore_client_prize_day_and_target(self):
        async def run():
            with patch.object(server, "send_json", new_callable=AsyncMock) as send:
                await server.handle_draw_lottery(None, {}, {"request_id": "draw-0001"})
                self.assertEqual(send.call_args.args[1]["type"], "rewards_error")
                state = {"user": {"username": "alice"}}
                with patch.object(server.time, "time", return_value=NOW):
                    await server.handle_daily_checkin(None, state, {"day": "2030-01-01", "tickets": 999})
                    self.assertEqual(send.call_args.args[1]["awarded"], 5)
                    with patch.object(rewards, "pick_prize", return_value=20):
                        await server.handle_draw_lottery(None, state,
                            {"request_id": "draw-0001", "amount": 999999, "username": "bob"})
                self.assertEqual(send.call_args.args[1]["amount"], 20)
                self.assertEqual(self.status("alice")["coins"], 1020)
                self.assertEqual(self.status("bob")["coins"], 1000)
        asyncio.run(run())

    def test_updates_only_go_to_same_account_connections(self):
        async def run():
            self.checkin()
            server.clients.update({"tab1": {"user": {"username": "alice"}},
                                   "tab2": {"user": {"username": "alice"}},
                                   "other": {"user": {"username": "bob"}}})
            with patch.object(server, "send_json", new_callable=AsyncMock) as send:
                await server.handle_draw_lottery("tab1", server.clients["tab1"], {"request_id": "draw-0001"})
                self.assertEqual([call.args[0] for call in send.call_args_list], ["tab1", "tab1", "tab2"])
                self.assertTrue(all(call.args[1]["tickets"] == 4 for call in send.call_args_list))
        asyncio.run(run())


class MigrationTests(unittest.TestCase):
    def test_existing_account_starts_with_zero_chances(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "DB_FILE", str(Path(tmp) / "old.db")):
                with closing(sqlite3.connect(server.DB_FILE)) as conn, conn:
                    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
                                 "password_hash TEXT, salt TEXT, role TEXT, created_at INTEGER, coins REAL)")
                    conn.execute("INSERT INTO users VALUES (1, 'old', '', '', 'user', 0, 321)")
                server.init_db()
                with server.database() as conn:
                    status = rewards.rewards_state(conn, "old", NOW)
                    self.assertEqual(status["tickets"], 0)
                    self.assertEqual(status["coins"], 321)
                    self.assertFalse(status["checked_in"])
                    self.assertEqual(status["history"], [])
                    self.assertEqual(status["holdem_turnover"]["amount"], 0)
                    self.assertTrue(all(not t["claimed"] and not t["claimable"]
                                        for t in status["holdem_turnover"]["tiers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
