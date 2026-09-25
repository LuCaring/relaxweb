"""Bounded asyncio scheduler that pumps authoritative runs at a fixed tick rate.

The scheduler owns the single worker executor every host mutation goes through:
``RunHost._runs`` is unsynchronized in-memory state, so serializing pump, input,
frame and pause calls on one thread is the mutual-exclusion mechanism. Scheduling
itself lives on the event loop; only ``host`` calls block on the executor.

Catch-up is bounded: a late loop pumps up to ``host.max_step_ticks`` ticks, and
lateness beyond that budget is dropped (time dilation) instead of spiralling.
The loop never raises into the task: every terminal outcome (room cleared, host
paused the run, storage failure) is reported through ``on_stopped``/``on_room_cleared``
and the task removes itself, so a client can re-attach after the next room.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from dungeon.domain.errors import DungeonError

logger = logging.getLogger("live-chat")

STOP_CODES = frozenset({"run_paused", "run_closed", "run_owned", "control_lost",
                        "version_conflict", "ruleset_unavailable", "not_found"})


def ticks_for_lateness(lateness, period, max_step_ticks):
    """Ticks owed when the loop is ``lateness`` seconds behind its deadline."""
    if lateness <= 0:
        return 1
    return min(1 + int(lateness / period), max_step_ticks)


class RunScheduler:
    def __init__(self, host, *, tick_rate=30, executor=None, frame_rate=10,
                 on_room_cleared=None, on_frame=None, on_stopped=None):
        if type(tick_rate) is not int or not 1 <= tick_rate <= 240:
            raise ValueError("tick_rate must be an integer in [1, 240]")
        if type(frame_rate) is not int or not 1 <= frame_rate <= tick_rate:
            raise ValueError("frame_rate must be an integer in [1, tick_rate]")
        self.host = host
        self.tick_rate = tick_rate
        self.frame_every = tick_rate // frame_rate
        self.on_room_cleared = on_room_cleared
        self.on_frame = on_frame
        self.on_stopped = on_stopped
        self._executor = executor
        self._owns_executor = executor is None
        self._tasks = {}

    def _ensure_executor(self):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1,
                                                thread_name_prefix="dungeon-sim")
        return self._executor

    async def run_io(self, function, *args):
        """Run one blocking host call on the serialization thread."""
        return await asyncio.get_running_loop().run_in_executor(
            self._ensure_executor(), function, *args)

    @property
    def active_run_ids(self):
        return tuple(run_id for run_id, task in self._tasks.items() if not task.done())

    async def attach(self, run_id):
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._pump_loop(run_id), name=f"dungeon-pump-{run_id}")
        task.add_done_callback(lambda done, run_id=run_id: self._forget(run_id, done))
        self._tasks[run_id] = task
        logger.info("dungeon run attached: run_id=%s active=%d", run_id, len(self.active_run_ids))

    def _forget(self, run_id, task):
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
            if not task.cancelled() and task.exception() is None:
                logger.info("dungeon run detached: run_id=%s active=%d",
                            run_id, len(self.active_run_ids))

    async def detach(self, run_id):
        task = self._tasks.get(run_id)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stop(self, run_id):
        """Stop pumping and leave the run at a durable paused checkpoint."""
        await self.detach(run_id)
        try:
            await self.run_io(self.host.pause, run_id)
        except DungeonError as error:
            if error.code not in STOP_CODES:
                raise

    async def aclose(self):
        for run_id in list(self._tasks):
            await self.detach(run_id)
        if self._executor is not None:
            await self.run_io(self.host.close)
            self._executor.shutdown()
            self._executor = None

    async def _pump_loop(self, run_id):
        loop = asyncio.get_running_loop()
        executor = self._ensure_executor()
        period = 1.0 / self.tick_rate
        next_deadline = loop.time() + period
        ticks_done = 0
        next_frame = self.frame_every
        try:
            while True:
                lateness = next_deadline - loop.time()
                if lateness > 0:
                    await asyncio.sleep(lateness)
                ticks = ticks_for_lateness(loop.time() - next_deadline, period,
                                           self.host.max_step_ticks)
                started = time.monotonic()
                result = await loop.run_in_executor(executor, self.host.pump, run_id, ticks)
                elapsed = time.monotonic() - started
                if elapsed > 0.25:
                    logger.warning("dungeon slow pump: run_id=%s ticks=%d took=%.3fs",
                                   run_id, ticks, elapsed)
                ticks_done += ticks
                next_deadline += ticks * period
                if loop.time() - next_deadline > period * self.host.max_step_ticks:
                    logger.warning("dungeon time dilation: run_id=%s dropped backlog",
                                   run_id)
                    next_deadline = loop.time() + period
                if result.get("reward") is not None:
                    # host.pump already popped the run; only the clear push goes out.
                    if self.on_room_cleared is not None:
                        self.on_room_cleared(run_id, result)
                    return
                if ticks_done >= next_frame:
                    next_frame = ticks_done + self.frame_every
                    try:
                        frame = await loop.run_in_executor(executor, self.host.frame, run_id)
                    except DungeonError as error:
                        if error.code not in STOP_CODES:
                            logger.warning("dungeon frame failed: run_id=%s code=%s",
                                           run_id, error.code)
                        frame = None
                    if frame is not None and self.on_frame is not None:
                        self.on_frame(run_id, frame)
        except asyncio.CancelledError:
            raise
        except DungeonError as error:
            logger.info("dungeon run stopped: run_id=%s code=%s", run_id, error.code)
            if self.on_stopped is not None:
                self.on_stopped(run_id, error)
        except Exception as error:
            # host.pump already discarded the run and parked it as paused.
            logger.exception("dungeon pump crashed: run_id=%s", run_id)
            if self.on_stopped is not None:
                self.on_stopped(run_id, error)
