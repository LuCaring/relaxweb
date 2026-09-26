"""铲除作物只清空自己的田块，不产生收益。"""
import sqlite3
import unittest

from estate.farming import clear_plot
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate


class ClearPlotTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("INSERT INTO users VALUES ('alice',100)")
        init_estate(self.conn)
        ensure_estate(self.conn, "alice", 1000)

    def tearDown(self):
        self.conn.close()

    def test_clear_growing_crop_without_rewards_and_replay(self):
        self.conn.execute("UPDATE estate_plots SET crop_id='rice',planted_at=1000,ready_at=2000 "
                          "WHERE username='alice' AND plot_index=0")
        result = clear_plot(self.conn, "alice", "clear-plot-1", 0, 1100)
        self.assertEqual(result["crop_id"], "rice")
        self.assertEqual(self.conn.execute(
            "SELECT crop_id,planted_at,ready_at FROM estate_plots "
            "WHERE username='alice' AND plot_index=0").fetchone(), (None, None, None))
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 100)
        self.assertTrue(clear_plot(self.conn, "alice", "clear-plot-1", 0, 1101)["replayed"])
        with self.assertRaises(EstateError):
            clear_plot(self.conn, "alice", "clear-plot-2", 0, 1102)

    def test_clear_mature_crop(self):
        self.conn.execute("UPDATE estate_plots SET crop_id='rice',planted_at=1000,ready_at=1100 "
                          "WHERE username='alice' AND plot_index=0")
        clear_plot(self.conn, "alice", "clear-mature", 0, 1200)
        self.assertIsNone(self.conn.execute(
            "SELECT crop_id FROM estate_plots WHERE username='alice' AND plot_index=0"
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
