"""马戏团模拟指数：优先参考 SOL 报价，断线时使用服务端共享模拟行情。"""
import json
import math
import re
import secrets
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from urllib.request import Request, urlopen

from estate.store import credit, debit, estate_error, run_action


INDEX_NAME = "星潮模拟指数"
FEE_RATE = Decimal("0.005")
MIN_PRICE_CENTS = 10000
MAX_PRICE_CENTS = 1000000
QUANTITY_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]{1,3})?$")
UNFETCHED = object()


def fetch_source_price():
    """两个公开现货源按顺序回退；两者不可用时暂停交易。"""
    sources = (
        ("https://api.coinbase.com/v2/prices/SOL-USD/spot",
         lambda data: data["data"]["amount"]),
        ("https://api.kraken.com/0/public/Ticker?pair=SOLUSD",
         lambda data: next(iter(data["result"].values()))["c"][0]),
    )
    for url, extract in sources:
        try:
            request = Request(url, headers={"User-Agent": "relaxweb-estate/1.0",
                                            "Cache-Control": "no-cache"})
            with urlopen(request, timeout=3) as response:
                price = Decimal(str(extract(json.load(response))))
            if price.is_finite() and price > 0:
                return price
        except (OSError, ValueError, KeyError, TypeError, StopIteration, ArithmeticError):
            continue
    return None


def quote_refresh_due(conn, now):
    row = conn.execute("SELECT attempted_minute FROM estate_market_index WHERE id=1").fetchone()
    return row is None or row[0] < int(now) // 60


def _advance(conn, now, source_price=UNFETCHED):
    minute = int(now) // 60
    conn.execute("INSERT OR IGNORE INTO estate_market_index(id) VALUES (1)")
    attempt, price_cents, old_source, source_minute, available, source_kind = conn.execute(
        "SELECT attempted_minute,price_cents,source_price,source_minute,available,source_kind "
        "FROM estate_market_index WHERE id=1").fetchone()
    if attempt >= minute:
        return price_cents, bool(available)
    source = fetch_source_price() if source_price is UNFETCHED else source_price
    if source is None:
        # 每分钟只生成一次全服共享的报价；不按离线时长补算，避免无人访问时跳价。
        change = Decimal(secrets.randbelow(1601) - 800) / Decimal(100000)
        price_cents = max(MIN_PRICE_CENTS, min(MAX_PRICE_CENTS,
            int((Decimal(price_cents) * (1 + change)).to_integral_value())))
        conn.execute("UPDATE estate_market_index SET attempted_minute=?,price_cents=?,"
                     "source_kind='simulated',available=1 WHERE id=1",
                     (minute, price_cents))
        conn.execute("INSERT OR REPLACE INTO estate_market_ticks(minute,price_cents) VALUES (?,?)",
                     (minute, price_cents))
        conn.execute("DELETE FROM estate_market_ticks WHERE minute<?", (minute - 1440,))
        return price_cents, True
    previous = Decimal(old_source)
    if previous > 0 and source_kind == "live":
        gap = max(1, minute - source_minute)
        real_move = max(Decimal("-0.5"), min(Decimal("0.5"), source / previous - 1))
        span = min(6000, 600 * math.isqrt(gap))
        hidden_move = Decimal(secrets.randbelow(span * 2 + 1) - span) / Decimal(100000)
        cap = min(Decimal("0.5"), Decimal("0.015") * gap)
        change = max(-cap, min(cap, real_move * Decimal("0.7") + hidden_move))
        price_cents = max(MIN_PRICE_CENTS, min(MAX_PRICE_CENTS,
            int((Decimal(price_cents) * (1 + change)).to_integral_value())))
    conn.execute(
        "UPDATE estate_market_index SET attempted_minute=?,price_cents=?,source_price=?,source_minute=?,"
        "source_kind='live',available=1 WHERE id=1",
        (minute, price_cents, str(source), minute),
    )
    conn.execute("INSERT OR REPLACE INTO estate_market_ticks(minute,price_cents) VALUES (?,?)",
                 (minute, price_cents))
    conn.execute("DELETE FROM estate_market_ticks WHERE minute<?", (minute - 1440,))
    return price_cents, True


def refresh_market_quote(conn, now, source_price):
    """供异步协议层在后台线程取得参考价后写入一次全局报价。"""
    return _advance(conn, now, source_price)


