#!/usr/bin/env python3
"""休闲庄园工具、钓鱼和矿场领域测试。"""
import sqlite3
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.activities import (
    make_board, pick_fishing_catch, buy_tool, finish_fishing, finish_mining, mine_cell, repair_tool,
    start_fishing, start_mining, upgrade_tool,
)
from estate.catalog import FISHING_TREASURES, MINERALS, bait_item
from estate.farming import buy
from estate.schema import init_estate
from estate.store import EstateError, estate_state

NOW = 2_000_000_000


def adjust_coins(conn, username, delta, kind, detail="", ref=""):
    balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0] + delta
    if balance < 0:
        raise ValueError("金币不足")
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance, username))
    conn.execute("INSERT INTO coin_transactions(username,amount,balance,kind,detail,created_at,ref) "
                 "VALUES (?,?,?,?,?,?,?)", (username, delta, balance, kind, detail, NOW, ref))
    return balance


def winning_trace(pattern, rod_level=1):
    factor = {1: 1.0, 2: .82, 3: .68}[rod_level]
    tension, progress, trace = .18, .08, []
    for index in range(360):
        force = pattern[min(len(pattern) - 1, index // 10)]
        held = tension < .64
        trace.append(held)
        if held:
            tension += .026 * (.68 + force) * factor
            progress += .013 * (1.12 - force * .3)
        else:
            tension = max(0, tension - .045)
            progress = max(0, progress - .0035 * (.5 + force))
        if progress >= 1:
            return trace
    return trace


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("CREATE TABLE coin_transactions(id INTEGER PRIMARY KEY,username TEXT,amount REAL,balance REAL,kind TEXT,detail TEXT,created_at INTEGER,ref TEXT)")
        self.conn.execute("INSERT INTO users VALUES ('alice',10000)")
        init_estate(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def call(self, function, *args):
        with self.conn:
            return function(self.conn, *args)

    def test_tools_buy_use_repair_and_upgrade(self):
        bought = self.call(buy_tool, "alice", "buy-rod-0001", "rod", NOW, adjust_coins)
        self.assertEqual(bought["durability"], 20)
        self.conn.execute("UPDATE estate_tools SET durability=5 WHERE username='alice' AND tool_type='rod'")
        repaired = self.call(repair_tool, "alice", "repair-rod-01", "rod", NOW, adjust_coins)
        self.assertEqual(repaired["durability"], 20)
        self.conn.execute("UPDATE estate_profiles SET level=3 WHERE username='alice'")
        upgraded = self.call(upgrade_tool, "alice", "upgrade-rod-1", "rod", NOW, adjust_coins)
        self.assertEqual((upgraded["level"], upgraded["durability"]), (2, 35))
        replay = self.call(upgrade_tool, "alice", "upgrade-rod-1", "rod", NOW, adjust_coins)
        self.assertTrue(replay["replayed"])

    def test_fishing_consumes_cost_and_awards_verified_fish(self):
        self.call(buy_tool, "alice", "buy-rod-0002", "rod", NOW, adjust_coins)
        self.call(buy, "alice", "buy-bait-001", "bait", "worm", 2, NOW, adjust_coins)
        started = self.call(start_fishing, "alice", "fish-start-01", "worm", NOW)
        self.assertEqual(started["bait_id"], "worm")
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual(next(i for i in state["inventory"] if i["id"] == bait_item("worm"))["quantity"], 1)
        self.assertEqual(state["tools"]["rod"]["durability"], 19)
        self.assertEqual(state["profile"]["warehouse_reserved"], 1)
        result = self.call(finish_fishing, "alice", "fish-done-001", started["session_id"],
                           winning_trace(started["pattern"]), NOW + 25)
        self.assertEqual(result["outcome"], "caught")
        self.assertIn("rarity", result)
        state = self.call(estate_state, "alice", NOW + 25)
        self.assertEqual(state["profile"]["warehouse_reserved"], 0)
        self.assertTrue(any(item["kind"] == "fish" for item in state["inventory"]))

    def test_failed_fishing_does_not_refund(self):
        self.call(buy_tool, "alice", "buy-rod-0003", "rod", NOW, adjust_coins)
        self.call(buy, "alice", "buy-bait-002", "bait", "worm", 1, NOW, adjust_coins)
        started = self.call(start_fishing, "alice", "fish-start-02", "worm", NOW)
        result = self.call(finish_fishing, "alice", "fish-done-002", started["session_id"],
                           [True] * 100, NOW + 10)
        self.assertEqual(result["outcome"], "snapped")
        state = self.call(estate_state, "alice", NOW + 10)
        self.assertFalse(any(item["kind"] == "bait" for item in state["inventory"]))
        self.assertEqual(state["tools"]["rod"]["durability"], 19)

    def test_fishing_reuses_bait_slot_in_full_warehouse(self):
        self.call(buy_tool, "alice", "full-rod-0001", "rod", NOW, adjust_coins)
        self.call(buy, "alice", "full-bait-001", "bait", "worm", 100, NOW, adjust_coins)
        started = self.call(start_fishing, "alice", "full-start-01", "worm", NOW)
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual(state["profile"]["warehouse_used"], 100)
        self.call(finish_fishing, "alice", "full-finish-1", started["session_id"],
                  winning_trace(started["pattern"]), NOW + 25)
        self.assertEqual(self.call(estate_state, "alice", NOW + 25)["profile"]["warehouse_used"], 100)

    def test_locked_bait_cannot_bypass_level_check(self):
        self.call(buy_tool, "alice", "lock-rod-0001", "rod", NOW, adjust_coins)
        self.conn.execute("INSERT INTO estate_inventory VALUES ('alice','bait:glow_grub',1)")
        self.conn.commit()
        with self.assertRaises(EstateError) as error:
            self.call(start_fishing, "alice", "lock-start-01", "glow_grub", NOW)
        self.assertEqual(error.exception.code, "level_locked")
        self.assertEqual(self.call(estate_state, "alice", NOW)["tools"]["rod"]["durability"], 20)

    def test_mining_reserves_maximum_loot_and_awards_xp_once(self):
        self.call(buy_tool, "alice", "max-pick-0001", "pickaxe", NOW, adjust_coins)
        self.conn.execute("UPDATE estate_tools SET level=3,durability=55 WHERE username='alice'")
        self.conn.execute("UPDATE estate_profiles SET level=6 WHERE username='alice'")
        self.conn.execute("INSERT INTO estate_inventory VALUES ('alice','seed:wheat',85)")
        self.conn.commit()
        with self.assertRaises(EstateError) as error:
            self.call(start_mining, "alice", "max-block-01", 3, NOW)
        self.assertEqual(error.exception.code, "warehouse_full")
        self.conn.execute("UPDATE estate_inventory SET quantity=84 WHERE username='alice'")
        self.conn.commit()
        with patch("estate.activities.make_board", return_value=["extra", "extra"] + ["copper"] * 23):
            started = self.call(start_mining, "alice", "max-start-01", 3, NOW)
        self.assertEqual(self.call(estate_state, "alice", NOW)["profile"]["warehouse_used"], 100)
        for index in range(18):
            mined = self.call(mine_cell, "alice", f"max-cell-{index:04}", started["run_id"], index, NOW)
        self.assertTrue(mined["finished"])
        self.assertEqual(mined["result"]["loot"], {"copper": 16})
        self.assertEqual(mined["result"]["xp_awarded"], 16 * MINERALS["copper"]["xp"])
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual(state["profile"]["warehouse_used"], 100)
        self.assertEqual(state["profile"]["warehouse_reserved"], 0)
        self.call(finish_mining, "alice", "max-finish-1", started["run_id"], NOW)
        self.assertEqual(self.call(estate_state, "alice", NOW)["profile"]["xp"], state["profile"]["xp"])

    def test_best_gear_can_reach_rarest_collectible(self):
        class TreasureRng:
            @staticmethod
            def random():
                return 0

            @staticmethod
            def choices(items, weights, k):
                return [items[-1]]

        catch_id, catch = pick_fishing_catch(
            TreasureRng(), {"rarity_bonus": 1}, {"level": 3},
        )
        self.assertEqual(catch_id, "treasure:xiaopang_underwear")
        self.assertEqual(catch["name"], "一条不知道是谁的内裤")

    def test_caught_collectible_is_kept_and_not_sellable(self):
        self.call(buy_tool, "alice", "buy-rod-rare", "rod", NOW, adjust_coins)
        self.call(buy, "alice", "buy-bait-rare", "bait", "worm", 1, NOW, adjust_coins)
        rare = FISHING_TREASURES["xiaopang_underwear"]
        with patch("estate.activities.pick_fishing_catch",
                   return_value=("treasure:xiaopang_underwear", rare)):
            started = self.call(start_fishing, "alice", "fish-start-rare", "worm", NOW)
        result = self.call(
            finish_fishing, "alice", "fish-done-rare", started["session_id"],
            winning_trace(started["pattern"]), NOW + 25,
        )
        self.assertEqual(result["catch_kind"], "collectible")
        self.assertEqual(result["catch_name"], "一条不知道是谁的内裤")
        item = next(i for i in self.call(estate_state, "alice", NOW + 25)["inventory"]
                    if i["id"] == "collectible:xiaopang_underwear")
        self.assertFalse(item["sellable"])

    def test_mining_is_hidden_idempotent_and_settles(self):
        self.call(buy_tool, "alice", "buy-pick-001", "pickaxe", NOW, adjust_coins)
        initial_durability = self.call(estate_state, "alice", NOW)["tools"]["pickaxe"]["durability"]
        started = self.call(start_mining, "alice", "mine-start-01", 1, NOW)
        self.assertNotIn("board", started)
        first = self.call(mine_cell, "alice", "mine-cell-001", started["run_id"], 0, NOW)
        replay = self.call(mine_cell, "alice", "mine-cell-001", started["run_id"], 0, NOW)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["outcome"], replay["outcome"])
        result = self.call(finish_mining, "alice", "mine-finish-1", started["run_id"], NOW)
        self.assertTrue(result["finished"])
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual(state["profile"]["warehouse_reserved"], 0)
        self.assertEqual(state["tools"]["pickaxe"]["durability"], initial_durability - 1)
        with self.assertRaises(EstateError):
            self.call(mine_cell, "alice", "mine-cell-002", started["run_id"], 1, NOW)

    def test_deeper_mines_have_more_bombs(self):
        for level, expected in ((1, 1), (2, 2), (3, 3)):
            board = make_board(1200 + level, level)
            self.assertEqual(board.count("bomb"), expected)
            self.assertEqual(len(board), 25)

    def test_bomb_ends_run_and_keeps_existing_loot(self):
        self.call(buy_tool, "alice", "buy-pick-bomb", "pickaxe", NOW, adjust_coins)
        board = ["copper", "bomb"] + ["empty"] * 23
        with patch("estate.activities.make_board", return_value=board):
            started = self.call(start_mining, "alice", "mine-start-bomb", 1, NOW)
        first = self.call(mine_cell, "alice", "mine-safe-bomb", started["run_id"], 0, NOW)
        self.assertEqual(first["loot"], {"copper": 1})
        exploded = self.call(mine_cell, "alice", "mine-hit-bomb", started["run_id"], 1, NOW)
        self.assertEqual(exploded["outcome"], "bomb")
        self.assertTrue(exploded["finished"])
        self.assertTrue(exploded["exploded"])
        self.assertEqual(exploded["result"]["loot"], {"copper": 1})
        self.assertEqual(exploded["result"]["reason"], "bomb")
        self.assertEqual(exploded["result"]["xp_awarded"], MINERALS["copper"]["xp"])
        self.assertEqual(self.call(estate_state, "alice", NOW)["profile"]["warehouse_reserved"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
