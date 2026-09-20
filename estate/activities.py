"""小胖庄园的工具、钓鱼与矿场规则。

依赖方向单向：本模块使用 ``estate.store`` 的公开内核，内核不反向依赖本模块。
``make_board`` 与 ``pick_fishing_catch`` 是公开的随机性接缝：服务端用它们生成
棋盘与鱼获，测试按名称替换即可获得确定性。
"""
import json
import random
import secrets

from estate.catalog import (
    BAITS, FISH, FISHING_TREASURES, FISH_RARITY_WEIGHTS, MINERALS, MINING_LEVELS,
    TOOLS, CATCH_BOOST_PER_ROD_LEVEL, CATCH_RARITY_BASE, CATCH_TREASURE_BAIT_BONUS,
    CATCH_TREASURE_CHANCE, CATCH_TREASURE_RARITY_BASE, CATCH_TREASURE_ROD_BONUS,
    FISHING_STEPS, FISHING_STEPS_PER_FRAME, FISHING_TIMEOUT_SECONDS,
    HOLD_PROGRESS_BASE, HOLD_PROGRESS_FORCE_SCALE, HOLD_PROGRESS_GAIN,
    HOLD_TENSION_FORCE_BASE, HOLD_TENSION_GAIN, MINE_BOARD_SIZE, MINE_CELLS,
    MINE_EMPTY_WEIGHT, MINE_EXTRA_CELLS, PATTERN_DIFFICULTY_WEIGHT, PATTERN_MAX,
    PATTERN_MIN, PATTERN_SPAN, PROGRESS_CAUGHT_AT, PROGRESS_START,
    RELEASE_PROGRESS_DROP, RELEASE_PROGRESS_FORCE_BASE, RELEASE_TENSION_DROP,
    TENSION_SNAPPED_AT, TENSION_START, TRACE_MAX_STEPS, TRACE_MIN_STEPS,
    bait_item, collectible_item, fish_item, mineral_item,
)
from estate.store import (
    LEVEL_LOCKED, TOOL_MISSING, award_xp, change_inventory, debit, estate_error,
    load_profile, require_capacity, run_action,
)

# 只保留多处复用的规则；单处使用的文案直接内联在抛出处。
INVALID_TRACE = ("invalid_trace", "钓鱼操作记录无效")
INVALID_CELL = ("invalid_cell", "矿格无效")
RUN_MISSING = ("run_missing", "矿场记录不存在")

# 随机种子取值范围：由 secrets 提供，客户端无法预测。
_SEED_CEILING = 2_000_000_000


def _tool(conn, username, tool_type):
    row = conn.execute(
        "SELECT level,durability FROM estate_tools WHERE username=? AND tool_type=?",
        (username, tool_type),
    ).fetchone()
    return {"level": row[0], "durability": row[1]} if row else None


def _require_tool(conn, username, tool_type, label):
    tool = _tool(conn, username, tool_type)
    if not tool:
        raise estate_error(("tool_missing", f"请先购买{label}"))
    if tool["durability"] < 1:
        raise estate_error(("tool_broken", f"{label}需要修理"))
    return tool


def _require_idle(conn, table, username, message):
    if conn.execute(
        f"SELECT 1 FROM {table} WHERE username=? AND status='active' LIMIT 1",
        (username,),
    ).fetchone():
        raise estate_error(("session_active", message))


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def buy_tool(conn, username, request_id, tool_type, now, adjust_coins):
    tool_type = str(tool_type or "")

    def mutate():
        if tool_type not in TOOLS:
            raise estate_error(("unknown_tool", "工具不存在"))
        if _tool(conn, username, tool_type):
            raise estate_error(("tool_owned", "已经拥有这件工具"))
        profile = load_profile(conn, username)
        rule = TOOLS[tool_type][1]
        if profile["level"] < rule["unlock_level"]:
            raise estate_error(LEVEL_LOCKED)
        balance = debit(adjust_coins, conn, username, rule["price"],
                        f"小胖庄园购买：{rule['name']}", request_id)
        conn.execute(
            "INSERT INTO estate_tools(username,tool_type,level,durability,updated_at) "
            "VALUES (?,?,?,?,?)",
            (username, tool_type, 1, rule["max_durability"], int(now)),
        )
        return {"action": "buy_tool", "tool_type": tool_type, "level": 1,
                "durability": rule["max_durability"], "coins": balance}

    return run_action(conn, username, request_id, "buy_tool", {"tool_type": tool_type}, now, mutate)


