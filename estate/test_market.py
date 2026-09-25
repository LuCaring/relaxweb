"""马戏团模拟指数：一分钟报价、仓位、金币和幂等结算。"""
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from estate.market import NO_ADVANCE, market_snapshot, record_market_candles, trade_market
from estate.schema import init_estate
from estate.store import EstateError, ensure_estate
from server.estate.protocol import EstateProtocol


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
        with patch("estate.market.secrets.randbelow", return_value=160):
            second = market_snapshot(self.conn, "alice", NOW + 60, Decimal("101"))
        self.assertEqual(second["price"], 1007)
        self.assertEqual(market_snapshot(self.conn, "alice", NOW + 61, None)["price"], 1007)
        self.assertEqual(len(second["history"]), 2)

    def test_candles_aggregate_sampled_open_high_low_close(self):
        boundary = (NOW // 3600 + 1) * 3600
        first = market_snapshot(self.conn, "alice", boundary - 60, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=160):
            second = market_snapshot(self.conn, "alice", boundary, Decimal("101"))
        self.assertEqual(first["candles"]["minute"][-1]["close"], 1000)
        self.assertEqual(second["candles"]["minute"][-1], {
            "time": boundary, "open": 1000, "high": 1007,
            "low": 1000, "close": 1007,
        })
        self.assertEqual(len(second["candles"]["hour"]), 2)
        self.assertEqual(second["candles"]["hour"][-1]["open"], 1000)
        self.assertEqual(second["candles"]["hour"][-1]["close"], 1007)

    def test_existing_ticks_backfill_candles_on_migration(self):
        self.conn.execute("DROP TABLE estate_market_candles")
        self.conn.executemany("INSERT INTO estate_market_ticks VALUES (?,?)",
                              ((NOW // 60 - 1, 100000), (NOW // 60, 100700)))
        init_estate(self.conn)
        rows = self.conn.execute("SELECT open_cents,close_cents FROM estate_market_candles "
                                 "WHERE period='minute' ORDER BY start_minute").fetchall()
        self.assertEqual(rows, [(100000, 100000), (100000, 100700)])

    def test_daily_candles_roll_over_at_china_midnight(self):
        boundary = (NOW // 60 + 480) // 1440 * 1440 - 480 + 1440
        record_market_candles(self.conn, boundary - 1, 100000, 101000)
        record_market_candles(self.conn, boundary, 101000, 99000)
        record_market_candles(self.conn, boundary + 1, 99000, 102000)
        rows = self.conn.execute("SELECT start_minute,open_cents,high_cents,low_cents,close_cents "
                                 "FROM estate_market_candles WHERE period='day' "
                                 "ORDER BY start_minute").fetchall()
        self.assertEqual(rows, [(boundary - 1440, 100000, 101000, 100000, 101000),
                                (boundary, 101000, 102000, 99000, 102000)])

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
        with patch("estate.market.secrets.randbelow", return_value=1600):
            later = market_snapshot(self.conn, "alice", NOW + 180 * 60, Decimal("110"))
        self.assertEqual(later["price"], 1070)
        self.assertTrue(later["available"])

    def test_stale_feed_uses_shared_simulated_quote_and_can_trade(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=800):
            fallback = market_snapshot(self.conn, "alice", NOW + 60, None)
        self.assertTrue(fallback["available"])
        self.assertEqual(fallback["source_kind"], "simulated")
        self.assertEqual(fallback["price"], 1000)
        self.assertEqual(market_snapshot(self.conn, "alice", NOW + 61, None)["price"], 1000)
        with patch("estate.market.fetch_source_price", side_effect=AssertionError("unexpected fetch")):
            bought = trade_market(self.conn, "alice", "market-fallback-0001", "buy", "1",
                                  NOW + 61, adjust_coins, None, None)
        self.assertEqual(bought["price"], 1000)
        self.assertEqual(bought["market"]["shares"], 1)

    def test_live_feed_resumes_without_old_reference_jump(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        market_snapshot(self.conn, "alice", NOW + 60, None)
        before = market_snapshot(self.conn, "alice", NOW + 61, None)["price"]
        resumed = market_snapshot(self.conn, "alice", NOW + 120, Decimal("200"))
        self.assertEqual(resumed["source_kind"], "live")
        self.assertEqual(resumed["price"], before)

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

    def test_live_hidden_noise_stays_below_reference_signal(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=0):
            down = market_snapshot(self.conn, "alice", NOW + 60, Decimal("100"))
        self.assertEqual(down["price"], 998.4)

    def test_live_hidden_noise_upper_bound(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=320):
            up = market_snapshot(self.conn, "alice", NOW + 60, Decimal("100"))
        self.assertEqual(up["price"], 1001.6)

    def test_single_tick_change_is_capped_after_long_gap(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        with patch("estate.market.secrets.randbelow", return_value=1600):
            jumped = market_snapshot(self.conn, "alice", NOW + 600 * 60, Decimal("200"))
        self.assertEqual(jumped["price"], 1080)

    def test_simulated_quote_reverts_upward_toward_earlier_price(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        self.conn.execute("UPDATE estate_market_index SET price_cents=90000,"
                          "source_kind='simulated'")
        with patch("estate.market.secrets.randbelow", return_value=800):
            reverted = market_snapshot(self.conn, "alice", NOW + 241 * 60, None)
        self.assertEqual(reverted["price"], 900.19)

    def test_simulated_quote_reverts_downward_from_high(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        self.conn.execute("UPDATE estate_market_index SET price_cents=110000,"
                          "source_kind='simulated'")
        with patch("estate.market.secrets.randbelow", return_value=800):
            reverted = market_snapshot(self.conn, "alice", NOW + 241 * 60, None)
        self.assertEqual(reverted["price"], 1099.79)

    def test_hold_sentinel_reads_quote_without_generating_tick(self):
        market_snapshot(self.conn, "alice", NOW, Decimal("100"))
        held = market_snapshot(self.conn, "alice", NOW + 60, NO_ADVANCE)
        self.assertEqual(held["price"], 1000)
        self.assertEqual(held["source_kind"], "live")
        self.assertTrue(held["available"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM estate_market_ticks").fetchone()[0], 1)
        with patch("estate.market.secrets.randbelow", return_value=160):
            followed = market_snapshot(self.conn, "alice", NOW + 120, Decimal("101"))
        self.assertEqual(followed["price"], 1007)


class MarketWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_samples_without_any_player_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.db"
            with closing(sqlite3.connect(path)) as conn, conn:
                init_estate(conn)
            protocol = EstateProtocol(database=lambda: closing(sqlite3.connect(path)), clients={},
                                      send_json=None, send_encoded=None, presence=None)
            try:
                with patch("server.estate.protocol.fetch_source_price", return_value=None):
                    await asyncio.sleep(1.2)
                with closing(sqlite3.connect(path)) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM estate_market_candles "
                                         "WHERE period='minute'").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                protocol._market_watcher.cancel()
                await asyncio.gather(protocol._market_watcher, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