def _position(conn, username):
    row = conn.execute(
        "SELECT shares_milli,cost_basis_cents,realized_pnl_cents "
        "FROM estate_market_positions WHERE username=?", (username,),
    ).fetchone()
    return row or (0, 0, 0)


def market_snapshot(conn, username, now, source_price=UNFETCHED):
    price_cents, available = _advance(conn, now, source_price)
    source_kind = conn.execute("SELECT source_kind FROM estate_market_index WHERE id=1").fetchone()[0]
    shares, basis, realized = _position(conn, username)
    history = conn.execute(
        "SELECT minute,price_cents FROM estate_market_ticks WHERE minute>=? "
        "ORDER BY minute DESC LIMIT 120", (int(now) // 60 - 119,),
    ).fetchall()
    return {"name": INDEX_NAME, "price": price_cents / 100,
            "quote_minute": int(now) // 60, "available": available,
            "source": "SOL/USD" if source_kind == "live" else "游戏内模拟",
            "source_kind": source_kind, "fee_rate": float(FEE_RATE),
            "shares": shares / 1000, "cost_basis": basis / 100,
            "market_value": round(price_cents * shares / 100000, 2),
            "realized_pnl": realized / 100,
            "history": [{"time": minute * 60, "price": value / 100}
                        for minute, value in reversed(history)]}


def _quantity_milli(value):
    raw = str(value or "").strip()
    if not QUANTITY_PATTERN.fullmatch(raw):
        raise estate_error(("market_quantity", "请输入最多三位小数的交易份额"))
    quantity = int(Decimal(raw) * 1000)
    if not 1 <= quantity <= 1_000_000_000:
        raise estate_error(("market_quantity", "交易份额超出允许范围"))
    return quantity


def trade_market(conn, username, request_id, side, quantity, now, adjust_coins,
                 source_price=UNFETCHED, execution_source=UNFETCHED):
    side = str(side or "")
    amount_milli = _quantity_milli(quantity)
    if side not in ("buy", "sell"):
        raise estate_error(("market_side", "交易方向无效"))

    def mutate():
        price_cents, available = _advance(conn, now, source_price)
        if not available:
            raise estate_error(("market_unavailable", "行情暂不可用，交易已暂停"))
        source_kind, reference = conn.execute(
            "SELECT source_kind,source_price FROM estate_market_index WHERE id=1").fetchone()
        if source_kind == "live":
            live_source = fetch_source_price() if execution_source is UNFETCHED else execution_source
            if live_source is None:
                raise estate_error(("market_unavailable", "实时行情暂不可用，交易已暂停"))
            movement = max(Decimal("-0.5"), min(Decimal("0.5"), live_source / Decimal(reference) - 1))
            price_cents = max(MIN_PRICE_CENTS, min(MAX_PRICE_CENTS,
                int((Decimal(price_cents) * (1 + movement * Decimal("0.7"))).to_integral_value())))
        shares, basis, realized = _position(conn, username)
        notional = Decimal(price_cents) * amount_milli / 1000
        if side == "buy":
            cents = int((notional * (1 + FEE_RATE)).to_integral_value(rounding=ROUND_CEILING))
            balance = debit(adjust_coins, conn, username, cents / 100,
                            f"庄园模拟指数买入 {amount_milli / 1000:g} 份", request_id)
            shares += amount_milli
            basis += cents
        else:
            if amount_milli > shares:
                raise estate_error(("market_shares", "持有份额不足"))
            cents = int((notional * (1 - FEE_RATE)).to_integral_value(rounding=ROUND_FLOOR))
            removed = basis if amount_milli == shares else basis * amount_milli // shares
            balance = credit(adjust_coins, conn, username, cents / 100,
                             f"庄园模拟指数卖出 {amount_milli / 1000:g} 份", request_id)
            shares -= amount_milli
            basis -= removed
            realized += cents - removed
        conn.execute(
            "INSERT INTO estate_market_positions(username,shares_milli,cost_basis_cents,realized_pnl_cents) "
            "VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
            "shares_milli=excluded.shares_milli,cost_basis_cents=excluded.cost_basis_cents,"
            "realized_pnl_cents=excluded.realized_pnl_cents",
            (username, shares, basis, realized),
        )
        return {"action": "market_trade", "side": side, "quantity": amount_milli / 1000,
                "price": price_cents / 100, "amount": cents / 100, "coins": balance,
                "market": market_snapshot(conn, username, now)}

    return run_action(conn, username, request_id, "market_trade",
                      {"side": side, "quantity_milli": amount_milli}, now, mutate)
