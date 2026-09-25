#!/usr/bin/env python3
"""Internal host lifecycle with a deliberately small injected simulator."""

from functools import partial
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.items import create_item
from dungeon.application.runs import RunService
from dungeon.application.wallet import SharedWalletPort
from dungeon.content.loader import load_ruleset
from dungeon.domain.errors import DungeonError
from dungeon.runtime.host import RunHost
from dungeon.storage.runs import load_run
from dungeon.legacy.runs import start_run as legacy_start
from dungeon.legacy.service import dungeon_state
from server.database import database
from server.schema import init_db


ROOT = Path(__file__).resolve().parent.parent
ROUTE = "beta.p0.route.flamefield"


class TinySimulator:
    """A test double: clears after two single-tick steps, never shipped as play."""

    def create(self, initial, rules, services):
        previous = initial.get("previous_room_snapshot") or {}
        return {"room_index": initial["room_index"], "encounter_id": initial["encounter_id"],
                "room_ticks": 0, "last_input_seq": 0,
                "hp": previous.get("hp", 100),
                "run_resources": initial.get("run_resources", {}),
                "random": services.random_int("combat", 1, 1000)}

    def step(self, state, ordered_inputs, services):
        state = dict(state)
        state["room_ticks"] += 1
        state["hp"] -= 1
        if ordered_inputs:
            state["last_input_seq"] = ordered_inputs[-1]["input_seq"]
        state["random"] = services.random_int("combat", 1, 1000)
        if state["room_ticks"] == 2:
            services.emit({"type": "damage", "amount": 1})
            services.emit({"type": "room_cleared", "room_index": state["room_index"],
                           "encounter_id": state["encounter_id"]})
        return state

    def snapshot(self, state):
        return dict(state)

    def restore(self, snapshot, rules, services):
        return dict(snapshot)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.database = partial(database, str(Path(tmp.name) / "runtime.db"))
        init_db(self.database)
        self.rules = load_ruleset(ROOT / "content/dungeon/release-p0.json")
        with self.database() as conn, conn:
            conn.execute("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES ('alice','','',0,10)""")
            self.user_id = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            dungeon_state(conn, "alice", 100)
            self.item = create_item(conn, self.rules, "alice", "beta.sword.basic",
                                    source="integration_fixture", now=100)
            conn.execute("""INSERT INTO dungeon_loadout(username,slot,item_id)
                VALUES ('alice','weapon',?) ON CONFLICT(username,slot) DO UPDATE SET item_id=excluded.item_id""",
                         (self.item["item_id"],))
        self.service = RunService(self.database, self.rules, TinySimulator(), clock=lambda: 101)
        self.host = RunHost(self.service, checkpoint_ticks=1)

    def start(self):
        return self.service.start(self.user_id, "start-1", ROUTE)["run_id"]

    def state(self, run_id):
        with self.database() as conn:
            run = load_run(conn, run_id)
            balance = SharedWalletPort().balance(conn, "alice")
            rewards = conn.execute("SELECT COUNT(*) FROM dungeon_beta_room_rewards WHERE run_id=?",
                                   (run_id,)).fetchone()[0]
            tx = conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_beta_room_reward'").fetchone()[0]
            return run, balance, rewards, tx

    def test_full_lifecycle_and_exactly_once_rewards(self):
        run_id = self.start()
        self.assertTrue(self.service.start(self.user_id, "start-1", ROUTE)["replayed"])
        with self.assertRaises(DungeonError) as error:
            self.service.start(self.user_id, "start-2", ROUTE)
        self.assertEqual(error.exception.code, "active_job")
        with self.database() as conn:
            reservation = conn.execute("""SELECT purpose FROM dungeon_asset_reservations
                WHERE asset_id=?""", (self.item["item_id"],)).fetchone()
        self.assertEqual(reservation, ("active_run",))
        self.host.resume(run_id)
        epoch = self.host.status(run_id)["control_epoch"]
        value = {"move_x": 0, "move_y": 1, "aim_x": 0, "aim_y": 0,
                 "buttons": ["attack"]}
        self.assertTrue(self.host.input(run_id, epoch, 1, value))
        self.assertFalse(self.host.input(run_id, epoch, 1, value))
        self.host.pump(run_id)
        result = self.host.pump(run_id)
        self.assertEqual(result["reward"]["reward"]["coin_minor"], 1500)
        self.assertEqual(self.state(run_id)[1:], (2500, 1, 1))
        self.assertTrue(self.service.commit_room(self._outcome_for_replay(run_id))["replayed"])
        self.assertEqual(self.state(run_id)[1:], (2500, 1, 1))
        with self.database() as conn:
            previous_revision = conn.execute("SELECT revision FROM dungeon_beta_asset_revisions WHERE user_id=?",
                                             (self.user_id,)).fetchone()[0]
        for room in range(1, 4):
            self.host.resume(run_id)
            self.host.pump(run_id)
            result = self.host.pump(run_id)
            self.assertEqual(result["reward"]["room_index"], room)
            with self.database() as conn:
                revision = conn.execute("SELECT revision FROM dungeon_beta_asset_revisions WHERE user_id=?",
                                        (self.user_id,)).fetchone()[0]
            self.assertGreater(revision, previous_revision)
            previous_revision = revision
        run, balance, rewards, tx = self.state(run_id)
        self.assertEqual((run["status"], run["room_index"], balance, rewards, tx),
                         ("finished", 4, 12500, 4, 4))
        self.assertEqual(run["checkpoint"]["simulator"]["hp"], 92)
        self.assertEqual(run["checkpoint"]["run_resources"]["potions"],
                         [{"count": 1, "heal_max_hp_bp": 3000}])
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_asset_reservations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_beta_progress WHERE user_id=?",
                                          (self.user_id,)).fetchone()[0], 4)

    def _outcome_for_replay(self, run_id):
        from dungeon.runtime.host import RoomOutcome
        run = self.service.get(run_id)
        return RoomOutcome(run_id, 0, "beta.p0.encounter.rm01", 2,
                           run["control_epoch"], 1, {"room_index": 0},
                           run["checkpoint"]["rng"], {})

    def test_restart_pauses_at_durable_tick_and_rejects_old_epoch(self):
        run_id = self.start()
        self.host.resume(run_id)
        old_epoch = self.host.status(run_id)["control_epoch"]
        self.host.pump(run_id)
        self.assertEqual(self.state(run_id)[0]["durable_tick"], 1)
        restarted = RunService(self.database, self.rules, TinySimulator(), clock=lambda: 102)
        self.assertEqual(restarted.recover_unfinished(), [run_id])
        host = RunHost(restarted, checkpoint_ticks=1)
        self.assertEqual(host.attach(run_id)["status"], "paused")
        self.assertEqual(host.status(run_id)["server_tick"], 1)
        host.resume(run_id)
        with self.assertRaises(DungeonError) as error:
            host.input(run_id, old_epoch, 1,
                {"move_x": 0, "move_y": 0, "aim_x": 0, "aim_y": 0, "buttons": []})
        self.assertEqual(error.exception.code, "control_lost")
        self.assertEqual(host.pump(run_id)["reward"]["room_index"], 0)

    def test_input_bounds_and_invalid_clear_roll_back(self):
        run_id = self.start()
        host = RunHost(self.service, max_queue=1, max_step_ticks=1)
        host.resume(run_id)
        epoch = host.status(run_id)["control_epoch"]
        value = {"move_x": 0, "move_y": 0, "aim_x": 0, "aim_y": 0, "buttons": []}
        for sequence, invalid in ((2, value), (1, {**value, "damage": 999}),
                                  (1, {**value, "move_x": 9999})):
            with self.subTest(sequence=sequence, invalid=invalid), self.assertRaises(DungeonError):
                host.input(run_id, epoch, sequence, invalid)
        host.input(run_id, epoch, 1, value)
        with self.assertRaises(DungeonError) as error:
            host.input(run_id, epoch, 2, value)
        self.assertEqual(error.exception.code, "input_budget")
        with self.assertRaises(DungeonError):
            host.pump(run_id, ticks=2)
        self.assertEqual(host.pump(run_id)["server_tick"], 1)

    def test_wrong_clear_event_and_event_budget_pause_without_reward(self):
        class WrongClear(TinySimulator):
            def step(self, state, ordered_inputs, services):
                state = super().step(state, ordered_inputs, services)
                if state["room_ticks"] == 1:
                    services.emit({"type": "room_cleared", "room_index": 3,
                                   "encounter_id": "beta.p0.encounter.rm04"})
                return state
        self.service.simulator = WrongClear()
        host = RunHost(self.service, checkpoint_ticks=1)
        run_id = self.start()
        host.resume(run_id)
        with self.assertRaises(DungeonError) as error:
            host.pump(run_id)
        self.assertEqual(error.exception.code, "invalid_outcome")
        self.assertEqual(self.state(run_id)[0]["status"], "paused")
        self.assertEqual(self.state(run_id)[1:], (1000, 0, 0))

        self.host.abandon(run_id)
        self.service.simulator = TinySimulator()
        run_id = self.service.start(self.user_id, "start-2", ROUTE)["run_id"]
        host = RunHost(self.service, max_events_per_tick=1)
        host.resume(run_id)
        host.pump(run_id)
        with self.assertRaises(DungeonError) as error:
            host.pump(run_id)
        self.assertEqual(error.exception.code, "event_budget")
        self.assertEqual(self.state(run_id)[1:], (1000, 0, 0))

    def test_reward_wallet_failure_rolls_back_checkpoint_and_assets(self):
        class FailWallet(SharedWalletPort):
            def change(self, *args, **kwargs):
                super().change(*args, **kwargs)
                raise RuntimeError("injected after wallet credit")
        service = RunService(self.database, self.rules, TinySimulator(),
                             clock=lambda: 101, wallet=FailWallet())
        host = RunHost(service, checkpoint_ticks=1)
        run_id = service.start(self.user_id, "start-1", ROUTE)["run_id"]
        host.resume(run_id)
        host.pump(run_id)
        with self.assertRaises(RuntimeError):
            host.pump(run_id)
        run, balance, rewards, tx = self.state(run_id)
        self.assertEqual((run["status"], run["room_index"], run["durable_tick"],
                          balance, rewards, tx), ("paused", 0, 1, 1000, 0, 0))

    def test_item_creation_failure_rolls_back_every_room_effect(self):
        run_id = self.start()
        self.host.resume(run_id)
        self.host.pump(run_id)
        real_create = create_item
        def fail_after_item(*args, **kwargs):
            real_create(*args, **kwargs)
            raise RuntimeError("injected after item insert")
        with patch("dungeon.application.rewards.create_item", side_effect=fail_after_item):
            with self.assertRaises(RuntimeError):
                self.host.pump(run_id)
        run, balance, rewards, tx = self.state(run_id)
        self.assertEqual((run["status"], run["room_index"], run["durable_tick"],
                          balance, rewards, tx), ("paused", 0, 1, 1000, 0, 0))
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_beta_progress").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items WHERE beta_source='run_reward'").fetchone()[0], 0)

    def test_close_abandon_and_pinned_rules(self):
        run_id = self.start()
        self.host.resume(run_id)
        self.host.pump(run_id)
        self.host.close()
        self.assertEqual(self.state(run_id)[0]["status"], "paused")
        mismatch = RunService(self.database, replace(self.rules, ruleset_hash="sha256-different"),
                              TinySimulator())
        with self.assertRaises(DungeonError) as error:
            mismatch.get(run_id)
        self.assertEqual(error.exception.code, "ruleset_unavailable")
        self.assertEqual(self.host.abandon(run_id)["status"], "abandoned")
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_asset_reservations").fetchone()[0], 0)
        self.assertEqual(self.service.start(self.user_id, "start-2", ROUTE)["status"], "ready")

    def test_takeover_invalidates_old_host_and_uses_durable_checkpoint(self):
        run_id = self.start()
        self.host.resume(run_id)
        self.host.pump(run_id)
        second = RunHost(self.service, checkpoint_ticks=1)
        with self.assertRaises(DungeonError) as error:
            second.attach(run_id)
        self.assertEqual(error.exception.code, "run_owned")
        old_epoch = self.host.status(run_id)["control_epoch"]
        self.assertEqual(second.take_control(run_id)["status"], "paused")
        with self.assertRaises(DungeonError) as error:
            self.host.pump(run_id)
        self.assertEqual(error.exception.code, "control_lost")
        second.resume(run_id)
        self.assertGreater(second.status(run_id)["control_epoch"], old_epoch)
        self.assertEqual(second.pump(run_id)["reward"]["room_index"], 0)

    def test_same_host_takeover_requires_explicit_resume(self):
        run_id = self.start()
        self.host.resume(run_id)
        self.host.pump(run_id)
        old_epoch = self.host.status(run_id)["control_epoch"]
        control = self.host.take_control(run_id)
        self.assertEqual(control["status"], "paused")
        self.assertEqual(control["durable_tick"], 1)
        self.assertGreater(control["control_epoch"], old_epoch)
        with self.assertRaises(DungeonError) as error:
            self.host.pump(run_id)
        self.assertEqual(error.exception.code, "run_paused")
        self.host.resume(run_id)
        self.assertEqual(self.host.pump(run_id)["reward"]["room_index"], 0)

    def test_create_and_restore_failure_return_to_paused_checkpoint(self):
        class FailsCreate(TinySimulator):
            def create(self, initial, rules, services):
                raise RuntimeError("create failed")
        class FailsRestore(TinySimulator):
            def restore(self, snapshot, rules, services):
                raise RuntimeError("restore failed")
        run_id = self.start()
        before = self.state(run_id)[0]
        broken = RunService(self.database, self.rules, FailsCreate(), clock=lambda: 102)
        with self.assertRaisesRegex(RuntimeError, "create failed"):
            RunHost(broken).resume(run_id)
        after = self.state(run_id)[0]
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["checkpoint"], before["checkpoint"])
        self.assertEqual(after["control_epoch"], before["control_epoch"] + 1)

        healthy = RunHost(self.service, checkpoint_ticks=1)
        healthy.resume(run_id)
        healthy.pump(run_id)
        healthy.pause(run_id)
        before = self.state(run_id)[0]
        broken = RunService(self.database, self.rules, FailsRestore(), clock=lambda: 103)
        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            RunHost(broken).resume(run_id)
        after = self.state(run_id)[0]
        self.assertEqual(after["status"], "paused")
        self.assertEqual(after["checkpoint"], before["checkpoint"])
        self.assertEqual(after["control_epoch"], before["control_epoch"] + 1)
        recovered = RunHost(self.service, checkpoint_ticks=1)
        recovered.resume(run_id)
        self.assertEqual(recovered.pump(run_id)["reward"]["room_index"], 0)

    def test_consumed_run_resource_does_not_return_after_recovery(self):
        class UsesPotion(TinySimulator):
            def step(self, state, ordered_inputs, services):
                state = super().step(state, ordered_inputs, services)
                if any("potion" in command["buttons"] for command in ordered_inputs):
                    state["run_resources"]["potions"][0]["count"] -= 1
                return state
        service = RunService(self.database, self.rules, UsesPotion(), clock=lambda: 101)
        host = RunHost(service, checkpoint_ticks=1)
        run_id = service.start(self.user_id, "start-1", ROUTE)["run_id"]
        for _ in range(3):
            host.resume(run_id)
            host.pump(run_id)
            host.pump(run_id)
        host.resume(run_id)
        epoch = host.status(run_id)["control_epoch"]
        host.input(run_id, epoch, 1, {"move_x": 0, "move_y": 0,
                                     "aim_x": 0, "aim_y": 0, "buttons": ["potion"]})
        host.pump(run_id)
        run = self.state(run_id)[0]
        self.assertEqual(run["checkpoint"]["run_resources"]["potions"][0]["count"], 0)
        host.close()
        restarted = RunService(self.database, self.rules, UsesPotion(), clock=lambda: 102)
        restarted.recover_unfinished()
        new_host = RunHost(restarted, checkpoint_ticks=1)
        new_host.resume(run_id)
        self.assertEqual(new_host._runs[run_id]["state"]["run_resources"]["potions"][0]["count"], 0)

    def test_legacy_start_rejects_beta_active(self):
        self.start()
        with self.database() as conn, conn:
            state = dungeon_state(conn, "alice", 101)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(DungeonError) as error:
                legacy_start(conn, "alice", "legacy-1", "ruins_slime_01", "normal",
                             state["profile_version"], 101000)
            self.assertEqual(error.exception.code, "active_job")
        self.assertEqual(self.state(self.service.start(self.user_id, "start-1", ROUTE)["run_id"])[2], 0)

    def test_beta_start_rejects_legacy_active(self):
        with self.database() as conn, conn:
            state = dungeon_state(conn, "alice", 101)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            legacy_start(conn, "alice", "legacy-1", "ruins_slime_01", "normal",
                         state["profile_version"], 101000)
        with self.assertRaises(DungeonError) as error:
            self.service.start(self.user_id, "start-1", ROUTE)
        self.assertEqual(error.exception.code, "active_job")


if __name__ == "__main__":
    unittest.main()
