"""Bounded integer-minor-unit adapter for the existing shared REAL wallet."""

from decimal import Decimal, InvalidOperation
import math

from server.wallet import adjust_coins
from dungeon.domain.errors import DungeonError

MAX_MINOR = 10**12


def _minor(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise DungeonError("invalid_wallet", "金币余额格式异常")
    if isinstance(value, float) and not math.isfinite(value):
        raise DungeonError("invalid_wallet", "金币余额格式异常")
    try:
        amount = Decimal(str(value)) * 100
    except InvalidOperation as error:
        raise DungeonError("invalid_wallet", "金币余额格式异常") from error
    if not amount.is_finite() or amount != amount.to_integral_value() or not 0 <= amount <= MAX_MINOR:
        raise DungeonError("invalid_wallet", "金币余额超出支持范围或精度异常")
    return int(amount)


class SharedWalletPort:
    """Every operation uses the caller's connection and transaction."""

    def balance(self, conn, username):
        row = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            raise DungeonError("auth_required", "用户不存在")
        return _minor(row[0])

    def change(self, conn, username, delta_minor, *, kind, ref, detail=""):
        if isinstance(delta_minor, bool) or not isinstance(delta_minor, int) or abs(delta_minor) > MAX_MINOR:
            raise DungeonError("invalid_request", "金币金额无效")
        before = self.balance(conn, username)
        after = before + delta_minor
        if after < 0:
            raise DungeonError("insufficient_funds", "金币不足")
        if after > MAX_MINOR:
            raise DungeonError("invalid_wallet", "金币余额超出支持范围")
        # Legacy primitive rounds to cents and writes its existing audit row.
        adjust_coins(conn, username, delta_minor / 100, kind, detail, ref)
        if self.balance(conn, username) != after:
            raise DungeonError("invalid_wallet", "金币余额精度校验失败")
        return after
