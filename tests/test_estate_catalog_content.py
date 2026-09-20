#!/usr/bin/env python3
"""休闲庄园扩充内容目录契约。"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.catalog import (
    CROPS, FISH, FISHING_TREASURES, MINING_LEVELS, TOOLS, item_info, public_catalog,
)
from estate import catalog as rule_source


class EstateCatalogContentTests(unittest.TestCase):
    def test_catalog_has_twenty_four_crops_and_fish(self):
        self.assertEqual(len(CROPS), 24)
        self.assertEqual(len(FISH), 24)
        self.assertEqual(len(FISHING_TREASURES), 4)

    def test_rare_content_is_present_and_endgame_rare(self):
        self.assertEqual(CROPS["xiaopang_grass"]["name"], "神奇草")
        self.assertEqual(CROPS["xiaopang_flower"]["name"], "奇迹花")
        self.assertGreaterEqual(CROPS["xiaopang_grass"]["unlock_level"], 6)
        self.assertEqual(FISH["xiaopang_fish"]["name"], "神秘鱼")
        self.assertGreaterEqual(FISH["xiaopang_fish"]["rarity"], 6)
        underwear = FISHING_TREASURES["xiaopang_underwear"]
        self.assertEqual(underwear["name"], "一条不知道是谁的内裤")
        self.assertEqual(underwear["rarity"], max(v["rarity"] for v in FISHING_TREASURES.values()))
        self.assertGreaterEqual(underwear["required_rod_level"], 3)

    def test_collectibles_are_public_inventory_items(self):
        catalog = public_catalog()
        self.assertIn("fishing_treasures", catalog)
        info = item_info("collectible:xiaopang_underwear")
        self.assertEqual(info["kind"], "collectible")
        self.assertFalse(info["sellable"])
        self.assertEqual(info["name"], "一条不知道是谁的内裤")

    def test_catalog_uses_reviewed_economy_version(self):
        self.assertEqual(public_catalog()["balance_version"], "v1")
        for entry in (*CROPS.values(), *FISH.values(), *FISHING_TREASURES.values()):
            self.assertEqual(entry["balance_status"], "v1")

    def test_shipped_fishing_rules_match_server_constants(self):
        """客户端按这些值推进进度条，必须与服务端实现同源，不能漂移。"""
        rules = public_catalog()["fishing_rules"]
        expected = {
            "steps": rule_source.FISHING_STEPS,
            "steps_per_frame": rule_source.FISHING_STEPS_PER_FRAME,
            "tension_start": rule_source.TENSION_START,
            "progress_start": rule_source.PROGRESS_START,
            "hold_tension_gain": rule_source.HOLD_TENSION_GAIN,
            "hold_tension_force_base": rule_source.HOLD_TENSION_FORCE_BASE,
            "hold_progress_gain": rule_source.HOLD_PROGRESS_GAIN,
            "hold_progress_base": rule_source.HOLD_PROGRESS_BASE,
            "hold_progress_force_scale": rule_source.HOLD_PROGRESS_FORCE_SCALE,
            "release_tension_drop": rule_source.RELEASE_TENSION_DROP,
            "release_progress_drop": rule_source.RELEASE_PROGRESS_DROP,
            "release_progress_force_base": rule_source.RELEASE_PROGRESS_FORCE_BASE,
            "snapped_at": rule_source.TENSION_SNAPPED_AT,
            "caught_at": rule_source.PROGRESS_CAUGHT_AT,
        }
        self.assertEqual(rules, expected)

    def test_shipped_mining_rules_match_server_constants(self):
        rules = public_catalog()["mining_rules"]
        self.assertEqual(rules, {
            "cells": rule_source.MINE_CELLS,
            "board_size": rule_source.MINE_BOARD_SIZE,
            "extra_cells": rule_source.MINE_EXTRA_CELLS,
        })

    def test_rod_tension_factors_are_available_to_clients(self):
        """鱼竿系数已随 tools 下发，客户端不应另存一份。"""
        catalog = public_catalog()
        for level, rule in TOOLS["rod"].items():
            self.assertEqual(catalog["tools"]["rod"][level]["tension_factor"],
                             rule["tension_factor"])
        # 客户端拿到的是 JSON：数值键在传输后会变成字符串键。
        shipped = json.loads(json.dumps(catalog))["tools"]["rod"]
        for level in TOOLS["rod"]:
            self.assertIn(str(level), shipped)

    def test_new_catalog_keys_do_not_disturb_existing_ones(self):
        catalog = public_catalog()
        for key in ("balance_version", "crops", "land_levels", "plot_unlocks",
                    "warehouse_levels", "tools", "baits", "fish",
                    "fishing_treasures", "minerals", "mining_levels",
                    "initial_plots", "max_plots"):
            self.assertIn(key, catalog)
        self.assertEqual(catalog["mining_levels"], MINING_LEVELS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
