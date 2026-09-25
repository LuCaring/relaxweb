"""臭企鹅兑换、抽奖碎片与自动农务回归测试。"""
import sqlite3
import unittest
from unittest.mock import patch

from estate.catalog import CROPS, FISHING_TREASURES, SKIN_FRAGMENT_ITEM, collectible_item, crop_item, grow_seconds
from estate.farming import auto_harvest_penguin
from estate.lottery import draw_lottery, lottery_history
from estate.pets import buy_or_upgrade_penguin, set_active_pet
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate, estate_state
from estate.visits import steal_crop


NOW = 2_000_000_000


def adjust_coins(conn, username, delta, kind, detail="", ref=""):
    balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0] + delta
    if balance < 0:
        raise ValueError("金币不足")
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance, username))
    conn.execute("INSERT INTO coin_transactions(username,amount,balance,kind,detail,created_at,ref) "
                 "VALUES (?,?,?,?,?,?,?)", (username, delta, balance, kind, detail, NOW, ref))
    return balance


class PenguinTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("CREATE TABLE coin_transactions(username TEXT,amount REAL,balance REAL,kind TEXT,detail TEXT,created_at INTEGER,ref TEXT)")
        self.conn.executemany("INSERT INTO users VALUES (?,?)", (("alice", 200000), ("bob", 200000)))
        init_estate(self.conn)
        for username in ("alice", "bob"):
            ensure_estate(self.conn, username, NOW)
            self.conn.execute("UPDATE estate_profiles SET level=20 WHERE username=?", (username,))

    def tearDown(self):
        self.conn.close()

    def test_full_collection_awards_one_fragment_and_replay_is_idempotent(self):
        self.conn.executemany("INSERT INTO estate_collections VALUES (?,?)",
                              (("alice", collectible_item(key)) for key in FISHING_TREASURES))
        with patch("estate.lottery.secrets.randbelow", return_value=5):
            result = draw_lottery(self.conn, "alice", "fragment-draw-0001", NOW, adjust_coins)
            replay = draw_lottery(self.conn, "alice", "fragment-draw-0001", NOW, adjust_coins)
        self.assertEqual((result["item_id"], result["quantity"]), (SKIN_FRAGMENT_ITEM, 1))
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.conn.execute("SELECT quantity FROM estate_inventory WHERE username='alice' AND item_id=?",
                                           (SKIN_FRAGMENT_ITEM,)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 197000)

    def test_redeem_consumes_28_fragments_then_upgrade_prices(self):
        self.conn.execute("INSERT INTO estate_inventory VALUES (?,?,28)", ("alice", SKIN_FRAGMENT_ITEM))
        first = buy_or_upgrade_penguin(self.conn, "alice", "penguin-redeem-0001", NOW, adjust_coins)
        self.assertEqual((first["penguin_level"], first["cost"], first["fragments_spent"]), (1, 0, 28))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM estate_inventory WHERE item_id=?", (SKIN_FRAGMENT_ITEM,)).fetchone())
        for level, price in ((2, 20000), (3, 40000), (4, 60000)):
            result = buy_or_upgrade_penguin(self.conn, "alice", f"penguin-upgrade-{level}", NOW, adjust_coins)
            self.assertEqual((result["penguin_level"], result["cost"]), (level, price))
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 80000)

    def test_delayed_harvest_full_warehouse_and_auto_replant(self):
        self.conn.execute("UPDATE estate_profiles SET penguin_level=1,active_pet='stinky_penguin' WHERE username='alice'")
        ready = NOW - 80 * 60
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=0",
                          (ready - 1000, ready))
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW - 1, adjust_coins), 0)
        self.conn.execute("INSERT INTO estate_inventory VALUES ('alice','seed:wheat',1000)")
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 0)
        self.conn.execute("DELETE FROM estate_inventory WHERE item_id='seed:wheat'")
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 1)
        self.assertEqual(self.conn.execute("SELECT quantity FROM estate_inventory WHERE item_id=?", (crop_item("wheat"),)).fetchone()[0], 1)
        self.assertIsNone(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=0").fetchone()[0])

        self.conn.execute("UPDATE estate_profiles SET penguin_level=4 WHERE username='alice'")
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=1",
                          (NOW - 3000, NOW - 1800))
        before = self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0]
        estate_state(self.conn, "alice", NOW, adjust_coins)
        self.assertEqual(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=1").fetchone()[0], "wheat")
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], before - CROPS["wheat"]["seed_price"])

    def test_penguin_disables_doudou_defense(self):
        self.conn.execute("UPDATE estate_profiles SET pet_level=4,penguin_level=1,active_pet='stinky_penguin' WHERE username='alice'")
        self.conn.execute("UPDATE estate_profiles SET level=3 WHERE username='bob'")
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=0",
                          (NOW - 1000, NOW - 10))
        result = steal_crop(self.conn, "bob", "penguin-visit-0001", "alice", 0, NOW,
                            adjust_coins, lambda low, high: 1)
        self.assertEqual(result["outcome"], "stolen")

    def test_level_four_leaves_plot_empty_when_seed_unaffordable(self):
        self.conn.execute("UPDATE estate_profiles SET penguin_level=4,active_pet='stinky_penguin' WHERE username='alice'")
        self.conn.execute("UPDATE users SET coins=0 WHERE username='alice'")
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=0",
                          (NOW - 3000, NOW - 1800))
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 1)
        self.assertIsNone(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=0").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 0)

    def test_level_four_catches_up_offline_cycles(self):
        self.conn.execute("UPDATE estate_profiles SET penguin_level=4,active_pet='stinky_penguin',penguin_active_at=? WHERE username='alice'",
                          (NOW - 100000,))
        cycle = 30 * 60 + grow_seconds("wheat", 1)
        first_ready = NOW - 30 * 60 - 3 * cycle
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=0",
                          (first_ready - grow_seconds("wheat", 1), first_ready))
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 4)
        self.assertEqual(self.conn.execute("SELECT quantity FROM estate_inventory WHERE username='alice' AND item_id=?",
                                           (crop_item("wheat"),)).fetchone()[0], 4)
        self.assertEqual(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=0").fetchone()[0], "wheat")

    def test_level_four_does_not_purchase_lottery_only_seed(self):
        self.conn.execute("UPDATE estate_profiles SET penguin_level=4,active_pet='stinky_penguin' WHERE username='alice'")
        self.conn.execute("UPDATE estate_plots SET crop_id='legendary_flower',planted_at=?,ready_at=? WHERE username='alice' AND plot_index=0",
                          (NOW - 3000, NOW - 1800))
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 1)
        self.assertIsNone(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=0").fetchone()[0])

    def test_owned_pets_switch_and_only_active_ability_runs(self):
        self.conn.execute("UPDATE estate_profiles SET pet_level=4,penguin_level=1,"
                          "active_pet='stinky_penguin' WHERE username='alice'")
        ready = NOW - 80 * 60
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                          "WHERE username='alice' AND plot_index=0", (ready - 1000, ready))
        selected = set_active_pet(self.conn, "alice", "select-doudou-0001", "doudou", NOW)
        self.assertEqual(selected["pet"], "doudou")
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW + 60, adjust_coins), 0)
        self.conn.execute("UPDATE estate_profiles SET level=3 WHERE username='bob'")
        defended = steal_crop(self.conn, "bob", "doudou-visit-0001", "alice", 0, NOW + 60,
                              adjust_coins, lambda low, high: 1)
        self.assertEqual(defended["outcome"], "defended")
        replay = set_active_pet(self.conn, "alice", "select-doudou-0001", "doudou", NOW)
        self.assertTrue(replay["replayed"])
        set_active_pet(self.conn, "alice", "select-penguin-0001", "stinky_penguin", NOW + 120)
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW + 119, adjust_coins), 0)
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW + 120, adjust_coins), 1)
        self.assertEqual(estate_state(self.conn, "alice", NOW + 120)["profile"]["active_pet"],
                         "stinky_penguin")
        with self.assertRaises(EstateError):
            set_active_pet(self.conn, "bob", "select-unowned-0001", "stinky_penguin", NOW)

    def test_lottery_history_keeps_latest_100_once_per_draw(self):
        self.conn.execute("UPDATE users SET coins=1000000 WHERE username='alice'")
        with patch("estate.lottery.secrets.randbelow", return_value=0):
            for index in range(101):
                draw_lottery(self.conn, "alice", f"history-draw-{index:04d}", NOW + index,
                             adjust_coins)
            draw_lottery(self.conn, "alice", "history-draw-0100", NOW + 100,
                         adjust_coins)
        rows = lottery_history(self.conn, "alice")
        self.assertEqual(len(rows), 100)
        self.assertEqual((rows[0]["created_at"], rows[-1]["created_at"]),
                         (NOW + 100, NOW + 1))
        self.assertEqual(rows[0]["award"], "thanks")
        self.assertEqual(lottery_history(self.conn, "bob"), [])


if __name__ == "__main__":
    unittest.main()
