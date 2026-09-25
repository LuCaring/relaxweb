#!/usr/bin/env python3
"""RunScheduler: tick pacing, frame cadence, stop paths and crash parking."""

import asyncio
from functools import partial
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon.application.items import create_item
from dungeon.application.runs import RunService
from dungeon.content.loader import load_ruleset
from dungeon.domain.errors import DungeonError
from dungeon.runtime.host import RunHost
from dungeon.runtime.scheduler import RunScheduler, ticks_for_lateness
from dungeon.storage.runs import load_run
from dungeon.legacy.service import dungeon_state
from server.database import database
from server.schema import init_db


ROOT = Path(__file__).resolve().parent.parent
ROUTE = "beta.p0.route.flamefield"


class TinySimulator:
    """Clears each room after two single-tick steps; view is a tiny projection."""

    def create(self, initial, rules, services):
        previous = initial.get("previous_room_snapshot") or {}
        return {"room_index": initial["room_index"], "encounter_id": initial["encounter_id"],
                "room_ticks": 0, "hp": previous.get("hp", 100),
                "run_resources": initial.get("run_resources", {})}

    def step(self, state, ordered_inputs, services):
        state = dict(state)
        state["room_ticks"] += 1
        state["hp"] -= 1
        if state["room_ticks"] == 2:
            services.emit({"type": "room_cleared", "room_index": state["room_index"],
                           "encounter_id": state["encounter_id"]})
        return state

    def view(self, state):
        return {"hp": state["hp"], "room_ticks": state["room_ticks"]}

    def snapshot(self, state):
        return dict(state)

    def restore(self, snapshot, rules, services):
        return dict(snapshot)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = partial(database, str(Path(self.tmp.name) / "sched.db"))
        init_db(self.database)
        self.rules = load_ruleset(ROOT / "content/dungeon/release-p0.json")
        with self.database() as conn, conn:
            conn.execute("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES ('alice','','',0,10)""")
            self.user_id = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            dungeon_state(conn, "alice", 100)
            item = create_item(conn, self.rules, "alice", "beta.sword.basic",
                               source="integration_fixture", now=100)
            conn.execute("""INSERT INTO dungeon_loadout(username,slot,item_id)
                VALUES ('alice','weapon',?) ON CONFLICT(username,slot)
                DO UPDATE SET item_id=excluded.item_id""", (item["item_id"],))
        self.service = RunService(self.database, self.rules, TinySimulator(), clock=lambda: 101)
        self.host = RunHost(self.service, checkpoint_ticks=1)
        self.cleared = []
        self.frames = []
        self.stopped = []
        self.scheduler = RunScheduler(
            self.host, tick_rate=100, frame_rate=100,
            on_room_cleared=lambda run_id, result: self.cleared.append((run_id, result)),
            on_frame=lambda run_id, frame: self.frames.append((run_id, frame)),
            on_stopped=lambda run_id, error: self.stopped.append((run_id, error)))
        self.addAsyncCleanup(self.scheduler.aclose)

    async def start(self):
        run_id = self.service.start(self.user_id, f"start-{len(self.cleared)}-{id(self)}",
                                    ROUTE)["run_id"]
        self.host.resume(run_id)
        return run_id

    async def wait_for(self, predicate, timeout=5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("timed out waiting for scheduler condition")
            await asyncio.sleep(0.01)

    async def test_rooms_clear_in_order_and_frames_arrive(self):
        run_id = await self.start()
        await self.scheduler.attach(run_id)
        await self.wait_for(lambda: len(self.cleared) >= 1)
        self.assertEqual(self.cleared[0][1]["reward"]["room_index"], 0)
        self.assertTrue(any(rid == run_id for rid, _ in self.frames))
        view = self.frames[0][1]["view"]
        self.assertIn("hp", view)
        # The pump loop removed itself; the next room needs an explicit resume.
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.cleared), 1)
        self.assertEqual(self.scheduler.active_run_ids, ())
        for room in range(1, 4):
            self.host.resume(run_id)
            await self.scheduler.attach(run_id)
            await self.wait_for(lambda: len(self.cleared) >= room + 1)
        with self.database() as conn:
            run = load_run(conn, run_id)
        self.assertEqual((run["status"], run["room_index"]), ("finished", 4))

    async def test_host_pause_stops_the_loop_and_parks_durable(self):
        run_id = await self.start()
        await self.scheduler.attach(run_id)
        await self.wait_for(lambda: len(self.frames) >= 1)
        await self.scheduler.stop(run_id)
        with self.database() as conn:
            run = load_run(conn, run_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(self.scheduler.active_run_ids, ())

    async def test_simulator_crash_parks_run_paused_and_notifies(self):
        class Explodes(TinySimulator):
            def step(self, state, ordered_inputs, services):
                if state["room_ticks"] == 1:
                    raise RuntimeError("simulator bug")
                return super().step(state, ordered_inputs, services)
        self.service.simulator = Explodes()
        host = RunHost(self.service, checkpoint_ticks=1)
        stopped = []
        scheduler = RunScheduler(host, tick_rate=100, frame_rate=100,
                                 on_stopped=lambda run_id, error: stopped.append(error))
        self.addAsyncCleanup(scheduler.aclose)
        run_id = self.service.start(self.user_id, "start-crash", ROUTE)["run_id"]
        host.resume(run_id)
        await scheduler.attach(run_id)
        await self.wait_for(lambda: len(stopped) >= 1)
        self.assertIsInstance(stopped[0], RuntimeError)
        with self.database() as conn:
            run = load_run(conn, run_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(scheduler.active_run_ids, ())

    async def test_aclose_pauses_active_runs_and_shuts_executor(self):
        run_id = await self.start()
        await self.scheduler.attach(run_id)
        await self.wait_for(lambda: len(self.frames) >= 1)
        await self.scheduler.aclose()
        with self.database() as conn:
            run = load_run(conn, run_id)
        # paused = parked mid-room; ready = the room cleared before close and the
        # durable between-rooms checkpoint is already the parking spot.
        self.assertIn(run["status"], ("paused", "ready"))
        self.assertEqual(self.scheduler.active_run_ids, ())
        self.assertIsNone(self.scheduler._executor)

    async def test_double_attach_is_idempotent(self):
        run_id = await self.start()
        await self.scheduler.attach(run_id)
        await self.scheduler.attach(run_id)
        await self.wait_for(lambda: len(self.frames) >= 1)
        self.assertEqual(len(self.scheduler.active_run_ids), 1)
        await self.scheduler.stop(run_id)

    def test_ticks_for_lateness_is_bounded(self):
        self.assertEqual(ticks_for_lateness(-0.5, 1 / 30, 4), 1)
        self.assertEqual(ticks_for_lateness(0.0, 1 / 30, 4), 1)
        self.assertEqual(ticks_for_lateness(1.5 / 30, 1 / 30, 4), 2)
        self.assertEqual(ticks_for_lateness(10.0, 1 / 30, 4), 4)


if __name__ == "__main__":
    unittest.main()
