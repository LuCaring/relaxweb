"""德扑逐手摘要与累计统计。宿主提供事务；这里不连接数据库、不保存底牌。

金额以整数分存储，比例由累计分子/分母计算，绝不累计已取整的百分比。
新统计仅随新的真实结算写入，不从旧 rating_history 猜测行动或回填历史。
"""
import time
from decimal import Decimal, ROUND_HALF_UP


COUNTERS = (
    "hands", "wins", "folds", "manual_folds", "timeout_folds", "leave_folds",
    "vpip_hands", "pfr_hands", "flop_hands", "showdown_hands", "showdown_wins",
    "aggressive_actions", "call_actions",
)
TOTALS = (*COUNTERS, "net_cents", "score_delta", "net_bb")


def init_holdem_stats(conn):
    # 一行元数据固定启用时间，重启/重复迁移不能移动统计起点。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS holdem_stats_metadata (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            started_at INTEGER NOT NULL
        )
    """)
    conn.execute("INSERT OR IGNORE INTO holdem_stats_metadata VALUES (1, ?)",
                 (int(time.time()),))
    counters = ", ".join(f"{name} INTEGER NOT NULL DEFAULT 0 CHECK ({name} >= 0)"
                         for name in COUNTERS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS holdem_hand_stats (
            hand_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            initial_cents INTEGER NOT NULL CHECK (initial_cents > 0),
            final_cents INTEGER NOT NULL CHECK (final_cents >= 0),
            big_blind_cents INTEGER NOT NULL CHECK (big_blind_cents > 0),
            {counters},
            net_cents INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            net_bb REAL NOT NULL,
            settlement_reason TEXT NOT NULL CHECK (settlement_reason IN ('completed', 'leave')),
            created_at INTEGER NOT NULL,
            PRIMARY KEY (hand_id, user_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdem_stats_user "
                 "ON holdem_hand_stats(user_id, created_at)")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS holdem_player_stats (
            user_id INTEGER PRIMARY KEY,
            {counters},
            net_cents INTEGER NOT NULL DEFAULT 0,
            score_delta INTEGER NOT NULL DEFAULT 0,
            net_bb REAL NOT NULL DEFAULT 0
        )
    """)


def cents(amount):
    value = Decimal(str(amount))
    if not value.is_finite():
        raise ValueError("德扑统计金额必须是有限数值")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def record_holdem_hand(conn, hand_id, user_id, initial, final, delta, snapshot):
    """在宿主的段位事务中写入一手；只有新摘要才能递增累计统计。"""
    initial_cents, final_cents = cents(initial), cents(final)
    big_blind_cents = cents(snapshot["big_blind"])
    if initial_cents <= 0 or final_cents < 0 or big_blind_cents <= 0:
        raise ValueError("德扑统计筹码或盲注无效")
    net = final_cents - initial_cents
    folded = bool(snapshot["folded"])
    reason = snapshot["fold_reason"]
    if (folded and reason not in ("manual", "timeout", "leave")) or (not folded and reason):
        raise ValueError("德扑弃牌统计原因无效")
    flop, showdown = bool(snapshot["saw_flop"]), bool(snapshot["showdown"])
    if showdown and (not flop or folded):
        raise ValueError("德扑摊牌统计状态无效")
    vpip, pfr = bool(snapshot["vpip"]), bool(snapshot["pfr"])
    if pfr and not vpip:
        raise ValueError("德扑加注必须计入主动入池")
    values = {
        "hands": 1, "wins": int(net > 0), "folds": int(folded),
        "manual_folds": int(reason == "manual"), "timeout_folds": int(reason == "timeout"),
        "leave_folds": int(reason == "leave"), "vpip_hands": int(vpip),
        "pfr_hands": int(pfr), "flop_hands": int(flop),
        "showdown_hands": int(showdown), "showdown_wins": int(showdown and net > 0),
        "aggressive_actions": snapshot["aggressive_actions"], "call_actions": snapshot["call_actions"],
        "net_cents": net, "score_delta": delta, "net_bb": float(Decimal(net) / big_blind_cents),
    }
    for key in ("aggressive_actions", "call_actions"):
        if type(values[key]) is not int or values[key] < 0:
            raise ValueError("德扑动作次数无效")
    columns = ", ".join(TOTALS)
    placeholders = ", ".join("?" for _ in TOTALS)
    inserted = conn.execute(
        f"INSERT INTO holdem_hand_stats "
        f"(hand_id, user_id, initial_cents, final_cents, big_blind_cents, {columns}, "
        f"settlement_reason, created_at) VALUES (?, ?, ?, ?, ?, {placeholders}, ?, ?) "
        "ON CONFLICT(hand_id, user_id) DO NOTHING",
        (hand_id, user_id, initial_cents, final_cents, big_blind_cents,
         *(values[key] for key in TOTALS), snapshot["settlement_reason"], int(time.time())),
    ).rowcount
    if inserted:
        updates = ", ".join(f"{key} = {key} + excluded.{key}" for key in TOTALS)
        conn.execute(
            f"INSERT INTO holdem_player_stats (user_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {updates}",
            (user_id, *(values[key] for key in TOTALS)),
        )
    return bool(inserted)


def public_holdem_stats(row=None):
    """仅返回公开累计数据；所有概率为 0..1，无分母返回 JSON null。"""
    totals = dict(zip(TOTALS, row)) if row is not None else dict.fromkeys(TOTALS, 0)
    result = {key: totals[key] for key in COUNTERS}
    result.update(net_profit=totals["net_cents"] / 100, score_delta=totals["score_delta"],
                  net_bb=round(totals["net_bb"], 8), small_sample=totals["hands"] < 100)
    hands = totals["hands"]

    def ratio(numerator, denominator):
        return round(numerator / denominator, 8) if denominator else None

    for name, numerator in (("win_rate", "wins"), ("fold_rate", "folds"),
                            ("vpip", "vpip_hands"), ("pfr", "pfr_hands"),
                            ("flop_rate", "flop_hands")):
        result[name] = ratio(totals[numerator], hands)
    result.update(
        score_per_hand=ratio(totals["score_delta"], hands),
        profit_per_hand=ratio(totals["net_cents"] / 100, hands),
        bb_per_100=ratio(totals["net_bb"] * 100, hands),
        wtsd=ratio(totals["showdown_hands"], totals["flop_hands"]),
        showdown_win_rate=ratio(totals["showdown_wins"], totals["showdown_hands"]),
        af=ratio(totals["aggressive_actions"], totals["call_actions"]),
        af_no_calls=totals["aggressive_actions"] > 0 and totals["call_actions"] == 0,
    )
    return result


def load_holdem_stats(conn, user_ids):
    """一次批量查询当前页和本人，避免 N+1 或全量逐手扫描。"""
    if not user_ids:
        return {}
    placeholders = ", ".join("?" for _ in user_ids)
    rows = conn.execute(
        f"SELECT user_id, {', '.join(TOTALS)} FROM holdem_player_stats "
        f"WHERE user_id IN ({placeholders})", tuple(user_ids),
    ).fetchall()
    return {row[0]: public_holdem_stats(row[1:]) for row in rows}


def rebuild_holdem_stats(conn):
    """运维/审计入口：调用方持有写事务时，从摘要重建全部累计值。"""
    conn.execute("DELETE FROM holdem_player_stats")
    conn.execute(
        f"INSERT INTO holdem_player_stats (user_id, {', '.join(TOTALS)}) "
        f"SELECT user_id, {', '.join(f'SUM({key})' for key in TOTALS)} "
        "FROM holdem_hand_stats GROUP BY user_id"
    )
