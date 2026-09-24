#!/usr/bin/env python3
"""Directed trade offers: reservation, expiry, atomic settlement, migration."""

from functools import partial
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.trading import TradeService
from dungeon.application.wallet import SharedWalletPort
from dungeon.content.loader import load_ruleset
from dungeon.domain.errors import DungeonError
from dungeon.domain.trading import TradePolicy
from dungeon.storage.beta_schema import init_beta
from dungeon.storage.legacy_schema import init_dungeon
from server.database import database
from server.schema import init_db


RELEASE = Path(__file__).resolve().parent.parent / "content/dungeon/release.json"


class TradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = partial(database, str(Path(self.tmp.name) / "trading.db"))
        init_db(self.database)
        self.now = 10_000
        with self.database() as conn, conn:
            conn.executemany("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,10)""", [("alice",), ("bob",), ("carol",)])
            self.seller = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.buyer = conn.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
            self.other = conn.execute("SELECT id FROM users WHERE username='carol'").fetchone()[0]
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
                VALUES ('beta_sword','alice','beta.sword.basic','0.1.0','weapon','normal',
                '{"atk":30}',100)""")
        self.policy = TradePolicy.from_economy(load_ruleset(RELEASE).content("economy"))
        self.service = TradeService(self.database, self.policy,
                                     clock=lambda: self.now)

    # ------------------------------------------------------------------ helpers

    def assertCoins(self, state, **expected):
        for name, value in expected.items():
            self.assertAlmostEqual(state["coins"][name], value, places=6)

    def offer(self, *, price=500, buyer=None, item="beta_sword", request="offer-1",
              fee=None, policy_version=None):
        return self.service.create_offer(
            self.seller, request, item, expected_item_version=1,
            buyer_id=self.buyer if buyer is None else buyer, price_minor=price,
            expected_policy_version=self.policy.policy_version if policy_version is None else policy_version,
            expected_fee_minor=self.policy.quote(price, self.now)["fee_minor"] if fee is None else fee)

    def accept(self, offer_id, *, request="accept-1", version=1, user=None):
        return self.service.accept_offer(self.buyer if user is None else user, request,
                                         offer_id, expected_offer_version=version)

    def snapshot(self):
        with self.database() as conn:
            coins = dict(conn.execute("SELECT username,round(coins,2) FROM users").fetchall())
            item = conn.execute("SELECT owner,location,version FROM dungeon_items "
                                "WHERE item_id='beta_sword'").fetchone()
            offers = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT offer_id,status,offer_version FROM dungeon_trade_offers")}
            reservations = conn.execute("SELECT COUNT(*) FROM dungeon_asset_reservations"
                                        ).fetchone()[0]
            settlements = conn.execute("SELECT COUNT(*) FROM dungeon_trade_settlements"
                                       ).fetchone()[0]
            receipts = conn.execute("SELECT COUNT(*) FROM dungeon_beta_receipts").fetchone()[0]
            flows = list(conn.execute("""SELECT username,amount FROM coin_transactions
                WHERE kind IN ('dungeon_trade_buy','dungeon_trade_sell') ORDER BY id"""))
            return {"coins": coins, "item": item, "offers": offers,
                    "reservations": reservations, "settlements": settlements,
                    "receipts": receipts, "flows": flows}

    # ------------------------------------------------------------------- policy

    def test_policy_from_real_content_freezes_fee_and_expiry(self):
        self.assertEqual((self.policy.fee_bp, self.policy.minimum_price_minor,
                          self.policy.maximum_price_minor, self.policy.ttl_seconds),
                         (250, 100, 10_000_000, 86_400))
        quote = self.policy.quote(10_000, 5_000)
        self.assertEqual((quote["fee_minor"], quote["seller_net_minor"], quote["expires_at"]),
                         (250, 9_750, 5_000 + 86_400))
        self.assertEqual(TradePolicy(1, 0, 100, 200, 60).quote(150, 0)["fee_minor"], 0)
        with self.assertRaises(DungeonError):
            self.policy.validate_price(self.policy.minimum_price_minor - 1)
        with self.assertRaises(DungeonError):
            self.policy.validate_price(self.policy.maximum_price_minor + 1)
        for bad in (True, "500", 500.0, None):
            with self.subTest(price=bad), self.assertRaises(DungeonError):
                self.policy.validate_price(bad)
        for field in ("fee_bp", "minimum_price_minor", "ttl_seconds"):
            with self.subTest(field=field), self.assertRaises(DungeonError):
                TradePolicy.from_economy({"trade_policy": {
                    "policy_version": 1, "fee_bp": 250, "minimum_price_minor": 100,
                    "maximum_price_minor": 100, "ttl_seconds": 86_400, field: -1}})

    # ------------------------------------------------------------------- create

    def test_create_offer_freezes_snapshot_and_replays(self):
        result = self.offer()
        self.assertEqual(result["result"]["status"], "open")
        self.assertEqual(result["result"]["fee_minor"], 12)
        self.assertEqual(result["result"]["seller_net_minor"], 488)
        self.assertEqual(result["result"]["expires_at"], self.now + self.policy.ttl_seconds)
        self.assertEqual(result["result"]["item_snapshot"]["stats"], {"atk": 30})
        replay = self.offer()
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.snapshot()["offers"][result["result"]["offer_id"]], ("open", 1))
        with self.assertRaises(DungeonError) as error:
            self.offer(price=600)  # same request id, different payload
        self.assertEqual(error.exception.code, "request_conflict")
        self.assertEqual(len(self.snapshot()["offers"]), 1)

    def test_create_offer_rejects_bad_targets_prices_and_stale_expectations(self):
        cases = [
            ({"buyer": self.seller}, "invalid_request"),          # self trade
            ({"buyer": 999_999}, "auth_required"),                # unknown buyer
            ({"price": self.policy.minimum_price_minor - 1}, "invalid_request"),
            ({"price": self.policy.maximum_price_minor + 1}, "invalid_request"),
            ({"price": True}, "invalid_request"),
            ({"fee": 13}, "quote_changed"),                       # fee moved
            ({"policy_version": 2}, "quote_changed"),             # policy version moved
        ]
        for kwargs, code in cases:
            with self.subTest(**kwargs), self.assertRaises(DungeonError) as error:
                self.offer(**kwargs)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(self.snapshot()["reservations"], 0)

    def test_create_offer_rejects_untradable_or_reserved_items(self):
        self.offer()
        with self.assertRaises(DungeonError) as error:  # second offer on reserved item
            self.offer(request="offer-2")
        self.assertEqual(error.exception.code, "asset_reserved")
        offer_id = self.snapshot()["offers"].popitem()[0]
        self.service.cancel_offer(self.seller, "cancel-1", offer_id, expected_offer_version=1)
        restore = {"locked": 0, "location": "bag", "beta_bound_reason": None, "beta_source": None}
        for field, value in (("locked", 1), ("location", "pending"),
                             ("beta_bound_reason", "starter"), ("beta_source", "test")):
            with self.subTest(field=field):
                with self.database() as conn, conn:
                    conn.execute(f"UPDATE dungeon_items SET {field}=? WHERE item_id='beta_sword'", (value,))
                with self.assertRaises(DungeonError) as error:
                    self.offer(request=f"offer-{field}")
                self.assertEqual(error.exception.code, "asset_not_tradable")
                with self.database() as conn, conn:
                    conn.execute(f"UPDATE dungeon_items SET {field}=? WHERE item_id='beta_sword'",
                                 (restore[field],))
        with self.database() as conn, conn:
            conn.execute("UPDATE dungeon_items SET template_id='starter_sword' WHERE item_id='beta_sword'")
        with self.assertRaises(DungeonError) as error:
            self.offer(request="offer-starter")
        self.assertEqual(error.exception.code, "asset_not_tradable")
        with self.database() as conn, conn:
            conn.execute("UPDATE dungeon_items SET template_id='beta.sword.basic' WHERE item_id='beta_sword'")
            conn.execute("INSERT INTO dungeon_loadout VALUES ('alice','weapon','beta_sword')")
        with self.assertRaises(DungeonError) as error:
            self.offer(request="offer-equipped")
        self.assertEqual(error.exception.code, "asset_not_tradable")
        with self.database() as conn, conn:
            conn.execute("DELETE FROM dungeon_loadout WHERE item_id='beta_sword'")
            conn.execute("""INSERT INTO dungeon_runs
                (battle_id,username,challenge_id,difficulty_id,status,snapshot_json,snapshot_hash,
                 checkpoint_json,wall_anchor_ms,created_at,updated_at)
                VALUES ('battle-1','alice','c','normal','running',?,'hash','{}',0,100,100)""",
                (json.dumps({"equipment": [{"item_id": "beta_sword"}]}),))
            conn.execute("INSERT INTO dungeon_active_jobs VALUES ('alice','battle','battle-1')")
        with self.assertRaises(DungeonError) as error:
            self.offer(request="offer-active")
        self.assertEqual(error.exception.code, "asset_reserved")
        with self.assertRaises(DungeonError) as error:  # item version drifted
            self.service.create_offer(self.seller, "offer-stale", "beta_sword",
                                      expected_item_version=2, buyer_id=self.buyer,
                                      price_minor=500, expected_policy_version=1,
                                      expected_fee_minor=12)
        self.assertEqual(error.exception.code, "version_conflict")
        self.assertEqual(self.snapshot()["reservations"], 0)

    # ------------------------------------------------------------------- accept

    def test_accept_settles_atomically_and_conserves_money(self):
        offer_id = self.offer()["result"]["offer_id"]
        result = self.accept(offer_id)["result"]
        self.assertEqual((result["location"], result["item_version"], result["fee_minor"]),
                         ("bag", 2, 12))
        self.assertEqual(result["wallet_at_commit"]["coin_minor"], 500)
        state = self.snapshot()
        self.assertCoins(state, alice=14.88, bob=5.0, carol=10)
        self.assertEqual(state["item"], ("bob", "bag", 2))
        self.assertEqual(state["offers"][offer_id], ("accepted", 2))
        self.assertEqual(state["reservations"], 0)
        self.assertEqual(state["settlements"], 1)
        self.assertEqual(state["flows"], [("bob", -5.0), ("alice", 4.88)])
        # buyer total = seller net + burned fee, so site coin supply shrinks by the fee only
        self.assertAlmostEqual(sum(amount for _, amount in state["flows"]), -0.12, places=6)
        with self.database() as conn:
            row = conn.execute("""SELECT price_minor,fee_minor,seller_net_minor,item_location,
                buyer_request_id FROM dungeon_trade_settlements""").fetchone()
        self.assertEqual(row, (500, 12, 488, "bag", "accept-1"))

    def test_accept_replay_returns_original_result_and_blocks_reuse(self):
        offer_id = self.offer()["result"]["offer_id"]
        first = self.accept(offer_id)
        replay = self.accept(offer_id)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["result"], first["result"])
        self.assertEqual(self.snapshot()["settlements"], 1)
        with self.assertRaises(DungeonError) as error:  # same id, different offer
            self.accept("other", request="accept-1")
        self.assertEqual(error.exception.code, "request_conflict")

    def test_accept_guards_buyer_funds_version_and_ownership(self):
        offer_id = self.offer()["result"]["offer_id"]
        with self.assertRaises(DungeonError) as error:  # not the designated buyer
            self.accept(offer_id, user=self.other)
        self.assertEqual(error.exception.code, "forbidden")
        with self.assertRaises(DungeonError) as error:  # seller cannot accept own sale
            self.accept(offer_id, user=self.seller)
        self.assertEqual(error.exception.code, "forbidden")
        with self.assertRaises(DungeonError) as error:  # stale offer version
            self.accept(offer_id, version=2)
        self.assertEqual(error.exception.code, "version_conflict")
        with self.database() as conn, conn:
            conn.execute("UPDATE users SET coins=4 WHERE username='bob'")
        with self.assertRaises(DungeonError) as error:
            self.accept(offer_id)
        self.assertEqual(error.exception.code, "insufficient_funds")
        state = self.snapshot()
        self.assertEqual(state["item"], ("alice", "bag", 1))
        self.assertEqual(state["offers"][offer_id], ("open", 1))
        self.assertEqual((state["settlements"], state["flows"]), (0, []))
        with self.database() as conn, conn:  # item drifted: offer unusable, no money moves
            conn.execute("UPDATE dungeon_items SET version=7 WHERE item_id='beta_sword'")
        with self.assertRaises(DungeonError) as error:
            self.accept(offer_id, request="accept-drift")
        self.assertEqual(error.exception.code, "version_conflict")

    def test_accept_and_cancel_compete_for_a_single_terminal_state(self):
        offer_id = self.offer()["result"]["offer_id"]
        self.service.cancel_offer(self.seller, "cancel-1", offer_id, expected_offer_version=1)
        with self.assertRaises(DungeonError) as error:  # accept after cancel
            self.accept(offer_id)
        self.assertEqual(error.exception.code, "offer_closed")
        with self.assertRaises(DungeonError) as error:  # cancel after cancel
            self.service.cancel_offer(self.seller, "cancel-2", offer_id, expected_offer_version=2)
        self.assertEqual(error.exception.code, "offer_closed")
        self.assertCoins(self.snapshot(), alice=10, bob=10, carol=10)

        offer_id = self.offer(request="offer-2")["result"]["offer_id"]
        self.accept(offer_id)
        with self.assertRaises(DungeonError) as error:  # second accept loses the race
            self.accept(offer_id, request="accept-late")
        self.assertEqual(error.exception.code, "offer_closed")
        with self.assertRaises(DungeonError) as error:  # seller cancel after settlement
            self.service.cancel_offer(self.seller, "cancel-3", offer_id, expected_offer_version=2)
        self.assertEqual(error.exception.code, "offer_closed")
        state = self.snapshot()
        self.assertEqual((state["settlements"], state["reservations"]), (1, 0))
        self.assertCoins(state, alice=14.88, bob=5.0, carol=10)

    def test_cancel_releases_reservation_and_replays(self):
        offer_id = self.offer()["result"]["offer_id"]
        with self.assertRaises(DungeonError) as error:  # only the seller may cancel
            self.service.cancel_offer(self.buyer, "cancel-1", offer_id, expected_offer_version=1)
        self.assertEqual(error.exception.code, "forbidden")
        result = self.service.cancel_offer(self.seller, "cancel-1", offer_id,
                                           expected_offer_version=1)["result"]
        self.assertEqual(result["status"], "cancelled")
        replay = self.service.cancel_offer(self.seller, "cancel-1", offer_id,
                                           expected_offer_version=1)
        self.assertTrue(replay["replayed"])
        state = self.snapshot()
        self.assertEqual(state["offers"][offer_id], ("cancelled", 2))
        self.assertEqual(state["reservations"], 0)
        self.assertEqual(state["item"], ("alice", "bag", 1))
        self.assertAlmostEqual(state["coins"]["alice"], 10, places=6)

    def test_full_bag_routes_item_to_pending_and_creates_profile(self):
        with self.database() as conn, conn:
            conn.execute("""INSERT INTO dungeon_profiles
                (username,starter_granted,bag_capacity,version,created_at,updated_at)
                VALUES ('bob',0,1,1,1,1)""")
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
                VALUES ('bob_old','bob','beta.sword.basic','0.1.0','weapon','normal','{}',1)""")
        offer_id = self.offer()["result"]["offer_id"]
        result = self.accept(offer_id)["result"]
        self.assertEqual(result["location"], "pending")
        self.assertEqual(self.snapshot()["item"], ("bob", "pending", 2))
        with self.database() as conn:
            self.assertEqual(conn.execute("""SELECT location FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], "pending")

    # ------------------------------------------------------------------ expiry

    def test_expiry_blocks_accept_releases_reservation_and_survives_replay(self):
        offer_id = self.offer()["result"]["offer_id"]
        self.now += self.policy.ttl_seconds + 1
        with self.assertRaises(DungeonError) as error:
            self.accept(offer_id)
        self.assertEqual(error.exception.code, "offer_expired")
        state = self.snapshot()
        self.assertEqual(state["offers"][offer_id], ("expired", 2))
        self.assertEqual(state["reservations"], 0)
        self.assertEqual(state["item"], ("alice", "bag", 1))
        with self.assertRaises(DungeonError) as error:  # cancel of an expired offer
            self.service.cancel_offer(self.seller, "cancel-1", offer_id, expected_offer_version=1)
        self.assertEqual(error.exception.code, "offer_expired")
        with self.assertRaises(DungeonError) as error:  # still not acceptable afterwards
            self.accept(offer_id, request="accept-late")
        self.assertEqual(error.exception.code, "offer_expired")
        self.assertEqual(self.snapshot()["settlements"], 0)
        # list_offers reports the expired terminal state without leaking others
        self.assertEqual(self.service.list_offers(self.seller)[0]["status"], "expired")
        self.assertEqual(self.service.list_offers(self.buyer)[0]["role"], "buyer")
        self.assertEqual(self.service.list_offers(self.other), [])
        self.assertEqual(self.service.sweep_expired(), 0)  # already finalized on read

    def test_sweep_expired_finalizes_open_offers(self):
        offer_id = self.offer()["result"]["offer_id"]
        self.now += self.policy.ttl_seconds + 1
        with self.database() as conn, conn:
            conn.execute("""INSERT INTO dungeon_items
                (item_id,owner,template_id,template_version,slot,quality,stats_json,created_at)
                VALUES ('beta_sword_2','alice','beta.sword.basic','0.1.0','weapon','normal','{}',1)""")
        # created after the clock advanced, so this one is not due yet
        self.service.create_offer(self.seller, "offer-2", "beta_sword_2",
                                  expected_item_version=1, buyer_id=self.buyer,
                                  price_minor=500, expected_policy_version=1, expected_fee_minor=12)
        self.assertEqual(self.service.sweep_expired(), 1)
        state = self.snapshot()
        self.assertEqual(state["offers"][offer_id], ("expired", 2))
        self.assertEqual(state["reservations"], 1)  # offer-2 keeps its reservation
        self.assertEqual(state["settlements"], 0)
        self.now += self.policy.ttl_seconds + 1
        self.assertEqual(self.service.sweep_expired(), 1)  # now offer-2 is due too
        self.assertEqual(self.snapshot()["reservations"], 0)

    # ----------------------------------------------------------- fault rollback

    def test_fault_between_wallet_legs_rolls_back_everything(self):
        offer_id = self.offer()["result"]["offer_id"]
        for fail_on in (1, 2):  # before buyer debit; after buyer debit, before seller credit
            with self.subTest(fail_on=fail_on):
                class FaultWallet(SharedWalletPort):
                    calls = 0

                    def change(self, *args, **kwargs):
                        type(self).calls += 1
                        if self.calls == fail_on:
                            raise RuntimeError("fault")
                        return super().change(*args, **kwargs)

                service = TradeService(self.database, self.policy, wallet=FaultWallet(),
                                       clock=lambda: self.now)
                with self.assertRaises(RuntimeError):
                    service.accept_offer(self.buyer, f"fault-{fail_on}", offer_id,
                                         expected_offer_version=1)
                state = self.snapshot()
                self.assertCoins(state, alice=10, bob=10, carol=10)
                self.assertEqual(state["item"], ("alice", "bag", 1))
                self.assertEqual(state["offers"][offer_id], ("open", 1))
                self.assertEqual((state["settlements"], state["flows"]), (0, []))
                self.assertEqual(state["reservations"], 1)
                with self.database() as conn:
                    self.assertEqual(conn.execute(
                        "SELECT COUNT(*) FROM dungeon_beta_receipts WHERE request_id LIKE 'fault-%'"
                    ).fetchone()[0], 0)
        # the offer still settles cleanly after the fault
        self.assertEqual(self.accept(offer_id)["result"]["status"], "accepted")

    # --------------------------------------------------------------- migrations

    def test_migration_two_is_idempotent_and_cleanup_keeps_bought_items(self):
        with self.database() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM dungeon_migrations").fetchone()[0], 2)
        with self.database() as conn, conn:
            init_beta(conn)
        offer_id = self.offer()["result"]["offer_id"]
        self.accept(offer_id)
        with self.database() as conn, conn:  # deleting the seller keeps the buyer's item
            conn.execute("DELETE FROM users WHERE id=?", (self.seller,))
        with self.database() as conn:
            self.assertEqual(conn.execute("""SELECT owner FROM dungeon_items
                WHERE item_id='beta_sword'""").fetchone()[0], "bob")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_trade_offers"
                                          ).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_trade_settlements"
                                          ).fetchone()[0], 0)

        # the buyer now resells to carol; deleting him must void the open offer
        offer_id = self.service.create_offer(
            self.buyer, "offer-2", "beta_sword", expected_item_version=2,
            buyer_id=self.other, price_minor=500, expected_policy_version=1,
            expected_fee_minor=12)["result"]["offer_id"]
        with self.database() as conn, conn:
            conn.execute("DELETE FROM users WHERE id=?", (self.buyer,))
        with self.assertRaises(DungeonError) as error:
            self.service.accept_offer(self.other, "accept-carol", offer_id,
                                      expected_offer_version=1)
        self.assertIn(error.exception.code, ("not_found", "auth_required"))

    def test_migration_on_database_with_only_migration_one(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE)")
        init_dungeon(conn)
        conn.commit()
        init_beta(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_migrations").fetchone()[0], 2)
        conn.commit()
        init_beta(conn)  # replays without touching either checksum
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_migrations").fetchone()[0], 2)
        self.assertIsNotNone(conn.execute("""SELECT sql FROM sqlite_master
            WHERE type='trigger' AND name='delete_user_dungeon_beta'""").fetchone())


if __name__ == "__main__":
    unittest.main()
