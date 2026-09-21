#!/usr/bin/env python3
"""休闲庄园建档、快照和经济事务测试。"""
import sqlite3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.catalog import CROPS, crop_item, grow_seconds, seed_item
from estate.farming import buy, harvest, plant, sell, sell_all
from estate.pets import buy_or_upgrade_pet
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate, estate_state


NOW = 2_000_000_000


def adjust_coins(conn, username, delta, kind, detail="", ref=""):
    row = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()
    balance = round(float(row[0]) + float(delta), 2)
    if balance < 0:
        raise ValueError("金币不足")
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance, username))
    conn.execute(
        "INSERT INTO coin_transactions(username,amount,balance,kind,detail,created_at,ref) "
        "VALUES (?,?,?,?,?,?,?)", (username, delta, balance, kind, detail, NOW, ref),
    )
    return balance


class EstateServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL NOT NULL)")
        self.conn.execute("""
            CREATE TABLE coin_transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, amount REAL,
                balance REAL, kind TEXT, detail TEXT, created_at INTEGER, ref TEXT)
        """)
        self.conn.executemany(
            "INSERT INTO users(username,coins) VALUES (?,?)",
            (("alice", 10_000), ("bob", 500)),
        )
        init_estate(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def call(self, fn, *args, **kwargs):
        with self.conn:
            return fn(self.conn, *args, **kwargs)

    def buy_seed(self, crop="wheat", count=1, request="buy-seed-0001"):
        return self.call(buy, "alice", request, "seed", crop, count, NOW, adjust_coins)

    def test_lazy_profile_snapshot_and_user_isolation(self):
        first = self.call(estate_state, "alice", NOW)
        second = self.call(estate_state, "alice", NOW + 20)
        other = self.call(estate_state, "bob", NOW)
        self.assertEqual(first["profile"]["plot_count"], 4)
        self.assertEqual(len(first["plots"]), 12)
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(first["profile"]["warehouse_used"], 0)
        self.assertEqual(other["coins"], 500)

    def test_buy_seed_is_atomic_and_idempotent(self):
        result = self.buy_seed(count=3)
        replay = self.buy_seed(count=3)
        state = self.call(estate_state, "alice", NOW)
        self.assertFalse(result["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(state["coins"], 9940)
        self.assertEqual(state["profile"]["warehouse_used"], 3)
        self.assertEqual(len(self.conn.execute("SELECT * FROM coin_transactions").fetchall()), 1)
        with self.assertRaisesRegex(EstateError, "其他操作") as conflict:
            self.buy_seed(count=2)
        self.assertEqual(conflict.exception.code, "request_conflict")

    def test_failed_purchase_rolls_back_action_and_ledger(self):
        with self.assertRaises(EstateError) as failed:
            self.call(buy, "bob", "buy-seed-0002", "seed", "pumpkin", 3,
                      NOW, adjust_coins)
        self.assertEqual(failed.exception.code, "level_locked")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0], 0
        )
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='bob'").fetchone()[0], 500)

    def test_buy_and_upgrade_doudou(self):
        result = self.call(buy_or_upgrade_pet, "alice", "pet-buy-0001", NOW, adjust_coins)
        self.assertEqual((result["pet_level"], result["cost"]), (1, 10000.0))
        self.conn.execute("UPDATE users SET coins=200000 WHERE username='alice'")
        expected = ((2, 20000.0), (3, 40000.0), (4, 100000.0))
        for index, pair in enumerate(expected, 2):
            result = self.call(buy_or_upgrade_pet, "alice", f"pet-upgrade-000{index}", NOW, adjust_coins)
            self.assertEqual((result["pet_level"], result["cost"]), pair)
        with self.assertRaises(EstateError) as error:
            self.call(buy_or_upgrade_pet, "alice", "pet-upgrade-max", NOW, adjust_coins)
        self.assertEqual(error.exception.code, "pet_max_level")

    def test_plant_mature_harvest_and_sell(self):
        self.buy_seed()
        planted = self.call(plant, "alice", "plant-0001", 0, "wheat", NOW)
        self.assertEqual(planted["ready_at"], NOW + CROPS["wheat"]["grow_seconds"])
        growing = self.call(estate_state, "alice", NOW + 10)["plots"][0]
        self.assertFalse(growing["mature"])
        with self.assertRaises(EstateError) as early:
            self.call(harvest, "alice", "harvest-early", 0, NOW + 10)
        self.assertEqual(early.exception.code, "crop_growing")
        ready_at = planted["ready_at"]
        picked = self.call(harvest, "alice", "harvest-0001", 0, ready_at)
        self.assertEqual(picked["quantity"], 1)
        state = self.call(estate_state, "alice", ready_at)
        self.assertIsNone(state["plots"][0]["crop_id"])
        self.assertEqual(next(i for i in state["inventory"] if i["id"] == crop_item("wheat"))["quantity"], 1)
        sold = self.call(sell, "alice", "sell-wheat", crop_item("wheat"), 1,
                         ready_at, adjust_coins)
        self.assertEqual(sold["earned"], CROPS["wheat"]["sell_price"])
        self.assertEqual(self.call(estate_state, "alice", ready_at)["coins"], 10010)

    def test_land_upgrade_changes_only_future_planting(self):
        self.buy_seed(count=2)
        self.call(plant, "alice", "plant-old-0001", 0, "wheat", NOW)
        old_ready = self.call(estate_state, "alice", NOW)["plots"][0]["ready_at"]
        with self.assertRaises(EstateError) as busy:
            self.call(buy, "alice", "land-busy-0001", "land", 0, 1, NOW, adjust_coins)
        self.assertEqual(busy.exception.code, "plot_busy")
        self.call(harvest, "alice", "harvest-old-1", 0, old_ready)
        self.conn.execute("UPDATE estate_profiles SET level=2 WHERE username='alice'")
        self.call(buy, "alice", "land-upgrade-1", "land", 0, 1, old_ready, adjust_coins)
        planted = self.call(plant, "alice", "plant-fast-001", 0, "wheat", old_ready)
        self.assertEqual(planted["ready_at"] - old_ready, grow_seconds("wheat", 2))

    def test_warehouse_full_keeps_mature_crop_on_plot(self):
        self.buy_seed()
        planted = self.call(plant, "alice", "plant-full-01", 0, "wheat", NOW)
        self.conn.execute(
            "INSERT INTO estate_inventory(username,item_id,quantity) VALUES ('alice','seed:carrot',100)"
        )
        with self.assertRaises(EstateError) as full:
            self.call(harvest, "alice", "harvest-full", 0, planted["ready_at"])
        self.assertEqual(full.exception.code, "warehouse_full")
        plot = self.call(estate_state, "alice", planted["ready_at"])["plots"][0]
        self.assertEqual(plot["crop_id"], "wheat")
        self.assertTrue(plot["mature"])

    def test_leveling_plot_and_warehouse_upgrades(self):
        self.call(ensure_estate, "alice", NOW)
        self.conn.execute("UPDATE estate_profiles SET level=2,xp=198 WHERE username='alice'")
        self.buy_seed(request="buy-level-001")
        planted = self.call(plant, "alice", "plant-level1", 0, "wheat", NOW)
        picked = self.call(harvest, "alice", "harvest-level", 0, planted["ready_at"])
        self.assertEqual(picked["level"], 3)
        self.assertEqual(self.call(estate_state, "alice", planted["ready_at"])["profile"]["xp"], 3)
        self.call(buy, "alice", "buy-plot-0001", "plot", 4, 1, NOW, adjust_coins)
        self.call(buy, "alice", "warehouse-up1", "warehouse", 1, 1, NOW, adjust_coins)
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual(state["profile"]["plot_count"], 5)
        self.assertEqual(state["profile"]["warehouse_capacity"], 200)

    def test_sell_all_leaves_seeds(self):
        self.call(ensure_estate, "alice", NOW)
        self.conn.executemany(
            "INSERT INTO estate_inventory(username,item_id,quantity) VALUES ('alice',?,?)",
            ((seed_item("wheat"), 4), (crop_item("wheat"), 3), (crop_item("carrot"), 2)),
        )
        result = self.call(sell_all, "alice", "sell-all-001", NOW, adjust_coins)
        self.assertEqual(len(result["sold"]), 2)
        state = self.call(estate_state, "alice", NOW)
        self.assertEqual([(i["id"], i["quantity"]) for i in state["inventory"]],
                         [(seed_item("wheat"), 4)])

    def test_invalid_request_and_non_sellable_item(self):
        with self.assertRaises(EstateError) as invalid:
            self.call(buy, "alice", "short", "seed", "wheat", 1, NOW, adjust_coins)
        self.assertEqual(invalid.exception.code, "invalid_request")
        self.buy_seed()
        with self.assertRaises(EstateError) as seed_sale:
            self.call(sell, "alice", "sell-seed-01", seed_item("wheat"), 1,
                      NOW, adjust_coins)
        self.assertEqual(seed_sale.exception.code, "not_sellable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