def upgrade_tool(conn, username, request_id, tool_type, now, adjust_coins):
    tool_type = str(tool_type or "")

    def mutate():
        current = _tool(conn, username, tool_type)
        if not current or tool_type not in TOOLS:
            raise estate_error(TOOL_MISSING)
        rule = TOOLS[tool_type].get(current["level"])
        next_level = current["level"] + 1
        target = TOOLS[tool_type].get(next_level)
        if not rule or not target or rule["upgrade_price"] is None:
            raise estate_error(("max_level", "工具已达到最高等级"))
        if load_profile(conn, username)["level"] < target["unlock_level"]:
            raise estate_error(LEVEL_LOCKED)
        balance = debit(adjust_coins, conn, username, rule["upgrade_price"],
                        f"小胖庄园升级：{target['name']}", request_id)
        conn.execute(
            "UPDATE estate_tools SET level=?,durability=?,updated_at=? "
            "WHERE username=? AND tool_type=?",
            (next_level, target["max_durability"], int(now), username, tool_type),
        )
        return {"action": "upgrade_tool", "tool_type": tool_type,
                "level": next_level, "durability": target["max_durability"],
                "coins": balance}

    return run_action(conn, username, request_id, "upgrade_tool", {"tool_type": tool_type}, now, mutate)


def repair_tool(conn, username, request_id, tool_type, now, adjust_coins):
    tool_type = str(tool_type or "")

    def mutate():
        current = _tool(conn, username, tool_type)
        if not current or tool_type not in TOOLS:
            raise estate_error(TOOL_MISSING)
        rule = TOOLS[tool_type][current["level"]]
        if current["durability"] >= rule["max_durability"]:
            raise estate_error(("repair_unneeded", "工具耐久已满"))
        missing = rule["max_durability"] - current["durability"]
        cost = max(1.0, round(rule["repair_price"] * missing / rule["max_durability"], 2))
        balance = debit(adjust_coins, conn, username, cost,
                        f"小胖庄园修理：{rule['name']}", request_id)
        conn.execute(
            "UPDATE estate_tools SET durability=?,updated_at=? "
            "WHERE username=? AND tool_type=?",
            (rule["max_durability"], int(now), username, tool_type),
        )
        return {"action": "repair_tool", "tool_type": tool_type,
                "durability": rule["max_durability"], "cost": cost, "coins": balance}

    return run_action(conn, username, request_id, "repair_tool", {"tool_type": tool_type}, now, mutate)


# --------------------------------------------------------------------------
# 钓鱼
# --------------------------------------------------------------------------


def pick_fishing_catch(rng, bait, rod):
    """从服务端目录选择鱼或极稀有收藏物。

    公开接缝：测试替换本函数即可让鱼获确定，无需关心权重细节。
    """
    rarity_cap = CATCH_RARITY_BASE + bait["rarity_bonus"] + rod["level"]
    treasures = [
        (key, value) for key, value in FISHING_TREASURES.items()
        if value["required_rod_level"] <= rod["level"]
        and value["rarity"] <= CATCH_TREASURE_RARITY_BASE + bait["rarity_bonus"] + rod["level"]
    ]
    treasure_chance = (CATCH_TREASURE_CHANCE
                       + bait["rarity_bonus"] * CATCH_TREASURE_BAIT_BONUS
                       + max(0, rod["level"] - 1) * CATCH_TREASURE_ROD_BONUS)
    if treasures and rng.random() < treasure_chance:
        treasure_id = rng.choices(
            [key for key, _ in treasures],
            weights=[value["weight"] for _, value in treasures], k=1,
        )[0]
        return f"treasure:{treasure_id}", FISHING_TREASURES[treasure_id]
    allowed = [key for key, value in FISH.items() if value["rarity"] <= rarity_cap]
    boost = 1 + CATCH_BOOST_PER_ROD_LEVEL * (rod["level"] - 1) + bait["rarity_bonus"]
    weights = [FISH_RARITY_WEIGHTS[FISH[key]["rarity"]] * boost ** (FISH[key]["rarity"] - 1)
               for key in allowed]
    fish_id = rng.choices(allowed, weights=weights, k=1)[0]
    return fish_id, FISH[fish_id]


