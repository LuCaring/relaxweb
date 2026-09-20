"""每日签到、免费抽奖与德扑流水奖励。写操作由宿主在 SQLite 写事务中调用。"""
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

CHECKIN_TICKETS = 5
CHECKIN_TZ = timezone(timedelta(hours=8))
PRIZE_TIERS = ((20, 100, 88), (101, 200, 10), (201, 500, 2))
HOLDEM_REWARDS = ((100, 20), (200, 40), (500, 100), (1000, 200))


def init_rewards(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "lottery_tickets" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN lottery_tickets INTEGER NOT NULL DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_checkins (
        user_id INTEGER NOT NULL, day TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, day))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lottery_draws (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        request_id TEXT NOT NULL, amount INTEGER NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(user_id, request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lottery_user ON lottery_draws(user_id, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS holdem_turnover (
        hand_id TEXT NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL,
        amount_cents INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(hand_id, user_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdem_turnover_day ON holdem_turnover(user_id, day)")
    conn.execute("""CREATE TABLE IF NOT EXISTS holdem_reward_claims (
        user_id INTEGER NOT NULL, day TEXT NOT NULL, threshold INTEGER NOT NULL,
        amount INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, day, threshold))""")


def checkin_day(now):
    return datetime.fromtimestamp(now, CHECKIN_TZ).date().isoformat()


def record_holdem_turnover(conn, hand_id, stakes, now):
    """只由正常结算调用；按结算日归档，与积分、筹码托管在同一事务提交。"""
    day = checkin_day(now)
    for username, amount in stakes.items():
        cents = Decimal(str(amount)) * 100
        if not cents.is_finite() or cents < 0 or cents != cents.to_integral_value():
            raise ValueError("德扑下注流水金额无效")
        if not cents:
            continue
        conn.execute("""INSERT OR IGNORE INTO holdem_turnover
            (hand_id, user_id, day, amount_cents, created_at)
            SELECT ?, id, ?, ?, ? FROM users WHERE username = ?""",
            (hand_id, day, int(cents), int(now), username))


def holdem_turnover_state(conn, user_id, day):
    cents = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM holdem_turnover "
                         "WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()[0]
    claimed = {row[0] for row in conn.execute(
        "SELECT threshold FROM holdem_reward_claims WHERE user_id = ? AND day = ?", (user_id, day))}
    return {"amount": cents / 100,
            "tiers": [{"threshold": threshold, "amount": amount, "claimed": threshold in claimed,
                       "claimable": cents >= threshold * 100 and threshold not in claimed}
                      for threshold, amount in HOLDEM_REWARDS]}


def claim_holdem_reward(conn, username, threshold, day, now, credit_coins):
    if type(threshold) is not int or threshold not in dict(HOLDEM_REWARDS):
        raise ValueError("请选择有效的德扑流水奖励档位")
    if day != checkin_day(now):
        raise ValueError("日期已切换，请刷新后领取今日奖励")
    user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise ValueError("账号不存在，请重新登录")
    saved = conn.execute("SELECT amount FROM holdem_reward_claims "
                         "WHERE user_id = ? AND day = ? AND threshold = ?",
                         (user[0], day, threshold)).fetchone()
    if saved:
        return saved[0], True
    status = holdem_turnover_state(conn, user[0], day)
    tier = next(t for t in status["tiers"] if t["threshold"] == threshold)
    if not tier["claimable"]:
        raise ValueError("今日德扑下注流水尚未达到该档位")
    amount = tier["amount"]
    conn.execute("INSERT INTO holdem_reward_claims(user_id, day, threshold, amount, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (user[0], day, threshold, amount, int(now)))
    credit_coins(conn, username, amount, "holdem_daily_reward", f"每日德扑下注流水满 {threshold} 奖励",
                 ref=f"holdem_daily:{user[0]}:{day}:{threshold}")
    return amount, False


def rewards_state(conn, username, now):
    user = conn.execute("SELECT id, lottery_tickets, coins FROM users WHERE username = ?",
                        (username,)).fetchone()
    if user is None:
        raise ValueError("账号不存在，请重新登录")
    day = checkin_day(now)
    checked_in = conn.execute("SELECT 1 FROM daily_checkins WHERE user_id = ? AND day = ?",
                              (user[0], day)).fetchone() is not None
    midnight = datetime.fromtimestamp(now, CHECKIN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    recent = conn.execute("SELECT amount, created_at FROM lottery_draws WHERE user_id = ? "
                          "ORDER BY id DESC LIMIT 10", (user[0],)).fetchall()
    return {"username": username, "day": day, "checked_in": checked_in,
            "tickets": user[1], "coins": round(user[2], 2), "daily_tickets": CHECKIN_TICKETS,
            "server_time": int(now),
            "next_reset_at": int((midnight + timedelta(days=1)).timestamp()),
            "holdem_turnover": holdem_turnover_state(conn, user[0], day),
            "prize_tiers": [{"min": low, "max": high, "percent": weight}
                            for low, high, weight in PRIZE_TIERS],
            "history": [{"amount": amount, "created_at": created} for amount, created in recent]}


def claim_checkin(conn, username, now):
    user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise ValueError("账号不存在，请重新登录")
    inserted = conn.execute("INSERT OR IGNORE INTO daily_checkins(user_id, day, created_at) "
                            "VALUES (?, ?, ?)", (user[0], checkin_day(now), int(now))).rowcount
    if inserted:
        conn.execute("UPDATE users SET lottery_tickets = lottery_tickets + ? WHERE id = ?",
                     (CHECKIN_TICKETS, user[0]))
    return CHECKIN_TICKETS if inserted else 0


def pick_prize():
    """先按整百分比选档，再在该档的整数金币范围内均匀抽取（均含端点）。"""
    roll = secrets.randbelow(100)
    for low, high, weight in PRIZE_TIERS:
        if roll < weight:
            return low + secrets.randbelow(high - low + 1)
        roll -= weight
    raise AssertionError("奖池权重必须合计 100")


def draw_lottery(conn, username, request_id, now, credit_coins):
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
        raise ValueError("抽奖请求无效，请重新打开抽奖面板")
    user = conn.execute("SELECT id, lottery_tickets FROM users WHERE username = ?",
                        (username,)).fetchone()
    if user is None:
        raise ValueError("账号不存在，请重新登录")
    saved = conn.execute("SELECT amount FROM lottery_draws WHERE user_id = ? AND request_id = ?",
                         (user[0], request_id)).fetchone()
    if saved:
        return saved[0], True
    if user[1] <= 0:
        raise ValueError("抽奖机会不足，签到可领取 5 次机会")
    amount = pick_prize()
    conn.execute("UPDATE users SET lottery_tickets = lottery_tickets - 1 WHERE id = ?", (user[0],))
    draw = conn.execute("INSERT INTO lottery_draws(user_id, request_id, amount, created_at) "
                        "VALUES (?, ?, ?, ?)", (user[0], request_id, amount, int(now)))
    credit_coins(conn, username, amount, "lottery_win", "每日签到抽奖奖励",
                 ref=f"lottery:{draw.lastrowid}")
    return amount, False
