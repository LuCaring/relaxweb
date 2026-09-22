"""Catalog ID migration is lossless, repeatable, and leaves the source untouched."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from estate import init_estate, estate_state, set_skin, EstateError
from estate.store import request_hash
from tools.migrate_estate_ids import migrate_copy, migrate_ids


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.source = Path(self.tmp) / "source.db"
        self.output = Path(self.tmp) / "migrated.db"
        self.mapping = {"skins": {"legacy_reward": "collection_reward"},
                        "crops": {"legacy_crop": "magic_grass"},
                        "fish": {"legacy_fish": "mystery_fish"},
                        "collectibles": {"legacy_watch": "antique_watch"}}
        conn = sqlite3.connect(self.source)
        try:
            conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL)")
            conn.execute("INSERT INTO users VALUES ('legacy_reward',1234)")
            init_estate(conn)
            estate_state(conn, "legacy_reward", 2_000_000_000)
            conn.execute("UPDATE estate_profiles SET skin_id='legacy_reward',xp=42")
            conn.executemany("INSERT INTO estate_owned_skins VALUES ('legacy_reward',?)",
                             [("legacy_reward",), ("collection_reward",)])
            conn.execute("UPDATE estate_plots SET crop_id='legacy_crop',planted_at=1,ready_at=2 WHERE plot_index=0")
            conn.executemany("INSERT INTO estate_inventory VALUES ('legacy_reward',?,?)", [
                ("crop:legacy_crop", 3), ("crop:magic_grass", 4),
                ("fish:legacy_fish", 2), ("collectible:legacy_watch", 1)])
            conn.executemany("INSERT INTO estate_collections VALUES ('legacy_reward',?)",
                             [("collectible:legacy_watch",), ("collectible:antique_watch",)])
            result = {"action": "set_skin", "skin_id": "legacy_reward", "owner_username": "legacy_reward"}
            digest = request_hash("set_skin", {"skin_id": "legacy_reward"})
            conn.execute("INSERT INTO estate_actions VALUES ('legacy_reward','saved-action','set_skin',?,?,1)",
                         (digest, json.dumps(result)))
            conn.execute("INSERT INTO estate_fishing_sessions "
                         "(session_id,username,bait_id,rod_level,fish_id,seed,pattern_json,started_at,expires_at,result_json) "
                         "VALUES ('old-session','legacy_reward','worm',1,'treasure:legacy_watch',1,'[]',1,2,?)",
                         (json.dumps({"collectible_id": "legacy_watch"}),))
            conn.commit()
        finally:
            conn.close()

    def test_copy_preserves_progress_merges_inventory_and_migrates_results(self):
        source_bytes = self.source.read_bytes()
        migrate_copy(self.source, self.output, self.mapping)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        conn = sqlite3.connect(self.output)
        try:
            self.assertEqual(conn.execute("SELECT skin_id,xp FROM estate_profiles").fetchone(),
                             ("collection_reward", 42))
            self.assertEqual(conn.execute("SELECT coins FROM users").fetchone()[0], 1234)
            self.assertEqual(conn.execute("SELECT username FROM users").fetchone()[0], "legacy_reward")
            inventory = dict(conn.execute("SELECT item_id,quantity FROM estate_inventory"))
            self.assertEqual(inventory, {"crop:magic_grass": 7, "fish:mystery_fish": 2,
                                        "collectible:antique_watch": 1})
            self.assertEqual(conn.execute("SELECT crop_id FROM estate_plots WHERE plot_index=0").fetchone()[0],
                             "magic_grass")
            fish = conn.execute("SELECT fish_id,result_json FROM estate_fishing_sessions").fetchone()
            self.assertEqual(fish[0], "treasure:antique_watch")
            self.assertEqual(json.loads(fish[1])["collectible_id"], "antique_watch")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM estate_collections").fetchone()[0], 1)
            digest, raw = conn.execute("SELECT request_hash,result_json FROM estate_actions").fetchone()
            self.assertEqual(digest, request_hash("set_skin", {"skin_id": "legacy_reward"}))
            self.assertEqual(json.loads(raw)["skin_id"], "collection_reward")
            self.assertEqual(json.loads(raw)["owner_username"], "legacy_reward")
            before = list(conn.iterdump())
            with conn:
                migrate_ids(conn, self.mapping)
            self.assertEqual(list(conn.iterdump()), before)
            with conn:
                self.assertEqual(estate_state(conn, "legacy_reward", 2_000_000_000)["profile"]["skin_id"],
                                 "collection_reward")
                with self.assertRaises(EstateError) as caught:
                    set_skin(conn, "legacy_reward", "saved-action", "collection_reward", 2_000_000_000)
                self.assertEqual(caught.exception.code, "request_conflict")
        finally:
            conn.close()

    def test_existing_destination_and_invalid_mapping_never_overwrite(self):
        self.output.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            migrate_copy(self.source, self.output, self.mapping)
        self.assertEqual(self.output.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            migrate_copy(self.source, Path(self.tmp) / "invalid.db",
                         {"skins": {"one": "two", "two": "three"}})
        self.assertFalse((Path(self.tmp) / "invalid.db").exists())
        with self.assertRaises(FileExistsError):
            migrate_copy(self.source, self.source, self.mapping)

    def test_failed_migration_removes_partial_copy_and_preserves_original(self):
        conn = sqlite3.connect(self.source)
        try:
            conn.execute("UPDATE estate_actions SET result_json='broken'")
            conn.commit()
        finally:
            conn.close()
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            migrate_copy(self.source, self.output, self.mapping)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_older_schema_without_skin_column_or_optional_tables_is_supported(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE estate_profiles(username TEXT PRIMARY KEY, xp INTEGER)")
            conn.execute("INSERT INTO estate_profiles VALUES ('alice',42)")
            conn.execute("CREATE TABLE estate_inventory(username TEXT, item_id TEXT, quantity INTEGER, "
                         "PRIMARY KEY(username,item_id))")
            conn.execute("INSERT INTO estate_inventory VALUES ('alice','crop:legacy_crop',3)")
            with conn:
                migrate_ids(conn, self.mapping)
            self.assertEqual(conn.execute("SELECT * FROM estate_profiles").fetchone(), ("alice",42))
            self.assertEqual(conn.execute("SELECT item_id,quantity FROM estate_inventory").fetchone(),
                             ("crop:magic_grass",3))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
