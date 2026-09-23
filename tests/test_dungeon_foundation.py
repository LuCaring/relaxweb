#!/usr/bin/env python3
"""地下城基础目录、存档、效果来源和真实协议入口。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.dungeon.catalog import CATALOG, DungeonConfigError, public_catalog, validate_catalog
from estate.dungeon.combat import make_battle_snapshot
from estate.dungeon.effects import EffectError, resolve_stats
from estate.dungeon.service import dungeon_state
from server.app import create_app
from server.database import database
from server.schema import init_db


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))


class DungeonFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = partial(database, str(Path(self.tmp.name) / "test.db"))
        init_db(self.database)
        init_db(self.database)
        with self.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at) VALUES ('alice','','',0)")

    def test_starter_is_atomic_and_idempotent(self):
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            first = dungeon_state(conn, "alice", 100)
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            again = dungeon_state(conn, "alice", 200)
        self.assertEqual(first, again)
        self.assertEqual(first["phase"], "equipment")
        self.assertEqual(len(first["items"]), 6)
        self.assertEqual(len(first["loadout"]), 6)
        self.assertEqual(first["stats"]["values"],
                         {"max_hp": 300, "atk": 30, "defense": 20,
                          "crit_bp": 500, "crit_damage_bp": 15000, "speed": 100})
        self.assertEqual(first["progress"], [{"challenge_id": "ruins_slime_01",
                                               "difficulty_id": "normal", "clear_count": 0,
                                               "unlocked": True}])
        self.assertIn("dungeon_equip", first["available_actions"])
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items").fetchone()[0], 6)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_progress").fetchone()[0], 1)

    def test_parallel_first_open_grants_only_six_items(self):
        def open_state():
            with self.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                return dungeon_state(conn, "alice", 100)

        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(lambda _: open_state(), range(2)))
        self.assertEqual(snapshots[0], snapshots[1])
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items").fetchone()[0], 6)

    def test_account_delete_cleans_dungeon_rows_without_foreign_keys(self):
        with self.database() as conn, conn:
            dungeon_state(conn, "alice", 100)
            conn.execute("""INSERT INTO dungeon_runs
                (battle_id,username,challenge_id,difficulty_id,status,snapshot_json,
                 snapshot_hash,checkpoint_json,wall_anchor_ms,created_at,updated_at)
                VALUES ('battle-1','alice','ruins_slime_01','normal','running','{}','hash','{}',0,0,0)""")
            conn.execute("INSERT INTO dungeon_active_jobs VALUES ('alice','battle','battle-1')")
            conn.execute("INSERT INTO dungeon_events VALUES ('battle-1',0,0,'{}')")
            conn.execute("DELETE FROM users WHERE username='alice'")
        with self.database() as conn:
            for table in ("dungeon_profiles", "dungeon_items", "dungeon_loadout",
                          "dungeon_progress", "dungeon_actions", "dungeon_runs",
                          "dungeon_active_jobs", "dungeon_events", "dungeon_rewards"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

    def test_catalog_checks_cycles_and_unsupported_effects(self):
        broken = deepcopy(CATALOG)
        broken["challenges"][0]["requires"] = ["ruins_slime_01"]
        with self.assertRaises(DungeonConfigError):
            validate_catalog(broken)
        broken = deepcopy(CATALOG)
        broken["items"][0]["effects"] = [{"rule_id": "unimplemented", "trigger": "on_hit",
                                             "operation": "stat_flat", "stat": "atk", "value": 1}]
        with self.assertRaises(DungeonConfigError):
            validate_catalog(broken)
        public = public_catalog()
        public["challenges"][0]["enemy"]["stats"]["max_hp"] = 1
        self.assertEqual(CATALOG["enemies"][0]["stats"]["max_hp"], 100)

    def test_reward_catalog_rejects_missing_refs_and_oversized_drops(self):
        broken = deepcopy(CATALOG)
        broken["challenges"][0]["reward_table_id"] = "missing"
        with self.assertRaises(DungeonConfigError):
            validate_catalog(broken)
        broken = deepcopy(CATALOG)
        broken["reward_tables"][0]["rolls"] = 21
        with self.assertRaises(DungeonConfigError):
            validate_catalog(broken)
        broken = deepcopy(CATALOG)
        broken["reward_tables"][0]["entries"][0]["weight"] = 0
        with self.assertRaises(DungeonConfigError):
            validate_catalog(broken)

    def test_equipment_roguelike_and_environment_share_stat_resolution(self):
        active = [
            {"source_kind": "roguelike", "source_id": "choice-1", "effect":
             {"rule_id": "battle_focus", "trigger": "passive", "operation": "stat_add_bp",
              "stat": "atk", "value": 2000}},
            {"source_kind": "environment", "source_id": "wet_floor", "effect":
             {"rule_id": "slippery", "trigger": "passive", "operation": "stat_flat",
              "stat": "speed", "value": -10}},
        ]
        result = resolve_stats(CATALOG["base_stats"],
                               [{"item_id": "weapon-1", "stats": {"atk": 30}, "effects": []}], active)
        self.assertEqual(result["values"]["atk"], 36)
        self.assertEqual(result["values"]["speed"], 90)
        self.assertEqual({part["source_kind"] for part in result["sources"]},
                         {"equipment", "roguelike", "environment"})
        active[0]["effect"]["trigger"] = "on_hit"
        with self.assertRaises(EffectError):
            resolve_stats(CATALOG["base_stats"], active_effects=active)

    def test_snapshot_is_canonical_and_detached_from_mutable_inputs(self):
        equipment = [{"item_id": "weapon-1", "stats": {"atk": 30}, "effects": []}]
        panel = resolve_stats(CATALOG["base_stats"], equipment)
        args = ("ruins_slime_01", "normal", panel["values"], equipment,
                panel["sources"], bytes(32))
        first = make_battle_snapshot(*args)
        self.assertEqual(first, make_battle_snapshot(*args))
        self.assertEqual(first.as_dict()["combat"]["normal_timeout_us"], 60_000_000)
        self.assertEqual(first.as_dict()["effect_sources"][0]["source_kind"], "equipment")
        equipment[0]["stats"]["atk"] = 900
        self.assertEqual(first.as_dict()["equipment"][0]["stats"]["atk"], 30)
        with self.assertRaises(ValueError):
            make_battle_snapshot(*args[:-1], b"short")


class DungeonProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(partial(database, str(Path(self.tmp.name) / "protocol.db")))
        init_db(self.app.database)
        with self.app.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at) VALUES ('alice','','',0)")
        self.addAsyncCleanup(self.app.aclose)

    async def test_get_dungeon_requires_login_and_returns_foundation_state(self):
        ws = Socket()
        state = {"user": None, "send_lock": asyncio.Lock()}
        self.app.hub.clients[ws] = state
        await self.app.handlers["get_dungeon"](ws, state, {"request_id": "read-1"})
        self.assertEqual(ws.messages[-1]["code"], "auth_required")
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_profiles").fetchone()[0], 0)
        state["user"] = {"username": "alice"}
        await self.app.handlers["get_dungeon"](ws, state, {"request_id": "read-2"})
        result = ws.messages[-1]
        self.assertEqual(result["type"], "dungeon_state")
        self.assertEqual(result["request_id"], "read-2")
        self.assertEqual(result["stats"]["values"]["max_hp"], 300)
        self.assertNotIn("seed", json.dumps(result))

    async def test_corrupt_saved_effect_returns_error_without_regrant(self):
        ws = Socket()
        state = {"user": {"username": "alice"}, "send_lock": asyncio.Lock()}
        self.app.hub.clients[ws] = state
        await self.app.handlers["get_dungeon"](ws, state, {})
        with self.app.database() as conn, conn:
            conn.execute("UPDATE dungeon_items SET effects_json='broken' WHERE owner='alice'")
        with self.assertLogs("live-chat", level="ERROR"):
            await self.app.handlers["get_dungeon"](ws, state, {"request_id": "bad-save"})
        self.assertEqual(ws.messages[-1]["code"], "invalid_save")
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items").fetchone()[0], 6)


if __name__ == "__main__":
    unittest.main()
