#!/usr/bin/env python3
"""小胖庄园已解锁皮肤：目录、旧库迁移、持久化、幂等与经济隔离。"""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from estate import EstateError, estate_state, init_estate, plant, set_skin, start_fishing, start_mining
from estate.catalog import public_catalog
from estate.store import run_action


NOW = 2_000_000_000
EXPECTED_SKINS = {
    "berry": {"name": "小女孩", "description": "莓果色长发，陪你照料每一寸田野。"},
    "steve": {"name": "史蒂夫", "description": "方块风格的冒险家，准备探索庄园的每个角落。"},
    "dva": {"name": "DVA", "description": "粉蓝机甲风格，轻快地穿行在田野之间。"},
    "little_gwen": {"name": "小小格温", "description": "蓝发与蝴蝶结装束，带着优雅来到庄园。"},
    "jamie": {"name": "杰米", "description": "亮黄色运动装，活力满满地照料庄园。"},
    "xiaofei": {"name": "小菲", "description": "粉发小礼帽装束，把田园生活变得甜美。"},
    "weichong": {"name": "威虫", "description": "战术装甲造型，沉稳守护庄园的收获。"},
    "xiaoxiaopang": {"name": "小小胖", "description": "黑色棉服与圆框眼镜，悠闲漫步在庄园。"},
}


class EstateSkinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "skins.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY COLLATE NOCASE, coins REAL NOT NULL)")
        self.conn.executemany("INSERT INTO users VALUES (?,?)", (("alice", 10000), ("bob", 0)))
        self.conn.execute("CREATE TABLE coin_transactions(username TEXT, amount REAL, kind TEXT)")
        init_estate(self.conn)
        # These regression cases exercise switching already-owned skins.
        self.conn.executemany("INSERT INTO estate_owned_skins VALUES (?,?)",
                              ((user, skin) for user in ("alice", "bob") for skin in EXPECTED_SKINS))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def call(self, fn, *args):
        with self.conn:
            return fn(self.conn, *args)

    def state(self, username="alice", now=NOW):
        return self.call(estate_state, username, now)

    def switch(self, skin_id, request_id="skin-request-001", username="alice", now=NOW):
        return self.call(set_skin, username, request_id, skin_id, now)

    def test_new_profile_defaults_to_berry_and_exposes_catalog(self):
        initial = self.state()
        self.assertEqual(initial["profile"]["skin_id"], "berry")
        self.assertEqual(initial["version"], 1)
        self.assertEqual(self.state(), initial)
        self.assertEqual(initial["catalog"]["skins"], public_catalog()["skins"])
        self.assertEqual(initial["catalog"]["skins"], {
            skin_id: {"id": skin_id, **metadata, "price": 0 if skin_id in ("berry", "xiaoxiaopang") else 20000, "unlock": "default" if skin_id == "berry" else "collection" if skin_id == "xiaoxiaopang" else "purchase"} for skin_id, metadata in EXPECTED_SKINS.items()
        })
        initial["catalog"]["skins"]["steve"]["name"] = "客户端修改"
        self.assertEqual(public_catalog()["skins"]["steve"]["name"], "史蒂夫")

    def test_legacy_profile_migration_and_repeated_init_preserve_saves(self):
        for reserved_column in (False, True):
            with self.subTest(reserved_column=reserved_column), closing(sqlite3.connect(":memory:")) as conn:
                conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY, coins REAL NOT NULL)")
                conn.executemany("INSERT INTO users VALUES (?,?)", (("alice", 700), ("bob", 0)))
                reserved_sql = "reserved_capacity INTEGER NOT NULL DEFAULT 0," if reserved_column else ""
                conn.execute(f"""
                    CREATE TABLE estate_profiles (
                        username TEXT PRIMARY KEY COLLATE NOCASE,
                        level INTEGER NOT NULL DEFAULT 1,
                        xp INTEGER NOT NULL DEFAULT 0,
                        warehouse_level INTEGER NOT NULL DEFAULT 1,
                        plot_count INTEGER NOT NULL DEFAULT 0,
                        {reserved_sql}
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT INTO estate_profiles(username,level,xp,warehouse_level,plot_count,"
                    "version,created_at,updated_at) VALUES ('alice',3,41,2,6,7,?,?)", (NOW, NOW + 10),
                )
                if reserved_column:
                    conn.execute("UPDATE estate_profiles SET reserved_capacity=4")
                for _ in range(3):
                    init_estate(conn)
                migrated = estate_state(conn, "alice", NOW + 20)
                self.assertEqual(migrated["profile"]["skin_id"], "berry")
                self.assertEqual(conn.execute(
                    "SELECT level,xp,warehouse_level,plot_count,version,created_at,updated_at,"
                    "reserved_capacity FROM estate_profiles WHERE username='alice'"
                ).fetchone(), (3, 41, 2, 6, 7, NOW, NOW + 10, 4 if reserved_column else 0))
                self.assertEqual(migrated["coins"], 700)
                conn.execute("INSERT INTO estate_owned_skins VALUES ('alice','steve')")
                set_skin(conn, "alice", "legacy-skin-001", "steve", NOW + 30)
                saved = estate_state(conn, "alice", NOW + 30)
                for _ in range(3):
                    init_estate(conn)
                self.assertEqual(estate_state(conn, "alice", NOW + 30), saved)
                self.assertEqual(estate_state(conn, "bob", NOW)["profile"]["skin_id"], "berry")
                skin_column = next(row for row in conn.execute("PRAGMA table_info(estate_profiles)")
                                   if row[1] == "skin_id")
                self.assertEqual(skin_column[2:5], ("TEXT", 1, "'berry'"))

    def test_switch_persists_across_connections_and_isolates_users(self):
        self.switch("steve")
        self.assertEqual(self.state("bob")["profile"]["skin_id"], "berry")
        self.switch("xiaofei", username="bob")  # 同一 request_id 在不同账号互不冲突。
        self.conn.close()
        self.conn = sqlite3.connect(self.db_path)
        with self.conn:
            init_estate(self.conn)
            init_estate(self.conn)
        self.assertEqual(self.state()["profile"]["skin_id"], "steve")
        self.assertEqual(self.state("bob")["profile"]["skin_id"], "xiaofei")
        self.assertTrue(self.switch("steve")["replayed"])
        self.switch("berry", "skin-case-001", username="ALICE")
        self.assertEqual(self.state()["profile"]["skin_id"], "berry")
        self.assertEqual(self.state("bob")["profile"]["skin_id"], "xiaofei")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_profiles").fetchone()[0], 2)

    def test_invalid_skin_types_and_ids_are_rejected_without_mutation(self):
        self.switch("xiaofei")
        before = self.state()
        invalid = (None, True, False, 0, 1, 1.5, float("nan"), float("inf"),
                   [], ["steve"], {}, {"skin_id": "steve"}, ("steve",), {"steve"}, b"steve", object(),
                   "", "unknown", "Xiaopang", " steve", "steve ", "steve\x00", "__proto__",
                   "mint", "sky", "wisteria", "farmer",
                   "'; DROP TABLE estate_profiles; --", "x" * 10000)
        for index, skin_id in enumerate(invalid):
            with self.subTest(skin_id=skin_id):
                with self.assertRaises(EstateError) as rejected:
                    self.switch(skin_id, f"invalid-skin-{index:03}")
                self.assertEqual(rejected.exception.code, "invalid_skin")
                self.assertEqual(self.state(), before)
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0], 1)

    def test_invalid_skin_does_not_create_an_account_save(self):
        with self.assertRaises(EstateError) as rejected:
            self.switch([], username="bob")
        self.assertEqual(rejected.exception.code, "invalid_skin")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_profiles").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0], 0)

    def test_replay_and_conflict_do_not_revert_current_skin(self):
        first = self.switch("steve")
        self.assertEqual(first, {"action": "set_skin", "skin_id": "steve",
                                 "replayed": False, "request_id": "skin-request-001"})
        self.switch("berry", "skin-request-002", now=NOW + 1)
        current = self.state(now=NOW + 1)
        replay = self.switch("steve", now=NOW + 2)
        self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(self.state(now=NOW + 1), current)
        with self.assertRaises(EstateError) as conflict:
            self.switch("xiaofei")
        self.assertEqual(conflict.exception.code, "request_conflict")
        self.assertEqual(self.state(now=NOW + 1), current)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0], 2)

    def test_request_ids_share_existing_validation_and_action_namespace(self):
        for request_id in (None, [], 42, True, "", "short", "with space", "x" * 81):
            with self.subTest(request_id=request_id), self.assertRaises(EstateError) as rejected:
                self.switch("steve", request_id)
            self.assertEqual(rejected.exception.code, "invalid_request")
        self.call(run_action, "alice", "shared-action-001", "buy", {}, NOW,
                  lambda: {"action": "buy"})
        with self.assertRaises(EstateError) as conflict:
            self.switch("steve", "shared-action-001")
        self.assertEqual(conflict.exception.code, "request_conflict")
        self.assertEqual(self.state()["profile"]["skin_id"], "berry")

    def test_selecting_current_skin_succeeds_and_replays_without_version_bump(self):
        initial = self.state()
        selected = self.switch("berry", now=NOW + 1)
        current = self.state(now=NOW + 1)
        self.assertFalse(selected["replayed"])
        self.assertEqual(current["profile"]["skin_id"], "berry")
        self.assertEqual(current["version"], initial["version"] + 1)
        self.assertEqual(current["profile"]["updated_at"], NOW + 1)
        self.assertTrue(self.switch("berry", now=NOW + 2)["replayed"])
        self.assertEqual(self.state(now=NOW + 1), current)
        self.switch("berry", "skin-same-again", now=NOW + 3)
        self.assertEqual(self.state()["version"], current["version"] + 1)

    def test_owned_skins_switch_free_and_leave_economy_unchanged(self):
        self.state("bob")
        with self.conn:
            self.conn.execute("UPDATE estate_profiles SET xp=79 WHERE username='bob'")
            self.conn.executemany("INSERT INTO estate_inventory VALUES ('bob',?,?)",
                                  (("seed:wheat", 11), ("bait:worm", 2)))
            self.conn.executemany("INSERT INTO estate_tools VALUES ('bob',?,1,20,?)",
                                  (("rod", NOW), ("pickaxe", NOW)))
            self.conn.execute("INSERT INTO coin_transactions VALUES ('bob',0,'existing')")
        self.call(plant, "bob", "free-plant-001", 0, "wheat", NOW)
        self.call(start_fishing, "bob", "free-fishing-001", "worm", NOW)
        self.call(start_mining, "bob", "free-mining-001", 1, NOW)
        before = self.state("bob")
        self.assertEqual(before["coins"], 0)
        self.assertIsNotNone(before["plots"][0]["crop_id"])
        self.assertIsNotNone(before["fishing_session"])
        self.assertIsNotNone(before["mining_run"])
        self.assertGreater(before["profile"]["warehouse_reserved"], 0)
        ledger = self.conn.execute("SELECT * FROM coin_transactions").fetchall()
        gameplay = {table: self.conn.execute(f"SELECT * FROM {table}").fetchall() for table in (
            "estate_plots", "estate_inventory", "estate_tools",
            "estate_fishing_sessions", "estate_mining_runs",
        )}
        ignored_profile_keys = {"skin_id", "version", "updated_at"}
        for index, skin_id in enumerate(EXPECTED_SKINS):
            with self.subTest(skin_id=skin_id):
                result = self.switch(skin_id, f"free-skin-{index:03}", "bob", NOW + index + 1)
                after = self.state("bob")
                self.assertEqual(result["skin_id"], skin_id)
                self.assertEqual(after["profile"]["skin_id"], skin_id)
                for key in ("coins", "inventory", "plots", "tools", "fishing_session", "mining_run"):
                    self.assertEqual(after[key], before[key], key)
                self.assertEqual({k: v for k, v in after["profile"].items() if k not in ignored_profile_keys},
                                 {k: v for k, v in before["profile"].items() if k not in ignored_profile_keys})
                self.assertEqual(self.conn.execute("SELECT * FROM coin_transactions").fetchall(), ledger)
                for table, rows in gameplay.items():
                    self.assertEqual(self.conn.execute(f"SELECT * FROM {table}").fetchall(), rows, table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
