#!/usr/bin/env python3
"""Beta upgrade, shared wallet, migration, and legacy asset-gate regression."""

from functools import partial
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.assets import AssetService
from dungeon.application.wallet import SharedWalletPort, _minor, MAX_MINOR
from dungeon.content.loader import load_ruleset
from dungeon.domain.errors import DungeonError
from dungeon.domain.growth import FixedLevelPolicy
from dungeon.legacy.actions import run_dungeon_action
from dungeon.legacy.service import dungeon_state
from dungeon.storage.assets import ensure_available, reserve_item, release_item
from dungeon.storage.beta_schema import init_beta
from dungeon.storage.legacy_schema import init_dungeon
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins


RELEASE = Path(__file__).resolve().parent.parent / "content/dungeon/release.json"


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = partial(database, str(Path(self.tmp.name) / "assets.db"))
        init_db(self.database)
        with self.database() as conn, conn:
            conn.executemany("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,10)""", [("alice",), ("bob",)])
            self.alice_id = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.version = dungeon_state(conn, "alice", 100)["profile_version"]
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
                VALUES ('beta_sword','alice','beta.sword.basic','0.1.0','weapon','normal','{"atk":30}',100)""")
            conn.execute("INSERT INTO dungeon_beta_progress(user_id,progress_id) VALUES (?,'beta.clear.first_boss')",
                         (self.alice_id,))
        ruleset = load_ruleset(RELEASE)
        self.policy = FixedLevelPolicy(ruleset.ruleset_id, ruleset.mutable_content("progression"))
        self.service = AssetService(self.database, self.policy, clock=lambda: 101)

    def quote(self):
        return self.service.quote_upgrade(self.alice_id, "beta_sword", 1)

    def upgrade(self, request_id="upgrade-1", costs=None, **kwargs):
        return self.service.upgrade(self.alice_id, request_id, "beta_sword", 1,
                                    expected_item_version=kwargs.get("version", 1),
                                    expected_ruleset_id=kwargs.get("ruleset", self.policy.ruleset_id),
                                    expected_costs=self.quote()["costs"] if costs is None else costs)

    def state(self):
        with self.database() as conn:
            item = conn.execute("""SELECT stats_json,beta_upgrade_level,version FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()
            coins = conn.execute("SELECT coins FROM users WHERE username='alice'").fetchone()[0]
            entries = conn.execute("SELECT COUNT(*) FROM coin_transactions WHERE kind='dungeon_beta_upgrade'").fetchone()[0]
            return item, coins, entries

    def test_real_content_quote_upgrade_replay_and_conflict(self):
        quote = self.quote()
        self.assertEqual(quote["costs"], [{"resource_id": "wallet:coins", "amount": 200}])
        result = self.upgrade()
        self.assertEqual(result["result"]["wallet_at_commit"]["coin_minor"], 800)
        self.assertEqual(result["result"]["stats"], {"atk": 32})
        replay = self.service.upgrade(self.alice_id, "upgrade-1", "beta_sword", 1,
                                      expected_item_version=1, expected_ruleset_id=self.policy.ruleset_id,
                                      expected_costs=quote["costs"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.state()[2], 1)
        with self.assertRaises(DungeonError) as error:
            self.service.upgrade(self.alice_id, "upgrade-1", "beta_sword", 1,
                                 expected_item_version=1, expected_ruleset_id=self.policy.ruleset_id,
                                 expected_costs=[])
        self.assertEqual(error.exception.code, "request_conflict")

    def test_insufficient_progress_quote_change_and_funds_do_not_write(self):
        with self.database() as conn, conn:
            conn.execute("DELETE FROM dungeon_beta_progress WHERE user_id=?", (self.alice_id,))
        with self.assertRaises(DungeonError) as error:
            self.quote()
        self.assertEqual(error.exception.code, "forbidden")
        with self.database() as conn, conn:
            conn.execute("INSERT INTO dungeon_beta_progress VALUES (?,'beta.clear.first_boss')", (self.alice_id,))
        with self.assertRaises(DungeonError) as error:
            self.upgrade(costs=[{"resource_id": "wallet:coins", "amount": 300}])
        self.assertEqual(error.exception.code, "quote_changed")
        with self.database() as conn, conn:
            conn.execute("UPDATE users SET coins=1 WHERE username='alice'")
        with self.assertRaises(DungeonError) as error:
            self.upgrade()
        self.assertEqual(error.exception.code, "insufficient_funds")
        self.assertEqual(self.state(), (("{\"atk\":30}", 0, 1), 1, 0))

    def test_post_debit_exception_rolls_back_wallet_item_and_receipt(self):
        class FaultWallet(SharedWalletPort):
            def change(self, *args, **kwargs):
                super().change(*args, **kwargs)
                raise RuntimeError("fault after shared wallet debit")
        fault = AssetService(self.database, self.policy, wallet=FaultWallet(), clock=lambda: 101)
        quote = self.quote()
        with self.assertRaises(RuntimeError):
            fault.upgrade(self.alice_id, "fault-1", "beta_sword", 1,
                          expected_item_version=1, expected_ruleset_id=self.policy.ruleset_id,
                          expected_costs=quote["costs"])
        self.assertEqual(self.state(), (("{\"atk\":30}", 0, 1), 10, 0))
        with self.database() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM dungeon_beta_receipts WHERE request_id='fault-1'").fetchone())

    def test_material_and_coin_costs_are_atomic(self):
        rows = load_ruleset(RELEASE).mutable_content("progression")
        rows[0]["rows"][0]["costs"].append({"resource_id": "material:beta.scrap", "amount": 2})
        policy = FixedLevelPolicy("ruleset-with-material", rows)
        service = AssetService(self.database, policy)
        quote = service.quote_upgrade(self.alice_id, "beta_sword", 1)
        with self.assertRaises(DungeonError) as error:
            service.upgrade(self.alice_id, "material-1", "beta_sword", 1,
                            expected_item_version=1, expected_ruleset_id=policy.ruleset_id,
                            expected_costs=quote["costs"])
        self.assertEqual(error.exception.code, "insufficient_materials")
        self.assertEqual(self.state()[1:], (10, 0))
        with self.database() as conn, conn:
            conn.execute("INSERT INTO dungeon_material_balances VALUES (?,'beta.scrap',3)", (self.alice_id,))
        service.upgrade(self.alice_id, "material-1", "beta_sword", 1,
                        expected_item_version=1, expected_ruleset_id=policy.ruleset_id,
                        expected_costs=quote["costs"])
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM dungeon_material_balances WHERE user_id=?",
                                          (self.alice_id,)).fetchone()[0], 1)
        self.assertEqual(self.state()[1:], (8, 1))

    def test_reservation_blocks_upgrade_and_legacy_lock_sell_equip_start(self):
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            reserve_item(conn, "alice", "beta_sword", "trade_offer", "offer-1", 101)
        with self.assertRaises(DungeonError) as error:
            self.quote()
        self.assertEqual(error.exception.code, "asset_reserved")
        for kind, data in (("dungeon_lock_item", {"item_id": "beta_sword", "locked": True}),
                           ("dungeon_sell_item", {"item_id": "beta_sword"}),
                           ("dungeon_equip", {"slot": "weapon", "item_id": "beta_sword"})):
            with self.subTest(kind=kind), self.assertRaises(DungeonError) as error:
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    run_dungeon_action(conn, "alice", kind + "-1", kind, data,
                                       self.version, 101, adjust_coins)
            self.assertEqual(error.exception.code, "asset_reserved")
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            release_item(conn, "beta_sword", "trade_offer", "offer-1")
        self.assertEqual(self.quote()["target_level"], 1)

    def test_wallet_amount_boundaries_and_invalid_policy(self):
        self.assertEqual(_minor(10.01), 1001)
        self.assertEqual(_minor(MAX_MINOR / 100), MAX_MINOR)
        for value in (True, float("nan"), float("inf"), 1.001, (MAX_MINOR + 1) / 100):
            with self.subTest(value=value), self.assertRaises(DungeonError):
                _minor(value)
        ruleset_id = self.policy.ruleset_id
        class UnsafePolicy:
            def quote(self, item, progress, target):
                return {"item_id": item["item_id"], "item_version": item["version"],
                        "target_level": target, "ruleset_id": ruleset_id,
                        "costs": [{"resource_id": "run:cash", "amount": 200}],
                        "stats": {"atk": 32}}
        unsafe = AssetService(self.database, UnsafePolicy())
        with self.assertRaises(DungeonError) as error:
            unsafe.upgrade(self.alice_id, "unsafe-1", "beta_sword", 1,
                           expected_item_version=1, expected_ruleset_id=self.policy.ruleset_id,
                           expected_costs=[{"resource_id": "run:cash", "amount": 200}])
        self.assertEqual(error.exception.code, "invalid_config")
        self.assertEqual(self.state()[2], 0)

    def test_trade_reservation_checks_binding_location_and_active_snapshot(self):
        for field, value in (("locked", 1), ("location", "pending"),
                             ("beta_bound_reason", "starter"), ("beta_source", "test")):
            with self.subTest(field=field):
                with self.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(f"UPDATE dungeon_items SET {field}=? WHERE item_id='beta_sword'", (value,))
                    with self.assertRaises(DungeonError) as error:
                        reserve_item(conn, "alice", "beta_sword", "trade_offer", "offer-2", 101)
                    self.assertEqual(error.exception.code, "asset_not_tradable")
                    conn.execute("ROLLBACK")
        with self.database() as conn, conn:
            conn.execute("""INSERT INTO dungeon_runs
                (battle_id,username,challenge_id,difficulty_id,status,snapshot_json,snapshot_hash,
                 checkpoint_json,wall_anchor_ms,created_at,updated_at)
                VALUES ('battle-1','alice','c','normal','running',?,'hash','{}',0,100,100)""",
                (json.dumps({"equipment": [{"item_id": "beta_sword"}]}),))
            conn.execute("INSERT INTO dungeon_active_jobs VALUES ('alice','battle','battle-1')")
            with self.assertRaises(DungeonError) as error:
                reserve_item(conn, "alice", "beta_sword", "trade_offer", "offer-3", 101)
            self.assertEqual(error.exception.code, "asset_reserved")


