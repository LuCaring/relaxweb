"""臭企鹅兑换、抽奖碎片与自动农务回归测试。"""
import sqlite3
import unittest
from unittest.mock import patch

from estate.catalog import CROPS, FISHING_TREASURES, GPU_MODELS, SKIN_FRAGMENT_ITEM, collectible_item, crop_item, grow_seconds
from estate.farming import auto_harvest_penguin, harvest, sell
from estate.lottery import draw_lottery, lottery_history
from estate.pets import auto_fertilize_maodie, buy_or_upgrade_maodie, buy_or_upgrade_penguin, set_active_pet
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate, estate_state
from estate.visits import public_estate_state, steal_crop


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
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 200000)

    def test_redeem_consumes_28_fragments_then_upgrade_prices(self):
        self.conn.execute("INSERT INTO estate_inventory VALUES (?,?,28)", ("alice", SKIN_FRAGMENT_ITEM))
        first = buy_or_upgrade_penguin(self.conn, "alice", "penguin-redeem-0001", NOW, adjust_coins)
        self.assertEqual((first["penguin_level"], first["cost"], first["fragments_spent"]), (1, 0, 28))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM estate_inventory WHERE item_id=?", (SKIN_FRAGMENT_ITEM,)).fetchone())
        for level, price in ((2, 20000), (3, 40000), (4, 60000)):
            result = buy_or_upgrade_penguin(self.conn, "alice", f"penguin-upgrade-{level}", NOW, adjust_coins)
            self.assertEqual((result["penguin_level"], result["cost"]), (level, price))
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 80000)

    def test_mystery_seed_is_even_and_gpu_model_is_revealed_on_harvest(self):
        with patch("estate.lottery.secrets.randbelow", side_effect=[4, 0]):
            flower = draw_lottery(self.conn, "alice", "mystery-flower-1", NOW, adjust_coins)
        with patch("estate.lottery.secrets.randbelow", side_effect=[4, 1]):
            gpu = draw_lottery(self.conn, "alice", "mystery-gpu-0001", NOW, adjust_coins)
        self.assertEqual(flower["item_id"], "seed:legendary_flower")
        self.assertEqual(gpu["item_id"], "seed:gpu_fruit")
        self.assertNotIn("gpu", str(gpu.get("model", "")))
        self.conn.execute("UPDATE estate_plots SET crop_id='gpu_fruit',planted_at=?,ready_at=? "
                          "WHERE username='alice' AND plot_index=0", (NOW - 18001, NOW - 1))
        with patch("estate.farming.secrets.randbelow", return_value=9):
            result = harvest(self.conn, "alice", "gpu-harvest-0001", 0, NOW)
        self.assertEqual(result["item_id"], "gpu:rtx_5090")
        self.assertEqual(sell(self.conn, "alice", "gpu-sell-00001", result["item_id"], 1,
                              NOW, adjust_coins)["earned"], 30000)
        self.assertEqual(sum(value[1] for value in GPU_MODELS.values()) / 10, 5690)

    def test_maodie_fertilizes_distinct_unripe_plots_without_inventory(self):
        self.conn.execute("INSERT INTO estate_inventory VALUES ('alice',?,28)",
                          (SKIN_FRAGMENT_ITEM,))
        redeemed = buy_or_upgrade_maodie(self.conn, "alice", "maodie-redeem-1", NOW,
                                          adjust_coins)
        self.assertEqual((redeemed["maodie_level"], redeemed["cost"]), (1, 0))
        for level, cost in ((2, 20000), (3, 40000), (4, 60000)):
            result = buy_or_upgrade_maodie(self.conn, "alice", f"maodie-upgrade-{level}",
                                            NOW, adjust_coins)
            self.assertEqual((result["maodie_level"], result["cost"]), (level, cost))
        for index in range(4):
            self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                              "WHERE username='alice' AND plot_index=?",
                              (NOW, NOW + 7200, index))
        with patch("estate.pets.secrets.randbelow", return_value=0):
            self.assertEqual(auto_fertilize_maodie(self.conn, "alice", NOW + 5400), 4)
        times = [row[0] for row in self.conn.execute("SELECT ready_at FROM estate_plots "
                 "WHERE username='alice' AND plot_index<4 ORDER BY plot_index")]
        self.assertEqual(times, [NOW + 5400] * 4)
        self.assertEqual(auto_fertilize_maodie(self.conn, "alice", NOW + 5401), 0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM estate_inventory WHERE "
                                            "item_id='supply:fertilizer'").fetchone())

    def test_penguin_replants_from_stock_before_buying(self):
        self.conn.execute("UPDATE estate_profiles SET penguin_level=4,active_pet='stinky_penguin' "
                          "WHERE username='alice'")
        self.conn.execute("INSERT INTO estate_inventory VALUES ('alice','seed:wheat',1)")
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                          "WHERE username='alice' AND plot_index=0",
                          (NOW - 3000, NOW - 1800))
        before = self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0]
        self.assertEqual(auto_harvest_penguin(self.conn, "alice", NOW, adjust_coins), 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM estate_inventory WHERE "
                                            "item_id='seed:wheat'").fetchone())
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], before)

    def test_existing_profile_migrates_to_maodie_and_keeps_progress(self):
        old = sqlite3.connect(":memory:")
        try:
            old.execute("CREATE TABLE estate_profiles (username TEXT PRIMARY KEY COLLATE NOCASE,"
                        "skin_id TEXT NOT NULL DEFAULT 'berry', level INTEGER NOT NULL DEFAULT 1,"
                        "xp INTEGER NOT NULL DEFAULT 0, warehouse_level INTEGER NOT NULL DEFAULT 1,"
                        "plot_count INTEGER NOT NULL DEFAULT 0,"
                        "reserved_capacity INTEGER NOT NULL DEFAULT 0, pet_level INTEGER NOT NULL DEFAULT 0,"
                        "penguin_level INTEGER NOT NULL DEFAULT 0, penguin_active_at INTEGER NOT NULL DEFAULT 0,"
                        "active_pet TEXT NOT NULL DEFAULT 'doudou' CHECK(active_pet IN "
                        "('doudou','stinky_penguin')), version INTEGER NOT NULL DEFAULT 1,"
                        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
            old.execute("INSERT INTO estate_profiles(username,level,xp,plot_count,penguin_level,"
                        "active_pet,created_at,updated_at) VALUES ('alice',20,150,12,2,"
                        "'stinky_penguin',?,?)", (NOW, NOW))
            init_estate(old)
            init_estate(old)
            row = old.execute("SELECT level,xp,plot_count,penguin_level,active_pet,maodie_level "
                              "FROM estate_profiles WHERE username='alice'").fetchone()
            self.assertEqual(row, (20, 150, 12, 2, "stinky_penguin", 0))
            old.execute("UPDATE estate_profiles SET active_pet='maodie' WHERE username='alice'")
        finally:
            old.close()

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

    def test_defense_spends_visitor_attempt_but_not_owner_six(self):
        self.conn.execute("UPDATE estate_profiles SET pet_level=4 WHERE username='alice'")
        self.conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                          "WHERE username='alice' AND plot_index=0", (NOW - 1000, NOW - 10))
        for index in range(2):
            result = steal_crop(self.conn, "bob", f"defended-limit-{index}",
                                "alice", 0, NOW, adjust_coins, lambda low, high: low)
            self.assertEqual(result["outcome"], "defended")
            self.assertEqual(result["visitor_remaining"], 1 - index)
            self.assertEqual(result["owner_remaining"], 6)
        state = public_estate_state(self.conn, "bob", "alice", NOW)
        self.assertEqual(state["steal_limits"], {"visitor_remaining": 0,
                                                   "owner_remaining": 6})
        with self.assertRaises(EstateError) as error:
            steal_crop(self.conn, "bob", "defended-limit-third", "alice", 0,
                       NOW, adjust_coins, lambda low, high: high)
        self.assertEqual(error.exception.code, "visitor_limit")

        self.conn.execute("INSERT INTO users VALUES ('carol',200000)")
        ensure_estate(self.conn, "carol", NOW)
        self.conn.execute("UPDATE estate_profiles SET level=20 WHERE username='carol'")
        stolen = steal_crop(self.conn, "carol", "after-defense-steal", "alice", 0,
                            NOW, adjust_coins, lambda low, high: high)
        self.assertEqual((stolen["outcome"], stolen["owner_remaining"]), ("stolen", 5))

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
        cycle = 15 * 60 + grow_seconds("wheat", 1)
        first_ready = NOW - 15 * 60 - 3 * cycle
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

    def test_five_free_draws_then_paid_and_reset_at_shanghai_midnight(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        before_midnight = int(datetime(2033, 5, 17, 23, 59, 50,
                                       tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        self.conn.execute("UPDATE users SET coins=0 WHERE username='alice'")
        with patch("estate.lottery.secrets.randbelow", return_value=0):
            for index in range(5):
                result = draw_lottery(self.conn, "alice", f"free-draw-{index}",
                                      before_midnight + index, adjust_coins)
                self.assertEqual((result["cost"], result["free_draws_remaining"]),
                                 (0, 4 - index))
            replay = draw_lottery(self.conn, "alice", "free-draw-4",
                                  before_midnight + 5, adjust_coins)
            self.assertTrue(replay["replayed"])
            with self.assertRaises(EstateError):
                draw_lottery(self.conn, "alice", "paid-without-coins",
                             before_midnight + 6, adjust_coins)
            self.conn.execute("UPDATE users SET coins=3000 WHERE username='alice'")
            paid = draw_lottery(self.conn, "alice", "paid-after-free",
                                before_midnight + 7, adjust_coins)
            self.assertEqual((paid["cost"], paid["free_draws_remaining"]), (3000, 0))
            self.assertEqual(self.conn.execute(
                "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 0)
            other_user = draw_lottery(self.conn, "bob", "bob-first-free",
                                      before_midnight + 8, adjust_coins)
            self.assertEqual(other_user["free_draws_remaining"], 4)
            next_day = draw_lottery(self.conn, "alice", "new-day-free",
                                    before_midnight + 10, adjust_coins)
        self.assertEqual((next_day["cost"], next_day["free_draws_remaining"]), (0, 4))
        self.assertEqual(estate_state(self.conn, "alice", before_midnight + 10)
                         ["lottery_daily"]["remaining"], 4)

    def test_fertilizer_prize_awards_one(self):
        with patch("estate.lottery.secrets.randbelow", return_value=6):
            result = draw_lottery(self.conn, "alice", "one-fertilizer", NOW,
                                  adjust_coins)
        self.assertEqual((result["award"], result["quantity"]), ("fertilizer_1", 1))
        self.assertEqual(self.conn.execute(
            "SELECT quantity FROM estate_inventory WHERE username='alice' "
            "AND item_id='supply:fertilizer'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
