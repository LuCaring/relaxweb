#!/usr/bin/env python3
"""dungeon_beta_* action messages: start/control/input/frame/clear over the app."""

import asyncio
from functools import partial
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.items import create_item
from dungeon.content.loader import load_ruleset
from dungeon.legacy.service import dungeon_state
from dungeon.storage.runs import load_run
from server.app import create_app
from server.database import database
from server.schema import init_db


ROOT = Path(__file__).resolve().parent.parent
P0_RELEASE = ROOT / "content/dungeon/release-p0.json"
ROUTE = "beta.p0.route.flamefield"
INPUT = {"move_x": 0, "move_y": 1, "aim_x": 0, "aim_y": 0, "buttons": ["attack"]}


class TinySimulator:
    """Clears each room after five single-tick steps; view is a tiny projection.

    Five ticks at the app's default 30Hz keeps a frame (every 3rd tick) between
    resume and the room clear, so the push pipeline is observable in tests.
    """

    def create(self, initial, rules, services):
        previous = initial.get("previous_room_snapshot") or {}
        return {"room_index": initial["room_index"], "encounter_id": initial["encounter_id"],
                "room_ticks": 0, "hp": previous.get("hp", 100),
                "run_resources": initial.get("run_resources", {})}

    def step(self, state, ordered_inputs, services):
        state = dict(state)
        state["room_ticks"] += 1
        state["hp"] -= 1
        if state["room_ticks"] == 5:
            services.emit({"type": "room_cleared", "room_index": state["room_index"],
                           "encounter_id": state["encounter_id"]})
        return state

    def view(self, state):
        return {"hp": state["hp"], "room_ticks": state["room_ticks"]}

    def snapshot(self, state):
        return dict(state)

    def restore(self, snapshot, rules, services):
        return dict(snapshot)


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))

    def last(self, kind):
        return next(row for row in reversed(self.messages) if row["type"] == kind)

    def all(self, kind):
        return [row for row in self.messages if row["type"] == kind]


class ActionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = partial(database, str(Path(self.tmp.name) / "action.db"))
        init_db(self.db)
        with self.db() as conn, conn:
            conn.executemany("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,'','',0,10)""", [("alice",), ("bob",)])
            self.user_id = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            dungeon_state(conn, "alice", 100)
            self.rules = load_ruleset(P0_RELEASE)
            item = create_item(conn, self.rules, "alice", "beta.sword.basic",
                               source="integration_fixture", now=100)
            conn.execute("""INSERT INTO dungeon_loadout(username,slot,item_id)
                VALUES ('alice','weapon',?) ON CONFLICT(username,slot)
                DO UPDATE SET item_id=excluded.item_id""", (item["item_id"],))
        self.addAsyncCleanup(self._release_reservations)
        with patch("server.dungeon.beta_protocol.RELEASE_PATH", P0_RELEASE):
            self.app = create_app(self.db, disconnect_grace=0.05,
                                  dungeon_simulator=TinySimulator())
        self.addAsyncCleanup(self.app.aclose)
        self.a, self.a2, self.b = Socket(), Socket(), Socket()
        self.a_state = self._connect(self.a, "alice")
        self.a2_state = self._connect(self.a2, "alice")
        self.b_state = self._connect(self.b, "bob")
        self.handlers = self.app.handlers

    async def _release_reservations(self):
        with self.db() as conn, conn:
            conn.execute("DELETE FROM dungeon_asset_reservations")

    def _connect(self, socket, username):
        state = {"user": {"username": username, "coins": 10}, "send_lock": asyncio.Lock()}
        self.app.hub.clients[socket] = state
        return state

    async def _call(self, handler, socket, state, message):
        await self.handlers[handler](socket, state, message)

    async def wait_for(self, predicate, timeout=5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("timed out waiting for action condition")
            await asyncio.sleep(0.01)

    async def start(self, socket=None, request_id="start-1"):
        socket = socket or self.a
        await self._call("dungeon_beta_start_run", socket,
                         self.a_state if socket is self.a else self.a2_state,
                         {"type": "dungeon_beta_start_run", "protocol_version": 1,
                          "request_id": request_id, "route_id": ROUTE})
        return socket.last("dungeon_beta_result")

    def run_row(self, run_id):
        with self.db() as conn:
            return load_run(conn, run_id)

    # ------------------------------------------------------------------ gates

    async def test_inactive_app_reports_simulator_unavailable(self):
        with patch("server.dungeon.beta_protocol.RELEASE_PATH", P0_RELEASE):
            app = create_app(self.db)
        self.addAsyncCleanup(app.aclose)
        app.hub.clients[self.a] = self.a_state
        # Action handlers stay registered without a simulator so clients get a
        # precise error instead of an unknown-message silence.
        handlers = app.dungeon_action.handlers()
        await handlers["dungeon_beta_start_run"](
            self.a, self.a_state, {"type": "dungeon_beta_start_run", "protocol_version": 1,
                                   "request_id": "s1", "route_id": ROUTE})
        self.assertEqual(self.a.last("dungeon_beta_error")["code"], "simulator_unavailable")

    async def test_malformed_action_requests_are_rejected(self):
        for message in (
            {"type": "dungeon_beta_start_run", "protocol_version": 1,
             "request_id": "bad-route", "route_id": {"bad": True}},
            {"type": "dungeon_beta_input", "protocol_version": 1, "run_id": "x",
             "control_epoch": 0, "input_seq": 1,
             "value": {"move_x": 0, "move_y": 0, "aim_x": 0, "aim_y": 0,
                       "buttons": ["teleport"]}},
            {"type": "dungeon_beta_sync", "protocol_version": 2,
             "request_id": "bad-version", "run_id": "x"},
        ):
            with self.subTest(message=message):
                await self._call(message["type"], self.a, self.a_state, message)
                error = self.a.last("dungeon_beta_error")
                self.assertEqual(error["code"], "invalid_request")

    # ------------------------------------------------------------- happy path

    async def test_start_control_input_frame_and_clear_push(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        self.assertEqual(result["result"]["status"], "running")
        epoch = result["result"]["control_epoch"]
        await self._call("dungeon_beta_input", self.a, self.a_state,
                         {"type": "dungeon_beta_input", "protocol_version": 1,
                          "run_id": run_id, "control_epoch": epoch, "input_seq": 1,
                          "value": INPUT})
        await self.wait_for(lambda: len(self.a.all("dungeon_beta_frame")) >= 1)
        frame = self.a.last("dungeon_beta_frame")
        self.assertEqual(frame["run_id"], run_id)
        self.assertEqual(frame["acked_input_seq"], 1)
        self.assertIn("hp", frame["view"])
        await self.wait_for(lambda: len(self.a.all("dungeon_beta_room_cleared")) >= 1)
        cleared = self.a.last("dungeon_beta_room_cleared")
        self.assertEqual((cleared["status"], cleared["room_index"]), ("ready", 0))
        self.assertEqual(cleared["reward"]["coin_minor"], 1500)
        self.assertGreater(self.run_row(run_id)["control_epoch"], epoch - 1)
        # Exactly-once: the same room never clears twice without a new resume.
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.a.all("dungeon_beta_room_cleared")), 1)
        row = self.run_row(run_id)
        self.assertEqual(row["status"], "ready")

    async def test_full_run_releases_reservations_and_replays_start(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        for room in range(4):
            if room > 0:
                await self._call("dungeon_beta_resume", self.a, self.a_state,
                                 {"type": "dungeon_beta_resume", "protocol_version": 1,
                                  "request_id": f"resume-{room}", "run_id": run_id})
                self.assertEqual(self.a.last("dungeon_beta_result")["result"]["status"],
                                 "running")
            await self.wait_for(
                lambda: len(self.a.all("dungeon_beta_room_cleared")) >= room + 1)
        self.assertEqual(self.run_row(run_id)["status"], "finished")
        with self.db() as conn:
            reservations = conn.execute(
                "SELECT COUNT(*) FROM dungeon_asset_reservations").fetchone()[0]
            rewards = conn.execute(
                "SELECT COUNT(*) FROM dungeon_beta_room_rewards WHERE run_id=?",
                (run_id,)).fetchone()[0]
        self.assertEqual((reservations, rewards), (0, 4))
        replay = await self.start(request_id="start-1")
        self.assertTrue(replay["result"]["replayed"])

    # ------------------------------------------------- control and ownership

    async def test_take_control_bumps_epoch_and_rejects_old_controller(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        old_epoch = result["result"]["control_epoch"]
        await self._call("dungeon_beta_take_control", self.a2, self.a2_state,
                         {"type": "dungeon_beta_take_control", "protocol_version": 1,
                          "request_id": "ctrl-1", "run_id": run_id})
        control = self.a2.last("dungeon_beta_result")
        self.assertEqual(control["result_kind"], "take_control")
        new_epoch = control["result"]["control_epoch"]
        self.assertGreater(new_epoch, old_epoch)
        await self._call("dungeon_beta_input", self.a, self.a_state,
                         {"type": "dungeon_beta_input", "protocol_version": 1,
                          "run_id": run_id, "control_epoch": old_epoch, "input_seq": 1,
                          "value": INPUT})
        self.assertEqual(self.a.last("dungeon_beta_error")["code"], "control_lost")

    async def test_foreign_user_cannot_control_or_see_run(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        await self._call("dungeon_beta_take_control", self.b, self.b_state,
                         {"type": "dungeon_beta_take_control", "protocol_version": 1,
                          "request_id": "steal", "run_id": run_id})
        self.assertEqual(self.b.last("dungeon_beta_error")["code"], "not_found")
        await self._call("dungeon_beta_input", self.b, self.b_state,
                         {"type": "dungeon_beta_input", "protocol_version": 1,
                          "run_id": run_id, "control_epoch": 0, "input_seq": 1,
                          "value": INPUT})
        self.assertEqual(self.b.last("dungeon_beta_error")["code"], "control_lost")

    async def test_duplicate_input_is_silent_and_gapped_input_fails(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        epoch = result["result"]["control_epoch"]
        message = {"type": "dungeon_beta_input", "protocol_version": 1, "run_id": run_id,
                   "control_epoch": epoch, "input_seq": 1, "value": INPUT}
        await self._call("dungeon_beta_input", self.a, self.a_state, message)
        await self._call("dungeon_beta_input", self.a, self.a_state, message)
        self.assertEqual(self.a.all("dungeon_beta_error"), [])
        await asyncio.sleep(0.05)  # clear the input rate limiter
        gapped = {**message, "input_seq": 3}
        await self._call("dungeon_beta_input", self.a, self.a_state, gapped)
        self.assertEqual(self.a.last("dungeon_beta_error")["code"], "input_gap")

    # -------------------------------------------------- pause, abandon, grace

    async def test_pause_detaches_scheduler_and_resume_reattaches(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        await self.wait_for(lambda: len(self.a.all("dungeon_beta_frame")) >= 1)
        await self._call("dungeon_beta_pause", self.a, self.a_state,
                         {"type": "dungeon_beta_pause", "protocol_version": 1,
                          "request_id": "pause-1", "run_id": run_id})
        self.assertEqual(self.a.last("dungeon_beta_result")["result"]["status"], "paused")
        self.assertEqual(self.app.dungeon_scheduler.active_run_ids, ())
        self.assertGreaterEqual(self.run_row(run_id)["durable_tick"], 1)
        await self._call("dungeon_beta_resume", self.a, self.a_state,
                         {"type": "dungeon_beta_resume", "protocol_version": 1,
                          "request_id": "resume-1", "run_id": run_id})
        self.assertEqual(self.app.dungeon_scheduler.active_run_ids, (run_id,))
        await self._call("dungeon_beta_pause", self.a, self.a_state,
                         {"type": "dungeon_beta_pause", "protocol_version": 1,
                          "request_id": "pause-2", "run_id": run_id})

    async def test_abandon_ends_run_and_releases_reservations(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        await self._call("dungeon_beta_abandon", self.a, self.a_state,
                         {"type": "dungeon_beta_abandon", "protocol_version": 1,
                          "request_id": "abandon-1", "run_id": run_id})
        self.assertEqual(self.a.last("dungeon_beta_result")["result"]["status"],
                         "abandoned")
        self.assertEqual(self.run_row(run_id)["status"], "abandoned")
        with self.db() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM dungeon_asset_reservations").fetchone()[0], 0)

    async def test_disconnect_grace_pauses_the_run(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        await self.wait_for(lambda: len(self.a.all("dungeon_beta_frame")) >= 1)
        await self.app.dungeon_action.on_disconnect(self.a)
        await self.wait_for(lambda: self.run_row(run_id)["status"] == "paused", timeout=2.0)
        await self.wait_for(lambda: bool(self.a.all("dungeon_beta_error")))
        error = self.a.last("dungeon_beta_error")
        self.assertEqual((error["code"], error["details"]["run_id"]), ("run_paused", run_id))
        # A fresh connection of the same owner takes over from the durable save.
        fresh = Socket()
        fresh_state = self._connect(fresh, "alice")
        await self._call("dungeon_beta_take_control", fresh, fresh_state,
                         {"type": "dungeon_beta_take_control", "protocol_version": 1,
                          "request_id": "ctrl-reconnect", "run_id": run_id})
        self.assertEqual(fresh.last("dungeon_beta_result")["result_kind"], "take_control")

    async def test_sync_returns_status_and_cached_frame(self):
        result = await self.start()
        run_id = result["result"]["run_id"]
        await self.wait_for(lambda: len(self.a.all("dungeon_beta_frame")) >= 1)
        await self._call("dungeon_beta_sync", self.a2, self.a2_state,
                         {"type": "dungeon_beta_sync", "protocol_version": 1,
                          "request_id": "sync-1", "run_id": run_id})
        sync = self.a2.last("dungeon_beta_result")
        self.assertEqual(sync["result_kind"], "sync")
        self.assertEqual(sync["result"]["status"]["run_id"], run_id)
        self.assertIsNotNone(sync["result"]["frame"])


if __name__ == "__main__":
    unittest.main()
