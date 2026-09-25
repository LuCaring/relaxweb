"""马戏团模拟指数：一分钟报价、仓位、金币和幂等结算。"""
import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch

from estate.market import market_snapshot, trade_market
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate


NOW = 2_000_000_000


def adjust_coins(conn, username, delta, kind, detail="", ref=""):
    balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0] + delta
    if balance < 0:
        raise ValueError("金币不足")
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance, username))
    conn.execute("INSERT INTO coin_transactions VALUES (?,?,?,?,?)",
                 (username, delta, kind, detail, ref))
    return balance


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("CREATE TABLE coin_transactions(username TEXT,amount REAL,kind TEXT,detail TEXT,ref TEXT)")
        self.conn.execute("INSERT INTO users VALUES ('alice',10000)")
        init_estate(self.conn)
        ensure_estate(self.conn, "alice", NOW)

    def tearDown(self):
        self.conn.close()

    def test_one_quote_per_minute_and_hidden_change(self):
        first = market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        self.assertEqual(first["price"], 1000)
        with patch("estate.market.secrets.randbelow", return_value=600):
            second = market_snapshot(self.conn, "alice", NOW + 60, Decimal("101"))
        self.assertEqual(second["price"], 1007)
        self.assertEqual(market_snapshot(self.conn, "alice", NOW + 61, None)["price"], 1007)
        self.assertEqual(len(second["history"]), 2)

    def test_fractional_t0_roundtrip_costs_fee_and_replay_does_not_double_spend(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        bought = trade_market(self.conn, "alice", "market-buy-0001", "buy", "0.125",
                              NOW, adjust_coins, None, Decimal("100"))
        replay = trade_market(self.conn, "alice", "market-buy-0001", "buy", "0.125",
                              NOW, adjust_coins, None, Decimal("100"))
        self.assertTrue(replay["replayed"])
        self.assertEqual(bought["market"]["shares"], 0.125)
        sold = trade_market(self.conn, "alice", "market-sell-0001", "sell", "0.125",
                            NOW, adjust_coins, None, Decimal("100"))
        self.assertEqual(sold["market"]["shares"], 0)
        self.assertLess(sold["coins"], 10000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0], 2)

    def test_long_gap_reflects_cumulative_reference_move(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=6000):
            later = market_snapshot(self.conn, "alice", NOW + 180 * 60, Decimal("110"))
        self.assertEqual(later["price"], 1070)
        self.assertTrue(later["available"])

    def test_stale_feed_pauses_trade_without_charging(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        stale = market_snapshot(self.conn, "alice", NOW + 60, None)
        self.assertFalse(stale["available"])
        with self.assertRaises(EstateError) as error:
            trade_market(self.conn, "alice", "market-stale-0001", "buy", "1",
                         NOW + 60, adjust_coins, None, Decimal("100"))
        self.assertEqual(error.exception.code, "market_unavailable")
        self.assertEqual(self.conn.execute("SELECT coins FROM users").fetchone()[0], 10000)

    def test_invalid_quantity_and_insufficient_holdings(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        for quantity in ("0", "-1", "0.0001", "NaN", "1e9"):
            with self.assertRaises(EstateError):
                trade_market(self.conn, "alice", "market-invalid-0001", "buy", quantity,
                             NOW, adjust_coins, None, Decimal("100"))
        with self.assertRaises(EstateError) as error:
            trade_market(self.conn, "alice", "market-short-0001", "sell", "1",
                         NOW, adjust_coins, None, Decimal("100"))
        self.assertEqual(error.exception.code, "market_shares")

    def test_execution_uses_live_reference_within_minute(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        result = trade_market(self.conn, "alice", "market-live-0001", "buy", "1",
                              NOW, adjust_coins, None, Decimal("101"))
        self.assertEqual(result["price"], 1007)
        self.assertGreater(result["amount"], 1007)


if __name__ == "__main__":
    unittest.main()
