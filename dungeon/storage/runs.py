"""Small SQLite helpers for the internal run host."""

import json

from dungeon.domain.errors import DungeonError


COLUMNS = ("run_id", "user_id", "route_id", "status", "room_index", "server_tick",
           "durable_tick", "run_revision", "control_epoch", "last_input_seq",
           "ruleset_id", "ruleset_hash", "simulation_version", "save_version",
           "initial_json", "checkpoint_json", "created_at", "updated_at")
ACTIVE = ("ready", "running", "paused")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def load_run(conn, run_id, user_id=None):
    row = conn.execute(f"SELECT {','.join(COLUMNS)} FROM dungeon_beta_runs WHERE run_id=?",
                       (run_id,)).fetchone()
    if row is None:
        raise DungeonError("not_found", "挑战不存在")
    run = dict(zip(COLUMNS, row))
    if user_id is not None and run["user_id"] != user_id:
        raise DungeonError("not_found", "挑战不存在")
    try:
        run["initial"] = json.loads(run["initial_json"])
        run["checkpoint"] = json.loads(run["checkpoint_json"])
    except (ValueError, TypeError) as error:
        raise DungeonError("invalid_save", "挑战存档异常") from error
    return run


def active_for_user(conn, user_id):
    return conn.execute("""SELECT run_id FROM dungeon_beta_runs
        WHERE user_id=? AND status IN ('ready','running','paused')""",
                        (user_id,)).fetchone()
