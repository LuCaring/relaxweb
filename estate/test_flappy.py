"""飞鸟游戏入场、服务端计分与最高分榜。"""
import sqlite3
import asyncio
import json
import unittest
from unittest.mock import patch

from estate.flappy import finish_flappy, flappy_leaderboard, replay_game, start_flappy
from estate.schema import init_estate
from estate.store import EstateError

NOW = 2_000_000_000


def adjust_coins(conn, username, delta, *_args, **_kwargs):
    balance = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
    if balance + delta < 0:
        raise ValueError("金币不足")
    conn.execute("UPDATE users SET coins=? WHERE username=?", (balance + delta, username))
    return balance + delta


class FlappyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE users(username TEXT PRIMARY KEY,coins REAL NOT NULL)")
        self.conn.execute("CREATE TABLE coin_transactions("
                          "username TEXT,amount REAL,balance REAL,kind TEXT,detail TEXT,"
                          "created_at INTEGER,ref TEXT)")
        self.conn.executemany("INSERT INTO users VALUES (?,?)",
                              (("alice", 100), ("bob", 100)))
        init_estate(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_entry_cost_replay_reward_and_leaderboard(self):
        with patch("estate.flappy.secrets.randbits", return_value=0):
            started = start_flappy(self.conn, "alice", "flappy-start-1", NOW, adjust_coins)
            repeated = start_flappy(self.conn, "alice", "flappy-start-1", NOW, adjust_coins)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 34)
        flaps = list(range(0, 406, 37))
        with self.assertRaises(EstateError):
            finish_flappy(self.conn, "alice", "flappy-forged-1", started["session_id"],
                          [0], 406, NOW + 7, adjust_coins)
        result = finish_flappy(self.conn, "alice", "flappy-finish-1",
                               started["session_id"], flaps, 406, NOW + 7, adjust_coins)
        self.assertEqual((result["score"], result["reward"], result["record_bonus"]),
                         (4, 6706, 6666))
        again = finish_flappy(self.conn, "alice", "flappy-finish-2",
                              started["session_id"], flaps, 406, NOW + 8, adjust_coins)
        self.assertTrue(again["session_replayed"])
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 6740)
        board = flappy_leaderboard(self.conn, "alice", NOW + 8)
        self.assertEqual((board["my_rank"], board["my_best"]), (1, 4))
        self.assertEqual(board["entries"][0]["username"], "alice")

    def test_invalid_timing_and_cross_account_submission(self):
        with patch("estate.flappy.secrets.randbits", return_value=0):
            started = start_flappy(self.conn, "alice", "flappy-start-2", NOW, adjust_coins)
        flaps = list(range(0, 406, 37))
        with self.assertRaises(EstateError):
            finish_flappy(self.conn, "alice", "flappy-fast-1", started["session_id"],
                          flaps, 406, NOW + 1, adjust_coins)
        with self.assertRaises(EstateError):
            finish_flappy(self.conn, "bob", "flappy-other-1", started["session_id"],
                          flaps, 406, NOW + 8, adjust_coins)
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 34)

    def test_no_record_bonus_when_score_does_not_exceed_global_best(self):
        self.conn.execute("INSERT INTO estate_flappy_scores VALUES ('bob',4,?)", (NOW,))
        with patch("estate.flappy.secrets.randbits", return_value=0):
            started = start_flappy(self.conn, "alice", "flappy-start-tie", NOW, adjust_coins)
        result = finish_flappy(self.conn, "alice", "flappy-finish-tie",
                               started["session_id"], list(range(0, 406, 37)), 406,
                               NOW + 7, adjust_coins)
        self.assertEqual((result["score"], result["reward"], result["global_record"]),
                         (4, 40, False))
        self.assertEqual(self.conn.execute(
            "SELECT coins FROM users WHERE username='alice'").fetchone()[0], 74)

    def test_rank_uses_each_persons_highest_score(self):
        self.conn.execute("INSERT INTO estate_flappy_scores VALUES ('bob',1,?)", (NOW,))
        self.conn.execute("INSERT INTO estate_flappy_scores VALUES ('alice',4,?)", (NOW + 1,))
        board = flappy_leaderboard(self.conn, "bob", NOW + 2)
        self.assertEqual((board["my_rank"], board["my_best"]), (2, 1))
        self.assertEqual([row["username"] for row in board["entries"]], ["alice", "bob"])

    def test_reference_trace(self):
        self.assertEqual(replay_game(0, list(range(0, 406, 37)), 406), 4)

    def test_websocket_protocol_start_finish_and_leaderboard(self):
        from server.estate.protocol import EstateProtocol
        self.conn.commit()
        socket = object()
        user = {"username": "alice", "coins": 100}
        state = {"user": user}
        clients = {socket: state}
        messages = []

        async def send_json(_socket, payload):
            messages.append(payload)

        async def send_encoded(_socket, payload):
            messages.append(json.loads(payload))

        protocol = EstateProtocol(database=lambda: self.conn, clients=clients,
                                  send_json=send_json, send_encoded=send_encoded,
                                  presence=None)

        async def scenario():
            with patch("estate.flappy.secrets.randbits", return_value=0), \
                    patch("server.estate.protocol.time.time", return_value=NOW):
                await protocol.handle_estate_flappy_start(
                    socket, state, {"request_id": "protocol-start-1"})
            started = messages[-1]
            self.assertEqual((started["type"], started["result"]["cost"],
                              started["coins"]), ("estate_state", 66, 34))
            with patch("server.estate.protocol.time.time", return_value=NOW + 7):
                await protocol.handle_estate_flappy_finish(socket, state, {
                    "request_id": "protocol-finish-1",
                    "session_id": started["result"]["session_id"],
                    "flaps": list(range(0, 406, 37)), "frames": 406})
                self.assertEqual((messages[-1]["result"]["score"], messages[-1]["coins"]),
                                 (4, 6740))
                await protocol.handle_estate_flappy_leaderboard(
                    socket, state, {"request_id": "protocol-board-1"})
            self.assertEqual(messages[-1]["leaderboard"]["my_rank"], 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
