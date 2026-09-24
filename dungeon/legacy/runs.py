"""持久化挑战：短事务起局，事务外补算，按 revision 原子提交。"""

import hashlib
import json
import secrets
from copy import deepcopy
from uuid import uuid4

from dungeon.legacy.catalog import (CATALOG, DungeonConfigError,
                                    prerequisite_key, validate_catalog)
from dungeon.legacy.combat import (BattleSnapshot, CombatError, advance,
                                   make_battle_snapshot, start)
from dungeon.legacy.receipts import (REQUEST_ID_PATTERN, load_receipt,
                                     request_digest, save_receipt)
from dungeon.legacy.service import DungeonError, dungeon_state
from dungeon.storage.assets import ensure_available


RUN_COLUMNS = ("battle_id", "username", "challenge_id", "difficulty_id", "status",
               "snapshot_json", "snapshot_hash", "checkpoint_json", "result_json",
               "reward_token", "revision", "sim_anchor_us", "wall_anchor_ms",
               "playback_rate", "created_at", "updated_at")
ID_PATTERN = REQUEST_ID_PATTERN
MAX_EVENT_PAGE = 200
MAX_EVENT_BYTES = 128 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _id(value, label):
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise DungeonError("invalid_request", f"{label}无效")
    return value


def _version(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DungeonError("invalid_request", f"{label}无效")
    return value


def _load(conn, username, battle_id):
    row = conn.execute(f"SELECT {','.join(RUN_COLUMNS)} FROM dungeon_runs "
                       "WHERE username=? AND battle_id=?", (username, battle_id)).fetchone()
    if row is None:
        raise DungeonError("not_found", "挑战不存在")
    run = dict(zip(RUN_COLUMNS, row))
    run["checkpoint"] = json.loads(run["checkpoint_json"])
    run["result"] = json.loads(run["result_json"]) if run["result_json"] else None
    return run


def _public(run):
    checkpoint = run["checkpoint"]
    return {"battle_id": run["battle_id"], "challenge_id": run["challenge_id"],
            "difficulty_id": run["difficulty_id"], "status": run["status"],
            "revision": run["revision"], "playback_rate": run["playback_rate"],
            "sim_time_us": checkpoint["sim_time_us"], "hp": checkpoint["hp"],
            "phase_index": checkpoint["phase_index"],
            "last_sequence": checkpoint["sequence_id"], "result": run["result"]}


def _events(conn, battle_id, after_sequence):
    rows = conn.execute("""SELECT sequence_id,event_json FROM dungeon_events
        WHERE battle_id=? AND sequence_id>? ORDER BY sequence_id LIMIT ?""",
        (battle_id, after_sequence, MAX_EVENT_PAGE + 1)).fetchall()
    page, size = [], 0
    for sequence, raw in rows[:MAX_EVENT_PAGE]:
        event_size = len(raw.encode("utf-8"))
        if page and size + event_size > MAX_EVENT_BYTES:
            break
        if event_size > MAX_EVENT_BYTES:
            raise DungeonError("invalid_save", "战斗事件过大")
        page.append(json.loads(raw))
        size += event_size
    cursor = page[-1]["sequence_id"] if page else after_sequence
    has_more = bool(conn.execute("""SELECT 1 FROM dungeon_events
        WHERE battle_id=? AND sequence_id>? LIMIT 1""", (battle_id, cursor)).fetchone())
    return {"events": page, "next_cursor": cursor, "has_more": has_more}


def _bump_profile(conn, username, now_ms):
    conn.execute("""UPDATE dungeon_profiles SET version=version+1,updated_at=?
        WHERE username=?""", (now_ms // 1000, username))
    return conn.execute("SELECT version FROM dungeon_profiles WHERE username=?",
                        (username,)).fetchone()[0]


def start_run(conn, username, request_id, challenge_id, difficulty_id,
              expected_version, now_ms, *, seed=None, catalog=CATALOG):
    """调用者持有 BEGIN IMMEDIATE；起局和回执必须一起提交。"""
    _id(request_id, "请求编号")
    _id(challenge_id, "挑战编号")
    _id(difficulty_id, "难度编号")
    _version(expected_version, "存档版本")
    payload = {"challenge_id": challenge_id, "difficulty_id": difficulty_id,
               "expected_version": expected_version}
    digest = request_digest("dungeon_start", payload)
    state = dungeon_state(conn, username, now_ms // 1000, catalog)
    replay = load_receipt(conn, username, request_id, "dungeon_start", digest)
    if replay is not None:
        replay["battle"] = _public(_load(conn, username, replay["battle"]["battle_id"]))
        return replay
    if state["profile_version"] != expected_version:
        raise DungeonError("version_conflict", "地下城存档已更新，请刷新后重试")
    try:
        validate_catalog(catalog)
    except DungeonConfigError as error:
        raise DungeonError("invalid_config", "地下城配置无效") from error
    challenge = next((row for row in catalog["challenges"]
                      if row["challenge_id"] == challenge_id
                      and row["difficulty_id"] == difficulty_id), None)
    if challenge is None:
        raise DungeonError("not_found", "挑战不存在")
    progress = conn.execute("""SELECT unlocked FROM dungeon_progress
        WHERE username=? AND challenge_id=? AND difficulty_id=?""",
        (username, challenge_id, difficulty_id)).fetchone()
    if progress is None or not progress[0]:
        raise DungeonError("challenge_locked", "挑战尚未解锁")
    if state["pending_count"]:
        raise DungeonError("pending_items", "请先领取待领取装备")
    if state["active_job"]:
        raise DungeonError("active_job", "已有进行中的挑战或扫荡")
    equipped_ids = set(state["loadout"].values())
    for item_id in equipped_ids:
        ensure_available(conn, username, item_id)
    equipment = [item for item in state["items"] if item["item_id"] in equipped_ids]
    battle_id = uuid4().hex
    snapshot = make_battle_snapshot(challenge_id, difficulty_id, state["stats"]["values"],
                                    equipment, state["stats"]["sources"],
                                    seed if seed is not None else secrets.token_bytes(32),
                                    catalog, battle_id)
    initial = start(snapshot)
    conn.execute("""INSERT INTO dungeon_runs
        (battle_id,username,challenge_id,difficulty_id,status,snapshot_json,snapshot_hash,
         checkpoint_json,revision,sim_anchor_us,wall_anchor_ms,playback_rate,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,1,0,?,1,?,?)""",
        (battle_id, username, challenge_id, difficulty_id, "running", snapshot.snapshot_json,
         snapshot.snapshot_hash, _json(initial.checkpoint), now_ms, now_ms // 1000,
         now_ms // 1000))
    _insert_events(conn, battle_id, initial.events)
    conn.execute("INSERT INTO dungeon_active_jobs VALUES (?,?,?)",
                 (username, "battle", battle_id))
    version = _bump_profile(conn, username, now_ms)
    run = _load(conn, username, battle_id)
    result = {"request_id": request_id, "battle": _public(run),
              "profile_version": version, "changed": True, "replayed": False}
    save_receipt(conn, username, request_id, "dungeon_start", digest, result, now_ms // 1000)
    return result


def _insert_events(conn, battle_id, events):
    conn.executemany("""INSERT INTO dungeon_events
        (battle_id,sequence_id,battle_time_us,event_json) VALUES (?,?,?,?)""",
        ((battle_id, event["sequence_id"], event["battle_time_us"], _json(event))
         for event in events))


def _target(run, now_ms):
    checkpoint_time = run["checkpoint"]["sim_time_us"]
    if run["status"] == "paused":
        return checkpoint_time
    return max(checkpoint_time, run["sim_anchor_us"] +
               max(0, now_ms - run["wall_anchor_ms"]) * 1000 * run["playback_rate"])


def _roll_reward_v1(data, battle_id):
    """纯掉落计算；旧快照缺版本字段时仍按 v1 重放。"""
    reward = data["reward_table"]
    seed = bytes.fromhex(data["seed_hex"])
    total_weight = sum(entry["weight"] for entry in reward["entries"])
    items = []
    for index in range(reward["rolls"]):
        draw = int.from_bytes(hashlib.sha256(seed + b":reward:" +
                                             index.to_bytes(4, "big")).digest(), "big") % total_weight
        selected = None
        for entry in reward["entries"]:
            if draw < entry["weight"]:
                selected = entry["item"]
                break
            draw -= entry["weight"]
        item_id = hashlib.sha256(f"{battle_id}:{index}:item".encode()).hexdigest()[:32]
        item = {"item_id": item_id, "template_id": selected["template_id"],
                "template_version": data["config_version"], "slot": selected["slot"],
                "name": selected["name"],
                "visual_id": selected.get("visual_id", selected["template_id"]),
                "quality": selected["quality"], "stats": selected["stats"],
                "tags": selected["tags"], "effects": selected["effects"],
                "affixes": [], "sell_coins": selected.get("sell_coins", 0)}
        items.append(item)
    return items


REWARD_ROLLERS = {1: _roll_reward_v1}


def _award(conn, run, now_ms, adjust_coins):
    data = json.loads(run["snapshot_json"])
    reward = data["reward_table"]
    roller = REWARD_ROLLERS.get(data.get("reward_rng_version", 1))
    if roller is None:
        raise DungeonError("unsupported_version", "挑战奖励规则版本暂不支持")
    token = hashlib.sha256(f"{run['username'].casefold()}:{run['battle_id']}".encode()).hexdigest()
    capacity = conn.execute("SELECT bag_capacity FROM dungeon_profiles WHERE username=?",
                            (run["username"],)).fetchone()[0]
    occupied = conn.execute("""SELECT COUNT(*) FROM dungeon_items
        WHERE owner=? AND location='bag'""", (run["username"],)).fetchone()[0]
    items = roller(data, run["battle_id"])
    for item in items:
        item["location"] = "bag" if occupied < capacity else "pending"
        occupied += item["location"] == "bag"
    payload = {"reward_token": token, "coins": reward["coins"], "items": items}
    conn.execute("""INSERT INTO dungeon_rewards
        (reward_token,username,battle_id,payload_json,created_at) VALUES (?,?,?,?,?)""",
        (token, run["username"], run["battle_id"], _json(payload), now_ms // 1000))
    if reward["coins"]:
        adjust_coins(conn, run["username"], reward["coins"], "dungeon_reward",
                     "地下城挑战奖励", ref=f"dungeon:{run['battle_id']}")
    for item in items:
        conn.execute("""INSERT INTO dungeon_items
            (item_id,owner,template_id,template_version,display_name,visual_id,
             slot,quality,stats_json,tags_json,effects_json,affixes_json,sell_coins,location,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["item_id"], run["username"], item["template_id"], item["template_version"],
             item["name"], item["visual_id"], item["slot"], item["quality"],
             _json(item["stats"]), _json(item["tags"]),
             _json(item["effects"]), _json(item["affixes"]), item["sell_coins"],
             item["location"], now_ms // 1000))
    conn.execute("""UPDATE dungeon_progress
        SET clear_count=clear_count+1,first_clear_at=COALESCE(first_clear_at,?)
        WHERE username=? AND challenge_id=? AND difficulty_id=?""",
        (now_ms // 1000, run["username"], run["challenge_id"], run["difficulty_id"]))
    for rule in data["unlock_rules"]:
        if not rule["requires"]:
            continue
        if all(conn.execute("""SELECT 1 FROM dungeon_progress
                WHERE username=? AND challenge_id=? AND difficulty_id=? AND clear_count>0""",
                (run["username"], *prerequisite_key(required, rule["difficulty_id"]))).fetchone()
               for required in rule["requires"]):
            conn.execute("""INSERT INTO dungeon_progress
                (username,challenge_id,difficulty_id,unlocked) VALUES (?,?,?,1)
                ON CONFLICT(username,challenge_id,difficulty_id) DO UPDATE SET unlocked=1""",
                (run["username"], rule["challenge_id"], rule["difficulty_id"]))
    return payload


def _write_step(conn, run, step, now_ms, adjust_coins, command=None, rate=None,
                simulation_error=None):
    checkpoint = deepcopy(step.checkpoint if step else run["checkpoint"])
    events = list(step.events) if step else []
    status = run["status"]
    result = run["result"]
    token = run["reward_token"]
    playback_rate = run["playback_rate"]
    anchor = run["sim_anchor_us"]
    wall_anchor = run["wall_anchor_ms"]
    if simulation_error:
        status = "error"
        result = {"outcome": "error", "error_code": simulation_error.code,
                  "duration_us": checkpoint["sim_time_us"],
                  "player_remaining_hp": checkpoint["hp"]["player:0"],
                  "damage_dealt": checkpoint["damage_dealt"],
                  "damage_taken": checkpoint["damage_taken"],
                  "coins_gained": 0, "items": []}
    elif step and step.result:
        status = "settled"
        result = {**step.result, "coins_gained": 0, "items": []}
        if step.result["outcome"] == "victory":
            reward = _award(conn, run, now_ms, adjust_coins)
            result.update({"coins_gained": reward["coins"], "items": reward["items"],
                           "reward_token": reward["reward_token"]})
            token = reward["reward_token"]
    elif command == "abandon":
        status = "abandoned"
        result = {"outcome": "abandoned", "duration_us": checkpoint["sim_time_us"],
                  "player_remaining_hp": checkpoint["hp"]["player:0"],
                  "damage_dealt": checkpoint["damage_dealt"],
                  "damage_taken": checkpoint["damage_taken"],
                  "event_count": checkpoint["sequence_id"],
                  "coins_gained": 0, "items": []}
    elif command == "pause" and status == "running":
        status = "paused"
    elif command == "resume" and status == "paused":
        status = "running"
    elif command == "set_rate":
        playback_rate = rate
    if status in ("abandoned", "error") and status != run["status"]:
        checkpoint["sequence_id"] += 1
        events.append({"battle_id": run["battle_id"],
                       "sequence_id": checkpoint["sequence_id"],
                       "battle_time_us": checkpoint["sim_time_us"],
                       "event_type": "BattleEnded", "source": None, "target": None,
                       "value": result["outcome"]})
        result["event_count"] = checkpoint["sequence_id"]
    if command in ("pause", "resume", "set_rate") and status in ("running", "paused"):
        anchor = checkpoint["sim_time_us"]
        wall_anchor = now_ms
    changed = bool((events or checkpoint != run["checkpoint"])
                   or status != run["status"] or playback_rate != run["playback_rate"])
    if not changed:
        return run, False
    if events:
        _insert_events(conn, run["battle_id"], events)
    if status in ("settled", "abandoned", "error"):
        conn.execute("""DELETE FROM dungeon_active_jobs
            WHERE username=? AND job_kind='battle' AND job_id=?""",
            (run["username"], run["battle_id"]))
    conn.execute("""UPDATE dungeon_runs SET status=?,checkpoint_json=?,result_json=?,
        reward_token=?,revision=revision+1,sim_anchor_us=?,wall_anchor_ms=?,
        playback_rate=?,updated_at=? WHERE battle_id=? AND username=? AND revision=?""",
        (status, _json(checkpoint), _json(result) if result else None, token,
         anchor, wall_anchor, playback_rate, now_ms // 1000,
         run["battle_id"], run["username"], run["revision"]))
    _bump_profile(conn, run["username"], now_ms)
    return _load(conn, run["username"], run["battle_id"]), True


def progress_run(database, username, battle_id, now_ms, adjust_coins, *,
                 after_sequence=0, command=None, request_id=None,
                 expected_revision=None, rate=None):
    """读取/纯模拟/条件提交；同步允许并发重试，控制要求客户端 revision。"""
    _id(battle_id, "挑战编号")
    if (isinstance(after_sequence, bool) or not isinstance(after_sequence, int)
            or not 0 <= after_sequence <= 2**63 - 1):
        raise DungeonError("invalid_request", "事件游标无效")
    if command is not None:
        _id(request_id, "请求编号")
        _version(expected_revision, "挑战版本")
        if command not in ("pause", "resume", "set_rate", "abandon"):
            raise DungeonError("invalid_request", "挑战控制指令无效")
        if command == "set_rate" and (isinstance(rate, bool) or rate not in (1, 2)):
            raise DungeonError("invalid_request", "倍速参数无效")
        if command != "set_rate" and rate is not None:
            raise DungeonError("invalid_request", "此指令不接受倍速参数")
    payload = {"battle_id": battle_id, "command": command,
               "expected_revision": expected_revision, "rate": rate}
    digest = request_digest("dungeon_control", payload) if command is not None else None
    for _ in range(3):
        with database() as conn:
            run = _load(conn, username, battle_id)
            if command is not None:
                replay = load_receipt(conn, username, request_id, "dungeon_control", digest)
                if replay is not None:
                    replay["battle"] = _public(run)
                    return replay
        if command is not None and run["revision"] != expected_revision:
            raise DungeonError("state_conflict", "挑战状态已更新，请刷新后重试")
        if run["status"] in ("settled", "abandoned", "error"):
            if command is not None:
                raise DungeonError("state_conflict", "挑战已经结束")
            with database() as conn:
                current = _load(conn, username, battle_id)
                return {"battle": _public(current), **_events(conn, battle_id, after_sequence),
                        "changed": False}
        snapshot = BattleSnapshot(run["snapshot_json"], run["snapshot_hash"])
        step, simulation_error = None, None
        if run["status"] == "running":
            try:
                step = advance(snapshot, run["checkpoint"], _target(run, now_ms))
            except CombatError as error:
                if error.code == "unsupported_version":
                    raise DungeonError("unsupported_version", "挑战规则版本暂不支持") from error
                simulation_error = error
        with database() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _load(conn, username, battle_id)
            if command is not None:
                replay = load_receipt(conn, username, request_id, "dungeon_control", digest)
                if replay is not None:
                    replay["battle"] = _public(current)
                    return replay
            if current["revision"] != run["revision"]:
                if command is not None:
                    raise DungeonError("state_conflict", "挑战状态已更新，请刷新后重试")
                continue
            current, changed = _write_step(conn, current, step, now_ms, adjust_coins,
                                           command, rate, simulation_error)
            if command is not None:
                result = {"request_id": request_id, "battle": _public(current),
                          "changed": changed, "replayed": False}
                save_receipt(conn, username, request_id, "dungeon_control", digest,
                             result, now_ms // 1000)
                return result
            return {"battle": _public(current), **_events(conn, battle_id, after_sequence),
                    "changed": changed}
    raise DungeonError("state_conflict", "挑战状态已更新，请刷新后重试")


def read_run(conn, username, battle_id):
    _id(battle_id, "挑战编号")
    return _public(_load(conn, username, battle_id))
