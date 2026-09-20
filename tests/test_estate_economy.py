"""Deterministic economy simulations; run directly to print the v1 audit."""
import random
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from estate.activities import make_board, pick_fishing_catch
from estate.catalog import BAITS, CROPS, MINERALS, TOOLS

SAMPLES = 20000


def fishing_audit(level, bait_id):
    rng = random.Random(190926 + level)
    catches = [pick_fishing_catch(rng, BAITS[bait_id], {"level": level})[1]
               for _ in range(SAMPLES)]
    mean_sale = sum(c.get("sell_price", 0) for c in catches) / SAMPLES
    rod = TOOLS["rod"][level]
    cost = BAITS[bait_id]["price"] + rod["repair_price"] / rod["max_durability"]
    return {"net_at_70_percent_catch": mean_sale * .7 - cost,
            "net_at_full_catch": mean_sale - cost,
            "legendary_rate": sum(c.get("sell_price", 0) > 1000 for c in catches) / SAMPLES}


def mining_audit(level):
    gross = bombs = losses = 0
    rule = TOOLS["pickaxe"][level]
    cost = rule["repair_price"] / rule["max_durability"]
    for seed in range(SAMPLES):
        board = make_board(seed, level)
        strikes, value = rule["strikes"], 0
        for cell in board:  # 隐藏棋盘的固定点击顺序，不窥视选择矿物。
            if strikes <= 0:
                break
            strikes -= 1
            if cell == "bomb":
                bombs += 1
                break
            if cell == "extra":
                strikes += 2
            elif cell in MINERALS:
                value += MINERALS[cell]["sell_price"]
        gross += value
        losses += value < cost
    return {"mean_net": gross / SAMPLES - cost, "bomb_rate": bombs / SAMPLES,
            "loss_rate": losses / SAMPLES}


class EstateEconomyTests(unittest.TestCase):
    def test_fishing_covers_costs_with_imperfect_play_and_keeps_legends_rare(self):
        for level, bait in ((1, "worm"), (2, "worm"), (2, "glow_grub"), (3, "worm"), (3, "glow_grub")):
            with self.subTest(level=level, bait=bait):
                audit = fishing_audit(level, bait)
                print(f"Fishing Lv{level} {bait}: {audit}")
                self.assertGreater(audit["net_at_70_percent_catch"], 10)
                self.assertLess(audit["net_at_full_catch"], 150)
                self.assertLess(audit["legendary_rate"], .005)

    def test_deeper_mines_increase_mean_reward_and_risk(self):
        previous = None
        for level in (1, 2, 3):
            audit = mining_audit(level)
            print(f"Mining Lv{level}: {audit}")
            self.assertGreater(audit["mean_net"], 15)
            self.assertLess(audit["mean_net"], 150)
            self.assertGreater(audit["loss_rate"], .02)
            if previous:
                self.assertGreater(audit["mean_net"], previous["mean_net"])
                self.assertGreater(audit["bomb_rate"], previous["bomb_rate"])
            previous = audit

    def test_farming_retains_profitable_long_term_progression(self):
        for crop in CROPS.values():
            hourly = (crop["sell_price"] * crop["yield"] - crop["seed_price"]) * 3600 / crop["grow_seconds"]
            self.assertGreaterEqual(hourly, 90)
            self.assertLessEqual(hourly, 211)
            self.assertGreaterEqual(crop["xp"] * 3600 / crop["grow_seconds"], 30)
        self.assertEqual(CROPS["wheat"]["seed_price"], 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
