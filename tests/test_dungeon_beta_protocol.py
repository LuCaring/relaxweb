#!/usr/bin/env python3
"""dungeon_beta_* protocol: auth, envelopes, upgrade and trade over WebSocket."""

import asyncio
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.trading import TradeService
from dungeon.content.loader import load_ruleset
from dungeon.contracts.json_validation import validation_errors
from dungeon.domain.trading import TradePolicy
from server.app import create_app
from server.database import database
from server.dungeon.beta_protocol import DungeonBetaProtocol
from server.schema import init_db


ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "contracts" / "dungeon" / "schemas"
FIXTURES = ROOT / "contracts" / "dungeon" / "fixtures"
RELEASE = ROOT / "content/dungeon/release.json"


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))

    def last(self, kind):
        return next(row for row in reversed(self.messages) if row["type"] == kind)


class FixtureTests(unittest.TestCase):
    def test_lifecycle_fixtures_match_the_frozen_message_schema(self):
        schema = json.loads((SCHEMAS / "beta_messages.schema.json").read_text())
        for name in ("upgrade_lifecycle.json", "trade_lifecycle.json"):
            fixture = json.loads((FIXTURES / name).read_text())
            for group, examples in fixture.items():
                if not isinstance(examples, dict):
                    continue
                for def_name, example in examples.items():
                    with self.subTest(file=name, definition=def_name):
                        self.assertEqual([],
                                         validation_errors({"$ref": f"#/$defs/{def_name}",
                                                            "$defs": schema["$defs"]}, example))


class BetaProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = partial(database, str(Path(self.tmp.name) / "beta.db"))
        self.app = create_app(self.db)
        self.addAsyncCleanup(self.app.aclose)
        init_db(self.db)
        with self.db() as conn, conn:
            conn.executemany("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,10)""", [("alice",), ("bob",), ("carol",)])
            self.seller = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.buyer = conn.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
                VALUES ('beta_sword','alice','beta.sword.basic','0.1.0','weapon','normal',
                '{"atk":30}',100)""")
            conn.execute("INSERT INTO dungeon_beta_progress VALUES (?,'beta.clear.first_boss')",
                         (self.seller,))
        self.a, self.a2, self.b, self.c = Socket(), Socket(), Socket(), Socket()
        self.a_state = self._connect(self.a, "alice")
        self._connect(self.a2, "alice")
        self.b_state = self._connect(self.b, "bob")
        self.c_state = self._connect(self.c, "carol")
        self.handlers = self.app.handlers

    def _connect(self, socket, username):
        state = {"user": {"username": username, "coins": 10}, "send_lock": asyncio.Lock()}
        self.app.hub.clients[socket] = state
        return state

    async def _call(self, handler, socket, state, message):
        await self.handlers[handler](socket, state, message)

    def _policy(self):
        ruleset = load_ruleset(RELEASE)
        return TradePolicy.from_economy(ruleset.content("economy"))

    # ------------------------------------------------------------------- auth

    async def test_auth_ruleset_and_catalog(self):
        await self._call("dungeon_beta_get_catalog", self.a, {"user": None},
                         {"type": "dungeon_beta_get_catalog", "protocol_version": 1,
                          "request_id": "cat-0"})
        error = self.a.last("dungeon_beta_error")
        self.assertEqual((error["code"], error["retryable"]), ("auth_required", False))
        await self._call("dungeon_beta_get_catalog", self.a, self.a_state,
                         {"type": "dungeon_beta_get_catalog", "protocol_version": 1,
                          "request_id": "cat-1"})
        result = self.a.last("dungeon_beta_result")
        self.assertEqual(result["result_kind"], "get_catalog")
        self.assertEqual(result["result"]["ruleset"]["ruleset_hash"],
                         load_ruleset(RELEASE).ruleset_hash)
        self.assertEqual(result["result"]["trade_policy"]["fee_bp"], 250)

        broken = create_app(self.db)  # broken release keeps handlers alive but unavailable
        broken.beta_protocol = DungeonBetaProtocol(
            database=self.db, hub=broken.hub,
            release_path=Path(self.tmp.name) / "missing.json")
        socket = Socket()
        broken.hub.clients[socket] = {"user": {"username": "alice", "coins": 10},
                                      "send_lock": asyncio.Lock()}
        await broken.beta_protocol.handle_get_catalog(
            socket, broken.hub.clients[socket],
            {"type": "dungeon_beta_get_catalog", "protocol_version": 1, "request_id": "cat-2"})
        self.assertEqual(socket.last("dungeon_beta_error")["code"], "ruleset_unavailable")
        await broken.aclose()

    # ---------------------------------------------------------------- upgrade

    async def test_upgrade_quote_commit_replay_and_invalidation(self):
        await self._call("dungeon_beta_quote_upgrade", self.a, self.a_state,
                         {"type": "dungeon_beta_quote_upgrade", "protocol_version": 1,
                          "request_id": "q-1", "item_id": "beta_sword", "target_level": 1})
        quote = self.a.last("dungeon_beta_result")["result"]
        self.assertEqual(quote["costs"], [{"resource_id": "wallet:coins", "amount": 200}])
        upgrade = {"type": "dungeon_beta_upgrade", "protocol_version": 1, "request_id": "up-1",
                   "item_id": "beta_sword", "target_level": 1, "expected_item_version": 1,
                   "expected_ruleset_id": quote["ruleset_id"], "expected_costs": quote["costs"]}
        await self._call("dungeon_beta_upgrade", self.a, self.a_state, upgrade)
        result = self.a.last("dungeon_beta_result")
        self.assertEqual(result["result"]["wallet_at_commit"]["coin_minor"], 800)
        self.assertEqual(self.a2.last("dungeon_beta_invalidate")["asset_revision"], 2)
        await self._call("dungeon_beta_upgrade", self.a, self.a_state, upgrade)
        self.assertTrue(self.a.last("dungeon_beta_result")["replayed"])
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT version FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], 2)
        await self._call("dungeon_beta_get_receipt", self.a, self.a_state,
                         {"type": "dungeon_beta_get_receipt", "protocol_version": 1,
                          "request_id": "rc-1", "lookup_request_id": "up-1"})
        receipt = self.a.last("dungeon_beta_result")["result"]
        self.assertTrue(receipt["found"])
        self.assertEqual(receipt["receipt"]["result"]["item_version"], 2)
        await self._call("dungeon_beta_get_receipt", self.a, self.a_state,
                         {"type": "dungeon_beta_get_receipt", "protocol_version": 1,
                          "request_id": "rc-2", "lookup_request_id": "never"})
        self.assertFalse(self.a.last("dungeon_beta_result")["result"]["found"])

    async def test_upgrade_errors_map_to_beta_envelope(self):
        await self._call("dungeon_beta_upgrade", self.a, self.a_state,
                         {"type": "dungeon_beta_upgrade", "protocol_version": 1,
                          "request_id": "up-bad", "item_id": "beta_sword", "target_level": 1,
                          "expected_item_version": 9, "expected_ruleset_id": "x",
                          "expected_costs": [{"resource_id": "wallet:coins", "amount": 200}]})
        error = self.a.last("dungeon_beta_error")
        self.assertEqual((error["code"], error["retryable"], error["request_id"]),
                         ("version_conflict", False, "up-bad"))
        with self.db() as conn, conn:
            conn.execute("UPDATE users SET coins=1 WHERE username='alice'")
        await self._call("dungeon_beta_quote_upgrade", self.a, self.a_state,
                         {"type": "dungeon_beta_quote_upgrade", "protocol_version": 1,
                          "request_id": "q-2", "item_id": "beta_sword", "target_level": 1})
        quote = self.a.last("dungeon_beta_result")["result"]
        await self._call("dungeon_beta_upgrade", self.a, self.a_state,
                         {"type": "dungeon_beta_upgrade", "protocol_version": 1,
                          "request_id": "up-poor", "item_id": "beta_sword", "target_level": 1,
                          "expected_item_version": 1, "expected_ruleset_id": quote["ruleset_id"],
                          "expected_costs": quote["costs"]})
        self.assertEqual(self.a.last("dungeon_beta_error")["code"], "insufficient_funds")
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT stats_json FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], '{"atk":30}')

    # ------------------------------------------------------------------ trade

    async def _offer(self):
        policy = self._policy()
        await self._call("dungeon_beta_create_offer", self.a, self.a_state,
                         {"type": "dungeon_beta_create_offer", "protocol_version": 1,
                          "request_id": "of-1", "item_id": "beta_sword",
                          "expected_item_version": 1, "buyer_id": self.buyer,
                          "price_minor": 500,
                          "expected_trade_policy_version": policy.policy_version,
                          "expected_fee_minor": policy.quote(500, 0)["fee_minor"]})
        return self.a.last("dungeon_beta_result")["result"]

    async def test_trade_over_protocol_notifies_both_parties(self):
        offer = await self._offer()
        self.assertEqual(offer["status"], "open")
        await self._call("dungeon_beta_accept_offer", self.b, self.b_state,
                         {"type": "dungeon_beta_accept_offer", "protocol_version": 1,
                          "request_id": "ac-1", "offer_id": offer["offer_id"],
                          "expected_offer_version": 1})
        result = self.b.last("dungeon_beta_result")["result"]
        self.assertEqual((result["price_minor"], result["fee_minor"],
                          result["seller_net_minor"], result["seller_id"]),
                         (500, 12, 488, self.seller))
        self.assertEqual(result["wallet_at_commit"]["coin_minor"], 500)
        self.assertIsNotNone(self.a.last("dungeon_beta_invalidate"))
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT owner FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], "bob")
        await self._call("dungeon_beta_list_offers", self.a, self.a_state,
                         {"type": "dungeon_beta_list_offers", "protocol_version": 1,
                          "request_id": "ls-1"})
        listed = self.a.last("dungeon_beta_result")["result"]["offers"]
        self.assertEqual(listed[0]["status"], "accepted")
        # a second browser tab of the seller keeps the refreshed coin cache
        self.assertAlmostEqual(self.a2_state_user_coins("alice"), 14.88, places=6)

    def a2_state_user_coins(self, username):
        for state in self.app.hub.clients.values():
            account = state.get("user")
            if account and account.get("username") == username:
                return account["coins"]
        raise AssertionError("alice connection missing")

    async def test_trade_guards_wrong_buyer_and_cancel_terminal_state(self):
        offer = await self._offer()
        await self._call("dungeon_beta_accept_offer", self.c, self.c_state,
                         {"type": "dungeon_beta_accept_offer", "protocol_version": 1,
                          "request_id": "ac-c", "offer_id": offer["offer_id"],
                          "expected_offer_version": 1})
        self.assertEqual(self.c.last("dungeon_beta_error")["code"], "forbidden")
        await self._call("dungeon_beta_cancel_offer", self.a, self.a_state,
                         {"type": "dungeon_beta_cancel_offer", "protocol_version": 1,
                          "request_id": "ca-1", "offer_id": offer["offer_id"],
                          "expected_offer_version": 1})
        self.assertEqual(self.a.last("dungeon_beta_result")["result"]["status"], "cancelled")
        await self._call("dungeon_beta_accept_offer", self.b, self.b_state,
                         {"type": "dungeon_beta_accept_offer", "protocol_version": 1,
                          "request_id": "ac-1", "offer_id": offer["offer_id"],
                          "expected_offer_version": 1})
        self.assertEqual(self.b.last("dungeon_beta_error")["code"], "offer_closed")
        with self.db() as conn:
            self.assertEqual(conn.execute("""SELECT owner FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], "alice")
            self.assertAlmostEqual(conn.execute(
                "SELECT coins FROM users WHERE username='bob'").fetchone()[0], 10, places=6)


