#!/usr/bin/env python3
"""休闲庄园拜访、偷菜额度与记录测试。"""
import sqlite3
from datetime import datetime
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate import ensure_estate, init_estate
from estate.catalog import FERTILIZER_ITEM
from estate.store import EstateError
from estate.visits import (
    day_key, fertilize, list_estates, mark_notifications_read, notifications,
    public_estate_state, steal_crop,
)
from server.estate.presence import valid_estate_position


class EstateVisitTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY, password_hash TEXT, salt TEXT, created_at INTEGER, coins REAL)")
        init_estate(self.conn)
        for username in ("alice", "bob", "carol", "dave", "erin"):
            self.conn.execute(
                "INSERT INTO users(username,password_hash,salt,created_at,coins) "
                "VALUES (?,?,?,0,10000)", (username, "", ""),
            )
            ensure_estate(self.conn, username, 100)
            self.conn.execute("UPDATE estate_profiles SET level=20 WHERE username=?", (username,))
            self.conn.execute("UPDATE estate_profiles SET plot_count=12 WHERE username=?", (username,))
        self.conn.commit()
        self.now = 2_000_000_000

    def tearDown(self):
        self.conn.close()

    def mature(self, owner, index, crop="wheat"):
        self.conn.execute(
            "UPDATE estate_plots SET crop_id=?,planted_at=?,ready_at=? "
            "WHERE username=? AND plot_index=?", (crop, self.now - 500, self.now - 1, owner, index),
        )

    def test_fertilizer_works_on_own_and_visitor_plots_once_per_request(self):
        self.conn.execute("INSERT INTO estate_inventory VALUES (?,?,?)",
                          ("alice", FERTILIZER_ITEM, 2))
        for owner, index in (("alice", 0), ("bob", 1)):
            self.conn.execute(
                "UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                "WHERE username=? AND plot_index=?",
                (self.now - 100, self.now + 4000, owner, index),
            )
            result = fertilize(self.conn, "alice", f"fert-{owner}", owner, index, self.now)
            self.assertEqual(result["ready_at"], self.now + 400)
            self.assertTrue(fertilize(self.conn, "alice", f"fert-{owner}", owner, index,
                                      self.now)["replayed"])
        self.assertIsNone(self.conn.execute(
            "SELECT quantity FROM estate_inventory WHERE username='alice' AND item_id=?",
            (FERTILIZER_ITEM,)).fetchone())

    def test_fertilizer_rejects_empty_or_mature_plots_without_consuming(self):
        self.conn.execute("INSERT INTO estate_inventory VALUES (?,?,?)",
                          ("alice", FERTILIZER_ITEM, 1))
        with self.assertRaises(EstateError) as empty:
            fertilize(self.conn, "alice", "fert-empty", "bob", 0, self.now)
        self.assertEqual(empty.exception.code, "plot_empty")
        self.mature("bob", 0)
        with self.assertRaises(EstateError) as mature:
            fertilize(self.conn, "alice", "fert-mature", "bob", 0, self.now)
        self.assertEqual(mature.exception.code, "crop_mature")
        self.assertEqual(self.conn.execute(
            "SELECT quantity FROM estate_inventory WHERE username='alice' AND item_id=?",
            (FERTILIZER_ITEM,)).fetchone()[0], 1)

    def test_schema_has_twelve_plots_and_new_unlocks(self):
        from estate.catalog import MAX_PLOTS, PLOT_UNLOCKS
        self.assertEqual(MAX_PLOTS, 12)
        self.assertEqual([PLOT_UNLOCKS[i]["unlock_level"] for i in range(8, 12)],
                         [10, 12, 15, 20])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM estate_plots WHERE username='alice'"
        ).fetchone()[0], 12)

    def test_expanded_map_position_validation(self):
        self.assertTrue(valid_estate_position(970, 260))
        self.assertTrue(valid_estate_position(1200, 400))
        self.assertFalse(valid_estate_position(1100, 120))  # 商店建筑内部
        self.assertFalse(valid_estate_position(1270, 400))  # 超出右边界
        self.assertFalse(valid_estate_position(600, 710))   # 超出下边界

    def test_level_three_required_and_directory_excludes_self(self):
        self.conn.execute("UPDATE estate_profiles SET level=2 WHERE username='alice'")
        with self.assertRaises(EstateError) as error:
            list_estates(self.conn, "alice", now=self.now)
        self.assertEqual(error.exception.code, "visit_level_locked")
        self.conn.execute("UPDATE estate_profiles SET level=3 WHERE username='alice'")
        names = [item["username"] for item in list_estates(self.conn, "alice", "bo", self.now)]
        self.assertEqual(names, ["bob"])

    def test_steal_moves_entire_crop_and_records_notification(self):
        self.mature("bob", 0)
        result = steal_crop(self.conn, "alice", "steal-bob-0001", "bob", 0, self.now)
        self.assertEqual(result["quantity"], 1)
        self.assertIsNone(self.conn.execute(
            "SELECT crop_id FROM estate_plots WHERE username='bob' AND plot_index=0"
        ).fetchone()[0])
        self.assertEqual(self.conn.execute(
            "SELECT quantity FROM estate_inventory WHERE username='alice' AND item_id='crop:wheat'"
        ).fetchone()[0], 1)
        rows = notifications(self.conn, "bob", self.now)
        self.assertEqual(rows[0]["visitor_username"], "alice")
        self.assertFalse(rows[0]["read"])
        mark_notifications_read(self.conn, "bob", [rows[0]["id"]], self.now)
        self.assertTrue(notifications(self.conn, "bob", self.now)[0]["read"])

    def test_per_visitor_limit_is_two_and_owner_limit_is_six(self):
        for index in range(7):
            self.mature("bob", index)
        for index in range(2):
            steal_crop(self.conn, "alice", f"steal-alice-{index:03}", "bob", index, self.now)
        with self.assertRaises(EstateError) as error:
            steal_crop(self.conn, "alice", "steal-alice-004", "bob", 2, self.now)
        self.assertEqual(error.exception.code, "visitor_limit")
        for index in range(2, 4):
            steal_crop(self.conn, "carol", f"steal-carol-{index:03}", "bob", index, self.now)
        for index in range(4, 6):
            steal_crop(self.conn, "dave", f"steal-dave-{index:03}", "bob", index, self.now)
        with self.assertRaises(EstateError) as error:
            steal_crop(self.conn, "erin", "steal-erin-006", "bob", 6, self.now)
        self.assertEqual(error.exception.code, "owner_protected")

    def test_doudou_defends_crop_and_transfers_dropped_coins(self):
        self.mature("bob", 0)
        self.conn.execute("UPDATE estate_profiles SET pet_level=1 WHERE username='bob'")

        def adjust(conn, username, delta, kind, detail="", ref=""):
            balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
            updated = balance + delta
            conn.execute("UPDATE users SET coins=? WHERE username=?", (updated, username))
            return updated

        rolls = iter((1, 777))
        result = steal_crop(self.conn, "alice", "defended-0001", "bob", 0, self.now,
                            adjust, lambda low, high: next(rolls))
        self.assertEqual(result["outcome"], "defended")
        self.assertEqual(result["coins_dropped"], 777)
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 9223)
        self.assertEqual(self.conn.execute("SELECT coins FROM users WHERE username='bob'").fetchone()[0], 10777)
        self.assertEqual(self.conn.execute("SELECT crop_id FROM estate_plots WHERE username='bob' AND plot_index=0").fetchone()[0], "wheat")
        owner_notice = notifications(self.conn, "bob", self.now)[0]
        visitor_notice = notifications(self.conn, "alice", self.now)[0]
        self.assertEqual((owner_notice["outcome"], owner_notice["role"]), ("defended", "owner"))
        self.assertEqual(visitor_notice["role"], "visitor")

    def test_warehouse_full_does_not_consume_plot_or_limit(self):
        self.mature("bob", 0)
        self.conn.execute("UPDATE estate_profiles SET warehouse_level=1 WHERE username='alice'")
        self.conn.execute(
            "INSERT INTO estate_inventory(username,item_id,quantity) VALUES ('alice','seed:wheat',100)"
        )
        with self.assertRaises(EstateError) as error:
            steal_crop(self.conn, "alice", "steal-full-0001", "bob", 0, self.now)
        self.assertEqual(error.exception.code, "warehouse_full")
        self.assertEqual(self.conn.execute(
            "SELECT crop_id FROM estate_plots WHERE username='bob' AND plot_index=0"
        ).fetchone()[0], "wheat")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_thefts").fetchone()[0], 0)

    def test_shanghai_day_changes_at_midnight(self):
        tz = ZoneInfo("Asia/Shanghai")
        before = int(datetime(2030, 1, 1, 23, 59, tzinfo=tz).timestamp())
        after = int(datetime(2030, 1, 2, 0, 0, tzinfo=tz).timestamp())
        self.assertNotEqual(day_key(before), day_key(after))

    def test_public_state_contains_no_owner_inventory_or_coins(self):
        state = public_estate_state(self.conn, "alice", "bob", self.now)
        self.assertEqual(state["owner_username"], "bob")
        self.assertNotIn("inventory", state)
        self.assertNotIn("coins", state)
        self.assertEqual(len(state["plots"]), 12)


if __name__ == "__main__":
    unittest.main()
