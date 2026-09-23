#!/usr/bin/env python3
"""地下城装备变更、幂等、并发、金币事务及同账号同步。"""

import asyncio
from functools import partial
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate.dungeon.actions import run_dungeon_action
from estate.dungeon.service import DungeonError, compare_item, dungeon_state
from server.app import create_app
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins


def insert_item(conn, item_id, owner="alice", slot="weapon", stats=None,
                location="bag", sell_coins=20):
    conn.execute("""INSERT INTO dungeon_items
        (item_id,owner,template_id,template_version,slot,quality,stats_json,
         tags_json,effects_json,affixes_json,sell_coins,location,created_at)
        VALUES (?,?,?,'test-v1',?,'normal',?,'[]','[]','[]',?,?,1)""",
        (item_id, owner, f"test_{slot}", slot,
         json.dumps(stats or {"atk": 60}), sell_coins, location))


class EquipmentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = partial(database, str(Path(self.tmp.name) / "equipment.db"))
        init_db(self.database)
        with self.database() as conn, conn:
            conn.executemany("""INSERT INTO users
                (username,password_hash,salt,created_at,coins) VALUES (?,'','',0,1000)""",
                ((name,) for name in ("alice", "bob")))
            self.initial = dungeon_state(conn, "alice", 100)
            dungeon_state(conn, "bob", 100)

    def action(self, request_id, kind, data, version=None, adjust=adjust_coins):
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            result = run_dungeon_action(conn, "alice", request_id, kind, data,
                                        self.initial["profile_version"] if version is None else version,
                                        101, adjust)
            return result, dungeon_state(conn, "alice", 101)

    def test_compare_equip_unequip_and_stale_version(self):
        with self.database() as conn, conn:
            insert_item(conn, "other_weapon", stats={"atk": 90})
            preview = compare_item(conn, "alice", "other_weapon", 101)
        self.assertEqual(preview["current"]["atk"], 30)
        self.assertEqual(preview["preview"]["values"]["atk"], 90)
        self.assertEqual(preview["delta"]["atk"], 60)
        first, state = self.action("equip-1", "dungeon_equip",
                                   {"slot": "weapon", "item_id": "other_weapon"})
        self.assertEqual(state["stats"]["values"]["atk"], 90)
        self.assertEqual(first["profile_version"], self.initial["profile_version"] + 1)
        with self.assertRaises(DungeonError) as stale:
            self.action("equip-2", "dungeon_equip", {"slot": "weapon", "item_id": None})
        self.assertEqual(stale.exception.code, "version_conflict")
        again, current = self.action("equip-1", "dungeon_equip",
                                      {"slot": "weapon", "item_id": "other_weapon"})
        self.assertTrue(again["replayed"])
        self.assertEqual(current["profile_version"], first["profile_version"])
        same, current = self.action("equip-3", "dungeon_equip",
                                    {"slot": "weapon", "item_id": "other_weapon"},
                                    version=current["profile_version"])
        self.assertFalse(same["changed"])
        self.assertEqual(current["profile_version"], first["profile_version"])
        removed, current = self.action("equip-4", "dungeon_equip",
                                       {"slot": "weapon", "item_id": None},
                                       version=current["profile_version"])
        self.assertTrue(removed["changed"])
        self.assertEqual(current["stats"]["values"]["atk"], 0)

    def test_owner_slot_and_pending_items_cannot_be_equipped(self):
        with self.database() as conn, conn:
            insert_item(conn, "bob_weapon", owner="bob")
            insert_item(conn, "pending_weapon", location="pending")
        for item_id, slot, code in (("bob_weapon", "weapon", "not_found"),
                                    ("pending_weapon", "weapon", "item_unavailable"),
                                    ("pending_weapon", "chest", "item_unavailable")):
            with self.subTest(item_id=item_id, slot=slot):
                with self.assertRaises(DungeonError) as raised:
                    self.action(f"try-{item_id}-{slot}", "dungeon_equip",
                                {"slot": slot, "item_id": item_id})
                self.assertEqual(raised.exception.code, code)

    def test_lock_sale_replay_and_request_conflict(self):
        with self.database() as conn, conn:
            insert_item(conn, "sale_weapon", sell_coins=45)
        locked, state = self.action("lock-1", "dungeon_lock_item",
                                    {"item_id": "sale_weapon", "locked": True})
        self.assertTrue(locked["locked"])
        with self.assertRaises(DungeonError) as protected:
            self.action("sale-1", "dungeon_sell_item", {"item_id": "sale_weapon"},
                        version=state["profile_version"])
        self.assertEqual(protected.exception.code, "item_protected")
        unlocked, state = self.action("lock-2", "dungeon_lock_item",
                                      {"item_id": "sale_weapon", "locked": False},
                                      version=state["profile_version"])
        sold, state = self.action("sale-2", "dungeon_sell_item",
                                  {"item_id": "sale_weapon"}, version=state["profile_version"])
        self.assertEqual(sold["coins_gained"], 45)
        self.assertEqual(state["coins"], 1045)
        self.assertNotIn("sale_weapon", {item["item_id"] for item in state["items"]})
        replay, current = self.action("sale-2", "dungeon_sell_item",
                                      {"item_id": "sale_weapon"}, version=unlocked["profile_version"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(current["coins"], 1045)
        with self.assertRaises(DungeonError) as conflict:
            self.action("sale-2", "dungeon_lock_item",
                        {"item_id": "sale_weapon", "locked": True},
                        version=unlocked["profile_version"])
        self.assertEqual(conflict.exception.code, "request_conflict")
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_sale'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_actions WHERE request_id='sale-2'").fetchone()[0], 1)

    def test_equipped_foreign_and_already_sold_items_never_pay(self):
        with self.database() as conn, conn:
            insert_item(conn, "bob_spare", owner="bob", sell_coins=70)
            insert_item(conn, "one_sale", sell_coins=25)
            starter_weapon = self.initial["loadout"]["weapon"]
            conn.execute("UPDATE dungeon_items SET sell_coins=80 WHERE item_id=?",
                         (starter_weapon,))
        for request_id, item_id, code in (("foreign-sale", "bob_spare", "not_found"),
                                          ("equipped-sale", starter_weapon, "item_protected")):
            with self.subTest(item_id=item_id):
                with self.assertRaises(DungeonError) as error:
                    self.action(request_id, "dungeon_sell_item", {"item_id": item_id})
                self.assertEqual(error.exception.code, code)
        sold, state = self.action("first-sale", "dungeon_sell_item", {"item_id": "one_sale"})
        self.assertEqual(sold["coins_gained"], 25)
        with self.assertRaises(DungeonError) as error:
            self.action("second-sale", "dungeon_sell_item", {"item_id": "one_sale"},
                        version=state["profile_version"])
        self.assertEqual(error.exception.code, "not_found")
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 1025)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_sale'").fetchone()[0], 1)

    def test_wallet_failure_rolls_back_item_and_receipt(self):
        with self.database() as conn, conn:
            insert_item(conn, "rollback_weapon", sell_coins=40)

        def fail(*args, **kwargs):
            raise RuntimeError("injected wallet failure")

        with self.assertRaises(RuntimeError):
            self.action("rollback-sale", "dungeon_sell_item",
                        {"item_id": "rollback_weapon"}, adjust=fail)
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT location FROM dungeon_items WHERE item_id='rollback_weapon'").fetchone()[0], "bag")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_actions WHERE request_id='rollback-sale'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0], 1000)

    def test_active_snapshot_protects_used_gear_and_blocks_equip(self):
        with self.database() as conn, conn:
            insert_item(conn, "used_weapon")
            insert_item(conn, "free_weapon")
            conn.execute("""INSERT INTO dungeon_runs
                (battle_id,username,challenge_id,difficulty_id,status,snapshot_json,
                 snapshot_hash,checkpoint_json,wall_anchor_ms,created_at,updated_at)
                VALUES ('battle-1','alice','ruins_slime_01','normal','running',?,'hash','{}',0,0,0)""",
                (json.dumps({"equipment": [{"item_id": "used_weapon"}]}),))
            conn.execute("INSERT INTO dungeon_active_jobs VALUES ('alice','battle','battle-1')")
        with self.assertRaises(DungeonError) as active:
            self.action("active-equip", "dungeon_equip",
                        {"slot": "weapon", "item_id": "free_weapon"})
        self.assertEqual(active.exception.code, "active_job")
        with self.assertRaises(DungeonError) as protected:
            self.action("used-sale", "dungeon_sell_item", {"item_id": "used_weapon"})
        self.assertEqual(protected.exception.code, "item_protected")
        sold, state = self.action("free-sale", "dungeon_sell_item", {"item_id": "free_weapon"})
        self.assertEqual(sold["coins_gained"], 20)
        self.assertEqual(state["active_job"]["id"], "battle-1")

    def test_partial_pending_claim_is_capacity_checked(self):
        with self.database() as conn, conn:
            insert_item(conn, "pending_1", location="pending")
            insert_item(conn, "pending_2", location="pending")
            conn.execute("UPDATE dungeon_profiles SET bag_capacity=7 WHERE username='alice'")
        with self.assertRaises(DungeonError) as full:
            self.action("claim-both", "dungeon_claim_items",
                        {"item_ids": ["pending_1", "pending_2"]})
        self.assertEqual(full.exception.code, "inventory_full")
        claimed, state = self.action("claim-one", "dungeon_claim_items",
                                     {"item_ids": ["pending_1"]})
        self.assertTrue(claimed["changed"])
        self.assertEqual(state["pending_count"], 1)
        self.assertEqual(next(item for item in state["items"] if item["item_id"] == "pending_1")["location"], "bag")
        replay, state = self.action("claim-one", "dungeon_claim_items",
                                    {"item_ids": ["pending_1"]})
        self.assertTrue(replay["replayed"])
        self.assertEqual(state["pending_count"], 1)


class ExistingDatabaseUpgradeTests(unittest.TestCase):
    def test_existing_account_and_wallet_survive_repeatable_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "existing.db")
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user', created_at INTEGER NOT NULL,
                    coins REAL NOT NULL DEFAULT 100)""")
                conn.execute("""INSERT INTO users
                    (username,password_hash,salt,created_at,coins)
                    VALUES ('alice','','',1,837.5)""")
                conn.execute("""CREATE TABLE coin_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
                    amount REAL NOT NULL, balance REAL NOT NULL, kind TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)""")
                conn.execute("""INSERT INTO coin_transactions
                    (username,amount,balance,kind,created_at)
                    VALUES ('alice',15,837.5,'old_reward',1)""")
            db = partial(database, path)
            init_db(db)
            init_db(db)
            with db() as conn, conn:
                state = dungeon_state(conn, "alice", 100)
                self.assertEqual(state["coins"], 837.5)
                self.assertEqual(len(state["items"]), 6)
                self.assertEqual(conn.execute("""SELECT amount,balance,kind
                    FROM coin_transactions WHERE username='alice'""").fetchall(),
                    [(15, 837.5, "old_reward")])
                self.assertEqual(conn.execute("""SELECT COUNT(*) FROM dungeon_profiles
                    WHERE username='alice'""").fetchone()[0], 1)


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))


class EquipmentProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(partial(database, str(Path(self.tmp.name) / "protocol.db")))
        init_db(self.app.database)
        with self.app.database() as conn, conn:
            conn.execute("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES ('alice','','',0,1000)""")
        self.addAsyncCleanup(self.app.aclose)
        self.a, self.b = Socket(), Socket()
        self.state_a = {"user": {"username": "alice", "coins": 1000}, "send_lock": asyncio.Lock()}
        self.state_b = {"user": {"username": "alice", "coins": 1000}, "send_lock": asyncio.Lock()}
        self.app.hub.clients[self.a] = self.state_a
        self.app.hub.clients[self.b] = self.state_b
        await self.app.handlers["get_dungeon"](self.a, self.state_a, {})
        self.version = self.a.messages[-1]["profile_version"]

    async def test_compare_is_read_only_and_rejects_bad_identifiers(self):
        with self.app.database() as conn, conn:
            insert_item(conn, "compare_weapon", stats={"atk": 90})
        await self.app.handlers["dungeon_compare_item"](
            self.a, self.state_a, {"request_id": "compare-1", "item_id": "compare_weapon"})
        result = self.a.messages[-1]
        self.assertEqual(result["type"], "dungeon_comparison")
        self.assertEqual(result["delta"]["atk"], 60)
        self.assertEqual(result["profile_version"], self.version)
        await self.app.handlers["dungeon_compare_item"](
            self.a, self.state_a, {"request_id": "compare-2", "item_id": ["compare_weapon"]})
        self.assertEqual(self.a.messages[-1]["code"], "invalid_request")
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_actions").fetchone()[0], 0)

    async def test_two_tabs_receive_current_state_and_conflicting_write_fails(self):
        with self.app.database() as conn, conn:
            insert_item(conn, "alternate", stats={"atk": 90})
        await asyncio.gather(
            self.app.handlers["dungeon_equip"](
                self.a, self.state_a, {"type": "dungeon_equip", "request_id": "tab-a",
                                       "slot": "weapon", "item_id": "alternate",
                                       "expected_version": self.version}),
            self.app.handlers["dungeon_equip"](
                self.b, self.state_b, {"type": "dungeon_equip", "request_id": "tab-b",
                                       "slot": "weapon", "item_id": None,
                                       "expected_version": self.version}),
        )
        results = [message for socket in (self.a, self.b) for message in socket.messages
                   if message["type"] == "dungeon_result"]
        errors = [message for socket in (self.a, self.b) for message in socket.messages
                  if message["type"] == "dungeon_error"]
        self.assertEqual(len(results), 1)
        self.assertEqual([error["code"] for error in errors], ["version_conflict"])
        self.assertEqual(results[0]["state"]["stats"]["values"]["atk"], 90)
        self.assertTrue(any(message["type"] == "dungeon_state" and
                            message["stats"]["values"]["atk"] == 90 for message in self.b.messages))

    async def test_sale_updates_shared_coin_cache_once(self):
        with self.app.database() as conn, conn:
            insert_item(conn, "sale-spare", sell_coins=30)
        data = {"type": "dungeon_sell_item", "request_id": "sale-spare-1",
                "item_id": "sale-spare", "expected_version": self.version}
        await self.app.handlers["dungeon_sell_item"](self.a, self.state_a, data)
        await self.app.handlers["dungeon_sell_item"](self.a, self.state_a, data)
        self.assertEqual(self.state_a["user"]["coins"], 1030)
        self.assertEqual(self.state_b["user"]["coins"], 1030)
        self.assertTrue(self.a.messages[-2]["result"]["replayed"])
        self.assertEqual(self.b.messages[-1]["coins"], 1030)
        with self.app.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_sale'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
