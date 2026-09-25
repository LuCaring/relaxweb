"""Internal Beta run creation and recovery. No client transport is registered."""

import hashlib
import secrets
import time
from uuid import uuid4

from dungeon.domain.errors import DungeonError
from dungeon.legacy.receipts import REQUEST_ID_PATTERN
from dungeon.storage.assets import reserve_item
from dungeon.storage.runs import active_for_user, canonical, load_run
from dungeon.storage.assets import release_item
from dungeon.application.wallet import SharedWalletPort
from dungeon.application.rewards import plan_room_reward, apply_room_reward
from dungeon.runtime.random import NamedRandom


class RunService:
    def __init__(self, database, rules, simulator, *, clock=None, wallet=None):
        self.database = database
        self.rules = rules
        self.simulator = simulator
        self.clock = clock or time.time
        self.wallet = wallet or SharedWalletPort()

    def _route(self, route_id):
        try:
            routes = self.rules.content("routes")
        except KeyError as error:
            raise DungeonError("content_unavailable", "当前规则未开放路线") from error
        route = next((entry for entry in routes if entry["id"] == route_id), None)
        if route is None or not route.get("encounter_ids"):
            raise DungeonError("content_unavailable", "路线不存在或未开放")
        return route

    def assert_compatible(self, run):
        rules = self.rules
        if (run["ruleset_id"] != rules.ruleset_id or run["ruleset_hash"] != rules.ruleset_hash
                or run["simulation_version"] != rules.simulation_version
                or run["save_version"] != rules.save_version):
            raise DungeonError("ruleset_unavailable", "挑战原规则或模拟版本不可用")
        self._route(run["route_id"])

    def start(self, user_id, request_id, route_id, *, expected_asset_revision=None):
        if type(user_id) is not int or user_id < 1:
            raise DungeonError("invalid_request", "用户编号无效")
        if not isinstance(request_id, str) or REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            raise DungeonError("invalid_request", "请求编号无效")
        if not isinstance(route_id, str) or not route_id or len(route_id) > 128:
            raise DungeonError("invalid_request", "路线编号无效")
        if expected_asset_revision is not None and (type(expected_asset_revision) is not int
                                                    or expected_asset_revision < 1):
            raise DungeonError("invalid_request", "资产版本无效")
        payload = {"kind": "start_run", "route_id": route_id,
                   "expected_asset_revision": expected_asset_revision}
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        now = int(self.clock())
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("""SELECT request_hash,result_json FROM dungeon_beta_receipts
                WHERE user_id=? AND request_id=?""", (user_id, request_id)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise DungeonError("request_conflict", "请求编号已用于其他操作")
                import json
                result = json.loads(prior[1])
                return {**result, "replayed": True}
            self._route(route_id)
            user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None:
                raise DungeonError("auth_required", "用户不存在")
            username = user[0]
            if conn.execute("SELECT 1 FROM dungeon_active_jobs WHERE username=?", (username,)).fetchone():
                raise DungeonError("active_job", "已有进行中的挑战或扫荡")
            if active_for_user(conn, user_id):
                raise DungeonError("active_job", "已有进行中的地下城挑战")
            revision_row = conn.execute("""SELECT revision FROM dungeon_beta_asset_revisions
                WHERE user_id=?""", (user_id,)).fetchone()
            revision = revision_row[0] if revision_row else 1
            if expected_asset_revision is not None and expected_asset_revision != revision:
                raise DungeonError("version_conflict", "资产已变化，请刷新")
            equipped = conn.execute("""SELECT l.item_id,i.template_id,i.template_version,i.stats_json,
                i.version,i.slot,i.beta_upgrade_level,i.location,i.beta_effects_json,
                i.beta_ruleset_id,i.beta_ruleset_hash,i.beta_bound_reason,i.beta_source,
                i.effects_json,i.tags_json,i.affixes_json,i.quality,i.visual_id,i.display_name
                FROM dungeon_loadout l LEFT JOIN dungeon_items i
                ON i.item_id=l.item_id AND i.owner=l.username
                WHERE l.username=? ORDER BY l.slot""", (username,)).fetchall()
            import json
            if any(row[1] is None or row[7] != "bag" for row in equipped):
                raise DungeonError("invalid_save", "已穿戴装备记录异常")
            try:
                equipment = [{"item_id": row[0], "template_id": row[1],
                              "template_version": row[2], "stats": json.loads(row[3]),
                              "version": row[4], "slot": row[5], "upgrade_level": row[6],
                              "effects": json.loads(row[8]) if row[8] else [],
                              "ruleset_id": row[9], "ruleset_hash": row[10],
                              "bound_reason": row[11], "source": row[12],
                              "legacy_effects": json.loads(row[13]) if row[13] else [],
                              "tags": json.loads(row[14]) if row[14] else [],
                              "affixes": json.loads(row[15]) if row[15] else [],
                              "quality": row[16], "visual_id": row[17],
                              "display_name": row[18]}
                             for row in equipped]
            except (TypeError, ValueError) as error:
                raise DungeonError("invalid_save", "已穿戴装备快照异常") from error
            run_id = uuid4().hex
            for item in equipment:
                reserve_item(conn, username, item["item_id"], "active_run", run_id, now)
            seed_hex = secrets.token_hex(32)
            initial = {"run_id": run_id, "route_id": route_id, "equipment": equipment}
            checkpoint = {"phase": "new", "room_index": 0, "tick": 0,
                          "last_input_seq": 0, "simulator": None,
                          "rng": {"version": 1, "seed_hex": seed_hex, "positions": {}}}
            rules = self.rules
            conn.execute("""INSERT INTO dungeon_beta_runs
                (run_id,user_id,route_id,status,room_index,server_tick,durable_tick,
                 run_revision,control_epoch,last_input_seq,ruleset_id,ruleset_hash,
                 simulation_version,save_version,initial_json,checkpoint_json,created_at,updated_at)
                VALUES (?,?,?,'ready',0,0,0,1,0,0,?,?,?,?,?,?,?,?)""",
                (run_id, user_id, route_id, rules.ruleset_id, rules.ruleset_hash,
                 rules.simulation_version, rules.save_version, canonical(initial),
                 canonical(checkpoint), now, now))
            result = {"run_id": run_id, "status": "ready", "route_id": route_id,
                      "ruleset_id": rules.ruleset_id, "ruleset_hash": rules.ruleset_hash,
                      "replayed": False}
            conn.execute("""INSERT INTO dungeon_beta_receipts
                (user_id,request_id,request_hash,status,result_json,created_at)
                VALUES (?,?,?,'success',?,?)""",
                (user_id, request_id, digest, canonical(result), now))
            return result

    def recover_unfinished(self):
        """Call at process startup: unfinished runs become paused at durable saves."""
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""SELECT run_id FROM dungeon_beta_runs
                WHERE status IN ('ready','running')""").fetchall()
            conn.execute("""UPDATE dungeon_beta_runs SET status='paused',
                control_epoch=control_epoch+1,run_revision=run_revision+1,updated_at=?
                WHERE status IN ('ready','running')""", (int(self.clock()),))
            return [row[0] for row in rows]

    def get(self, run_id, user_id=None):
        with self.database() as conn:
            run = load_run(conn, run_id, user_id)
            self.assert_compatible(run)
            return run

    def checkpoint(self, run_id, *, room_index, tick, epoch, last_input_seq,
                   simulator_snapshot, rng_snapshot, run_resources=None, phase="combat"):
        checkpoint = {"phase": phase, "room_index": room_index, "tick": tick,
                      "last_input_seq": last_input_seq, "simulator": simulator_snapshot,
                      "rng": rng_snapshot, "run_resources": run_resources or {}}
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            run = load_run(conn, run_id)
            self.assert_compatible(run)
            if (run["status"] != "running" or run["room_index"] != room_index
                    or run["control_epoch"] != epoch or tick < run["durable_tick"]):
                raise DungeonError("version_conflict", "挑战状态已变化")
            conn.execute("""UPDATE dungeon_beta_runs SET checkpoint_json=?,server_tick=?,
                durable_tick=?,last_input_seq=?,updated_at=? WHERE run_id=?""",
                (canonical(checkpoint), tick, tick, last_input_seq, int(self.clock()), run_id))
        return checkpoint

    def commit_room(self, outcome):
        """Internal simulator outcome; one transaction is the reward boundary."""
        from dungeon.runtime.host import RoomOutcome
        if not isinstance(outcome, RoomOutcome):
            raise TypeError("internal RoomOutcome required")
        if (type(outcome.room_index) is not int or type(outcome.tick) is not int
                or type(outcome.control_epoch) is not int
                or type(outcome.last_input_seq) is not int
                or not isinstance(outcome.simulator_snapshot, dict)
                or outcome.simulator_snapshot.get("room_index") != outcome.room_index
                or not isinstance(outcome.run_resources, dict)):
            raise DungeonError("invalid_outcome", "房间结果存档无效")
        now = int(self.clock())
        with self.database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            run = load_run(conn, outcome.run_id)
            self.assert_compatible(run)
            prior = conn.execute("""SELECT result_json FROM dungeon_beta_room_rewards
                WHERE run_id=? AND room_index=? AND reward_kind='room'""",
                (outcome.run_id, outcome.room_index)).fetchone()
            if prior is not None:
                import json
                return {**json.loads(prior[0]), "replayed": True}
            if (run["status"] != "running" or run["room_index"] != outcome.room_index
                    or run["control_epoch"] != outcome.control_epoch
                    or outcome.tick <= run["durable_tick"]
                    or outcome.last_input_seq < run["last_input_seq"]):
                raise DungeonError("version_conflict", "房间状态已变化")
            route = self._route(run["route_id"])
            encounter_ids = route["encounter_ids"]
            if outcome.room_index >= len(encounter_ids) or outcome.encounter_id != encounter_ids[outcome.room_index]:
                raise DungeonError("invalid_outcome", "房间结果与路线不符")
            encounter = next((entry for entry in self.rules.content("encounters")
                              if entry["id"] == outcome.encounter_id), None)
            if encounter is None:
                raise DungeonError("invalid_config", "房间内容不存在")
            rng = NamedRandom.restore(outcome.rng_snapshot)
            durable_rng = NamedRandom.restore(run["checkpoint"]["rng"])
            if rng.seed != durable_rng.seed or any(
                    rng.positions.get(name, 0) < position
                    for name, position in durable_rng.positions.items()):
                raise DungeonError("invalid_outcome", "随机状态与当前存档不符")
            plan = plan_room_reward(self.rules, encounter, rng)
            username_row = conn.execute("SELECT username FROM users WHERE id=?",
                                        (run["user_id"],)).fetchone()
            if username_row is None:
                raise DungeonError("auth_required", "用户不存在")
            applied = apply_room_reward(conn, self.rules, username_row[0], run["user_id"],
                                        run["run_id"], outcome.room_index, plan,
                                        self.wallet, now)
            resources = {"potions": list(outcome.run_resources.get("potions", []))}
            resources["potions"].extend(applied["run"].get("potions", []))
            next_index = outcome.room_index + 1
            finished = next_index == len(encounter_ids)
            checkpoint = {"phase": "finished" if finished else "between_rooms",
                          "room_index": next_index, "tick": outcome.tick,
                          "last_input_seq": outcome.last_input_seq,
                          "simulator": outcome.simulator_snapshot,
                          "rng": rng.snapshot(),
                          "run_resources": resources}
            result = {"run_id": run["run_id"], "room_index": outcome.room_index,
                      "encounter_id": outcome.encounter_id, "status": "finished" if finished else "ready",
                      "reward": applied, "durable_tick": outcome.tick, "replayed": False}
            conn.execute("""INSERT INTO dungeon_beta_room_rewards
                (run_id,room_index,reward_kind,result_json,committed_at)
                VALUES (?,?,'room',?,?)""",
                (run["run_id"], outcome.room_index, canonical(result), now))
            updated = conn.execute("""UPDATE dungeon_beta_runs SET status=?,room_index=?,
                server_tick=?,durable_tick=?,last_input_seq=?,checkpoint_json=?,
                run_revision=run_revision+1,updated_at=?
                WHERE run_id=? AND status='running' AND room_index=? AND control_epoch=?""",
                ("finished" if finished else "ready", next_index, outcome.tick, outcome.tick,
                 outcome.last_input_seq, canonical(checkpoint), now, run["run_id"],
                 outcome.room_index, outcome.control_epoch))
            if updated.rowcount != 1:
                raise DungeonError("version_conflict", "房间状态已变化")
            if finished:
                rows = conn.execute("""SELECT asset_id FROM dungeon_asset_reservations
                    WHERE purpose='active_run' AND reservation_ref=?""",
                    (run["run_id"],)).fetchall()
                for (item_id,) in rows:
                    release_item(conn, item_id, "active_run", run["run_id"])
            return result
