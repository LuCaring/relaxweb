#!/usr/bin/env python3
"""Beta item snapshots, consistent camp reads, and metadata across asset writes."""

from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.assets import AssetService
from dungeon.application.items import create_item
from dungeon.application.state import StateService
from dungeon.application.trading import TradeService
from dungeon.application.wallet import SharedWalletPort
from dungeon.content import load_ruleset
from dungeon.contracts.json_validation import validation_errors
from dungeon.domain.growth import FixedLevelPolicy
from dungeon.domain.trading import TradePolicy
from dungeon.domain.errors import DungeonError
from dungeon.legacy.actions import run_dungeon_action
from dungeon.legacy.service import ensure_dungeon
from dungeon.storage.assets import reserve_item
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins


ROOT = Path(__file__).resolve().parent.parent


class BetaStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = partial(database, str(Path(self.temp.name) / "state.db"))
        init_db(self.db)
        self.ruleset = load_ruleset(ROOT / "content/dungeon/release-p0.json")
        with self.db() as conn, conn:
            conn.executemany("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,1000)""", [("alice",), ("bob",)])
            self.alice = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.bob = conn.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
        self.state = StateService(self.db)

    def _grant(self, owner, template, **kwargs):
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return create_item(conn, self.ruleset, owner, template,
                               source="run_reward", now=100, **kwargs)

    def test_empty_state_does_not_mutate_profile_or_grant_starter(self):
        before = self.state.get_state(self.alice)
        self.assertEqual((before["wallet"]["coin_minor"], before["items"],
                          before["profile_version"], before["asset_revision"]),
                         (100000, [], None, 1))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_profiles").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_items").fetchone()[0], 0)

    def test_factory_freezes_distinct_effects_and_routes_full_bag_to_pending(self):
        with self.db() as conn, conn:
            conn.execute("INSERT INTO dungeon_profiles VALUES ('alice',0,1,1,100,100)")
        first = self._grant("alice", "beta.p0.weapon.bulwark_blade", item_id="bulwark")
        second = self._grant("alice", "beta.p0.weapon.tempo_blade", item_id="tempo")
        self.assertEqual((first["location"], second["location"]), ("bag", "pending"))
        self.assertEqual(first["stats"], second["stats"])
        self.assertNotEqual(first["effects"], second["effects"])
        snapshot = self.state.get_state(self.alice)
        self.assertEqual(snapshot["pending_count"], 1)
        self.assertEqual(snapshot["items"][0]["effects"], first["effects"])
        self.assertEqual(snapshot["items"][0]["legacy_effects"], [])
        self.assertFalse(snapshot["items"][1]["can_trade"])
        with self.db() as conn:
            rows = conn.execute("""SELECT effects_json,beta_effects_json,beta_ruleset_hash
                FROM dungeon_items ORDER BY item_id""").fetchall()
        self.assertEqual([row[0] for row in rows], ["[]", "[]"])
        self.assertEqual(json.loads(rows[0][1]), first["effects"])
        self.assertEqual(rows[0][2], self.ruleset.ruleset_hash)
        schema = json.loads((ROOT / "contracts/dungeon/schemas/beta_messages.schema.json").read_text())
        snapshot["ruleset"] = self.ruleset.reference()
        self.assertEqual(validation_errors({"$ref": "#/$defs/get_state_result",
                                            "$defs": schema["$defs"]}, snapshot), [])

    def test_state_fixture_matches_current_p0_factory_and_ruleset(self):
        fixture = json.loads((ROOT / "contracts/dungeon/fixtures/state_lifecycle.json").read_text())
        expected = fixture["results"]["get_state_result"]
        self.assertEqual(expected["ruleset"], self.ruleset.reference())
        granted = self._grant("alice", "beta.p0.weapon.bulwark_blade",
                              item_id="example-sword")
        self.assertEqual(expected["items"][0]["effects"], granted["effects"])
        self.assertEqual(expected["items"][0]["template_version"], granted["template_version"])
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO dungeon_beta_progress VALUES (?,'beta.p0.clear.rm01')",
                         (self.alice,))
            reserve_item(conn, "alice", "example-sword", "trade_offer", "offer-1", 101)
        actual = self.state.get_state(self.alice)
        actual["ruleset"] = self.ruleset.reference()
        self.assertEqual(actual, expected)

    def test_p0_grant_upgrade_trade_preserves_frozen_effects(self):
        granted = self._grant("alice", "beta.p0.weapon.bulwark_blade", item_id="p0_sword")
        with self.db() as conn, conn:
            conn.execute("INSERT INTO dungeon_beta_progress VALUES (?,'beta.p0.clear.rm01')",
                         (self.alice,))
        growth = FixedLevelPolicy(self.ruleset.ruleset_id,
                                  self.ruleset.mutable_content("progression"))
        assets = AssetService(self.db, growth, clock=lambda: 200)
        quote = assets.quote_upgrade(self.alice, "p0_sword", 1)
        upgraded = assets.upgrade(self.alice, "upgrade-p0", "p0_sword", 1,
                                  expected_item_version=1,
                                  expected_ruleset_id=quote["ruleset_id"],
                                  expected_costs=quote["costs"])
        self.assertEqual(upgraded["result"]["item_version"], 2)
        policy = TradePolicy.from_economy(self.ruleset.content("economy"))
        trades = TradeService(self.db, policy, clock=lambda: 300)
        fee = policy.quote(500, 300)["fee_minor"]
        offer = trades.create_offer(self.alice, "offer-p0", "p0_sword",
                                    expected_item_version=2, buyer_id=self.bob,
                                    price_minor=500, expected_policy_version=policy.policy_version,
                                    expected_fee_minor=fee)
        snapshot = offer["result"]["item_snapshot"]
        self.assertEqual(snapshot["effects"], granted["effects"])
        self.assertEqual(snapshot["ruleset_hash"], self.ruleset.ruleset_hash)
        trades.accept_offer(self.bob, "accept-p0", offer["result"]["offer_id"],
                            expected_offer_version=1)
        bought = self.state.get_state(self.bob)["items"][0]
        self.assertEqual((bought["effects"], bought["source"], bought["ruleset_hash"]),
                         (granted["effects"], "run_reward", self.ruleset.ruleset_hash))
        self.assertEqual((bought["upgrade_level"], bought["version"]), (1, 3))

    def test_test_source_is_bound_and_open_offer_buyer_deletion_releases_reservation(self):
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            test_item = create_item(conn, self.ruleset, "alice", "beta.sword.basic",
                                    source="test", item_id="test_item", now=100)
        self.assertEqual(test_item["bound_reason"], "test")
        self.assertFalse(self.state.get_state(self.alice)["items"][0]["can_trade"])
        tradable = self._grant("alice", "beta.sword.basic", item_id="real_item")
        policy = TradePolicy.from_economy(self.ruleset.content("economy"))
        trades = TradeService(self.db, policy, clock=lambda: 300)
        with self.assertRaises(DungeonError):
            trades.create_offer(self.alice, "offer-test", "test_item",
                                expected_item_version=1, buyer_id=self.bob, price_minor=500,
                                expected_policy_version=policy.policy_version,
                                expected_fee_minor=policy.quote(500, 300)["fee_minor"])
        offer = trades.create_offer(self.alice, "offer-real", tradable["item_id"],
                                    expected_item_version=1, buyer_id=self.bob, price_minor=500,
                                    expected_policy_version=policy.policy_version,
                                    expected_fee_minor=policy.quote(500, 300)["fee_minor"])
        before = self.state.get_state(self.alice)["asset_revision"]
        with self.db() as conn, conn:
            conn.execute("DELETE FROM users WHERE id=?", (self.bob,))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_asset_reservations"
                                          ).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_trade_offers"
                                          ).fetchone()[0], 0)
        self.assertGreater(self.state.get_state(self.alice)["asset_revision"], before)
        self.assertTrue(next(item for item in self.state.get_state(self.alice)["items"]
                             if item["item_id"] == "real_item")["can_trade"])

    def test_legacy_starter_equip_and_lock_advance_beta_revision(self):
        self.assertEqual(self.state.get_state(self.alice)["asset_revision"], 1)
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_dungeon(conn, "alice", 100)
        starter_revision = self.state.get_state(self.alice)["asset_revision"]
        self.assertGreater(starter_revision, 1)
        granted = self._grant("alice", "beta.sword.basic", item_id="legacy_equip")
        created_revision = self.state.get_state(self.alice)["asset_revision"]
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("SELECT version FROM dungeon_profiles WHERE username='alice'").fetchone()[0]
            run_dungeon_action(conn, "alice", "legacy-equip-1", "dungeon_equip",
                               {"slot": "weapon", "item_id": granted["item_id"]},
                               version, 101, adjust_coins)
        equipped_revision = self.state.get_state(self.alice)["asset_revision"]
        self.assertEqual(equipped_revision, created_revision + 1)
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("SELECT version FROM dungeon_profiles WHERE username='alice'").fetchone()[0]
            run_dungeon_action(conn, "alice", "legacy-lock-1", "dungeon_lock_item",
                               {"item_id": granted["item_id"], "locked": True},
                               version, 102, adjust_coins)
        locked = next(item for item in self.state.get_state(self.alice)["items"]
                      if item["item_id"] == granted["item_id"])
        self.assertTrue(locked["locked"])
        self.assertEqual(self.state.get_state(self.alice)["asset_revision"], equipped_revision + 1)

    def test_state_pins_one_wal_snapshot_across_concurrent_asset_and_wallet_commit(self):
        self._grant("alice", "beta.sword.basic", item_id="before_commit")
        with self.db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")

        outer = self

        class RacingWallet(SharedWalletPort):
            def balance(self, conn, username):
                with outer.db() as writer, writer:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute("UPDATE users SET coins=900 WHERE username='alice'")
                    create_item(writer, outer.ruleset, "alice", "beta.sword.basic",
                                source="run_reward", item_id="after_commit", now=101)
                return super().balance(conn, username)

        stale = StateService(self.db, wallet=RacingWallet()).get_state(self.alice)
        self.assertEqual(stale["wallet"]["coin_minor"], 100000)
        self.assertEqual([item["item_id"] for item in stale["items"]], ["before_commit"])
        fresh = self.state.get_state(self.alice)
        self.assertEqual(fresh["wallet"]["coin_minor"], 90000)
        self.assertEqual({item["item_id"] for item in fresh["items"]},
                         {"before_commit", "after_commit"})


if __name__ == "__main__":
    unittest.main()
