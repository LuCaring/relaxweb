#!/usr/bin/env python3
"""M3 WebSocket 指令、同账号推送、越权与断线重查。"""

import asyncio
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import create_app
from server.database import database
from server.schema import init_db


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))


class DungeonProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = partial(database, str(Path(self.tmp.name) / "protocol.db"))
        self.app = create_app(self.db)
        self.addAsyncCleanup(self.app.aclose)
        init_db(self.db)
        with self.db() as conn, conn:
            conn.executemany("""INSERT INTO users
                (username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,1000)""", ((name,) for name in ("alice", "bob")))
        self.now = 100.0
        self.app.dungeon_protocol.clock = lambda: self.now
        self.a, self.a2, self.b = Socket(), Socket(), Socket()
        self.a_state = self._connect(self.a, "alice")
        self.a2_state = self._connect(self.a2, "alice")
        self.b_state = self._connect(self.b, "bob")
        await self.app.handlers["get_dungeon"](self.a, self.a_state, {"request_id": "read-1"})
        self.version = self.a.messages[-1]["profile_version"]

    def _connect(self, socket, username):
        state = {"user": {"username": username, "coins": 1000}, "send_lock": asyncio.Lock()}
        self.app.hub.clients[socket] = state
        return state

    async def _start(self):
        await self.app.handlers["dungeon_start"](self.a, self.a_state,
            {"type": "dungeon_start", "request_id": "start-1",
             "challenge_id": "ruins_slime_01", "difficulty_id": "normal",
             "expected_version": self.version})
        message = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_result")
        return message["result"]["battle"]

    async def test_start_sync_reward_and_other_account_is_private(self):
        battle = await self._start()
        battle_id = battle["battle_id"]
        self.assertEqual(self.a2.messages[-1]["active_job"]["id"], battle_id)
        self.assertEqual(self.b.messages, [])
        self.now = 115.0
        await self.app.handlers["dungeon_sync"](self.a, self.a_state,
            {"request_id": "sync-1", "battle_id": battle_id, "after_sequence": 0})
        message = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_events")
        self.assertEqual(message["battle"]["result"]["outcome"], "victory")
        self.assertEqual(message["events"][0]["event_type"], "BattleStarted")
        self.assertEqual(self.a2_state["user"]["coins"], 1020)
        self.assertEqual(self.b.messages, [])
        await self.app.handlers["dungeon_sync"](self.a, self.a_state,
            {"request_id": "sync-2", "battle_id": battle_id,
             "after_sequence": message["next_cursor"]})
        again = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_events")
        self.assertEqual(again["events"], [])
        self.assertFalse(again["changed"])
        await self.app.handlers["dungeon_get_result"](self.a, self.a_state,
            {"request_id": "result-1", "battle_id": battle_id})
        self.assertEqual(self.a.messages[-1]["result"]["coins_gained"], 20)
        await self.app.handlers["dungeon_get_result"](self.b, self.b_state,
            {"request_id": "foreign-1", "battle_id": battle_id})
        self.assertEqual(self.b.messages[-1]["code"], "not_found")

    async def test_start_replay_and_stale_second_tab(self):
        battle = await self._start()
        await self.app.handlers["dungeon_start"](self.a, self.a_state,
            {"type": "dungeon_start", "request_id": "start-1",
             "challenge_id": "ruins_slime_01", "difficulty_id": "normal",
             "expected_version": self.version})
        replay = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_result")
        self.assertTrue(replay["result"]["replayed"])
        self.assertEqual(replay["result"]["battle"]["battle_id"], battle["battle_id"])
        await self.app.handlers["dungeon_start"](self.a2, self.a2_state,
            {"type": "dungeon_start", "request_id": "start-other",
             "challenge_id": "ruins_slime_01", "difficulty_id": "normal",
             "expected_version": self.version})
        self.assertEqual(self.a2.messages[-1]["code"], "version_conflict")

    async def test_pause_and_reconnect_reads_authoritative_checkpoint(self):
        battle = await self._start()
        battle_id = battle["battle_id"]
        self.now = 101.0
        await self.app.handlers["dungeon_control"](self.a, self.a_state,
            {"request_id": "pause-1", "battle_id": battle_id,
             "command": "pause", "expected_revision": battle["revision"]})
        paused = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_result")
        self.assertEqual(paused["result"]["battle"]["status"], "paused")
        frozen_time = paused["result"]["battle"]["sim_time_us"]
        self.now = 109.0
        await self.app.handlers["dungeon_sync"](self.a2, self.a2_state,
            {"request_id": "sync-after-reconnect", "battle_id": battle_id})
        synced = next(row for row in reversed(self.a2.messages) if row["type"] == "dungeon_events")
        self.assertEqual(synced["battle"]["sim_time_us"], frozen_time)
        self.assertEqual(synced["battle"]["status"], "paused")

    async def test_lost_settlement_reply_is_recoverable(self):
        battle = await self._start()
        battle_id = battle["battle_id"]

        async def fail_send(_payload):
            raise ConnectionError("closed")

        self.a.send = fail_send
        self.now = 115.0
        with self.assertLogs("live-chat", level="WARNING"):
            await self.app.handlers["dungeon_sync"](self.a, self.a_state,
                {"request_id": "lost-sync", "battle_id": battle_id})
        self.assertNotIn(self.a, self.app.hub.clients)
        await self.app.handlers["dungeon_get_result"](self.a2, self.a2_state,
            {"request_id": "recover-result", "battle_id": battle_id})
        self.assertEqual(self.a2.messages[-1]["result"]["outcome"], "victory")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dungeon_rewards").fetchone()[0], 1)

    async def test_progress_worker_does_not_block_other_coroutines(self):
        with patch("server.estate.dungeon.progress_run", side_effect=lambda: time.sleep(0.08)):
            work = asyncio.create_task(self.app.dungeon_protocol._advance())
            await asyncio.sleep(0.01)
            self.assertFalse(work.done())
            await work

    async def test_result_messages_discriminate_action_and_lookup(self):
        battle = await self._start()
        action = next(row for row in reversed(self.a.messages) if row["type"] == "dungeon_result")
        self.assertEqual(action["result_kind"], "action")
        await self.app.handlers["dungeon_get_result"](self.a, self.a_state,
            {"request_id": "lookup-1", "battle_id": battle["battle_id"]})
        self.assertEqual(self.a.messages[-1]["result_kind"], "lookup")


if __name__ == "__main__":
    unittest.main()