class InitTestDbTests(unittest.TestCase):
    def test_creates_loginable_accounts_and_is_idempotent(self):
        from dungeon.tools.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.db")
            for _ in range(2):  # second run must skip, not duplicate
                self.assertEqual(main(["init-test-db", path, "--coins", "77"]), 0)
            factory = partial(database, path)
            with factory() as conn:
                rows = dict(conn.execute("SELECT username,coins FROM users").fetchall())
                self.assertEqual(rows, {"beta_seller": 77.0, "beta_buyer": 77.0})
                sword = conn.execute("""SELECT owner,template_id FROM dungeon_items
                    WHERE template_id='beta.sword.basic'""").fetchall()
                self.assertEqual(sword, [("beta_seller", "beta.sword.basic")])
                seller_id = conn.execute(
                    "SELECT id FROM users WHERE username='beta_seller'").fetchone()[0]
                self.assertEqual(conn.execute("""SELECT COUNT(*) FROM dungeon_beta_progress
                    WHERE user_id=? AND progress_id='beta.clear.first_boss'""",
                    (seller_id,)).fetchone()[0], 1)
            # the created accounts can actually log in through the real verifier
            from server.accounts import Accounts
            accounts = Accounts(factory)
            self.assertIsNotNone(accounts.authenticate_user("beta_seller", "dungeon-beta"))
            self.assertIsNone(accounts.authenticate_user("beta_seller", "wrong"))


if __name__ == "__main__":
    unittest.main()