def start_fishing(conn, username, request_id, bait_id, now):
    bait_id = str(bait_id or "")

    def mutate():
        profile = load_profile(conn, username)
        rod = _require_tool(conn, username, "rod", "鱼竿")
        bait = BAITS.get(bait_id)
        if not bait:
            raise estate_error(("unknown_item", "鱼饵不存在"))
        if profile["level"] < bait["unlock_level"]:
            raise estate_error(LEVEL_LOCKED)
        _require_idle(conn, "estate_fishing_sessions", username, "已有一局钓鱼正在进行")
        # 消耗的一份鱼饵腾出一格，满仓时仍可用现有鱼饵钓鱼。
        require_capacity(conn, username, profile, 0)
        change_inventory(conn, username, bait_item(bait_id), -1)
        conn.execute("UPDATE estate_tools SET durability=durability-1,updated_at=? "
                     "WHERE username=? AND tool_type='rod'", (int(now), username))
        seed = secrets.randbelow(_SEED_CEILING)
        rng = random.Random(seed)
        fish_id, catch = pick_fishing_catch(rng, bait, rod)
        difficulty = catch["difficulty"]
        pattern = [
            round(min(PATTERN_MAX, max(PATTERN_MIN,
                      rng.random() * PATTERN_SPAN + difficulty * PATTERN_DIFFICULTY_WEIGHT)), 3)
            for _ in range(FISHING_STEPS)
        ]
        session_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO estate_fishing_sessions"
            "(session_id,username,bait_id,rod_level,fish_id,seed,pattern_json,started_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, username, bait_id, rod["level"], fish_id, seed,
             json.dumps(pattern), int(now), int(now) + FISHING_TIMEOUT_SECONDS),
        )
        conn.execute("UPDATE estate_profiles SET reserved_capacity=reserved_capacity+1 "
                     "WHERE username=?", (username,))
        return {"action": "start_fishing", "session_id": session_id,
                "bait_id": bait_id,
                "fish_name": "水下的鱼影", "pattern": pattern,
                "difficulty": difficulty, "duration_limit": FISHING_STEPS,
                "rod_level": rod["level"]}

    return run_action(conn, username, request_id, "start_fishing", {"bait_id": bait_id}, now, mutate)


