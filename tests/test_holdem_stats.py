#!/usr/bin/env python3
"""德扑统计专项回归：.venv/bin/python tests/test_holdem_stats.py。

只使用临时 SQLite、确定性完整牌堆和随机监听端口；不读取真实用户库。
引擎用例走真实动作/结算，协议用例走真实 WebSocket handler。
"""
import asyncio
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.app import create_app
from server.schema import init_db
from server import accounts as account_helpers
from server.rooms import settlement as settlement_module

import server.database as storage
import games.holdem as holdem
import holdem_stats as stats_module
from games.base import BaseRoom, create_room
from games.rating import rating_info

server = create_app()


NAMES = ("alice", "bob", "carol", "dave")
RATIOS = ("win_rate", "fold_rate", "vpip", "pfr", "flop_rate", "score_per_hand",
          "profit_per_hand", "bb_per_100", "wtsd", "showdown_win_rate", "af")
PUBLIC_FIELDS = {
    "hands", "wins", "folds", "manual_folds", "timeout_folds", "leave_folds",
    "vpip_hands", "pfr_hands", "flop_hands", "showdown_hands", "showdown_wins",
    "aggressive_actions", "call_actions", "net_profit", "score_delta", "net_bb",
    "small_sample", "af_no_calls", *RATIOS,
}


def fixed_deck(draws=()):
    """pop() 按 draws 顺序发牌，剩余牌仍是合法、无重复的完整牌堆。"""
    full = [(rank, suit) for rank in range(2, 15) for suit in range(4)]
    if len(set(draws)) != len(draws) or not set(draws) <= set(full):
        raise AssertionError("测试牌堆有重复或非法牌")
    return [card for card in full if card not in draws] + list(reversed(draws))


def snapshot(**overrides):
    """仅供纯存储/公式用例；引擎集成用例从 hand_statistics 获取摘要。"""
    value = dict(big_blind=10, folded=False, fold_reason=None, saw_flop=False,
                 showdown=False, vpip=False, pfr=False, aggressive_actions=0,
                 call_actions=0, settlement_reason="completed")
    value.update(overrides)
    return value


class FormulaAndStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        stats_module.init_holdem_stats(self.conn)
        self.conn.commit()

    def totals(self, user_id=1):
        row = self.conn.execute(
            f"SELECT {', '.join(stats_module.TOTALS)} FROM holdem_player_stats WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return stats_module.public_holdem_stats(row)

    def test_empty_denominators_are_null_not_zero(self):
        value = stats_module.public_holdem_stats()
        self.assertEqual(set(value), PUBLIC_FIELDS)
        for key in RATIOS:
            with self.subTest(key=key):
                self.assertIsNone(value[key])
        self.assertTrue(value["small_sample"])
        self.assertFalse(value["af_no_calls"])
        for key in stats_module.COUNTERS:
            self.assertEqual(value[key], 0)

    def test_profit_not_payout_determines_wins_and_showdown_wins(self):
        for index, final in enumerate((100, 40, 160)):
            with self.conn:
                stats_module.record_holdem_hand(self.conn, str(index), 1, 100, final, 0,
                                               snapshot(saw_flop=True, showdown=True))
        value = self.totals()
        self.assertEqual(value["hands"], 3)
        self.assertEqual(value["wins"], 1)
        self.assertEqual(value["showdown_wins"], 1)
        self.assertEqual(value["win_rate"], round(1 / 3, 8))
        self.assertEqual(value["showdown_win_rate"], round(1 / 3, 8))
        self.assertEqual(value["net_profit"], 0)
        self.assertEqual(value["wtsd"], 1)

    def test_aggregate_ratios_use_raw_numerators_and_correct_denominators(self):
        for index in range(3):
            value = snapshot(vpip=index != 0, pfr=index == 2, saw_flop=index != 0,
                             showdown=index == 2, aggressive_actions=index,
                             call_actions=1 if index == 2 else 0)
            with self.conn:
                stats_module.record_holdem_hand(self.conn, str(index), 1, 100,
                                               (90, 100, 130)[index], (-2, 0, 12)[index], value)
        result = self.totals()
        self.assertEqual(result["vpip"], round(2 / 3, 8))
        self.assertEqual(result["pfr"], round(1 / 3, 8))
        self.assertEqual(result["flop_rate"], round(2 / 3, 8))
        self.assertEqual(result["wtsd"], 0.5)
        self.assertEqual(result["showdown_win_rate"], 1)
        self.assertEqual(result["af"], 3)
        self.assertFalse(result["af_no_calls"])
        self.assertEqual(result["score_per_hand"], round(10 / 3, 8))
        self.assertEqual(result["profit_per_hand"], round(20 / 3, 8))
        self.assertEqual(result["bb_per_100"], round(200 / 3, 8))

    def test_zero_calls_and_zero_aggression_have_distinct_af_flags(self):
        with self.conn:
            stats_module.record_holdem_hand(self.conn, "aggressive", 1, 100, 110, 4,
                                           snapshot(aggressive_actions=2, saw_flop=True))
            stats_module.record_holdem_hand(self.conn, "passive", 2, 100, 90, -2, snapshot())
            stats_module.record_holdem_hand(self.conn, "caller", 3, 100, 100, 0,
                                           snapshot(call_actions=2, saw_flop=True))
        self.assertIsNone(self.totals(1)["af"])
        self.assertTrue(self.totals(1)["af_no_calls"])
        self.assertIsNone(self.totals(2)["af"])
        self.assertFalse(self.totals(2)["af_no_calls"])
        self.assertEqual(self.totals(3)["af"], 0)

    def test_integer_cents_and_half_up_rounding(self):
        for amount, expected in [(0.1 + 0.2, 30), ("1.005", 101), ("-1.005", -101),
                                 ("0.004", 0), ("0.005", 1)]:
            with self.subTest(amount=amount):
                self.assertEqual(stats_module.cents(amount), expected)
        with self.conn:
            stats_module.record_holdem_hand(self.conn, "penny", 1, "0.03", "0.04", 0,
                                           snapshot(big_blind="0.02"))
        self.assertEqual(self.totals()["net_profit"], 0.01)
        self.assertEqual(self.totals()["net_bb"], 0.5)
        for bad in (float("inf"), float("nan"), "-Infinity"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stats_module.cents(bad)

    def test_invalid_snapshot_does_not_leave_detail_or_aggregate(self):
        bad_values = [dict(big_blind=0), dict(big_blind=-1),
                      dict(folded=True), dict(folded=True, fold_reason="unknown"),
                      dict(fold_reason="manual"), dict(showdown=True),
                      dict(showdown=True, saw_flop=True, folded=True, fold_reason="manual"),
                      dict(pfr=True), dict(aggressive_actions=-1), dict(call_actions=0.5),
                      dict(call_actions=True), dict(settlement_reason="restart")]
        for overrides in bad_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValueError, sqlite3.IntegrityError)):
                    with self.conn:
                        stats_module.record_holdem_hand(self.conn, "bad", 1, 100, 100, 0,
                                                       snapshot(**overrides))
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM holdem_hand_stats").fetchone()[0], 0)
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM holdem_player_stats").fetchone()[0], 0)
        for initial, final in ((0, 100), (-1, 100), (100, -1)):
            with self.subTest(initial=initial, final=final), self.assertRaises(ValueError):
                stats_module.record_holdem_hand(self.conn, "bad", 1, initial, final, 0, snapshot())

    def test_duplicate_hand_only_increments_once_and_users_are_independent(self):
        with self.conn:
            self.assertTrue(stats_module.record_holdem_hand(self.conn, "same", 1, 100, 110, 4, snapshot()))
            self.assertFalse(stats_module.record_holdem_hand(self.conn, "same", 1, 100, 999, 40, snapshot()))
            self.assertTrue(stats_module.record_holdem_hand(self.conn, "same", 2, 100, 90, -2, snapshot()))
        self.assertEqual(self.totals()["hands"], 1)
        self.assertEqual(self.totals()["net_profit"], 10)
        self.assertEqual(self.totals()["score_delta"], 4)
        self.assertEqual(self.totals(2)["net_profit"], -10)

    def test_each_hand_uses_its_own_big_blind(self):
        with self.conn:
            stats_module.record_holdem_hand(self.conn, "small", 1, 100, 110, 4,
                                           snapshot(big_blind=2))
            stats_module.record_holdem_hand(self.conn, "large", 1, 100, 90, -2,
                                           snapshot(big_blind=20))
        self.assertEqual(self.totals()["net_profit"], 0)
        self.assertEqual(self.totals()["net_bb"], 4.5)
        self.assertEqual(self.totals()["bb_per_100"], 225)

    def test_small_sample_boundary_and_batch_empty_input(self):
        with self.conn:
            for index in range(99):
                stats_module.record_holdem_hand(self.conn, str(index), 1, 100, 100, 0, snapshot())
        self.assertTrue(self.totals()["small_sample"])
        with self.conn:
            stats_module.record_holdem_hand(self.conn, "100", 1, 100, 100, 0, snapshot())
        self.assertFalse(self.totals()["small_sample"])
        statements = []
        self.conn.set_trace_callback(statements.append)
        self.assertEqual(stats_module.load_holdem_stats(self.conn, []), {})
        self.assertEqual(statements, [])
        self.assertEqual(set(stats_module.load_holdem_stats(self.conn, [1, 999])), {1})


class DatabaseFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        tmp = self.resources.enter_context(tempfile.TemporaryDirectory(prefix="holdem-stats-test-"))
        self.db_path = str(Path(tmp) / "isolated.sqlite3")
        self.resources.enter_context(patch.object(storage, "DB_FILE", self.db_path))
        for state in (server.hub.clients, server.rooms.game_rooms, server.rooms.leave_timers):
            self.resources.enter_context(patch.dict(state, {}, clear=True))
        self.resources.enter_context(patch.object(server.betting, "active_bet", None))
        self.resources.enter_context(patch.object(holdem, "new_deck", side_effect=fixed_deck))
        self.rooms = []
        self.resources.callback(self.close_rooms)
        init_db(server.database)
        with server.database() as conn, conn:
            conn.executemany("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?, '', '', 0, 900)", [(name,) for name in NAMES])

    def close_rooms(self):
        for room in self.rooms:
            room.close()
        for timer in server.rooms.leave_timers.values():
            timer.cancel()
        server.rooms.leave_timers.clear()
        server.rooms.game_rooms.clear()
        server.hub.clients.clear()

    def room(self, names=("alice", "bob"), stacks=None, blind=5, game="holdem"):
        room = create_room(game, room_id=len(self.rooms) + 1, name="统计专项", owner=names[0],
                           buy_in=100, blind=blind)
        server.rooms.attach_host(room)
        for name in names:
            amount = (stacks or {}).get(name, 100)
            room.add_member(name, amount)
            server.settlement.set_escrow(name, room.id, amount)
        self.rooms.append(room)
        server.rooms.game_rooms[room.id] = room
        return room

    async def start_rigged(self, room, holes, board):
        # 第一手庄家是 seating[0]；引擎依次给庄家后面的人发两张牌。
        order = room.seating[1:] + room.seating[:1]
        draws = [card for name in order for card in holes[name]] + list(board)
        with patch.object(holdem, "new_deck", side_effect=lambda: fixed_deck(draws)):
            await room.start()
        self.assertEqual(room.game["holes"], {name: holes[name] for name in order})

    def rows(self, query, parameters=()):
        with server.database() as conn:
            return conn.execute(query, parameters).fetchall()

    def count(self, table="holdem_hand_stats"):
        return self.rows(f"SELECT COUNT(*) FROM {table}")[0][0]

    def detail(self, room, name):
        with server.database() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT s.* FROM holdem_hand_stats s JOIN users u ON u.id=s.user_id "
                               "WHERE hand_id=? AND username=?", (room.rating_hand_id, name)).fetchone()
            self.assertIsNotNone(row, (room.rating_hand_id, name))
            return dict(row)

    def stats(self, name="alice"):
        user_id = self.rows("SELECT id FROM users WHERE username=?", (name,))[0][0]
        with server.database() as conn:
            return stats_module.load_holdem_stats(conn, [user_id]).get(user_id, stats_module.public_holdem_stats())

    async def leaderboard(self, username="alice", **request):
        with patch.object(server.hub, "send_json", new_callable=AsyncMock) as send:
            await server.ranking.handle_get_rating_leaderboard(None, {"user": {"username": username}}, request)
            send.assert_awaited_once()
            return send.call_args.args[1]

    async def passive_until(self, room, stage="showdown"):
        for _ in range(50):
            if room.game["stage"] == stage:
                return
            self.assertTrue(room.in_hand(), (room.game["stage"], stage))
            who = room.game["to_act"]
            self.assertIsNotNone(who)
            options = room.legal_actions(who)
            if options["check"]:
                await room.perform_action(who, "check")
            elif options["call"]:
                await room.perform_action(who, "call")
            else:
                await room.perform_action(who, "raise", {"raise_to": options["allin_to"]})
        self.fail("确定性动作未能在 50 步内到达目标街")

    def assert_no_settlement(self):
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("holdem_player_stats"), 0)
        self.assertEqual(self.count("rating_history"), 0)


