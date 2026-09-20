#!/usr/bin/env python3
"""admin.py 用户命令回归：删除清理、开放竞猜保护、字段编辑与改名迁移。

status / restart 依赖 systemd 与 sudo，不在单元测试覆盖内。
"""
import contextlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import admin
import chat_server as server


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, "DB_FILE", str(Path(self.tmp.name) / "test.db"))
        self.db_patch.start()
        server.init_db()
        self.seed_base_data()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    # ---- 工具 ----

    def seed_base_data(self):
        with server.database() as conn, conn:
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                         "VALUES ('alice', 'h', 's', 0, 500)")
            conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                         "VALUES ('bob', 'h', 's', 0, 300)")

    def run_admin(self, *args):
        """跑 admin.main，捕获 stdout；命令失败（SystemExit）时原样抛出。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            admin.main(list(args))
        return out.getvalue()

    def user_row(self, username):
        with server.database() as conn:
            return conn.execute(
                "SELECT username, nickname, role, coins FROM users WHERE username = ?",
                (username,),
            ).fetchone()

    def add_bet(self, creator, entries=(), status="open"):
        with server.database() as conn, conn:
            cur = conn.execute(
                "INSERT INTO bets(question, options, creator, status, created_at) "
                "VALUES ('q?', '[\"a\",\"b\"]', ?, ?, 0)",
                (creator, status),
            )
            bet_id = cur.lastrowid
            for name, index, amount in entries:
                conn.execute(
                    "INSERT INTO bet_entries(bet_id, username, option_index, amount, created_at) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (bet_id, name, index, amount),
                )
        return bet_id

    def seed_user_data(self, username):
        """给用户铺满各关联表数据（竞猜为已结算，不阻挡删除）。"""
        bet_id = self.add_bet("bob", [(username, 0, 50)], status="settled")
        with server.database() as conn, conn:
            conn.execute(
                "INSERT INTO auth_sessions(token_hash, user_id, expires_at) "
                "VALUES ('tok', (SELECT id FROM users WHERE username = ?), 9999999999)",
                (username,),
            )
            conn.execute(
                "INSERT INTO coin_transactions(username, amount, balance, kind, created_at) "
                "VALUES (?, 10, 100, 'admin', 0)", (username,))
            conn.execute(
                "INSERT INTO game_escrows(username, room_id, amount) VALUES (?, 1, 20)",
                (username,))
            conn.execute(
                "INSERT INTO invite_codes(code, created_at, created_by, used_by) "
                "VALUES ('CODE1', 0, ?, ?)", (username, username))
            conn.execute(
                "INSERT INTO estate_profiles(username, created_at, updated_at) "
                "VALUES (?, 0, 0)",
                (username,))
        return {"bet_entries": 1, "coin_transactions": 1, "game_escrows": 1,
                "invite_codes": 2, "estate_profiles": 1, "bets.creator": 1,
                "auth_sessions": 1, "bet_id": bet_id}

    # ---- delete ----

    def test_delete_removes_user_and_all_related_rows(self):
        made = self.seed_user_data("alice")
        out = self.run_admin("delete", "alice", "-y")
        self.assertIn("已删除", out)
        with server.database() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM users WHERE username = 'alice'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM auth_sessions WHERE token_hash = 'tok'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM coin_transactions WHERE username = 'alice'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM game_escrows WHERE username = 'alice'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM bet_entries WHERE username = 'alice'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM estate_profiles WHERE username = 'alice'").fetchone())
            # 别人发起的竞猜还在，只是没有 alice 的投注了
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM bets WHERE id = ?", (made["bet_id"],)).fetchone())
            # bob 不受影响
            self.assertIsNotNone(self.user_row("bob"))

    def test_delete_clears_holdem_details_and_totals_only_for_target_user(self):
        with server.database() as conn, conn:
            ids = dict(conn.execute("SELECT username,id FROM users"))
            for user_id in ids.values():
                server.record_holdem_hand(conn, "test-hand", user_id, 100, 110, 4,
                    {"big_blind": 10, "folded": False, "fold_reason": None,
                     "saw_flop": False, "showdown": False, "vpip": False, "pfr": False,
                     "aggressive_actions": 0, "call_actions": 0, "settlement_reason": "completed"})
            server.record_holdem_turnover(conn, "test-hand", {name: 100 for name in ids}, 0)
            for name in ids:
                server.claim_holdem_reward(conn, name, 100, "1970-01-01", 0, server.adjust_coins)
            metadata = conn.execute("SELECT * FROM holdem_stats_metadata").fetchall()
        self.run_admin("delete", "alice", "-y")
        with server.database() as conn:
            for table in ("holdem_hand_stats", "holdem_player_stats", "holdem_turnover", "holdem_reward_claims"):
                self.assertEqual(conn.execute(f"SELECT user_id FROM {table}").fetchall(), [(ids["bob"],)])
            self.assertEqual(conn.execute("SELECT * FROM holdem_stats_metadata").fetchall(), metadata)

    def test_delete_refused_while_user_in_open_bet(self):
        self.seed_user_data("alice")
        self.add_bet("bob", [("alice", 0, 50)])  # 开放竞猜：bob 发起、alice 参与
        for name in ("alice", "bob"):
            with self.assertRaises(SystemExit):
                self.run_admin("delete", name, "-y")
        # 结账（关闭竞猜）后即可删除
        with server.database() as conn, conn:
            conn.execute("UPDATE bets SET status = 'settled'")
        out = self.run_admin("delete", "alice", "-y")
        self.assertIn("已删除", out)

    def test_delete_requires_existing_user(self):
        with self.assertRaises(SystemExit):
            self.run_admin("delete", "ghost", "-y")

    # ---- edit ----

    def test_edit_nickname_and_role(self):
        out = self.run_admin("edit", "alice", "nickname", "小爱")
        self.assertIn("小爱", out)
        row = self.user_row("alice")
        self.assertEqual(row[1], "小爱")
        self.run_admin("edit", "alice", "role", "admin")
        self.assertEqual(self.user_row("alice")[2], "admin")
        with self.assertRaises(SystemExit):
            self.run_admin("edit", "alice", "role", "root")

    def test_edit_password_enables_login(self):
        self.run_admin("edit", "alice", "password", "newpass123")
        with server.database() as conn:
            hash_, salt = conn.execute(
                "SELECT password_hash, salt FROM users WHERE username = 'alice'"
            ).fetchone()
        self.assertTrue(server.authenticate_user("alice", "newpass123"))
        self.assertNotEqual(hash_, "h")
        self.assertNotEqual(salt, "s")
        with self.assertRaises(SystemExit):
            self.run_admin("edit", "alice", "password", "123")

    def test_edit_username_migrates_all_references(self):
        self.seed_user_data("alice")
        out = self.run_admin("edit", "alice", "username", "alice2", "-y")
        self.assertIn("alice2", out)
        with server.database() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM users WHERE username = 'alice'").fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM users WHERE username = 'alice2'").fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM coin_transactions WHERE username = 'alice2'").fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM coin_transactions WHERE username = 'alice'").fetchone())
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM bet_entries WHERE username = 'alice2'").fetchone())
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM invite_codes "
                "WHERE created_by = 'alice2' AND used_by = 'alice2'").fetchone()[0], 1)
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM estate_profiles WHERE username = 'alice2'").fetchone())
        with server.database() as conn:
            self.assertIsNone(admin.get_user(conn, "alice"))
            self.assertIsNotNone(admin.get_user(conn, "alice2"))

    def test_edit_username_rejects_clash_and_invalid(self):
        with self.assertRaises(SystemExit):
            self.run_admin("edit", "alice", "username", "BOB", "-y")  # NOCASE 冲突
        with self.assertRaises(SystemExit):
            self.run_admin("edit", "alice", "username", "!", "-y")

    def test_edit_rejects_unknown_field(self):
        with self.assertRaises(SystemExit):
            self.run_admin("edit", "alice", "coins", "1")  # 金币请用 set/add/sub

    # ---- status（数据库部分，不依赖 systemd）----

    def test_status_prints_database_section(self):
        self.add_bet("alice", [("bob", 1, 30)])
        text = self.run_admin("status")
        self.assertIn("进行中竞猜", text)
        self.assertIn("alice", text)
        self.assertIn("用户 2 名", text)


if __name__ == "__main__":
    unittest.main()