def simulate_fishing(trace, pattern, rod_level):
    """按服务端保存的张力曲线重放客户端操作序列。

    客户端只提交按住/松开的布尔序列，进度与张力全部由服务端复算，
    因此改包无法伪造鱼获。
    """
    if not isinstance(trace, list) or not TRACE_MIN_STEPS <= len(trace) <= TRACE_MAX_STEPS:
        raise estate_error(INVALID_TRACE)
    if any(type(value) is not bool for value in trace):
        raise estate_error(INVALID_TRACE)
    tension, progress = TENSION_START, PROGRESS_START
    factor = TOOLS["rod"][rod_level]["tension_factor"]
    peak = tension
    for index, held in enumerate(trace):
        force = pattern[min(len(pattern) - 1, index // FISHING_STEPS_PER_FRAME)]
        if held:
            tension += HOLD_TENSION_GAIN * (HOLD_TENSION_FORCE_BASE + force) * factor
            progress += HOLD_PROGRESS_GAIN * (HOLD_PROGRESS_BASE - force * HOLD_PROGRESS_FORCE_SCALE)
        else:
            tension -= RELEASE_TENSION_DROP
            progress -= RELEASE_PROGRESS_DROP * (RELEASE_PROGRESS_FORCE_BASE + force)
        tension = max(0, tension)
        progress = max(0, progress)
        peak = max(peak, tension)
        if tension >= TENSION_SNAPPED_AT:
            return {"outcome": "snapped", "progress": progress, "peak_tension": peak}
        if progress >= PROGRESS_CAUGHT_AT:
            return {"outcome": "caught", "progress": 1, "peak_tension": peak}
    return {"outcome": "escaped", "progress": progress, "peak_tension": peak}


def finish_fishing(conn, username, request_id, session_id, trace, now):
    session_id = str(session_id or "")

    def mutate():
        row = conn.execute(
            "SELECT fish_id,rod_level,pattern_json,expires_at,status,result_json "
            "FROM estate_fishing_sessions WHERE session_id=? AND username=?",
            (session_id, username),
        ).fetchone()
        if not row:
            raise estate_error(("session_missing", "钓鱼会话不存在"))
        if row[4] != "active":
            return {**json.loads(row[5]), "session_replayed": True}
        result = ({"outcome": "expired", "progress": 0, "peak_tension": 0}
                  if int(now) > row[3] else simulate_fishing(trace, json.loads(row[2]), row[1]))
        payload = {**result, "action": "finish_fishing", "session_id": session_id}
        if result["outcome"] == "caught":
            if row[0].startswith("treasure:"):
                catch_id = row[0].split(":", 1)[1]
                catch = FISHING_TREASURES[catch_id]
                item = collectible_item(catch_id)
                payload.update({"catch_kind": "collectible", "collectible_id": catch_id})
            else:
                catch_id = row[0]
                catch = FISH[catch_id]
                item = fish_item(catch_id)
                payload.update({"catch_kind": "fish", "fish_id": catch_id})
            change_inventory(conn, username, item, 1)
            payload.update({"fish_name": catch["name"], "catch_name": catch["name"],
                            "quantity": 1, "xp_awarded": catch["xp"],
                            "rarity": catch["rarity"], "difficulty": catch["difficulty"]})
            payload["level"] = award_xp(conn, username, catch["xp"])
        conn.execute("UPDATE estate_profiles SET reserved_capacity=max(0,reserved_capacity-1) "
                     "WHERE username=?", (username,))
        conn.execute("UPDATE estate_fishing_sessions SET status='finished',result_json=? "
                     "WHERE session_id=?", (json.dumps(payload, ensure_ascii=False), session_id))
        return payload

    return run_action(conn, username, request_id, "finish_fishing",
                      {"session_id": session_id, "trace": trace}, now, mutate)


# --------------------------------------------------------------------------
# 矿场
# --------------------------------------------------------------------------


def make_board(seed, mine_level):
    """按固定种子生成隐藏矿壁。

    公开接缝：测试替换本函数即可指定棋盘，无需重现权重抽样。
    """
    rng = random.Random(seed)
    minerals = list(MINERALS)
    rule = MINING_LEVELS[mine_level]
    board = rng.choices(["empty", *minerals],
                        weights=[MINE_EMPTY_WEIGHT, *rule["weights"]], k=MINE_CELLS)
    extra_cells = rng.sample(range(MINE_CELLS), MINE_EXTRA_CELLS)
    for index in extra_cells:
        board[index] = "extra"
    bomb_cells = rng.sample([index for index in range(MINE_CELLS) if index not in extra_cells],
                            rule["bombs"])
    for index in bomb_cells:
        board[index] = "bomb"
    return board


def start_mining(conn, username, request_id, mine_level, now):
    try:
        mine_level = int(mine_level)
    except (TypeError, ValueError):
        raise estate_error(("invalid_mine", "矿层无效")) from None

    def mutate():
        profile = load_profile(conn, username)
        pickaxe = _require_tool(conn, username, "pickaxe", "矿镐")
        rule = MINING_LEVELS.get(mine_level)
        if not rule or profile["level"] < rule["unlock_level"] or pickaxe["level"] < mine_level:
            raise estate_error(("level_locked", "该矿层尚未解锁"))
        _require_idle(conn, "estate_mining_runs", username, "已有一次挖矿正在进行")
        strikes = TOOLS["pickaxe"][pickaxe["level"]]["strikes"]
        # 两个 +2 格各自也消耗一镐，最多产出 strikes + 2 份矿物。
        reserved_slots = strikes + MINE_EXTRA_CELLS
        require_capacity(conn, username, profile, reserved_slots)
        seed = secrets.randbelow(_SEED_CEILING)
        board = make_board(seed, mine_level)
        run_id = secrets.token_urlsafe(12)
        conn.execute("UPDATE estate_tools SET durability=durability-1,updated_at=? "
                     "WHERE username=? AND tool_type='pickaxe'", (int(now), username))
        conn.execute("UPDATE estate_profiles SET reserved_capacity=reserved_capacity+? "
                     "WHERE username=?", (reserved_slots, username))
        conn.execute(
            "INSERT INTO estate_mining_runs"
            "(run_id,username,mine_level,pickaxe_level,seed,board_json,strikes_left,started_at,reserved_slots) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, username, mine_level, pickaxe["level"], seed,
             json.dumps(board), strikes, int(now), reserved_slots),
        )
        return {"action": "start_mining", "run_id": run_id,
                "mine_level": mine_level, "mine_name": rule["name"],
                "strikes_left": strikes, "size": MINE_BOARD_SIZE,
                "revealed": [], "loot": {}}

    return run_action(conn, username, request_id, "start_mining",
                      {"mine_level": mine_level}, now, mutate)


def _finish_run(conn, username, run_id, loot, reason="completed"):
    reserved_slots = conn.execute(
        "SELECT reserved_slots FROM estate_mining_runs WHERE run_id=? AND username=?",
        (run_id, username),
    ).fetchone()[0]
    # 老存档保留原来的预留格数；额外产物必须仍然能放入仓库。
    require_capacity(conn, username, load_profile(conn, username),
                     max(0, sum(loot.values()) - reserved_slots))
    for mineral_id, quantity in loot.items():
        change_inventory(conn, username, mineral_item(mineral_id), quantity)
    conn.execute("UPDATE estate_profiles SET reserved_capacity=max(0,reserved_capacity-?) "
                 "WHERE username=?", (reserved_slots, username))
    xp = sum(MINERALS[key]["xp"] * count for key, count in loot.items())
    level = award_xp(conn, username, xp)
    result = {"action": "finish_mining", "run_id": run_id, "loot": loot,
              "finished": True, "reason": reason, "xp_awarded": xp, "level": level}
    conn.execute("UPDATE estate_mining_runs SET status='finished',result_json=? WHERE run_id=?",
                 (json.dumps(result, ensure_ascii=False), run_id))
    return result


def mine_cell(conn, username, request_id, run_id, cell, now):
    run_id = str(run_id or "")
    if isinstance(cell, bool):
        raise estate_error(INVALID_CELL)
    try:
        cell = int(cell)
    except (TypeError, ValueError):
        raise estate_error(INVALID_CELL) from None
    if not 0 <= cell < MINE_CELLS:
        raise estate_error(INVALID_CELL)

    def mutate():
        row = conn.execute(
            "SELECT board_json,revealed_json,loot_json,strikes_left,status "
            "FROM estate_mining_runs WHERE run_id=? AND username=?",
            (run_id, username),
        ).fetchone()
        if not row:
            raise estate_error(RUN_MISSING)
        if row[4] != "active":
            raise estate_error(("run_finished", "本次挖矿已经结束"))
        board, revealed, loot = json.loads(row[0]), json.loads(row[1]), json.loads(row[2])
        if cell in revealed:
            raise estate_error(("cell_revealed", "这个矿格已经敲过"))
        revealed.append(cell)
        outcome = board[cell]
        strikes = row[3] - 1
        exploded = outcome == "bomb"
        if exploded:
            strikes = 0
        elif outcome == "extra":
            strikes += MINE_EXTRA_CELLS
        elif outcome in MINERALS:
            loot[outcome] = loot.get(outcome, 0) + 1
        conn.execute(
            "UPDATE estate_mining_runs SET revealed_json=?,loot_json=?,strikes_left=? WHERE run_id=?",
            (json.dumps(revealed), json.dumps(loot), strikes, run_id),
        )
        payload = {"action": "mine_cell", "run_id": run_id, "cell": cell,
                   "outcome": outcome, "strikes_left": strikes, "loot": loot,
                   "revealed": revealed, "finished": exploded or strikes <= 0,
                   "exploded": exploded}
        if payload["finished"]:
            payload["result"] = _finish_run(
                conn, username, run_id, loot, "bomb" if exploded else "exhausted")
        return payload

    return run_action(conn, username, request_id, "mine_cell",
                      {"run_id": run_id, "cell": cell}, now, mutate)


def finish_mining(conn, username, request_id, run_id, now):
    run_id = str(run_id or "")

    def mutate():
        row = conn.execute(
            "SELECT loot_json,status,result_json FROM estate_mining_runs "
            "WHERE run_id=? AND username=?", (run_id, username),
        ).fetchone()
        if not row:
            raise estate_error(RUN_MISSING)
        if row[1] != "active":
            return {**json.loads(row[2]), "session_replayed": True}
        return _finish_run(conn, username, run_id, json.loads(row[0]), "left")

    return run_action(conn, username, request_id, "finish_mining", {"run_id": run_id}, now, mutate)
