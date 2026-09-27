"""庄园轮盘连续再抽的金币结算。"""
import sqlite3
import unittest
from unittest.mock import patch

from estate.catalog import CROPS, LOTTERY_GRAND_PRIZES
from estate.lottery import draw_lottery
from estate.schema import init_estate
from estate.store import ensure_estate


def adjust_coins(conn, username, delta, *_args, **_kwargs):
    balance = conn.execute("SELECT coins FROM users WHERE username=?",
                           (username,)).fetchone()[0] + delta
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance, username))
    return balance


class LotteryRerollTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("INSERT INTO users VALUES ('alice',10000)")
        init_estate(self.conn)
        ensure_estate(self.conn, "alice", 1000)

    def tearDown(self):
        self.conn.close()

    def test_probabilities_and_flower_values(self):
        self.assertEqual(dict(LOTTERY_GRAND_PRIZES)["land_ticket"], 20)
        self.assertEqual(dict(LOTTERY_GRAND_PRIZES)["reroll"], 30)
        self.assertEqual(sum(weight for _, weight in LOTTERY_GRAND_PRIZES), 100)
        self.assertEqual((CROPS["legendary_flower"]["grow_seconds"],
                          CROPS["legendary_flower"]["sell_price"]),
                         (24 * 3600, 25888))

    def test_two_rerolls_award_twelve_thousand_once(self):
        with patch("estate.lottery.secrets.randbelow", side_effect=[7, 10, 7, 10, 0]):
            result = draw_lottery(self.conn, "alice", "reroll-test-001", 1000,
                                  adjust_coins)
        self.assertEqual((result["award"], result["reroll_count"],
                          result["reroll_bonus"], result["cost"]),
                         ("thanks", 2, 12000, 0))
        self.assertEqual(result["coins"], 22000)
        replay = draw_lottery(self.conn, "alice", "reroll-test-001", 1001,
                              adjust_coins)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 22000)

    def test_one_paid_reroll_returns_entry_price(self):
        with patch("estate.lottery.secrets.randbelow", return_value=0):
            for index in range(5):
                draw_lottery(self.conn, "alice", f"free-test-{index}", 1000,
                             adjust_coins)
        with patch("estate.lottery.secrets.randbelow", side_effect=[7, 10, 0]):
            result = draw_lottery(self.conn, "alice", "paid-reroll-test", 1000,
                                  adjust_coins)
        self.assertEqual((result["cost"], result["reroll_count"],
                          result["reroll_bonus"], result["coins"]),
                         (3000, 1, 3000, 10000))


if __name__ == "__main__":
    unittest.main()
