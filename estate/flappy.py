"""飞鸟游戏：服务端重放输入，结算金币与个人最高分。"""
import json
import secrets

from estate.store import debit, ensure_estate, estate_error, run_action

ENTRY_PRICE = 66
REWARD_PER_POINT = 10
RECORD_BONUS = 6666
FRAME_RATE = 60
MAX_FRAMES = FRAME_RATE * 15 * 60
MAX_FLAPS = 10000
SESSION_SECONDS = 30 * 60
PIPE_GAP = 104
PIPE_WIDTH = 46
PIPE_SPACING = 168


def _next_gap(seed):
    seed = (seed * 1664525 + 1013904223) & 0xffffffff
    return seed, 115 + seed % 146


def replay_game(seed, flaps, frames):
    """返回碰撞时的服务端分数；轨迹未结束或提前结束均拒绝。"""
    seed, gap = _next_gap(seed)
    pipes = [{"x": 358.0, "gap": gap, "scored": False}]
    bird_y = 176.0
    velocity = 0.0
    score = 0
    flap_index = 0
    for frame in range(frames):
        if flap_index < len(flaps) and flaps[flap_index] == frame:
            velocity = -285.0
            flap_index += 1
        velocity += 920 / FRAME_RATE
        bird_y += velocity / FRAME_RATE
        ended = bird_y < 0 or bird_y + 16 >= 388
        if not ended:
            for pipe in pipes:
                pipe["x"] -= (135 + min(score, 10) * 4) / FRAME_RATE
                if not pipe["scored"] and pipe["x"] + PIPE_WIDTH < 72:
                    pipe["scored"] = True
                    score += 1
                if (72 + 16 > pipe["x"] and 72 < pipe["x"] + PIPE_WIDTH
                        and (bird_y < pipe["gap"] - PIPE_GAP / 2
                             or bird_y + 16 > pipe["gap"] + PIPE_GAP / 2)):
                    ended = True
                    break
            if not ended:
                pipes = [pipe for pipe in pipes if pipe["x"] + PIPE_WIDTH > 0]
                if pipes and pipes[-1]["x"] < 320 - PIPE_SPACING:
                    seed, gap = _next_gap(seed)
                    pipes.append({"x": 320.0, "gap": gap, "scored": False})
        if ended:
            if frame != frames - 1:
                raise estate_error(("invalid_trace", "飞鸟游戏轨迹在提交前已结束"))
            return score
    raise estate_error(("invalid_trace", "飞鸟游戏尚未结束"))


def start_flappy(conn, username, request_id, now, adjust_coins):
    def mutate():
        debit(adjust_coins, conn, username, ENTRY_PRICE, "飞鸟游戏入场", request_id)
        conn.execute("UPDATE estate_flappy_runs SET status='abandoned' "
                     "WHERE username=? AND status='active'", (username,))
        session_id = secrets.token_urlsafe(18)
        seed = secrets.randbits(32)
        conn.execute("INSERT INTO estate_flappy_runs(session_id,username,seed,started_at) "
                     "VALUES (?,?,?,?)", (session_id, username, seed, int(now)))
        return {"action": "flappy_start", "session_id": session_id,
                "seed": seed, "cost": ENTRY_PRICE}

    return run_action(conn, username, request_id, "flappy_start", {}, now, mutate)


def finish_flappy(conn, username, request_id, session_id, flaps, frames, now, adjust_coins):
    if (not isinstance(session_id, str) or not isinstance(frames, int)
            or isinstance(frames, bool) or not 1 <= frames <= MAX_FRAMES
            or not isinstance(flaps, list) or not 1 <= len(flaps) <= MAX_FLAPS
            or flaps[0] != 0 or any(type(step) is not int for step in flaps)
            or any(a >= b for a, b in zip(flaps, flaps[1:]))
            or flaps[-1] >= frames):
        raise estate_error(("invalid_trace", "飞鸟游戏轨迹无效"))

    def mutate():
        row = conn.execute(
            "SELECT seed,started_at,status,result_json FROM estate_flappy_runs "
            "WHERE session_id=? AND username=?", (session_id, username)).fetchone()
        if not row:
            raise estate_error(("session_missing", "飞鸟游戏场次不存在"))
        if row[2] == "finished":
            return {**json.loads(row[3]), "session_replayed": True}
        if row[2] != "active":
            raise estate_error(("session_expired", "这场飞鸟游戏已失效"))
        elapsed = int(now) - int(row[1])
        if elapsed > SESSION_SECONDS:
            raise estate_error(("session_expired", "飞鸟游戏已超时"))
        if frames / FRAME_RATE > elapsed + 3:
            raise estate_error(("invalid_trace", "飞鸟游戏进度快于实际时间"))
        score = replay_game(row[0], flaps, frames)
        global_best = conn.execute(
            "SELECT COALESCE(MAX(best_score),0) FROM estate_flappy_scores"
        ).fetchone()[0]
        global_record = score > global_best
        reward = score * REWARD_PER_POINT + (RECORD_BONUS if global_record else 0)
        if reward:
            adjust_coins(conn, username, reward, "estate_flappy_reward",
                         "飞鸟游戏得分奖励", ref=f"estate:{request_id}")
        conn.execute(
            "INSERT INTO estate_flappy_scores(username,best_score,achieved_at) VALUES (?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET best_score=excluded.best_score,"
            "achieved_at=excluded.achieved_at "
            "WHERE excluded.best_score>estate_flappy_scores.best_score",
            (username, score, int(now)))
        result = {"action": "flappy_finish", "session_id": session_id,
                  "score": score, "reward": reward,
                  "record_bonus": RECORD_BONUS if global_record else 0,
                  "global_record": global_record}
        conn.execute("UPDATE estate_flappy_runs SET status='finished',result_json=? "
                     "WHERE session_id=?", (json.dumps(result), session_id))
        return result

    return run_action(conn, username, request_id, "flappy_finish",
                      {"session_id": session_id, "flaps": flaps, "frames": frames}, now, mutate)


def flappy_leaderboard(conn, username, now):
    ensure_estate(conn, username, now)
    rows = conn.execute(
        "SELECT username,best_score FROM estate_flappy_scores "
        "ORDER BY best_score DESC,achieved_at ASC,username ASC LIMIT 20").fetchall()
    own = conn.execute(
        "SELECT best_score,achieved_at FROM estate_flappy_scores WHERE username=?",
        (username,)).fetchone()
    rank = None
    if own:
        rank = 1 + conn.execute(
            "SELECT COUNT(*) FROM estate_flappy_scores WHERE best_score>? "
            "OR (best_score=? AND (achieved_at<? OR "
            "(achieved_at=? AND username<?)))",
            (own[0], own[0], own[1], own[1], username)).fetchone()[0]
    return {"entries": [{"rank": index + 1, "username": name, "score": score}
                        for index, (name, score) in enumerate(rows)],
            "my_rank": rank, "my_best": own[0] if own else 0}
