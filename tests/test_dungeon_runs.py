#!/usr/bin/env python3
"""M3 持久化挑战、控制、奖励事务、并发与故障恢复。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.dungeon.catalog import CATALOG
from estate.dungeon.runs import progress_run, read_run, start_run
from estate.dungeon.service import DungeonError, dungeon_state
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins


class DungeonRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = partial(database, str(Path(self.tmp.name) / "runs.db"))
        init_db(self.db)
        with self.db() as conn, conn:
            conn.executemany("""INSERT INTO users
                (username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,1000)""", ((name,) for name in ("alice", "bob")))
            self.initial = dungeon_state(conn, "alice", 100)
            dungeon_state(conn, "bob", 100)

    def start(self, request_id="start-1", *, username="alice", version=None,
              catalog=CATALOG, now_ms=100_000):
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return start_run(conn, username, request_id, "ruins_slime_01", "normal",
                             self.initial["profile_version"] if version is None else version,
                             now_ms, seed=bytes(32), catalog=catalog)

    def sync(self, battle_id, now_ms, **kwargs):
        return progress_run(self.db, "alice", battle_id, now_ms, adjust_coins, **kwargs)

    def test_start_is_idempotent_and_one_active_job_per_account(self):
        first = self.start()
        battle_id = first["battle"]["battle_id"]
        replay = self.start()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["battle"]["battle_id"], battle_id)
        with self.assertRaises(DungeonError) as conflict:
            self.start("start-2", version=first["profile_version"])
        self.assertEqual(conflict.exception.code, "active_job")
        other = self.start("bob-start", username="bob")
        self.assertNotEqual(other["battle"]["battle_id"], battle_id)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_runs").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_active_jobs").fetchone()[0], 2)
            saved = conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=?",
                                 (battle_id,)).fetchone()[0]
            self.assertEqual(json.loads(saved)["reward_table"]["coins"], 20)
            self.assertEqual(json.loads(saved)["affix_rules"]["version"], "ruins-affixes-v1")
            self.assertNotIn("seed", json.dumps(first))

    def test_concurrent_start_only_one_succeeds(self):
        def attempt(index):
            try:
                return self.start(f"concurrent-{index}")
            except DungeonError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("version_conflict", results)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_runs").fetchone()[0], 1)

    def test_victory_rewards_once_and_restarts_from_saved_result(self):
        battle_id = self.start()["battle"]["battle_id"]
        first = self.sync(battle_id, 115_000)
        self.assertEqual(first["battle"]["status"], "settled")
        self.assertEqual(first["battle"]["result"]["outcome"], "victory")
        self.assertEqual(first["battle"]["result"]["coins_gained"], 20)
        self.assertEqual(len(first["battle"]["result"]["items"]), 1)
        self.assertEqual(first["battle"]["result"]["currencies"]["transmutation"], 1)
        self.assertEqual(first["battle"]["result"]["items"][0]["item_level"], 35)
        self.assertEqual(len(first["battle"]["result"]["items"][0]["affixes"]), 1)
        self.assertEqual([event["sequence_id"] for event in first["events"]],
                         list(range(1, first["battle"]["last_sequence"] + 1)))
        item_id = first["battle"]["result"]["items"][0]["item_id"]
        again = self.sync(battle_id, 300_000, after_sequence=first["next_cursor"])
        self.assertFalse(again["changed"])
        self.assertEqual(again["events"], [])
        with self.db() as conn:
            self.assertEqual(read_run(conn, "alice", battle_id)["result"],
                             first["battle"]["result"])
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 1020)
            self.assertEqual(conn.execute("SELECT clear_count FROM dungeon_progress WHERE username='alice'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_reward'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items WHERE item_id=?",
                                          (item_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("""SELECT amount FROM dungeon_currency
                WHERE username='alice' AND currency_id='transmutation'""").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_active_jobs WHERE username='alice'").fetchone()[0], 0)

    def test_reward_uses_frozen_catalog_copy(self):
        catalog = deepcopy(CATALOG)
        battle_id = self.start(catalog=catalog)["battle"]["battle_id"]
        catalog["reward_tables"][0]["coins"] = 9999
        catalog["items"][-1]["stats"]["atk"] = 9999
        awarded = self.sync(battle_id, 115_000)["battle"]["result"]
        self.assertEqual(awarded["coins_gained"], 20)
        self.assertEqual(awarded["items"][0]["stats"]["atk"], 38)

    def test_cross_difficulty_clear_unlocks_following_challenge(self):
        catalog = deepcopy(CATALOG)
        hard = deepcopy(catalog["challenges"][0])
        hard["difficulty_id"] = "hard"
        hard["requires"] = [{"challenge_id": "ruins_slime_01", "difficulty_id": "normal"}]
        catalog["challenges"].append(hard)
        with self.db() as conn, conn:
            version = dungeon_state(conn, "alice", 100, catalog)["profile_version"]
        battle_id = self.start(catalog=catalog, version=version)["battle"]["battle_id"]
        self.sync(battle_id, 115_000)
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT unlocked FROM dungeon_progress
                WHERE username='alice' AND challenge_id='ruins_slime_01'
                AND difficulty_id='hard'""").fetchone()[0], 1)

    def test_start_replay_reads_current_battle_after_settlement(self):
        battle_id = self.start()["battle"]["battle_id"]
        self.sync(battle_id, 115_000)
        replay = self.start()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["battle"]["status"], "settled")

    def test_old_reward_snapshot_and_unknown_reward_version(self):
        battle_id = self.start()["battle"]["battle_id"]
        with self.db() as conn, conn:
            raw = json.loads(conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=?",
                                          (battle_id,)).fetchone()[0])
            raw["reward_rng_version"] = 99
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE dungeon_runs SET snapshot_json=?,snapshot_hash=? WHERE battle_id=?",
                         (encoded, hashlib.sha256(encoded.encode()).hexdigest(), battle_id))
        with self.assertRaises(DungeonError) as error:
            self.sync(battle_id, 115_000)
        self.assertEqual(error.exception.code, "unsupported_version")
        with self.db() as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 0)
            raw.pop("reward_rng_version")
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE dungeon_runs SET snapshot_json=?,snapshot_hash=? WHERE battle_id=?",
                         (encoded, hashlib.sha256(encoded.encode()).hexdigest(), battle_id))
        self.assertEqual(self.sync(battle_id, 115_000)["battle"]["result"]["outcome"], "victory")

    def test_pause_resume_rate_and_control_replay(self):
        battle = self.start()["battle"]
        battle_id = battle["battle_id"]
        paused = self.sync(battle_id, 101_000, command="pause", request_id="pause-1",
                           expected_revision=battle["revision"])
        self.assertEqual(paused["battle"]["status"], "paused")
        frozen_time = paused["battle"]["sim_time_us"]
        still = self.sync(battle_id, 110_000)
        self.assertEqual(still["battle"]["sim_time_us"], frozen_time)
        replay = self.sync(battle_id, 111_000, command="pause", request_id="pause-1",
                           expected_revision=battle["revision"])
        self.assertTrue(replay["replayed"])
        with self.assertRaises(DungeonError) as conflict:
            self.sync(battle_id, 111_000, command="resume", request_id="resume-stale",
                      expected_revision=battle["revision"])
        self.assertEqual(conflict.exception.code, "state_conflict")
        resumed = self.sync(battle_id, 111_000, command="resume", request_id="resume-1",
                            expected_revision=paused["battle"]["revision"])
        faster = self.sync(battle_id, 112_000, command="set_rate", request_id="rate-1",
                           expected_revision=resumed["battle"]["revision"], rate=2)
        self.assertEqual(faster["battle"]["playback_rate"], 2)
        self.assertEqual(faster["battle"]["sim_time_us"], frozen_time + 1_000_000)
        advanced = self.sync(battle_id, 113_000)
        self.assertEqual(advanced["battle"]["sim_time_us"], frozen_time + 3_000_000)

    def test_full_bag_persists_pending_and_blocks_next_start(self):
        with self.db() as conn, conn:
            conn.execute("UPDATE dungeon_profiles SET bag_capacity=6 WHERE username='alice'")
        first = self.start()
        battle_id = first["battle"]["battle_id"]
        done = self.sync(battle_id, 115_000)["battle"]
        self.assertEqual(done["result"]["items"][0]["location"], "pending")
        with self.db() as conn, conn:
            state = dungeon_state(conn, "alice", 115)
            self.assertEqual(state["pending_count"], 1)
        with self.assertRaises(DungeonError) as blocked:
            self.start("start-next", version=state["profile_version"])
        self.assertEqual(blocked.exception.code, "pending_items")
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM dungeon_loadout WHERE username='alice' AND slot='weapon'")
            conn.execute("UPDATE dungeon_items SET location='sold' WHERE item_id=?",
                         (self.initial["loadout"]["weapon"],))
            from estate.dungeon.actions import run_dungeon_action
            claimed = run_dungeon_action(conn, "alice", "claim-pending", "dungeon_claim_items",
                                         {"item_ids": [done["result"]["items"][0]["item_id"]]},
                                         state["profile_version"], 116, adjust_coins)
        self.assertTrue(claimed["changed"])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items WHERE location='pending'").fetchone()[0], 0)

    def test_wallet_failure_rolls_back_whole_settlement(self):
        battle_id = self.start()["battle"]["battle_id"]

        def fail(*args, **kwargs):
            raise RuntimeError("wallet write failed")

        with self.assertRaises(RuntimeError):
            progress_run(self.db, "alice", battle_id, 115_000, fail)
        with self.db() as conn:
            battle = read_run(conn, "alice", battle_id)
            self.assertEqual(battle["status"], "running")
            self.assertEqual(battle["last_sequence"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 1000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_active_jobs").fetchone()[0], 1)
        self.assertEqual(self.sync(battle_id, 115_000)["battle"]["result"]["outcome"], "victory")

    def _assert_database_fault_rolls_back(self, trigger_sql):
        battle_id = self.start()["battle"]["battle_id"]
        with self.db() as conn, conn:
            conn.execute(trigger_sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.sync(battle_id, 115_000)
        with self.db() as conn, conn:
            self.assertEqual(read_run(conn, "alice", battle_id)["status"], "running")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_reward'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items WHERE template_id='ruins_blade'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT clear_count FROM dungeon_progress WHERE username='alice'").fetchone()[0], 0)
            conn.execute("DROP TRIGGER reject_settlement")
        self.assertEqual(self.sync(battle_id, 115_000)["battle"]["result"]["outcome"], "victory")

    def test_item_failure_after_coin_write_rolls_back(self):
        self._assert_database_fault_rolls_back("""CREATE TRIGGER reject_settlement
            BEFORE INSERT ON dungeon_items WHEN NEW.template_id='ruins_blade'
            BEGIN SELECT RAISE(ABORT,'injected item failure'); END""")

    def test_progress_failure_after_item_write_rolls_back(self):
        self._assert_database_fault_rolls_back("""CREATE TRIGGER reject_settlement
            BEFORE UPDATE ON dungeon_progress WHEN NEW.clear_count>OLD.clear_count
            BEGIN SELECT RAISE(ABORT,'injected progress failure'); END""")

    def test_unsupported_version_keeps_active_save(self):
        battle_id = self.start()["battle"]["battle_id"]
        with self.db() as conn, conn:
            raw = json.loads(conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=?",
                                          (battle_id,)).fetchone()[0])
            raw["simulation_version"] = 99
            import hashlib
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            conn.execute("UPDATE dungeon_runs SET snapshot_json=?,snapshot_hash=? WHERE battle_id=?",
                         (encoded, digest, battle_id))
        with self.assertRaises(DungeonError) as raised:
            self.sync(battle_id, 115_000)
        self.assertEqual(raised.exception.code, "unsupported_version")
        with self.db() as conn:
            self.assertEqual(read_run(conn, "alice", battle_id)["status"], "running")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_active_jobs").fetchone()[0], 1)

    def test_deterministic_simulation_error_releases_job_without_reward(self):
        battle_id = self.start()["battle"]["battle_id"]
        with self.db() as conn, conn:
            raw = json.loads(conn.execute("SELECT snapshot_json FROM dungeon_runs WHERE battle_id=?",
                                          (battle_id,)).fetchone()[0])
            raw["seed_hex"] = "bad"
            import hashlib
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            conn.execute("UPDATE dungeon_runs SET snapshot_json=?,snapshot_hash=? WHERE battle_id=?",
                         (encoded, digest, battle_id))
        result = self.sync(battle_id, 102_000)
        self.assertEqual(result["battle"]["status"], "error")
        self.assertEqual(result["battle"]["result"]["outcome"], "error")
        self.assertEqual(result["events"][-1]["event_type"], "BattleEnded")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_active_jobs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 0)

    def test_abandon_after_server_time_victory_keeps_reward(self):
        battle = self.start()["battle"]
        result = self.sync(battle["battle_id"], 115_000, command="abandon",
                           request_id="late-abandon", expected_revision=battle["revision"])
        self.assertEqual(result["battle"]["status"], "settled")
        self.assertEqual(result["battle"]["result"]["outcome"], "victory")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 1)

    def test_defeat_timeout_and_abandon_never_award(self):
        for outcome in ("defeat", "timeout", "abandoned"):
            with self.subTest(outcome=outcome):
                catalog = deepcopy(CATALOG)
                if outcome == "defeat":
                    catalog["enemies"][0]["stats"].update(max_hp=1000, atk=1000)
                elif outcome == "timeout":
                    catalog["enemies"][0]["stats"].update(max_hp=1000, atk=0)
                with self.db() as conn, conn:
                    version = dungeon_state(conn, "alice", 200)["profile_version"]
                started = self.start(f"start-{outcome}", version=version,
                                     catalog=catalog, now_ms=200_000)
                battle = started["battle"]
                if outcome == "abandoned":
                    result = self.sync(battle["battle_id"], 200_000, command="abandon",
                                       request_id="abandon-1", expected_revision=battle["revision"])
                else:
                    result = self.sync(battle["battle_id"], 260_000)
                self.assertEqual(result["battle"]["result"]["outcome"], outcome)
                self.assertEqual(result["battle"]["result"]["coins_gained"], 0)
                with self.db() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0],
                                     0)
                    self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0],
                                     1000)

    def test_concurrent_sync_settles_once(self):
        battle_id = self.start()["battle"]["battle_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.sync(battle_id, 115_000), range(2)))
        self.assertEqual({entry["battle"]["status"] for entry in results}, {"settled"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_reward'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