class EngineStatisticsTests(DatabaseFixture):
    async def test_blinds_do_not_count_as_vpip_and_preflop_win_is_not_showdown(self):
        room = self.room()
        await room.start()
        self.assertEqual(room.rating_starts, {"alice": 100, "bob": 100})
        self.assertTrue(all(not item["vpip"] for item in room.game["stats"].values()))
        await room.perform_action("alice", "fold")
        for name in ("alice", "bob"):
            value = self.stats(name)
            self.assertEqual(value["hands"], 1)
            self.assertEqual(value["vpip"], 0)
            self.assertEqual(value["pfr"], 0)
            self.assertEqual(value["flop_hands"], 0)
            self.assertEqual(value["showdown_hands"], 0)
            self.assertIsNone(value["wtsd"])
        self.assertEqual(self.stats("bob")["win_rate"], 1)
        self.assertEqual(self.stats("alice")["manual_folds"], 1)
        self.assertEqual(self.detail(room, "alice")["initial_cents"], 10000)
        self.assertEqual(self.detail(room, "alice")["final_cents"], 9500)

    async def test_small_blind_completion_counts_but_big_blind_check_does_not(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "call")
        await room.perform_action("bob", "check")
        self.assertEqual(room.game["stage"], "flop")
        await room.perform_action("bob", "check")
        await room.perform_action("alice", "fold")
        self.assertEqual(self.stats("alice")["vpip"], 1)
        self.assertEqual(self.stats("bob")["vpip"], 0)
        for name in ("alice", "bob"):
            self.assertEqual(self.stats(name)["pfr"], 0)
            self.assertEqual(self.stats(name)["flop_rate"], 1)
            self.assertEqual(self.stats(name)["call_actions"], 0)
            self.assertEqual(self.stats(name)["wtsd"], 0)

    async def test_multiple_preflop_raises_only_count_one_pfr_hand_and_no_af(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "raise", {"raise_to": 20})
        await room.perform_action("bob", "raise", {"raise_to": 40})
        await room.perform_action("alice", "raise", {"raise_to": 60})
        await room.perform_action("bob", "fold")
        for name in ("alice", "bob"):
            value = self.stats(name)
            self.assertEqual(value["vpip_hands"], 1)
            self.assertEqual(value["pfr_hands"], 1)
            self.assertEqual(value["aggressive_actions"], 0)
            self.assertEqual(value["call_actions"], 0)

    async def test_short_allin_raise_button_is_call_unless_it_exceeds_current_bet(self):
        for amount, pfr in ((7, 0), (10, 0), (15, 1)):
            with self.subTest(amount=amount):
                room = self.room(stacks={"alice": amount})
                await room.start()
                await room.perform_action("alice", "raise", {"raise_to": amount})
                await self.passive_until(room)
                value = self.detail(room, "alice")
                self.assertEqual(value["vpip_hands"], 1)
                self.assertEqual(value["pfr_hands"], pfr)
                self.assertEqual(value["big_blind_cents"], 1000)
                self.assertEqual(value["aggressive_actions"], 0)
                room.close()

    async def test_rejected_out_of_turn_paused_and_illegal_actions_do_not_count(self):
        room = self.room()
        await room.start()
        before = copy.deepcopy(room.game["stats"])
        await room.perform_action("bob", "raise", {"raise_to": 100})
        await room.perform_action("alice", "check")
        await room.perform_action("alice", "not_an_action")
        room.pause()
        await room.perform_action("alice", "fold")
        room.resume()
        self.assertEqual(room.game["stats"], before)
        self.assertEqual(room.game["folded"], set())
        self.assert_no_settlement()
        await room.perform_action("alice", "fold")
        self.assertEqual(self.stats()["vpip_hands"], 0)

    async def test_short_raise_does_not_reopen_raise_and_rejected_retry_is_not_counted(self):
        room = self.room(names=NAMES[:3], stacks={"bob": 25})
        await room.start()
        await room.perform_action("alice", "raise", {"raise_to": 20})
        await room.perform_action("bob", "raise", {"raise_to": 25})
        await room.perform_action("carol", "call")
        self.assertEqual(room.game["to_act"], "alice")
        self.assertFalse(room.legal_actions("alice")["can_raise"])
        before = copy.deepcopy(room.game["stats"])
        committed = dict(room.game["committed"])
        await room.perform_action("alice", "raise", {"raise_to": 100})
        self.assertEqual(room.game["stats"], before)
        self.assertEqual(room.game["committed"], committed)
        await room.perform_action("alice", "call")
        await self.passive_until(room)
        self.assertEqual(self.stats("alice")["pfr_hands"], 1)
        self.assertEqual(self.stats("bob")["pfr_hands"], 1)
        self.assertEqual(self.stats("carol")["pfr_hands"], 0)

    async def test_postflop_af_counts_bets_raises_and_calls_across_three_streets(self):
        room = self.room()
        await room.start()
        await self.passive_until(room, "flop")
        for who, action, amount in [
            ("bob", "raise", 10), ("alice", "call", None),
            ("bob", "check", None), ("alice", "raise", 10), ("bob", "call", None),
            ("bob", "raise", 10), ("alice", "raise", 20), ("bob", "call", None),
        ]:
            self.assertEqual(room.game["to_act"], who)
            await room.perform_action(who, action, {"raise_to": amount})
        self.assertFalse(room.in_hand())
        self.assertEqual((self.stats("bob")["aggressive_actions"], self.stats("bob")["call_actions"]), (2, 2))
        self.assertEqual((self.stats()["aggressive_actions"], self.stats()["call_actions"]), (2, 1))
        self.assertEqual(self.stats("bob")["af"], 1)
        self.assertEqual(self.stats()["af"], 2)
        self.assertEqual(self.stats()["pfr"], 0)
        self.assertEqual(self.stats("bob")["vpip"], 0)  # 翻后下注不补计翻前 VPIP。
        self.assertEqual(self.stats()["vpip"], 1)

    async def test_postflop_short_allin_raise_button_counts_call_not_aggression(self):
        room = self.room(stacks={"bob": 25})
        await room.start()
        await self.passive_until(room, "flop")
        await room.perform_action("bob", "check")
        await room.perform_action("alice", "raise", {"raise_to": 20})
        self.assertFalse(room.legal_actions("bob")["call"])
        await room.perform_action("bob", "raise", {"raise_to": 15})
        await self.passive_until(room)
        self.assertEqual(self.stats("bob")["aggressive_actions"], 0)
        self.assertEqual(self.stats("bob")["call_actions"], 1)
        self.assertEqual(self.stats("bob")["af"], 0)
        self.assertEqual(self.stats()["aggressive_actions"], 1)
        self.assertIsNone(self.stats()["af"])
        self.assertTrue(self.stats()["af_no_calls"])

    async def test_postflop_short_allin_above_current_bet_is_aggression(self):
        room = self.room(stacks={"bob": 25})
        await room.start()
        await self.passive_until(room, "flop")
        await room.perform_action("bob", "check")
        await room.perform_action("alice", "raise", {"raise_to": 10})
        await room.perform_action("bob", "raise", {"raise_to": 15})
        await room.perform_action("alice", "call")
        await self.passive_until(room)
        self.assertEqual(self.stats("bob")["aggressive_actions"], 1)
        self.assertEqual(self.stats("bob")["call_actions"], 0)
        self.assertTrue(self.stats("bob")["af_no_calls"])

    async def test_manual_folds_on_every_street_count_once(self):
        for stage in ("preflop", "flop", "turn", "river"):
            with self.subTest(stage=stage):
                room = self.room()
                await room.start()
                await self.passive_until(room, stage)
                if stage != "preflop":
                    await room.perform_action("bob", "raise", {"raise_to": 10})
                await room.perform_action("alice", "fold")
                await room.perform_action("alice", "fold")
                await room.end_hand(False)
                value = self.detail(room, "alice")
                self.assertEqual((value["hands"], value["folds"], value["manual_folds"]), (1, 1, 1))
                self.assertEqual(value["timeout_folds"] + value["leave_folds"], 0)
                self.assertEqual(value["flop_hands"], int(stage != "preflop"))
                self.assertEqual(value["showdown_hands"], 0)
                room.close()

    async def test_timeout_folds_on_every_street_use_timeout_reason(self):
        for stage in ("preflop", "flop", "turn", "river"):
            with self.subTest(stage=stage):
                room = self.room()
                await room.start()
                await self.passive_until(room, stage)
                if stage != "preflop":
                    await room.perform_action("bob", "raise", {"raise_to": 10})
                await room.auto_action()
                value = self.detail(room, "alice")
                self.assertEqual((value["folds"], value["timeout_folds"]), (1, 1))
                self.assertEqual(value["manual_folds"] + value["leave_folds"], 0)
                self.assertEqual(value["flop_hands"], int(stage != "preflop"))
                room.close()

    async def test_automatic_checks_never_count_as_folds_or_voluntary_money(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "call")
        for _ in range(7):  # 大盲翻前 check，以及翻后双方三条街 check。
            await room.auto_action()
        self.assertFalse(room.in_hand())
        for name in ("alice", "bob"):
            value = self.stats(name)
            self.assertEqual(value["folds"], 0)
            self.assertEqual(value["timeout_folds"], 0)
            self.assertEqual(value["aggressive_actions"] + value["call_actions"], 0)
            self.assertEqual(value["showdown_hands"], 1)
        self.assertEqual(self.stats("bob")["vpip_hands"], 0)

    async def test_leave_on_each_street_folds_and_is_not_double_counted(self):
        for stage in ("preflop", "flop", "turn", "river"):
            with self.subTest(stage=stage):
                room = self.room(names=NAMES[:3])
                await room.start()
                await self.passive_until(room, stage)
                await server.rooms.leave_room_internal(room, "bob")
                value = self.detail(room, "bob")
                self.assertEqual((value["hands"], value["folds"], value["leave_folds"]), (1, 1, 1))
                self.assertEqual(value["settlement_reason"], "leave")
                self.assertEqual(value["flop_hands"], int(stage != "preflop"))
                self.assertEqual(value["showdown_hands"], 0)
                before = self.rows("SELECT coins FROM users WHERE username='bob'")
                await server.rooms.leave_room_internal(room, "bob")
                self.assertEqual(self.rows("SELECT coins FROM users WHERE username='bob'"), before)
                await room.perform_action(room.game["to_act"], "fold")
                self.assertEqual(self.detail(room, "bob"), value)
                self.assertEqual(set(room.game["result"]["ratings"]), set(NAMES[:3]))
                self.assertEqual(self.rows("SELECT COUNT(*) FROM holdem_hand_stats WHERE hand_id=?",
                                           (room.rating_hand_id,))[0][0], 3)
                self.assertEqual(self.rows("SELECT * FROM game_escrows WHERE room_id=? AND username='bob'",
                                           (room.id,)), [])
                room.close()

    async def test_leaving_after_manual_or_timeout_fold_preserves_original_reason(self):
        for auto, reason in ((False, "manual"), (True, "timeout")):
            with self.subTest(reason=reason):
                room = self.room(names=NAMES[:3])
                await room.start()
                await room.perform_action("alice", "fold", auto=auto)
                await server.rooms.leave_room_internal(room, "alice")
                value = self.detail(room, "alice")
                self.assertEqual(value[f"{reason}_folds"], 1)
                self.assertEqual(value["folds"], 1)
                self.assertEqual(value["leave_folds"], 0)
                self.assertEqual(value["settlement_reason"], "leave")
                await room.perform_action(room.game["to_act"], "fold")
                self.assertEqual(self.detail(room, "alice"), value)
                room.close()

    async def test_leaving_after_completed_hand_does_not_create_another_fold(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        before = self.stats("bob")
        await server.rooms.leave_room_internal(room, "bob")
        self.assertEqual(self.stats("bob"), before)
        self.assertEqual(self.count(), 2)

    async def test_hand_statistics_is_a_detached_nonmutating_snapshot(self):
        room = self.room()
        await room.start()
        before = copy.deepcopy(room.game)
        result = room.hand_statistics(["alice", "unknown"], leaving=True, showdown=True)
        self.assertEqual(set(result), {"alice"})
        self.assertTrue(result["alice"]["folded"])
        self.assertEqual(result["alice"]["fold_reason"], "leave")
        self.assertFalse(result["alice"]["showdown"])
        result["alice"]["vpip"] = True
        self.assertEqual(room.game, before)
        self.assert_no_settlement()

    async def test_forced_blind_allins_run_out_flop_and_showdown_without_vpip(self):
        room = self.room(stacks={"alice": 5, "bob": 10})
        await room.start()
        self.assertFalse(room.in_hand())
        self.assertEqual(len(room.game["board"]), 5)
        for name in ("alice", "bob"):
            value = self.stats(name)
            self.assertEqual(value["vpip_hands"] + value["pfr_hands"], 0)
            self.assertEqual(value["flop_hands"], 1)
            self.assertEqual(value["showdown_hands"], 1)
            self.assertEqual(value["wtsd"], 1)
            self.assertEqual(self.detail(room, name)["big_blind_cents"], 1000)

    async def test_split_pot_break_even_is_not_a_win(self):
        room = self.room()
        await self.start_rigged(room,
            {"alice": [(4, 2), (5, 2)], "bob": [(2, 1), (3, 1)]},
            [(rank, 0) for rank in range(10, 15)])
        await self.passive_until(room)
        self.assertEqual(room.game["result"]["payouts"], {"bob": 10, "alice": 10})
        for name in ("alice", "bob"):
            value = self.stats(name)
            self.assertEqual(room.members[name]["stack"], 100)
            self.assertEqual(value["net_profit"], 0)
            self.assertEqual(value["win_rate"], 0)
            self.assertEqual(value["showdown_win_rate"], 0)
            self.assertEqual(value["score_delta"], 0)

    async def test_side_pot_payout_with_net_loss_is_not_a_win(self):
        room = self.room(names=NAMES[:3], stacks={"carol": 80})
        await self.start_rigged(room,
            {"alice": [(13, 0), (13, 1)], "bob": [(12, 0), (12, 1)],
             "carol": [(14, 0), (14, 1)]},
            [(2, 0), (3, 1), (7, 2), (8, 3), (11, 0)])
        await room.perform_action("alice", "raise", {"raise_to": 100})
        await room.perform_action("bob", "call")
        await room.perform_action("carol", "raise", {"raise_to": 80})
        self.assertFalse(room.in_hand())
        self.assertEqual(room.game["result"]["payouts"], {"carol": 240, "alice": 40})
        self.assertEqual(sum(member["stack"] for member in room.members.values()), 280)
        self.assertEqual(self.stats()["net_profit"], -60)
        self.assertEqual(self.stats()["wins"], 0)
        self.assertEqual(self.stats()["showdown_wins"], 0)
        self.assertEqual(self.stats("carol")["net_profit"], 160)
        self.assertEqual(self.stats("carol")["showdown_wins"], 1)
        self.assertEqual(self.stats("carol")["pfr_hands"], 0)
        for name in NAMES[:3]:
            self.assertEqual(self.stats(name)["showdown_hands"], 1)
            self.assertEqual(self.stats(name)["flop_hands"], 1)

    async def test_preflop_fold_is_excluded_from_flop_and_showdown_of_other_players(self):
        room = self.room(names=NAMES[:3])
        await room.start()
        await room.perform_action("alice", "fold")
        await self.passive_until(room)
        self.assertEqual(self.stats()["folds"], 1)
        self.assertEqual(self.stats()["flop_hands"], 0)
        self.assertEqual(self.stats()["showdown_hands"], 0)
        self.assertIsNone(self.stats()["wtsd"])
        for name in ("bob", "carol"):
            self.assertEqual(self.stats(name)["folds"], 0)
            self.assertEqual(self.stats(name)["flop_hands"], 1)
            self.assertEqual(self.stats(name)["showdown_hands"], 1)

    async def test_river_fold_is_not_showdown_even_when_result_displays_cards(self):
        room = self.room()
        await room.start()
        await self.passive_until(room, "river")
        await room.perform_action("bob", "fold")
        result = room.game["result"]
        self.assertEqual(len(result["board"]), 5)
        self.assertTrue(all(item["cards"] for item in result["hands"]))
        self.assertEqual(result["reveal"], [])
        self.assertEqual(self.stats()["wins"], 1)
        for name in ("alice", "bob"):
            self.assertEqual(self.stats(name)["flop_hands"], 1)
            self.assertEqual(self.stats(name)["showdown_hands"], 0)
            self.assertEqual(self.stats(name)["wtsd"], 0)
            self.assertIsNone(self.stats(name)["showdown_win_rate"])

    async def test_next_hand_uses_new_stack_and_per_hand_frozen_big_blind(self):
        room = self.room(blind=5)
        await room.start()
        first_id = room.rating_hand_id
        room.blind = 2  # 摘要不能错误读取后来修改的房间配置。
        await room.perform_action("alice", "fold")
        self.assertEqual(self.detail(room, "alice")["big_blind_cents"], 1000)
        room.blind = 10
        await room.start_next_hand()
        self.assertNotEqual(first_id, room.rating_hand_id)
        self.assertEqual(room.rating_starts, {"alice": 95, "bob": 105})
        await room.perform_action("bob", "fold")
        self.assertEqual(self.detail(room, "alice")["big_blind_cents"], 2000)
        self.assertEqual(self.detail(room, "alice")["initial_cents"], 9500)
        self.assertEqual(self.stats()["net_profit"], 5)
        self.assertEqual(self.stats()["net_bb"], 0)
        self.assertEqual(self.stats()["bb_per_100"], 0)
        self.assertEqual(self.stats()["hands"], 2)
        self.assertEqual(self.stats()["win_rate"], 0.5)

    async def test_score_delta_is_actual_clipped_loss_not_formula_loss(self):
        room = self.room()
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET rating_score=7 WHERE username='bob'")
        await self.start_rigged(room,
            {"alice": [(14, 0), (14, 1)], "bob": [(13, 0), (13, 1)]},
            [(2, 0), (3, 1), (7, 2), (8, 3), (11, 0)])
        await room.perform_action("alice", "raise", {"raise_to": 100})
        await room.perform_action("bob", "call")
        self.assertEqual(server.ranking.get_rating("bob")["score"], 0)
        self.assertEqual(room.game["result"]["ratings"]["bob"]["delta"], -7)
        self.assertEqual(self.stats("bob")["score_delta"], -7)
        self.assertEqual(self.stats("bob")["score_per_hand"], -7)
        self.assertEqual(self.stats("bob")["net_profit"], -100)


class PersistenceTests(DatabaseFixture):
    async def test_completed_settlement_replays_cannot_change_totals_or_escrows(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        expected = room.game["result"]["ratings"]
        before = self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id")
        escrow = self.rows("SELECT * FROM game_escrows ORDER BY username")
        snapshots = room.hand_statistics(room.members)
        for _ in range(3):
            await room.end_hand(False)
            self.assertEqual(room.settle_ratings(), expected)
            replay = server.settlement.record_hand_ratings(room, room.rating_hand_id,
                {"alice": 1, "bob": 1}, {"alice": 999, "bob": 999}, statistics=snapshots)
            self.assertEqual(replay, expected)
        self.assertEqual(self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id"), before)
        self.assertEqual(self.rows("SELECT * FROM game_escrows ORDER BY username"), escrow)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count("rating_history"), 2)
        self.assertEqual(room.match_rating_delta, {"alice": -1, "bob": 2})

    async def test_injected_stats_failure_rolls_back_entire_transaction_and_can_retry(self):
        room = self.room()
        await room.start()
        stacks = copy.deepcopy(room.members)
        escrow = self.rows("SELECT * FROM game_escrows ORDER BY username")
        calls = []
        original = settlement_module.record_holdem_hand

        def fail_after_second_insert(conn, *args, **kwargs):
            self.assertTrue(conn.in_transaction)
            inserted = original(conn, *args, **kwargs)
            calls.append(args[1])
            if len(calls) == 2:
                raise sqlite3.OperationalError("injected statistics failure after insert")
            return inserted

        with patch.object(settlement_module, "record_holdem_hand", side_effect=fail_after_second_insert):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected statistics"):
                await room.perform_action("alice", "fold")
        self.assertEqual(len(calls), 2)
        self.assert_no_settlement()
        self.assertEqual(room.members, stacks)
        self.assertEqual(room.rating_results, {})
        self.assertEqual(room.match_rating_delta, {})
        self.assertEqual(room.pending_rating_updates, set())
        self.assertEqual(self.rows("SELECT * FROM game_escrows ORDER BY username"), escrow)
        self.assertEqual(server.ranking.get_rating("alice"), rating_info())
        self.assertEqual(server.ranking.get_rating("bob"), rating_info())
        self.assertTrue(room.in_hand())
        await room.end_hand(False)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count("rating_history"), 2)
        self.assertEqual(room.members["alice"]["stack"], 95)
        self.assertEqual(room.members["bob"]["stack"], 105)
        self.assertEqual(self.stats()["manual_folds"], 1)
        await room.end_hand(False)
        self.assertEqual(self.stats()["hands"], 1)

    async def test_turnover_failure_rolls_back_statistics_and_retry_records_each_once(self):
        room = self.room()
        await room.start()
        original = settlement_module.record_holdem_turnover

        def fail_after_turnover_write(conn, *args, **kwargs):
            original(conn, *args, **kwargs)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdem_hand_stats").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM holdem_turnover").fetchone()[0], 2)
            raise sqlite3.OperationalError("turnover rollback")

        with patch.object(settlement_module, "record_holdem_turnover", side_effect=fail_after_turnover_write):
            with self.assertRaisesRegex(sqlite3.OperationalError, "turnover rollback"):
                await room.perform_action("alice", "fold")
        self.assert_no_settlement()
        self.assertEqual(self.count("holdem_turnover"), 0)
        await room.finish_pending_settlement()
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.count("holdem_turnover"), 2)
        self.assertEqual(self.rows("SELECT SUM(amount_cents) FROM holdem_turnover"), [(1500,)])
        turnover = self.rows("SELECT * FROM holdem_turnover ORDER BY user_id")
        await room.end_hand(False)
        self.assertEqual(self.rows("SELECT * FROM holdem_turnover ORDER BY user_id"), turnover)
        self.assertEqual(self.stats()["hands"], 1)

    async def fail_allin_settlement(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "raise", {"raise_to": 100})
        with patch.object(settlement_module, "record_holdem_hand", side_effect=sqlite3.OperationalError("retry me")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "retry me"):
                await room.perform_action("bob", "call")
        self.assertTrue(room.game["pending_settlement"])
        self.assertIsNone(room.game["to_act"])
        self.assertIn("settlement", room.timers)
        return room

    async def test_failed_allin_repeated_call_or_fold_only_retries_original_showdown(self):
        for action in ("call", "fold"):
            with self.subTest(action=action):
                room = await self.fail_allin_settlement()
                before = copy.deepcopy(room.game["stats"])
                await room.perform_action("bob", action)
                self.assertFalse(room.in_hand())
                self.assertEqual(room.game["stats"], before)
                self.assertNotIn("settlement", room.timers)
                self.assertEqual(sum(m["stack"] for m in room.members.values()), 200)
                for name in ("alice", "bob"):
                    detail = self.detail(room, name)
                    self.assertEqual((detail["hands"], detail["showdown_hands"], detail["folds"]), (1, 1, 0))
                room.close()

    async def test_failed_settlement_automatically_retries_and_pause_resume_keeps_retry(self):
        room = await self.fail_allin_settlement()
        self.assert_no_settlement()
        room.pause()
        self.assertEqual(room.timers, {})
        room.resume()
        self.assertIn("settlement", room.timers)
        finished = asyncio.Event()
        room.broadcast_payload = AsyncMock(side_effect=lambda *args: finished.set())
        await asyncio.wait_for(finished.wait(), timeout=4)
        self.assertFalse(room.in_hand())
        self.assertEqual(self.count(), 2)
        self.assertNotIn("settlement", room.timers)

    async def test_pending_settlement_blocks_leave_restart_and_dissolve_until_retry_succeeds(self):
        room = self.room()
        await room.start()
        with patch.object(settlement_module, "record_holdem_hand", side_effect=sqlite3.OperationalError("retry me")):
            with self.assertRaises(sqlite3.OperationalError):
                await room.perform_action("alice", "fold")
            state = copy.deepcopy(room.game)
            stacks = copy.deepcopy(room.members)
            escrows = self.rows("SELECT * FROM game_escrows")
            for action in (lambda: room.restart(),
                           lambda: server.rooms.leave_room_internal(room, "alice"),
                           lambda: server.rooms.dissolve_room(room, "test")):
                with self.assertRaises(sqlite3.OperationalError):
                    await action()
                self.assertEqual(room.game, state)
                self.assertEqual(room.members, stacks)
                self.assertEqual(self.rows("SELECT * FROM game_escrows"), escrows)
                self.assertIn(room.id, server.rooms.game_rooms)
                self.assertIn("settlement", room.timers)
                self.assert_no_settlement()
        await server.rooms.leave_room_internal(room, "alice")
        self.assertEqual(self.detail(room, "alice")["settlement_reason"], "completed")
        self.assertEqual(self.stats()["manual_folds"], 1)
        self.assertEqual(self.stats()["leave_folds"], 0)
        await server.rooms.dissolve_room(room, "test")
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.rows("SELECT username,coins FROM users WHERE username IN ('alice','bob') ORDER BY username"),
                         [("alice", 995), ("bob", 1005)])

    async def test_restart_retries_finished_hand_instead_of_refunding_it(self):
        room = self.room()
        await room.start()
        with patch.object(settlement_module, "record_holdem_hand", side_effect=sqlite3.OperationalError("retry me")):
            with self.assertRaises(sqlite3.OperationalError):
                await room.perform_action("alice", "fold")
        await room.restart()
        self.assertEqual(self.count(), 2)
        self.assertEqual(room.rating_starts, {"alice": 95, "bob": 105})
        self.assertNotIn("pending_settlement", room.game)
        self.assertNotIn("settlement", room.timers)

    async def test_leave_statistics_failure_keeps_member_and_refund_retryable(self):
        room = self.room(names=NAMES[:3])
        await room.start()
        state = copy.deepcopy(room.game)
        money = self.rows("SELECT username,coins FROM users ORDER BY username")
        with patch.object(settlement_module, "record_holdem_hand", side_effect=sqlite3.OperationalError("leave failure")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "leave failure"):
                await server.rooms.leave_room_internal(room, "bob")
        self.assertTrue(room.has_member("bob"))
        self.assertEqual(room.game, state)
        self.assertEqual(self.rows("SELECT username,coins FROM users ORDER BY username"), money)
        self.assert_no_settlement()
        await server.rooms.leave_room_internal(room, "bob")
        self.assertFalse(room.has_member("bob"))
        self.assertEqual(self.stats("bob")["leave_folds"], 1)
        self.assertEqual(self.count(), 1)

    async def test_old_rating_history_and_omitted_statistics_are_never_backfilled(self):
        room = self.room()
        old = server.settlement.record_hand_ratings(room, "legacy-hand", {"alice": 100}, {"alice": 120})
        self.assertEqual(old["alice"]["delta"], 8)
        self.assertEqual(self.count(), 0)
        init_db(server.database)
        init_db(server.database)
        await room.start()
        server.settlement.record_hand_ratings(room, "legacy-hand", {"alice": 100}, {"alice": 120},
                                   statistics=room.hand_statistics(["alice"]))
        self.assertEqual(self.count(), 0)
        value = (await self.leaderboard())["self"]
        self.assertEqual(value["rating"]["games"], 1)
        self.assertEqual(value["holdem_stats"]["hands"], 0)
        for key in RATIOS:
            self.assertIsNone(value["holdem_stats"][key])
        await room.perform_action("alice", "fold")
        self.assertEqual(self.stats()["hands"], 1)
        self.assertEqual(server.ranking.get_rating("alice")["games"], 2)

    async def test_upgrade_from_database_without_stats_tables_keeps_old_history_unfilled(self):
        room = self.room()
        server.settlement.record_hand_ratings(room, "before-feature", {"alice": 100}, {"alice": 130})
        old_history = self.rows("SELECT * FROM rating_history")
        old_rating = server.ranking.get_rating("alice")
        with server.database() as conn, conn:
            for table in ("holdem_hand_stats", "holdem_player_stats", "holdem_stats_metadata"):
                conn.execute(f"DROP TABLE {table}")
        with patch.object(stats_module.time, "time", return_value=1700000000):
            init_db(server.database)
        with patch.object(stats_module.time, "time", return_value=1800000000):
            init_db(server.database)
        self.assertEqual(self.rows("SELECT * FROM holdem_stats_metadata"), [(1, 1700000000)])
        self.assertEqual(self.rows("SELECT * FROM rating_history"), old_history)
        self.assertEqual(server.ranking.get_rating("alice"), old_rating)
        self.assertEqual(self.stats()["hands"], 0)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("holdem_player_stats"), 0)

    async def test_legacy_manual_hand_without_statistics_does_not_backfill(self):
        room = self.room()
        await room.start()
        del room.game["stats"]
        room.game["folded"].add("alice")
        self.assertFalse(room.hand_statistics(room.members))
        await room.end_hand(False)
        self.assertEqual(self.count("rating_history"), 2)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count("holdem_player_stats"), 0)

    async def test_normal_three_argument_rating_mock_remains_usable_without_statistics(self):
        room = BaseRoom(room_id=1, name="legacy", owner="alice", buy_in=100, blind=5)
        room.add_member("alice", 100)
        room.begin_rating_hand(["alice"])
        calls = []

        def legacy_record(hand_id, starts, endings):
            calls.append((hand_id, starts, endings))
            return {"alice": {"delta": 0}}

        room.record_ratings = legacy_record
        self.assertEqual(room.settle_ratings(), {"alice": {"delta": 0}})
        room.settle_ratings()
        self.assertEqual(len(calls), 1)
        self.assert_no_settlement()

    async def test_initialization_is_idempotent_and_closed_reopened_database_keeps_totals(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        before = self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id")
        detail = self.rows("SELECT * FROM holdem_hand_stats ORDER BY user_id")
        with server.database() as conn, conn:
            conn.execute("UPDATE holdem_stats_metadata SET started_at=123456789 WHERE id=1")
        for _ in range(3):
            init_db(server.database)
        with closing(sqlite3.connect(self.db_path)) as reopened:
            self.assertEqual(reopened.execute("SELECT * FROM holdem_player_stats ORDER BY user_id").fetchall(), before)
            self.assertEqual(reopened.execute("SELECT * FROM holdem_hand_stats ORDER BY user_id").fetchall(), detail)
            self.assertEqual(reopened.execute("SELECT * FROM holdem_stats_metadata").fetchall(), [(1, 123456789)])
        self.assertEqual((await self.leaderboard())["stats_since"], 123456789)

    async def test_rebuild_from_details_matches_incremental_totals_and_is_idempotent(self):
        room = self.room(names=NAMES[:3])
        await room.start()
        await server.rooms.leave_room_internal(room, "bob")
        await room.perform_action(room.game["to_act"], "fold")
        room.blind = 2
        await room.start_next_hand()
        await self.passive_until(room)
        before = self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id")
        details = self.rows("SELECT * FROM holdem_hand_stats ORDER BY hand_id,user_id")
        with server.database() as conn, conn:
            conn.execute("UPDATE holdem_player_stats SET hands=999, net_bb=999")
            stats_module.rebuild_holdem_stats(conn)
        self.assertEqual(self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id"), before)
        with server.database() as conn, conn:
            stats_module.rebuild_holdem_stats(conn)
        self.assertEqual(self.rows("SELECT * FROM holdem_player_stats ORDER BY user_id"), before)
        self.assertEqual(self.rows("SELECT * FROM holdem_hand_stats ORDER BY hand_id,user_id"), details)

    async def test_waiting_dissolution_restart_and_void_refunds_do_not_count(self):
        waiting = self.room()
        await server.rooms.dissolve_room(waiting, "waiting cancellation")
        room = self.room()
        await room.start()
        old_id = room.rating_hand_id
        await room.perform_action("alice", "raise", {"raise_to": 20})
        await room.restart()
        self.assertNotEqual(room.rating_hand_id, old_id)
        self.assertEqual(room.rating_starts, {"alice": 100, "bob": 100})
        self.assertTrue(all(not value["vpip"] for value in room.game["stats"].values()))
        await server.rooms.dissolve_room(room, "void hand")
        self.assert_no_settlement()
        self.assertEqual(self.count("game_escrows"), 0)

    async def test_restart_escrow_refund_does_not_invent_stats_or_duplicate_completed_stats(self):
        unfinished = self.room()
        await unfinished.start()
        unfinished.close()
        server.rooms.game_rooms.pop(unfinished.id)  # 模拟重启：未完成房间内存态消失。
        server.settlement.refund_game_escrows()
        money = self.rows("SELECT username,coins FROM users ORDER BY username")
        server.settlement.refund_game_escrows()
        self.assertEqual(self.rows("SELECT username,coins FROM users ORDER BY username"), money)
        self.assert_no_settlement()
        completed = self.room()
        await completed.start()
        await completed.perform_action("alice", "fold")
        before = self.stats()
        completed.close()
        server.rooms.game_rooms.pop(completed.id)
        server.settlement.refund_game_escrows()
        server.settlement.refund_game_escrows()
        self.assertEqual(self.stats(), before)
        self.assertEqual(self.count(), 2)

    async def test_other_games_share_rating_but_never_holdem_statistics(self):
        for game in ("uno", "mahjong", "guandan"):
            with self.subTest(game=game):
                room = self.room(names=NAMES, game=game)
                room.begin_rating_hand(NAMES)
                results = room.settle_ratings({name: 110 if index % 2 == 0 else 90
                                              for index, name in enumerate(NAMES)},
                                             statistics={name: snapshot(vpip=True) for name in NAMES})
                self.assertEqual(set(results), set(NAMES))
                self.assertEqual(self.count(), 0)
                self.assertEqual(self.stats()["hands"], 0)
        self.assertEqual(self.count("rating_history"), 12)
        self.assertEqual(server.ranking.get_rating("alice")["games"], 3)


class LeaderboardTests(DatabaseFixture):
    def add_ranked_players(self):
        with server.database() as conn, conn:
            conn.executemany("INSERT INTO users(username,password_hash,salt,created_at,rating_score) "
                             "VALUES (?, '', '', 0, ?)",
                             [(f"player{i:03d}", 2000 if i < 105 else 1500) for i in range(205)])

    async def test_registered_new_account_has_no_statistics_denominators(self):
        with server.database() as conn, conn:
            conn.execute("INSERT INTO invite_codes(code,created_at) VALUES ('stats-new-user',0)")
        ok, _ = server.accounts.register_user("newplayer", "new-password", "stats-new-user")
        self.assertTrue(ok)
        data = await self.leaderboard(username="newplayer")
        self.assertEqual(data["self"]["rating"], rating_info())
        self.assertEqual(data["self"]["holdem_stats"]["hands"], 0)
        for key in RATIOS:
            self.assertIsNone(data["self"]["holdem_stats"][key])
        self.assert_no_settlement()

    async def test_new_user_has_only_public_fields_and_null_denominators(self):
        data = await self.leaderboard()
        self.assertEqual(data["total"], 4)
        self.assertIsInstance(data["stats_since"], int)
        for entry in data["entries"]:
            self.assertEqual(set(entry), {"username", "nickname", "rank", "rating", "holdem_stats"})
            self.assertEqual(set(entry["holdem_stats"]), PUBLIC_FIELDS)
            for key in RATIOS:
                self.assertIsNone(entry["holdem_stats"][key])
        self.assertEqual(data["self"], data["entries"][0])

    async def test_stats_are_public_for_every_player_without_holes_or_wallets(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        observer = await self.leaderboard(username="carol")
        values = {entry["username"]: entry for entry in observer["entries"]}
        self.assertEqual(values["alice"]["holdem_stats"], self.stats())
        self.assertEqual(values["bob"]["holdem_stats"], self.stats("bob"))
        for entry in values.values():
            self.assertEqual(set(entry), {"username", "nickname", "rank", "rating", "holdem_stats"})
            self.assertEqual(set(entry["holdem_stats"]), PUBLIC_FIELDS)
        self.assertEqual(observer["self"]["username"], "carol")

    async def test_pagination_ties_global_rank_and_self_on_or_outside_page(self):
        self.add_ranked_players()
        data = await self.leaderboard(username="player104", offset=0, request_id="page-0")
        self.assertEqual((data["total"], data["limit"], data["offset"]), (209, 100, 0))
        self.assertEqual(data["request_id"], "page-0")
        self.assertEqual(len(data["entries"]), 100)
        self.assertEqual(data["entries"][-1]["username"], "player099")
        self.assertEqual(data["self"]["username"], "player104")
        self.assertEqual(data["self"]["rank"], 1)
        data = await self.leaderboard(username="player104", offset=100)
        self.assertEqual(data["self"], data["entries"][4])
        self.assertEqual([entry["rank"] for entry in data["entries"][:6]], [1, 1, 1, 1, 1, 106])
        self.assertEqual(data["entries"][-1]["username"], "player199")
        last = await self.leaderboard(username="dave", offset=10**100)
        self.assertEqual(last["offset"], 200)
        self.assertEqual(len(last["entries"]), 9)
        self.assertEqual(last["self"]["rank"], 206)
        self.assertEqual(last["self"], last["entries"][-1])
        self.assertEqual(last["self"]["holdem_stats"]["hands"], 0)

    async def test_offset_type_validation_and_page_boundary_clamping(self):
        self.add_ranked_players()
        cases = [(None, 0), (True, 0), (False, 0), (199.0, 0), ("100", 0),
                 ([], 0), ({}, 0), (-1, 0), (-1000, 0), (99, 0), (100, 100),
                 (199, 100), (200, 200), (299, 200), (999999, 200)]
        for offset, expected in cases:
            with self.subTest(offset=offset):
                data = await self.leaderboard(offset=offset, limit=1, username="alice", rating_score=999999)
                self.assertEqual(data["offset"], expected)
                self.assertEqual(data["limit"], 100)
                self.assertEqual(data["self"]["username"], "alice")
                self.assertEqual(data["self"]["rating"]["score"], 1000)

    async def test_request_id_echo_is_bounded_and_does_not_coerce_values(self):
        for value, expected in [("", ""), ("请求-7", "请求-7"), ("x" * 128, "x" * 128),
                                ("x" * 129, None), (123, None), (False, None),
                                ([], None), ({"id": "x"}, None), (None, None)]:
            with self.subTest(value=value):
                self.assertEqual((await self.leaderboard(request_id=value))["request_id"], expected)

    async def test_unauthenticated_request_is_ignored_and_empty_database_is_safe(self):
        with patch.object(server.hub, "send_json", new_callable=AsyncMock) as send:
            await server.ranking.handle_get_rating_leaderboard(None, {}, {"request_id": "private"})
            send.assert_not_awaited()
        with server.database() as conn, conn:
            conn.execute("DELETE FROM users")
        data = await self.leaderboard(offset=1000)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["offset"], 0)
        self.assertEqual(data["entries"], [])
        self.assertIsNone(data["self"])

    async def test_reads_use_one_transaction_and_constant_batch_queries_not_hand_history(self):
        async def capture(offset=0):
            statements, connections = [], []

            @contextmanager
            def traced_database():
                with closing(sqlite3.connect(self.db_path)) as conn:
                    connections.append(conn)
                    conn.set_trace_callback(lambda sql: statements.append((sql, conn.in_transaction)))
                    yield conn

            with patch.object(server.ranking, "database", traced_database):
                result = await self.leaderboard(offset=offset)
            self.assertEqual(len(connections), 1)
            reads = [(sql, active) for sql, active in statements
                     if sql.lstrip().upper().startswith(("SELECT", "WITH"))]
            self.assertEqual(len(reads), 4)
            self.assertTrue(all(active for _, active in reads))
            self.assertEqual(statements[0][0].strip().upper(), "BEGIN")
            self.assertTrue(all("holdem_hand_stats" not in sql.lower() for sql, _ in reads))
            aggregate_reads = [sql for sql, _ in reads if "holdem_player_stats" in sql]
            self.assertEqual(len(aggregate_reads), 1)
            self.assertIn(" IN (", aggregate_reads[0])
            return result

        await capture()
        self.add_ranked_players()
        data = await capture(100)
        self.assertEqual(len(data["entries"]), 100)
        self.assertEqual(data["self"]["username"], "alice")

    async def test_read_transaction_keeps_rating_and_statistics_at_same_snapshot(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        before = await self.leaderboard()
        with server.database() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        wrote = []

        @contextmanager
        def racing_database():
            with closing(sqlite3.connect(self.db_path)) as conn:
                def trace(sql):
                    if "WITH ranked AS" in sql and not wrote:
                        with closing(sqlite3.connect(self.db_path)) as writer, writer:
                            writer.execute("UPDATE users SET rating_score=1999 WHERE username='alice'")
                            writer.execute("UPDATE holdem_player_stats SET hands=9 WHERE user_id="
                                           "(SELECT id FROM users WHERE username='alice')")
                        wrote.append(True)
                conn.set_trace_callback(trace)
                yield conn

        with patch.object(server.ranking, "database", racing_database):
            during = await self.leaderboard()
        self.assertEqual(wrote, [True])
        self.assertEqual(during, before)
        after = await self.leaderboard()
        self.assertEqual(after["self"]["rating"]["score"], 1999)
        self.assertEqual(after["self"]["holdem_stats"]["hands"], 9)

    async def test_account_must_leave_before_deletion_and_same_name_registration_starts_fresh(self):
        room = self.room(names=NAMES[:3])
        await room.start()
        old_id = self.rows("SELECT id FROM users WHERE username='bob'")[0][0]
        password_hash, salt = account_helpers.hash_password("test-password")
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET password_hash=?,salt=? WHERE username='bob'", (password_hash, salt))
            conn.execute("INSERT INTO invite_codes(code,created_at) VALUES ('stats-reuse-name',0)")
        state = {"user": {"username": "bob"}, "last_auth_attempt": -1000}
        other_socket = AsyncMock()
        other_state = {"user": {"username": "bob"}, "send_lock": asyncio.Lock()}
        server.hub.clients[other_socket] = other_state
        with patch.object(server.hub, "send_json", new_callable=AsyncMock) as send:
            await server.auth.handle_delete_account(None, state, {"password": "test-password"})
            self.assertEqual(send.call_args.args[1]["type"], "account_error")
            self.assertIsNotNone(state["user"])
            self.assertTrue(room.has_member("bob"))
            self.assertEqual(self.rows("SELECT id FROM users WHERE username='bob'"), [(old_id,)])
            await server.rooms.leave_room_internal(room, "bob")
            state["last_auth_attempt"] = -1000
            await server.auth.handle_delete_account(None, state, {"password": "test-password"})
            send.assert_any_await(other_socket, {"type": "account_deleted"})
        self.assertIsNone(state["user"])
        self.assertIsNone(other_state["user"])
        ok, _ = server.accounts.register_user("bob", "new-password", "stats-reuse-name")
        self.assertTrue(ok)
        self.assertNotEqual(self.rows("SELECT id FROM users WHERE username='bob'")[0][0], old_id)
        await room.perform_action("alice", "fold")
        self.assertEqual(self.stats("bob")["hands"], 0)
        self.assertEqual(server.ranking.get_rating("bob"), rating_info())
        self.assertEqual(self.rows("SELECT amount_cents FROM holdem_turnover WHERE user_id="
                                   "(SELECT id FROM users WHERE username='bob')"), [])
        for table in ("rating_history", "holdem_hand_stats", "holdem_player_stats", "holdem_turnover"):
            self.assertEqual(self.rows(f"SELECT * FROM {table} WHERE user_id=?", (old_id,)), [])

    async def test_delete_account_cleans_only_its_detail_totals_and_history(self):
        room = self.room()
        await room.start()
        await room.perform_action("alice", "fold")
        await server.rooms.dissolve_room(room, "test account deletion")
        old_id = self.rows("SELECT id FROM users WHERE username='alice'")[0][0]
        metadata = self.rows("SELECT * FROM holdem_stats_metadata")
        bob = self.stats("bob")
        password_hash, salt = account_helpers.hash_password("test-password")
        with server.database() as conn, conn:
            conn.execute("UPDATE users SET password_hash=?,salt=? WHERE username='alice'", (password_hash, salt))
        state = {"user": {"username": "alice"}, "last_auth_attempt": -1000}
        with patch.object(server.hub, "send_json", new_callable=AsyncMock) as send:
            await server.auth.handle_delete_account(None, state, {"password": "test-password"})
            self.assertEqual(send.call_args.args[1]["type"], "account_deleted")
        self.assertIsNone(state["user"])
        for table in ("rating_history", "holdem_hand_stats", "holdem_player_stats"):
            self.assertEqual(self.rows(f"SELECT * FROM {table} WHERE user_id=?", (old_id,)), [])
        self.assertEqual(self.stats("bob"), bob)
        self.assertEqual(self.rows("SELECT * FROM holdem_stats_metadata"), metadata)
        self.assertEqual((await self.leaderboard(username="bob"))["total"], 3)
        with server.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at) VALUES ('alice','','',0)")
        self.assertNotEqual(self.rows("SELECT id FROM users WHERE username='alice'")[0][0], old_id)
        self.assertEqual(self.stats()["hands"], 0)


class WebSocketTests(DatabaseFixture):
    async def receive(self, socket, kind):
        async def read_until():
            while True:
                value = json.loads(await socket.recv())
                if value.get("type") == kind:
                    return value
        return await asyncio.wait_for(read_until(), timeout=5)

    async def login(self, socket, name):
        await socket.send(json.dumps({"type": "resume", "token": server.accounts.create_session(name)}))
        value = await self.receive(socket, "resume_success")
        self.assertEqual(value["username"], name)

    async def request_board(self, socket, **request):
        await socket.send(json.dumps({"type": "get_rating_leaderboard", **request}))
        return await self.receive(socket, "rating_leaderboard")

    async def test_real_poker_action_and_public_stats_survive_reconnect_and_reinitialization(self):
        import websockets

        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
            async with websockets.connect(uri) as alice, websockets.connect(uri) as bob:
                await self.login(alice, "alice")
                await self.login(bob, "bob")
                before = await self.request_board(alice, request_id="before")
                self.assertEqual(before["request_id"], "before")
                self.assertIsNone(before["self"]["holdem_stats"]["vpip"])
                room = self.room()
                await room.start()
                await alice.send(json.dumps({"type": "poker_action", "action": "fold"}))
                result = await self.receive(alice, "hand_result")
                self.assertEqual(result["ratings"]["alice"]["delta"], -1)
                await self.receive(bob, "hand_result")
                data = await self.request_board(bob, request_id="after", username="alice", limit=999)
                self.assertEqual(data["request_id"], "after")
                self.assertEqual(data["self"]["username"], "bob")
                self.assertEqual(data["self"]["holdem_stats"]["wins"], 1)
                entries = {entry["username"]: entry for entry in data["entries"]}
                self.assertEqual(entries["alice"]["holdem_stats"]["manual_folds"], 1)
                self.assertEqual(entries["alice"]["holdem_stats"]["net_profit"], -5)
                self.assertEqual(set(entries["alice"]["holdem_stats"]), PUBLIC_FIELDS)
                # hand_result 在动作处理函数返回前广播；用同一连接请求作完成屏障。
                await self.request_board(alice, request_id="action-completed")
                await server.rooms.dissolve_room(room, "protocol cleanup")
            init_db(server.database)
            async with websockets.connect(uri) as reconnected:
                await self.login(reconnected, "alice")
                persisted = await self.request_board(reconnected, request_id="persisted")
                self.assertEqual(persisted["self"], entries["alice"])
                self.assertEqual(persisted["stats_since"], before["stats_since"])
        self.assertEqual(server.hub.clients, {})
        self.assertTrue(all(not room.timers for room in self.rooms))

    async def test_real_protocol_pagination_invalid_parameters_and_login_gate(self):
        import websockets

        LeaderboardTests.add_ranked_players(self)
        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
            async with websockets.connect(uri) as socket:
                await socket.send(json.dumps({"type": "get_rating_leaderboard", "request_id": "unauthorized"}))
                await socket.send(json.dumps({"type": "list_rooms"}))
                # 同连接顺序处理，以公开 list_rooms 作屏障；不靠超时猜测无响应。
                while True:
                    message = await asyncio.wait_for(socket.recv(), 5)
                    value = json.loads(message)
                    self.assertNotEqual(value["type"], "rating_leaderboard")
                    if value["type"] == "room_list":
                        break
                await self.login(socket, "alice")
                for offset, expected in ((0, 0), (199, 100), (999999, 200), (True, 0), ({"bad": 1}, 0)):
                    data = await self.request_board(socket, offset=offset, request_id=f"offset-{offset}")
                    self.assertEqual(data["offset"], expected)
                    self.assertEqual(data["request_id"], f"offset-{offset}")
                    self.assertEqual(data["self"]["rank"], 206)
                    self.assertEqual(data["limit"], 100)
                data = await self.request_board(socket, offset="100", request_id={"bad": "id"}, username="bob")
                self.assertEqual(data["offset"], 0)
                self.assertIsNone(data["request_id"])
                self.assertEqual(data["self"]["username"], "alice")

    async def test_real_leave_room_protocol_records_fold_once_then_final_settlement(self):
        import websockets

        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
            async with websockets.connect(uri) as socket:
                await self.login(socket, "bob")
                room = self.room(names=NAMES[:3])
                await room.start()
                await socket.send(json.dumps({"type": "leave_room"}))
                await self.receive(socket, "room_closed")
                left = await self.request_board(socket, request_id="left")
                self.assertEqual(left["self"]["holdem_stats"]["leave_folds"], 1)
                self.assertEqual(left["self"]["holdem_stats"]["hands"], 1)
                self.assertEqual(left["self"]["holdem_stats"]["net_profit"], -5)
                await room.perform_action(room.game["to_act"], "fold")
                done = await self.request_board(socket, request_id="done")
                self.assertEqual(done["self"]["holdem_stats"], left["self"]["holdem_stats"])
                self.assertEqual(done["self"]["rating"], left["self"]["rating"])
                # 其他玩家结算后全局名次可变，但退出者自己的分数和统计不能再累加。
                self.assertEqual(self.count(), 3)
                await server.rooms.dissolve_room(room, "protocol cleanup")


if __name__ == "__main__":
    unittest.main(verbosity=2)
