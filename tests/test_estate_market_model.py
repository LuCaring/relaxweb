"""星潮模拟指数价格模型的统计性质。

参数标定见 ``docs/stock-market-design.md`` §2.3：半衰期 2.5 天、稳态 σ 22%、
软墙 0.60×ln1.6 起加速、硬顶 ±60%。测试直接跑纯函数 ``step_deviation``，
不写数据库，因此可以在毫秒级重复几十万分钟。固定种子，断言区间取宽，
只用来锁住"波动量级/边界/无系统偏差"这三件事，不追求复现文档里的精确值。
"""
import math
import random
import statistics
import unittest

from estate.market import (
    BAND,
    SLOTS_PER_DAY,
    SLOTS_PER_MINUTE,
    SLOTS_PER_WEEK,
    INVENTORY_COEF,
    MARKET_SYMBOLS,
    WALL_K,
    cap_milli_for_price,
    inventory_skew,
    price_from,
    step_deviation,
    theta_effective,
)


BASE = MARKET_SYMBOLS[0]          # 基准档标的：XTIDE
VOLATILE = MARKET_SYMBOLS[-1]     # 最刺激的一档：σ 最大、半衰期最短


SEEDS = (20260926, 1)
# 行情每 SLOT_SECONDS 秒走一步；统计窗口按"格"计，换算成日/周看波动。
STEPS = SLOTS_PER_DAY * 30


def deviation_path(seed, steps=STEPS, skew=0.0, item=BASE, steps_per_minute=SLOTS_PER_MINUTE):
    rng = random.Random(seed)
    state = rng.gauss(0.0, item["sigma"])
    half_life_steps = item["half_life_minutes"] * steps_per_minute
    values = []
    for _ in range(steps):
        state, deviation = step_deviation(state, skew, rng.gauss(0.0, 1.0),
                                          item["sigma"], half_life_steps)
        values.append(deviation)
    return values


def stdev(values):
    return statistics.pstdev(values)


