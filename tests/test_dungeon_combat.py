#!/usr/bin/env python3
"""单敌人自动战斗的确定性、阶段、时间边界与检查点验收。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.dungeon.catalog import CATALOG, DungeonConfigError, validate_catalog
from estate.dungeon.combat import (
    ENEMY_ID, PLAYER_ID, BattleSnapshot, CombatError, advance,
    attack_interval_us, calculate_damage, draw_uniform, make_battle_snapshot,
    simulate, start,
)


def fixture(*, player=None, enemy=None, combat=None, boss=False, seed=bytes(32)):
    catalog = deepcopy(CATALOG)
    if enemy:
        catalog["enemies"][0]["stats"].update(enemy)
    if combat:
        catalog["combat"].update(combat)
    if boss:
        catalog["enemies"][0]["type"] = "boss"
        catalog["enemies"][0]["phases"] = [
            {"phase_id": "phase_1", "threshold_bp": 10_000, "atk_bp": 10_000,
             "speed_bp": 10_000, "extra_attack_bp": 0},
            {"phase_id": "phase_2", "threshold_bp": 5_000, "atk_bp": 11_500,
             "speed_bp": 11_000, "extra_attack_bp": 0},
            {"phase_id": "phase_3", "threshold_bp": 2_000, "atk_bp": 13_000,
             "speed_bp": 12_000, "extra_attack_bp": 15_000},
        ]
    stats = deepcopy(CATALOG["base_stats"])
    if player:
        stats.update(player)
    return make_battle_snapshot("ruins_slime_01", "normal", stats, [], [], seed, catalog)


class DamageTests(unittest.TestCase):
    def test_defense_crit_minimum_and_actual_hp_loss(self):
        attacker = deepcopy(CATALOG["base_stats"])
        defender = deepcopy(CATALOG["enemies"][0]["stats"])
        attacker["atk"] = 120
        defender["defense"] = 50
        self.assertEqual(calculate_damage(attacker, defender, 10_000, False), 80)
        self.assertEqual(calculate_damage(attacker, defender, 10_000, True), 120)
        attacker["atk"] = 0
        defender["defense"] = 1_000_000
        self.assertEqual(calculate_damage(attacker, defender, 9_500, False), 1)

    def test_rounding_and_seeded_draw(self):
        self.assertEqual(attack_interval_us(100, CATALOG["combat"]), 2_000_000)
        self.assertEqual(attack_interval_us(150, CATALOG["combat"]), 1_333_333)
        self.assertEqual(attack_interval_us(500, CATALOG["combat"]), 400_000)
        self.assertEqual(draw_uniform(bytes(32).hex(), 0, 9_500, 10_500),
                         draw_uniform(bytes(32).hex(), 0, 9_500, 10_500))
        with self.assertRaises(CombatError) as raised:
            draw_uniform("invalid", 0, 0, 1)
        self.assertEqual(raised.exception.code, "invalid_seed")


class TimelineTests(unittest.TestCase):
    def test_attack_count_and_first_action_at_ten_seconds(self):
        for speed, count, first_us in ((100, 5, 1_000_000), (150, 8, 666_667)):
            with self.subTest(speed=speed):
                snapshot = fixture(player={"max_hp": 1_000_000, "atk": 0, "speed": speed},
                                   enemy={"max_hp": 1_000_000, "atk": 0, "speed": 25})
                initial = start(snapshot)
                step = advance(snapshot, initial.checkpoint, 10_000_000)
                attacks = [event for event in step.events if event["event_type"] == "AttackStarted"
                           and event["source"] == PLAYER_ID]
                self.assertEqual(len(attacks), count)
                self.assertEqual(attacks[0]["battle_time_us"], first_us)
                self.assertIsNone(step.result)

    def test_same_timestamp_uses_speed_then_stable_actor_id(self):
        for speed, first_actor in ((100, ENEMY_ID), (150, PLAYER_ID)):
            with self.subTest(speed=speed):
                snapshot = fixture(player={"max_hp": 1_000_000, "atk": 0, "speed": speed},
                                   enemy={"max_hp": 1_000_000, "atk": 0, "speed": 100})
                checkpoint = start(snapshot).checkpoint
                checkpoint["next_action_us"] = {PLAYER_ID: 1_000_000, ENEMY_ID: 1_000_000}
                step = advance(snapshot, checkpoint, 1_000_000)
                attacks = [event for event in step.events if event["event_type"] == "AttackStarted"]
                self.assertEqual(attacks[0]["source"], first_actor)

    def test_death_cancels_pending_same_time_action(self):
        snapshot = fixture(player={"max_hp": 100, "atk": 0, "speed": 100},
                           enemy={"atk": 1_000, "speed": 100},
                           combat={"variance_min_bp": 10_000, "variance_max_bp": 10_000})
        step = advance(snapshot, start(snapshot).checkpoint, 1_000_000)
        self.assertEqual(step.result["outcome"], "defeat")
        self.assertEqual(step.checkpoint["rng_counter"], 2)
        self.assertEqual([event["source"] for event in step.events
                          if event["event_type"] == "AttackStarted"], [ENEMY_ID])

    def test_timeout_precedes_attack_at_exact_boundary(self):
        snapshot = fixture(combat={"normal_timeout_us": 1_000_000},
                           player={"max_hp": 1_000_000, "atk": 0},
                           enemy={"max_hp": 1_000_000, "atk": 0, "speed": 100})
        step = advance(snapshot, start(snapshot).checkpoint, 1_000_000)
        self.assertEqual(step.result["outcome"], "timeout")
        self.assertEqual(step.result["duration_us"], 1_000_000)
        self.assertFalse(any(event["event_type"] == "AttackStarted" for event in step.events))

    def test_checkpoint_partition_matches_full_run_without_input_mutation(self):
        snapshot = fixture(player={"atk": 30}, enemy={"max_hp": 100})
        initial = start(snapshot)
        original = deepcopy(initial.checkpoint)
        full = advance(snapshot, initial.checkpoint, 60_000_000)
        checkpoint = initial.checkpoint
        events = []
        for target in range(500_000, 60_000_001, 500_000):
            part = advance(snapshot, checkpoint, target)
            checkpoint = part.checkpoint
            events.extend(part.events)
            if part.result:
                break
        self.assertEqual(original, initial.checkpoint)
        self.assertEqual(events, full.events)
        self.assertEqual(checkpoint, full.checkpoint)
        self.assertEqual(full.result, simulate(snapshot).result)
        self.assertEqual(full.result["event_count"], initial.checkpoint["sequence_id"] + len(full.events))
        self.assertEqual(advance(snapshot, full.checkpoint, 60_000_000).events, [])

    def test_zero_attack_still_damages_and_statistics_use_actual_hp_loss(self):
        snapshot = fixture(player={"atk": 0, "crit_bp": 0, "speed": 500},
                           enemy={"max_hp": 2, "atk": 0, "speed": 25})
        result = simulate(snapshot)
        self.assertEqual(result.result["outcome"], "victory")
        self.assertEqual(result.result["damage_dealt"], 2)
        self.assertEqual(result.result["damage_taken"], 0)
        self.assertEqual([event["hp_after"] for event in result.events
                          if event["event_type"] == "DamageApplied"], [1, 0])


class BossTests(unittest.TestCase):
    def test_crossing_two_thresholds_triggers_one_extra_attack(self):
        snapshot = fixture(boss=True,
                           player={"atk": 50, "crit_bp": 0, "speed": 200},
                           enemy={"max_hp": 100, "atk": 15, "defense": 0,
                                  "crit_bp": 0, "speed": 100},
                           combat={"variance_min_bp": 10_000, "variance_max_bp": 10_000})
        checkpoint = start(snapshot).checkpoint
        checkpoint["hp"][ENEMY_ID] = 60
        step = advance(snapshot, checkpoint, 500_000)
        self.assertEqual(step.checkpoint["hp"][ENEMY_ID], 10)
        self.assertEqual(step.checkpoint["triggered_phases"],
                         ["phase_1", "phase_2", "phase_3"])
        self.assertEqual([event["value"] for event in step.events
                          if event["event_type"] == "BossPhaseChanged"],
                         ["phase_2", "phase_3"])
        extras = [event for event in step.events if event["event_type"] == "DamageApplied"
                  and event["action_kind"] == "phase_extra"]
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0]["damage"], 28)
        self.assertEqual(step.checkpoint["next_action_us"][ENEMY_ID], 1_000_000)
        after = advance(snapshot, step.checkpoint, 1_500_000)
        self.assertEqual(after.checkpoint["triggered_phases"],
                         ["phase_1", "phase_2", "phase_3"])
        self.assertFalse(any(event["event_type"] == "BossPhaseChanged" for event in after.events))

    def test_lethal_hit_skips_unreached_phase_actions(self):
        snapshot = fixture(boss=True,
                           player={"atk": 200, "crit_bp": 0, "speed": 200},
                           enemy={"max_hp": 100, "defense": 0},
                           combat={"variance_min_bp": 10_000, "variance_max_bp": 10_000})
        result = simulate(snapshot)
        self.assertEqual(result.result["outcome"], "victory")
        self.assertEqual(result.result["damage_dealt"], 100)
        self.assertFalse(any(event["event_type"] == "BossPhaseChanged" for event in result.events))
        self.assertFalse(any(event.get("action_kind") == "phase_extra" for event in result.events))

    def test_invalid_phase_order_is_rejected_before_snapshot(self):
        catalog = deepcopy(CATALOG)
        catalog["enemies"][0]["type"] = "boss"
        catalog["enemies"][0]["phases"] = [
            {"phase_id": "first", "threshold_bp": 10_000, "atk_bp": 10_000,
             "speed_bp": 10_000, "extra_attack_bp": 0},
            {"phase_id": "later", "threshold_bp": 10_000, "atk_bp": 10_000,
             "speed_bp": 10_000, "extra_attack_bp": 0},
        ]
        with self.assertRaises(DungeonConfigError):
            validate_catalog(catalog)


class SnapshotTests(unittest.TestCase):
    def test_hash_mismatch_and_old_version_are_rejected(self):
        snapshot = fixture()
        with self.assertRaises(CombatError) as raised:
            start(BattleSnapshot(snapshot.snapshot_json, "incorrect"))
        self.assertEqual(raised.exception.code, "invalid_snapshot")
        old = snapshot.as_dict()
        old["simulation_version"] = 99
        import hashlib
        import json
        raw = json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.assertRaises(CombatError) as raised:
            start(BattleSnapshot(raw, hashlib.sha256(raw.encode()).hexdigest()))
        self.assertEqual(raised.exception.code, "unsupported_version")


if __name__ == "__main__":
    unittest.main()
