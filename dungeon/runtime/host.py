"""Bounded single-process run host, driven by explicit one-tick pump calls.

The transport must never expose ``RoomOutcome`` or ``commit_room``. The only
clear signal accepted here comes from the injected trusted simulator's event
stream after a completed one-tick ``step``.
"""

from dataclasses import dataclass
import json
from collections.abc import Mapping

from dungeon.domain.errors import DungeonError
from dungeon.runtime.random import NamedRandom
from dungeon.storage.runs import canonical


@dataclass(frozen=True)
class RoomOutcome:
    run_id: str
    room_index: int
    encounter_id: str
    tick: int
    control_epoch: int
    last_input_seq: int
    simulator_snapshot: dict
    rng_snapshot: dict
    run_resources: dict


class _Services:
    def __init__(self, rng, max_events, max_event_bytes):
        self.rng = rng
        self.events = []
        self.max_events = max_events
        self.max_event_bytes = max_event_bytes
        self.event_bytes = 0

    def random_int(self, stream, lower, upper):
        return self.rng.random_int(stream, lower, upper)

    def emit(self, event):
        if not isinstance(event, Mapping):
            raise DungeonError("invalid_event", "模拟事件必须是对象")
        try:
            raw = canonical(dict(event))
        except (TypeError, ValueError) as error:
            raise DungeonError("invalid_event", "模拟事件无效") from error
        self.event_bytes += len(raw.encode("utf-8"))
        if len(self.events) >= self.max_events or self.event_bytes > self.max_event_bytes:
            raise DungeonError("event_budget", "模拟事件超出预算")
        self.events.append(json.loads(raw))