class PriceModelTests(unittest.TestCase):
    def test_no_hard_cap_but_the_tail_is_thin(self):
        """不设涨跌停：可以越过 ±60%，但越远越罕见。"""
        for seed in SEEDS:
            values = deviation_path(seed)
            beyond = sum(1 for value in values if abs(value) > BAND) / len(values)
            extreme = sum(1 for value in values if abs(value) > math.log(1.8)) / len(values)
            self.assertLess(beyond, 0.015, (seed, beyond))
            self.assertLess(extreme, 0.005, (seed, extreme))
        self.assertGreater(max(abs(value) for value in deviation_path(SEEDS[0])), BAND)

    def test_daily_and_weekly_volatility_stay_in_design_range(self):
        daily, weekly = [], []
        for seed in SEEDS:
            values = deviation_path(seed)
            daily.append(stdev([values[i + SLOTS_PER_DAY] - values[i]
                                for i in range(len(values) - SLOTS_PER_DAY)]))
            weekly.append(stdev([values[i + SLOTS_PER_WEEK] - values[i]
                                 for i in range(len(values) - SLOTS_PER_WEEK)]))
        # 纯 OU 分量：日σ 约 14.3%、周σ 约 21.6%（库存分量在交易时另外叠加）。
        self.assertTrue(0.11 <= statistics.mean(daily) <= 0.18, daily)
        self.assertTrue(0.18 <= statistics.mean(weekly) <= 0.28, weekly)

    def test_noise_has_no_systematic_bias(self):
        """软墙只压波动，不该把价格整体推离锚。"""
        means = [statistics.mean(deviation_path(seed)) for seed in SEEDS + (2, 3, 4, 5)]
        self.assertLess(abs(statistics.mean(means)), 0.08, means)

    def test_soft_wall_suppresses_extremes(self):
        for seed in SEEDS:
            values = deviation_path(seed)
            self.assertLess(max(abs(value) for value in values), math.log(2.6), seed)
            self.assertLess(sum(1 for value in values if abs(value) > 0.8) / len(values), 0.005, seed)

    def test_step_is_a_pure_mean_reverting_update(self):
        steps = BASE["half_life_minutes"] * SLOTS_PER_MINUTE
        state, deviation = step_deviation(0.30, 0.0, 0.0, BASE["sigma"], steps)
        self.assertAlmostEqual(state, 0.30 * math.exp(-theta_effective(0.30, steps)), places=12)
        self.assertEqual(deviation, state)
        # 未触及软墙时退化为普通 OU：半衰期由标的配置决定。
        plain, _ = step_deviation(0.05, 0.0, 0.0, BASE["sigma"], steps)
        self.assertAlmostEqual(plain, 0.05 * math.exp(-math.log(2) / steps), places=12)
    def test_step_size_does_not_change_the_volatility(self):
        """撮合从每分钟改成每 10 秒只改粒度：日波动必须一致。"""
        days = 60
        coarse = deviation_path(7, steps=1440 * days, steps_per_minute=1)
        fine = deviation_path(7, steps=1440 * days * SLOTS_PER_MINUTE)
        coarse_daily = stdev([coarse[i + 1440] - coarse[i] for i in range(len(coarse) - 1440)])
        fine_daily = stdev([fine[i + SLOTS_PER_DAY] - fine[i]
                            for i in range(len(fine) - SLOTS_PER_DAY)])
        self.assertAlmostEqual(coarse_daily, fine_daily, delta=0.05)
        self.assertTrue(0.11 <= fine_daily <= 0.20, fine_daily)

    def test_inventory_skew_shifts_the_distribution_without_widening_it(self):
        cap = cap_milli_for_price(100000)
        skew = inventory_skew(-cap * 0.2, cap)      # 玩家买掉两成额度
        self.assertAlmostEqual(skew, INVENTORY_COEF * 0.2, places=12)
        shifted = deviation_path(7, skew=skew)
        plain = deviation_path(7)
        self.assertAlmostEqual(statistics.mean(shifted) - statistics.mean(plain), skew, delta=0.05)
        self.assertLess(stdev(shifted), stdev(plain) + 0.02)

    def test_band_is_the_soft_wall_reference_not_a_cap(self):
        anchor = 100000.0
        self.assertEqual(price_from(anchor, BAND), 160000)
        self.assertEqual(price_from(anchor, -BAND), 62500)
        self.assertEqual(price_from(anchor, 0.0), 100000)
        # 价格可以越过 ±60%（没有涨跌停），只是几乎不会走远
        self.assertGreater(price_from(anchor, math.log(2.0)), 160000)
        # 锚按每周漂移复利：价格区间整体上移，不允许撞到任何固定上下限。
        year = anchor * (1 + BASE["weekly_growth"]) ** 52
        self.assertGreater(price_from(year, BAND), 160000)
        self.assertGreater(price_from(year, -BAND), 62500)

    def test_every_symbol_is_configured_and_scales_with_its_own_volatility(self):
        from estate.market import INVENTORY_CAP_COINS
        for item in MARKET_SYMBOLS:
            self.assertGreater(item["weekly_growth"], 0)
            self.assertGreater(item["sigma"], 0)
            self.assertGreater(item["half_life_minutes"], 0)
        # 全市场总额度按标的数均分：单只标的在基准点位下就是 200 万 / N
        per_symbol = INVENTORY_CAP_COINS // len(MARKET_SYMBOLS)
        self.assertEqual(cap_milli_for_price(100000) / 1000 * 1000, per_symbol)
        # 波动最大的标的，其日σ 必须显著高于基准档
        calm = statistics.pstdev([value for value in deviation_path(11, item=BASE)])
        wild = statistics.pstdev([value for value in deviation_path(11, item=VOLATILE)])
        self.assertGreater(wild, calm)


if __name__ == "__main__":
    unittest.main()
