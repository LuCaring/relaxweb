"""Pure trade pricing policy. Every parameter comes from the frozen economy pack."""

from dungeon.domain.errors import DungeonError

MAX_FEE_BP = 10000


def _whole(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DungeonError("invalid_config", f"{label}配置无效")
    return value


class TradePolicy:
    """Buyer pays ``price_minor``; seller nets ``price_minor - fee``; the fee is burned.

    The fee floors at ``price * fee_bp // 10000`` so integer minor units never
    split; every figure is frozen when the offer is created.
    """

    def __init__(self, policy_version, fee_bp, minimum_price_minor,
                 maximum_price_minor, ttl_seconds):
        self.policy_version = _whole(policy_version, "交易策略版本")
        self.fee_bp = _whole(fee_bp, "交易手续费比例")
        self.minimum_price_minor = _whole(minimum_price_minor, "最低交易价格")
        self.maximum_price_minor = _whole(maximum_price_minor, "最高交易价格")
        self.ttl_seconds = _whole(ttl_seconds, "报价有效期")
        if self.policy_version < 1 or self.fee_bp > MAX_FEE_BP or self.minimum_price_minor < 1:
            raise DungeonError("invalid_config", "交易策略参数超出范围")
        if self.maximum_price_minor < self.minimum_price_minor or self.ttl_seconds < 60:
            raise DungeonError("invalid_config", "交易策略参数超出范围")

    @classmethod
    def from_economy(cls, economy):
        policy = economy["trade_policy"]
        return cls(policy["policy_version"], policy["fee_bp"],
                   policy["minimum_price_minor"], policy["maximum_price_minor"],
                   policy["ttl_seconds"])

    def validate_price(self, price_minor):
        """Client-facing input check; policy bounds are not a quote change."""
        if (isinstance(price_minor, bool) or not isinstance(price_minor, int)
                or price_minor < self.minimum_price_minor or price_minor > self.maximum_price_minor):
            raise DungeonError("invalid_request", "报价金额超出允许范围")

    def quote(self, price_minor, now_seconds):
        self.validate_price(price_minor)
        fee_minor = price_minor * self.fee_bp // MAX_FEE_BP
        return {"price_minor": price_minor, "fee_minor": fee_minor,
                "seller_net_minor": price_minor - fee_minor,
                "policy_version": self.policy_version,
                "expires_at": int(now_seconds) + self.ttl_seconds}
