"""PoE2-inspired local crafting: tier gates, odds and persisted currency actions."""

import asyncio
from functools import partial
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.legacy.actions import run_dungeon_action
from dungeon.legacy.crafting import (GROUP_BY_ID, apply_currency,
                                     deterministic_rng, eligible_affixes,
                                     probability_table)
from dungeon.legacy.service import DungeonError, affix_probabilities, dungeon_state
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins
from server.dungeon.legacy_protocol import DungeonProtocol


class AffixRulesTests(unittest.TestCase):
    def test_item_level_gates_and_exact_weight_fraction(self):
        self.assertEqual({tier for _, tier, _ in eligible_affixes("belt", 1)}, {3})
        self.assertEqual({tier for _, tier, _ in eligible_affixes("belt", 35)}, {2, 3})
        self.assertEqual({tier for _, tier, _ in eligible_affixes("belt", 65)}, {1, 2, 3})
        odds = probability_table("belt", 65)
        self.assertEqual(sum(row["weight"] for row in odds), odds[0]["total_weight"])
        self.assertTrue(all(row["probability_bp"] == row["weight"] * 10000 // row["total_weight"]
                            for row in odds))
        self.assertFalse(eligible_affixes("invalid", 100))

    def test_currency_rarity_slots_and_stat_conservation(self):
        rng = deterministic_rng(b"test-seed", b"craft")
        base = {"quality": "normal", "slot": "belt", "item_level": 65,
                "stats": {"max_hp": 50}, "affixes": []}
        magic = apply_currency(base, "transmutation", rng)
        self.assertEqual((magic["quality"], len(magic["affixes"])), ("excellent", 1))
        magic = apply_currency(magic, "augmentation", rng)
        self.assertEqual({a["kind"] for a in magic["affixes"]}, {"prefix", "suffix"})
        rare = apply_currency(magic, "regal", rng)
        self.assertEqual((rare["quality"], len(rare["affixes"])), ("rare", 3))
        rare = apply_currency(rare, "exalted", rng)
        rare = apply_currency(rare, "chaos", rng)
        rare = apply_currency(rare, "divine", rng)
        self.assertEqual(len(rare["affixes"]), 4)
        self.assertEqual(len({a["group"] for a in rare["affixes"]}), 4)
        for affix in rare["affixes"]:
            low, high = GROUP_BY_ID[affix["group"]]["ranges"][affix["tier"]]
            self.assertTrue(low <= affix["value"] <= high)
        self.assertEqual(rare["stats"]["max_hp"],
                         50 + sum(a["value"] for a in rare["affixes"]
                                  if a["stat"] == "max_hp"))
        annulled = apply_currency(rare, "annulment", rng)
        self.assertEqual(len(annulled["affixes"]), 3)
        alchemy = apply_currency(magic, "alchemy", rng)
        self.assertEqual((alchemy["quality"], len(alchemy["affixes"])), ("rare", 4))
        full = apply_currency(apply_currency(alchemy, "exalted", rng), "exalted", rng)
        self.assertEqual(len(full["affixes"]), 6)
        self.assertEqual(probability_table("belt", 65, full["affixes"]), [])
        with self.assertRaises(DungeonError):
            apply_currency(full, "exalted", rng)
        self.assertEqual(base["affixes"], [])
        with self.assertRaises(DungeonError):
            apply_currency(base, "exalted", rng)


class CurrencyActionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = partial(database, str(Path(temporary.name) / "crafting.db"))
        init_db(self.db)
        with self.db() as conn, conn:
            conn.execute("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES ('alice','','',0,1000)""")
            state = dungeon_state(conn, "alice", 100)
            self.item_id = state["loadout"]["helmet"]
            self.version = state["profile_version"]
            conn.execute("INSERT INTO dungeon_currency VALUES ('alice','transmutation',2)")

    def test_atomic_debit_replay_and_invalid_target(self):
        payload = {"item_id": self.item_id, "currency_id": "transmutation"}
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            first = run_dungeon_action(conn, "alice", "craft-one", "dungeon_use_currency",
                                       payload, self.version, 101, adjust_coins)
            state = dungeon_state(conn, "alice", 101)
        self.assertEqual(first["currency_remaining"], 1)
        self.assertEqual(state["currencies"]["transmutation"], 1)
        item = next(item for item in state["items"] if item["item_id"] == self.item_id)
        self.assertEqual((item["quality"], len(item["affixes"])), ("excellent", 1))
        with self.db() as conn, conn:
            odds = affix_probabilities(conn, "alice", self.item_id, 101,
                                       currency_id="augmentation")
        opposite = "suffix" if item["affixes"][0]["kind"] == "prefix" else "prefix"
        self.assertTrue(all(GROUP_BY_ID[row["group"]]["kind"] == opposite
                            for row in odds["entries"]))
        self.assertEqual(state["stats"]["values"]["max_hp"],
                         200 + 50 + 30 + item["stats"].get("max_hp", 0))
        with self.db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = run_dungeon_action(conn, "alice", "craft-one", "dungeon_use_currency",
                                        payload, self.version, 102, adjust_coins)
        self.assertTrue(replay["replayed"])
        with self.assertRaises(DungeonError) as raised:
            with self.db() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                run_dungeon_action(conn, "alice", "craft-two", "dungeon_use_currency",
                                   payload, state["profile_version"], 103, adjust_coins)
        self.assertEqual(raised.exception.code, "currency_unavailable")
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT amount FROM dungeon_currency
                WHERE username='alice' AND currency_id='transmutation'""").fetchone()[0], 1)

    def test_protocol_exposes_odds_and_currency_result(self):
        class Hub:
            clients = {}

            def __init__(self):
                self.messages = []

            async def send_json(self, _websocket, message):
                self.messages.append(message)

            async def send_to_user(self, _username, message):
                self.messages.append(message)

        hub = Hub()
        protocol = DungeonProtocol(database=self.db, hub=hub, clock=lambda: 101)
        self.assertIn("dungeon_affix_probabilities", protocol.handlers())
        self.assertIn("dungeon_use_currency", protocol.handlers())
        state = {"user": {"username": "alice", "coins": 1000}}

        async def exercise():
            await protocol.handle_affix_probabilities(None, state,
                {"request_id": "odds-one", "item_id": self.item_id})
            await protocol.handle_action(None, state,
                {"type": "dungeon_use_currency", "request_id": "craft-one",
                 "item_id": self.item_id, "currency_id": "transmutation",
                 "expected_version": self.version})

        asyncio.run(exercise())
        odds = next(message for message in hub.messages
                    if message["type"] == "dungeon_affix_probabilities")
        self.assertTrue(odds["entries"])
        self.assertEqual(sum(row["weight"] for row in odds["entries"]),
                         odds["entries"][0]["total_weight"])
        result = next(message for message in hub.messages
                      if message["type"] == "dungeon_result")
        self.assertEqual(result["state"]["currencies"]["transmutation"], 1)

    def test_locked_item_and_user_deletion_protect_currency(self):
        with self.db() as conn, conn:
            conn.execute("UPDATE dungeon_items SET locked=1 WHERE item_id=?", (self.item_id,))
        with self.assertRaises(DungeonError) as raised:
            with self.db() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                run_dungeon_action(conn, "alice", "locked-craft", "dungeon_use_currency",
                    {"item_id": self.item_id, "currency_id": "transmutation"},
                    self.version, 101, adjust_coins)
        self.assertEqual(raised.exception.code, "item_protected")
        with self.db() as conn, conn:
            self.assertEqual(conn.execute("""SELECT amount FROM dungeon_currency
                WHERE username='alice' AND currency_id='transmutation'""").fetchone()[0], 2)
            conn.execute("DELETE FROM users WHERE username='alice'")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_currency").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