class MigrationTests(unittest.TestCase):
    def _legacy(self, conn):
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE)")
        init_dungeon(conn)

    def test_migration_repeat_and_atomic_failure_with_or_without_outer_transaction(self):
        for outer in (False, True):
            with self.subTest(outer=outer):
                conn = sqlite3.connect(":memory:")
                self.addCleanup(conn.close)
                self._legacy(conn)
                conn.commit()
                conn.execute("ALTER TABLE dungeon_items ADD COLUMN beta_upgrade_level INTEGER")
                conn.commit()
                if outer:
                    conn.execute("BEGIN IMMEDIATE")
                with self.assertRaises(sqlite3.OperationalError):
                    init_beta(conn)
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='dungeon_beta_receipts'").fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='dungeon_migrations'").fetchone())
                if outer:
                    self.assertTrue(conn.in_transaction)
                    conn.rollback()
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self._legacy(conn)
        conn.commit()
        init_beta(conn)
        init_beta(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_migrations").fetchone()[0], 4)

    def test_legacy_gate_works_before_beta_tables_exist(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self._legacy(conn)
        conn.execute("INSERT INTO users VALUES (1,'alice')")
        conn.execute("""INSERT INTO dungeon_items
            (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
            VALUES ('legacy_item','alice','legacy','v1','weapon','normal','{"atk":1}',0)""")
        ensure_available(conn, "alice", "legacy_item")
        with self.assertRaises(DungeonError) as error:
            ensure_available(conn, "bob", "legacy_item")
        self.assertEqual(error.exception.code, "not_found")

    def test_user_delete_trigger_cleans_beta_rows_without_foreign_keys(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self._legacy(conn)
        init_beta(conn)
        conn.execute("INSERT INTO users VALUES (1,'alice')")
        conn.execute("INSERT INTO dungeon_beta_progress VALUES (1,'milestone')")
        conn.execute("""INSERT INTO dungeon_beta_receipts
            (user_id,request_id,request_hash,status,result_json,created_at)
            VALUES (1,'r','h','success','{}',0)""")
        conn.execute("DELETE FROM users WHERE id=1")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_beta_progress").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_beta_receipts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