class RunHost:
    def __init__(self, service, *, max_queue=64, max_step_ticks=4,
                 max_inputs_per_tick=8, checkpoint_ticks=30,
                 max_events_per_tick=64, max_event_bytes=8192,
                 max_snapshot_bytes=262144):
        for value in (max_queue, max_step_ticks, max_inputs_per_tick, checkpoint_ticks,
                      max_events_per_tick, max_event_bytes, max_snapshot_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("host budgets must be positive integers")
        self.service = service
        self.simulator = service.simulator
        self.max_queue = max_queue
        self.max_step_ticks = max_step_ticks
        self.max_inputs_per_tick = max_inputs_per_tick
        self.checkpoint_ticks = checkpoint_ticks
        self.max_events_per_tick = max_events_per_tick
        self.max_event_bytes = max_event_bytes
        self.max_snapshot_bytes = max_snapshot_bytes
        self._runs = {}

    def _snapshot(self, state):
        value = self.simulator.snapshot(state)
        if not isinstance(value, Mapping):
            raise DungeonError("invalid_save", "模拟存档必须是对象")
        raw = canonical(dict(value))
        if len(raw.encode("utf-8")) > self.max_snapshot_bytes:
            raise DungeonError("snapshot_budget", "模拟存档超出预算")
        return json.loads(raw)

    def _make_state(self, run):
        checkpoint = run["checkpoint"]
        rng = NamedRandom.restore(checkpoint["rng"])
        services = _Services(rng, self.max_events_per_tick, self.max_event_bytes)
        if checkpoint["phase"] == "combat":
            state = self.simulator.restore(checkpoint["simulator"], self.service.rules, services)
        elif checkpoint["phase"] in ("new", "between_rooms"):
            route = self.service._route(run["route_id"])
            room_index = run["room_index"]
            encounter_id = route["encounter_ids"][room_index]
            initial = {**run["initial"], "room_index": room_index,
                       "encounter_id": encounter_id,
                       "encounter": self.service.rules.mutable_content("encounters")[
                           next(i for i, entry in enumerate(self.service.rules.content("encounters"))
                                if entry["id"] == encounter_id)],
                       "run_resources": checkpoint.get("run_resources", {}),
                       "previous_room_snapshot": checkpoint.get("simulator")}
            state = self.simulator.create(initial, self.service.rules, services)
        else:
            raise DungeonError("invalid_save", "挑战存档阶段异常")
        self._snapshot(state)
        if services.events:
            raise DungeonError("invalid_event", "创建或恢复时不允许发出事件")
        return {"run": run, "state": state, "rng": rng, "queue": [],
                "run_resources": checkpoint.get("run_resources", {}),
                "accepted_seq": run["last_input_seq"], "applied_seq": run["last_input_seq"],
                "tick": run["durable_tick"], "busy": False}

    def attach(self, run_id):
        if run_id in self._runs:
            return self.status(run_id)
        run = self.service.get(run_id)
        if run["status"] not in ("ready", "paused", "running"):
            raise DungeonError("run_closed", "挑战已结束")
        if run["status"] == "running":
            raise DungeonError("run_owned", "运行中的挑战已有权威宿主")
        self._runs[run_id] = self._make_state(run)
        return self.status(run_id)

    def status(self, run_id):
        entry = self._runs.get(run_id)
        run = entry["run"] if entry else self.service.get(run_id)
        return {"run_id": run_id, "status": run["status"], "room_index": run["room_index"],
                "server_tick": entry["tick"] if entry else run["server_tick"],
                "durable_tick": run["durable_tick"], "control_epoch": run["control_epoch"],
                "acked_input_seq": entry["applied_seq"] if entry else run["last_input_seq"]}

    def _change_status(self, run_id, before, after, *, increment_epoch=False):
        with self.service.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            from dungeon.storage.runs import load_run
            run = load_run(conn, run_id)
            self.service.assert_compatible(run)
            if run["status"] != before:
                raise DungeonError("version_conflict", "挑战状态已变化")
            target_epoch = run["control_epoch"] + (1 if increment_epoch else 0)
            conn.execute("""UPDATE dungeon_beta_runs SET status=?,run_revision=run_revision+1,
                control_epoch=control_epoch+?,updated_at=? WHERE run_id=?""",
                (after, 1 if increment_epoch else 0, int(self.service.clock()), run_id))
        self._runs.pop(run_id, None)
        if after == "running":
            try:
                run = self.service.get(run_id)
                self._runs[run_id] = self._make_state(run)
            except BaseException:
                self._runs.pop(run_id, None)
                self._pause_failed_initialization(run_id, target_epoch)
                raise
            return self.status(run_id)
        return self.attach(run_id) if after != "finished" else self.status(run_id)

    def _pause_failed_initialization(self, run_id, expected_epoch):
        """Retain the last durable save if create/restore fails after resume."""
        try:
            with self.service.database() as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""UPDATE dungeon_beta_runs SET status='paused',
                    control_epoch=control_epoch+1,run_revision=run_revision+1,
                    updated_at=? WHERE run_id=? AND status='running'
                    AND control_epoch=?""",
                    (int(self.service.clock()), run_id, expected_epoch))
        except BaseException:
            # A storage outage remains recoverable through recover_unfinished
            # at process startup; preserve the original simulator exception.
            pass

    def resume(self, run_id):
        entry = self._runs.get(run_id)
        run = entry["run"] if entry else self.service.get(run_id)
        if run["status"] not in ("ready", "paused"):
            raise DungeonError("version_conflict", "挑战未暂停")
        return self._change_status(run_id, run["status"], "running")

    def pause(self, run_id):
        entry = self._runs.get(run_id)
        if entry is None:
            self.attach(run_id)
            entry = self._runs[run_id]
        if entry["run"]["status"] == "ready":
            return self._change_status(run_id, "ready", "paused", increment_epoch=True)
        if entry["run"]["status"] != "running":
            return self.status(run_id)
        self._checkpoint_entry(run_id, entry)
        return self._change_status(run_id, "running", "paused", increment_epoch=True)

    def abandon(self, run_id):
        from dungeon.storage.runs import load_run
        from dungeon.storage.assets import release_item
        with self.service.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            run = load_run(conn, run_id)
            if run["status"] not in ("ready", "running", "paused"):
                raise DungeonError("run_closed", "挑战已结束")
            conn.execute("""UPDATE dungeon_beta_runs SET status='abandoned',
                control_epoch=control_epoch+1,run_revision=run_revision+1,updated_at=?
                WHERE run_id=?""", (int(self.service.clock()), run_id))
            rows = conn.execute("""SELECT asset_id FROM dungeon_asset_reservations
                WHERE purpose='active_run' AND reservation_ref=?""", (run_id,)).fetchall()
            for (item_id,) in rows:
                release_item(conn, item_id, "active_run", run_id)
        self._runs.pop(run_id, None)
        return {"run_id": run_id, "status": "abandoned"}

    def take_control(self, run_id):
        entry = self._runs.get(run_id)
        if entry is not None and entry["run"]["status"] == "running":
            self._checkpoint_entry(run_id, entry)
        with self.service.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            from dungeon.storage.runs import load_run
            run = load_run(conn, run_id)
            if run["status"] not in ("ready", "paused", "running"):
                raise DungeonError("run_closed", "挑战已结束")
            # Every takeover ends at a durable paused checkpoint. The new
            # controller explicitly resumes after clearing stale inputs.
            new_status = "paused"
            conn.execute("""UPDATE dungeon_beta_runs SET status=?,
                control_epoch=control_epoch+1,run_revision=run_revision+1,
                updated_at=? WHERE run_id=?""",
                (new_status, int(self.service.clock()), run_id))
        self._runs.pop(run_id, None)
        return self.attach(run_id)

    def input(self, run_id, control_epoch, input_seq, value):
        entry = self._runs.get(run_id)
        if entry is None:
            self.attach(run_id)
            entry = self._runs[run_id]
        run = entry["run"]
        self._assert_current(run_id, entry)
        if (run["status"] != "running" or type(control_epoch) is not int
                or control_epoch != run["control_epoch"]):
            raise DungeonError("control_lost", "控制权已变化")
        if type(input_seq) is not int or input_seq < 1:
            raise DungeonError("invalid_input", "输入序号无效")
        if input_seq <= entry["accepted_seq"]:
            return False
        if input_seq != entry["accepted_seq"] + 1:
            raise DungeonError("input_gap", "输入序号不连续")
        if len(entry["queue"]) >= self.max_queue:
            raise DungeonError("input_budget", "输入队列已满")
        if not isinstance(value, dict) or set(value) != {"move_x", "move_y", "aim_x", "aim_y", "buttons"}:
            raise DungeonError("invalid_input", "输入字段无效")
        if any(type(value[key]) is not int or not -1024 <= value[key] <= 1024
               for key in ("move_x", "move_y", "aim_x", "aim_y")):
            raise DungeonError("invalid_input", "输入方向超出范围")
        buttons = value["buttons"]
        if (not isinstance(buttons, (tuple, list)) or len(buttons) > 3
                or any(not isinstance(button, str) for button in buttons)
                or len(set(buttons)) != len(buttons)
                or any(button not in ("attack", "dash", "potion") for button in buttons)):
            raise DungeonError("invalid_input", "输入按键无效")
        entry["queue"].append({"input_seq": input_seq, **value, "buttons": list(buttons)})
        entry["accepted_seq"] = input_seq
        return True

    def _assert_current(self, run_id, entry):
        with self.service.database() as conn:
            row = conn.execute("""SELECT status,room_index,control_epoch
                FROM dungeon_beta_runs WHERE run_id=?""", (run_id,)).fetchone()
        if row != (entry["run"]["status"], entry["run"]["room_index"],
                   entry["run"]["control_epoch"]):
            self._runs.pop(run_id, None)
            raise DungeonError("control_lost", "挑战控制权已变化")

    def _checkpoint_entry(self, run_id, entry):
        snapshot = self._snapshot(entry["state"])
        resources = snapshot.get("run_resources", entry["run_resources"])
        if not isinstance(resources, dict):
            raise DungeonError("invalid_save", "局内资源存档必须是对象")
        checkpoint = self.service.checkpoint(run_id, room_index=entry["run"]["room_index"],
            tick=entry["tick"], epoch=entry["run"]["control_epoch"],
            last_input_seq=entry["applied_seq"], simulator_snapshot=snapshot,
            rng_snapshot=entry["rng"].snapshot(), run_resources=resources)
        entry["run_resources"] = resources
        entry["run"]["checkpoint"] = checkpoint
        entry["run"]["durable_tick"] = entry["tick"]
        entry["run"]["last_input_seq"] = entry["applied_seq"]
        return checkpoint

    def pump(self, run_id, ticks=1):
        if type(ticks) is not int or not 1 <= ticks <= self.max_step_ticks:
            raise DungeonError("step_budget", "推进步数超出预算")
        entry = self._runs.get(run_id)
        if entry is None:
            self.attach(run_id)
            entry = self._runs[run_id]
        if entry["run"]["status"] != "running":
            raise DungeonError("run_paused", "挑战未运行")
        self._assert_current(run_id, entry)
        if entry["busy"]:
            raise DungeonError("run_busy", "挑战正在推进")
        entry["busy"] = True
        try:
            for _ in range(ticks):
                batch = entry["queue"][:self.max_inputs_per_tick]
                del entry["queue"][:len(batch)]
                services = _Services(entry["rng"], self.max_events_per_tick,
                                     self.max_event_bytes)
                entry["state"] = self.simulator.step(entry["state"], batch, services)
                entry["tick"] += 1
                if batch:
                    entry["applied_seq"] = batch[-1]["input_seq"]
                snapshot = self._snapshot(entry["state"])
                clears = [event for event in services.events if event.get("type") == "room_cleared"]
                if clears:
                    if len(clears) != 1:
                        raise DungeonError("invalid_outcome", "清房事件无效")
                    route = self.service._route(entry["run"]["route_id"])
                    room_index = entry["run"]["room_index"]
                    encounter_id = route["encounter_ids"][room_index]
                    if clears[0] != {"type": "room_cleared", "room_index": room_index,
                                     "encounter_id": encounter_id}:
                        raise DungeonError("invalid_outcome", "清房事件与当前房间不符")
                    result = self.service.commit_room(RoomOutcome(run_id, room_index,
                        encounter_id, entry["tick"], entry["run"]["control_epoch"],
                        entry["applied_seq"], snapshot, entry["rng"].snapshot(),
                        snapshot.get("run_resources", entry["run_resources"])))
                    self._runs.pop(run_id, None)
                    return {"status": result["status"], "reward": result,
                            "server_tick": entry["tick"], "durable_tick": entry["tick"]}
                if entry["tick"] - entry["run"]["durable_tick"] >= self.checkpoint_ticks:
                    self._checkpoint_entry(run_id, entry)
            return self.status(run_id)
        except BaseException:
            # Discard all uncommitted in-memory progress; a new host may replay
            # from the last durable checkpoint. A failed DB write cannot be
            # acknowledged to a client as durable.
            self._runs.pop(run_id, None)
            try:
                with self.service.database() as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("""UPDATE dungeon_beta_runs SET status='paused',
                        control_epoch=control_epoch+1,run_revision=run_revision+1,
                        updated_at=? WHERE run_id=? AND status='running'
                        AND control_epoch=?""",
                        (int(self.service.clock()), run_id, entry["run"]["control_epoch"]))
            except BaseException:
                pass
            raise
        finally:
            entry["busy"] = False

    def close(self):
        for run_id in list(self._runs):
            if self._runs[run_id]["run"]["status"] in ("ready", "running"):
                self.pause(run_id)
        self._runs.clear()
