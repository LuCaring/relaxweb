#!/usr/bin/env python3
"""飞行棋引擎单元测试：python3 tests/test_ludo.py

只测纯逻辑（不发网络请求）：
  - 棋盘几何：52 格主圈、起飞格相距、终点跑道映射（games/ludo.py 纯函数）
  - 行走判定：反弹、同色跳格、飞行捷径、可动飞机、撞机
  - 房间流程：起飞门槛、掷 6 连投与三个 6 受罚、完赛结算（冠军通吃 /
    按名次递增）、中途离桌完赛、随机完整对局
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games import randomness  # noqa: E402
from games.base import create_room  # noqa: E402
from games.ludo import (  # noqa: E402
    ENTRY, GOAL, LOOP, LOOP_STEPS, PLANES_PER_PLAYER, captures_at, cell_of,
    landing_effects, loop_index, movable_planes, step_journey,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


class FixedDice:
    """按脚本依次掷点；脚本耗尽后抛错，避免测试悄悄走偏。"""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("骰子脚本耗尽")
        return self.values.pop(0)


def make_room(rules=None, players=("a", "b"), buy_in=100, blind=5):
    room = create_room("ludo", room_id=1, name="测试", owner=players[0],
                       buy_in=buy_in, blind=blind, rules=rules or {})
    for name in players:
        room.add_member(name, buy_in)

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def play_full_race(room, guard_limit=30000):
    """随机完整对局：掷骰 -> 自动选子（优先起飞、其次最靠前）直到完赛。"""

    async def run():
        await room.start_race()
        guard = 0
        while room.in_hand() and guard < guard_limit:
            guard += 1
            g = room.game
            await room.perform_action(g["to_act"], "roll")
            while room.game and room.game["stage"] == "play" \
                    and room.game.get("awaiting_move"):
                who = room.game["to_act"]
                planes = room.game["planes"][who]
                movable = movable_planes(planes, room.game["dice"],
                                         room.rules["launch"])
                pick = next((i for i in movable if planes[i] == -1),
                            max(movable, key=lambda i: planes[i]))
                await room.perform_action(who, "move", {"plane": pick})
        return guard

    return asyncio.run(run())


def test_geometry():
    check("主圈 52 格且不重复", len(LOOP) == 52 and len(set(LOOP)) == 52)
    check("起飞格颜色对齐", all(ENTRY[c] % 4 == c for c in range(4)))
    ok = all(loop_index(c, j) is not None and
             cell_of(c, j) == LOOP[loop_index(c, j)]
             for c in range(4) for j in range(LOOP_STEPS))
    check("主圈 journey 映射一致", ok)
    check("跑道映射 下", cell_of(0, LOOP_STEPS) == (7, 1) and cell_of(0, 55) == (7, 5))
    check("跑道映射 上", cell_of(1, LOOP_STEPS) == (1, 7) and cell_of(1, 55) == (5, 7))
    check("跑道映射 右", cell_of(2, LOOP_STEPS) == (7, 13) and cell_of(2, 55) == (7, 9))
    check("跑道映射 左", cell_of(3, LOOP_STEPS) == (13, 7) and cell_of(3, 55) == (9, 7))
    check("终点在中心", all(cell_of(c, GOAL) == (7, 7) for c in range(4)))
    check("机场无坐标", all(cell_of(c, -1) is None for c in range(4)))


def test_steps_and_effects():
    rules = {"jump4": True, "fly12": True}
    check("普通前进", step_journey(10, 3) == 13)
    check("恰好到达", step_journey(53, 3) == GOAL)
    check("超终点反弹", step_journey(55, 4) == 53 and step_journey(54, 6) == 52)
    check("同色跳格", landing_effects(4, rules) == 8)
    check("主圈末格不再跳", landing_effects(48, rules) == 48)
    check("飞行格直飞", landing_effects(16, rules) == 28)
    check("跳到飞行格再飞", landing_effects(12, rules) == 28)
    check("飞行后不再跳", landing_effects(28, rules) == 32)
    check("跑道不触发效果", landing_effects(52, rules) == 52)
    check("关闭跳格飞行", landing_effects(16, {"jump4": False, "fly12": False}) == 16)
    check("起飞格不跳", landing_effects(0, rules) == 0)


def test_movable_and_captures():
    planes = [-1, -1, -1, -1]
    check("未掷到起飞点数不可起飞", movable_planes(planes, 4, 6) == [])
    check("掷到 6 可起飞", movable_planes(planes, 6, 6) == [0, 1, 2, 3])
    check("掷 5 规则可起飞", movable_planes(planes, 5, 5) == [0, 1, 2, 3])
    planes = [GOAL, 10, -1, 30]
    check("到达的飞机不可再动", movable_planes(planes, 2, 6) == [1, 3])
    # 与引擎一致：planes 以用户名为键，colors 给出用户名 -> 颜色
    all_planes = {"a": [13], "b": [0], "c": [51]}
    colors = {"a": 0, "b": 1, "c": 2}
    # a 的 journey 13 在主圈下标 13；b 的 journey 0 也在下标 13；c 在跑道安全
    check("落点撞机", captures_at(all_planes, colors, 0, 13) == [("b", 0)])
    check("跑道无撞机", captures_at(all_planes, colors, 0, 51) == [])


def test_launch_and_turn_flow():
    async def run():
        room = make_room({"launch": 6})
        randomness.roll_die = FixedDice([4, 6])
        await room.start_race()
        await room.perform_action("a", "roll")          # 掷 4，无法起飞
        advanced = room.game["to_act"] == "b" and room.game["dice"] is None
        await room.perform_action("b", "roll")          # 掷 6，起飞后再掷
        movable = movable_planes(room.game["planes"]["b"], room.game["dice"],
                                 room.rules["launch"])
        await room.perform_action("b", "move", {"plane": 0})
        launched = room.game["planes"]["b"][0] == 0 and room.game["to_act"] == "b" \
            and room.game["dice"] is None
        return advanced, movable, launched

    old = randomness.roll_die
    try:
        advanced, movable, launched = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("掷不到起飞点数过门", advanced)
    check("掷 6 后可再掷", movable == [0, 1, 2, 3])
    check("起飞落在起飞格且继续掷", launched)


def test_triple_six_penalty():
    async def run():
        room = make_room({"launch": 6, "extra_roll": True})
        randomness.roll_die = FixedDice([6, 6, 6])
        await room.start_race()
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 0})   # 起飞
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 1})   # 再起飞
        await room.perform_action("a", "roll")                 # 第三个 6
        planes = room.game["planes"]["a"]
        return planes, room.game["to_act"]

    old = randomness.roll_die
    try:
        planes, to_act = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("三连 6 罚回最后移动的飞机",
          planes == [0, -1, -1, -1] and to_act == "b", str(planes))


def test_capture_on_landing():
    async def run():
        room = make_room({"launch": 6, "extra_roll": False}, players=("a", "b"))
        await room.start_race()
        g = room.game
        g["planes"]["a"] = [12, -1, -1, -1]
        g["planes"]["b"] = [0, -1, -1, -1]
        g["awaiting_move"] = False
        randomness.roll_die = FixedDice([1])
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 0})
        return g["planes"]["b"][0], g["to_act"], g["last_action"]["text"]

    old = randomness.roll_die
    try:
        pos, to_act, text = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("撞机回机场", pos == -1 and to_act == "b" and "撞回" in text,
          f"pos={pos} text={text}")


def test_win_champion_payout():
    async def run():
        room = make_room({"launch": 6, "payout": "champion"}, players=("a", "b"))
        await room.start_race()
        g = room.game
        g["planes"]["a"] = [55, GOAL, GOAL, GOAL]
        g["awaiting_move"] = False
        randomness.roll_die = FixedDice([1])
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 0})
        finished = g["stage"] == "showdown" and g["result"]["winner"] == "a"
        payouts = g["result"]["payouts"]
        stacks = (room.members["a"]["stack"], room.members["b"]["stack"])
        return finished, payouts, stacks

    old = randomness.roll_die
    try:
        finished, payouts, stacks = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("率先 4 架到达获胜", finished)
    check("冠军通吃赔付", payouts == {"b": 5} and stacks == (105.0, 95.0),
          f"{payouts} {stacks}")


def test_rank_payout():
    async def run():
        room = make_room({"launch": 6, "payout": "rank"},
                         players=("a", "b", "c"))
        await room.start_race()
        g = room.game
        g["planes"]["a"] = [GOAL, GOAL, GOAL, 55]
        g["planes"]["b"] = [GOAL, GOAL, 30, -1]
        g["planes"]["c"] = [GOAL, 5, -1, -1]
        g["awaiting_move"] = False
        randomness.roll_die = FixedDice([1])
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 3})
        ranking = g["result"]["ranking"]
        order = [row["username"] for row in ranking]
        stacks = {name: room.members[name]["stack"] for name in "abc"}
        return order, stacks, g["result"]["gains"]

    old = randomness.roll_die
    try:
        order, stacks, gains = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("按进度排名", order == ["a", "b", "c"], str(order))
    check("按名次递增赔付 第2付1份第3付2份",
          stacks == {"a": 115.0, "b": 95.0, "c": 90.0} and gains == {"a": 15.0},
          str(stacks))


def test_bounce_move():
    async def run():
        room = make_room({"launch": 6, "extra_roll": False})
        await room.start_race()
        g = room.game
        g["planes"]["a"] = [55, -1, -1, -1]
        g["awaiting_move"] = False
        randomness.roll_die = FixedDice([4])
        await room.perform_action("a", "roll")
        await room.perform_action("a", "move", {"plane": 0})
        return g["planes"]["a"][0], g["to_act"]

    old = randomness.roll_die
    try:
        pos, to_act = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("终点反弹", pos == 53 and to_act == "b", f"pos={pos} to_act={to_act}")


def test_leave_mid_race_finishes():
    async def run():
        room = make_room({"launch": 6}, players=("a", "b"))
        await room.start_race()
        mid = room.note_leave("b")
        await room.progress_game()
        return mid, room.game["stage"], room.game["result"]["winner"]

    asyncio.run(run()) if False else None
    old = randomness.roll_die
    try:
        mid, stage, winner = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("离桌后剩一人直接完赛", mid and stage == "showdown" and winner == "a")


def test_full_random_race():
    for seats in (2, 3, 4):
        room = make_room({}, players=tuple("abcd"[:seats]))
        guard = play_full_race(room)
        result = room.game["result"]
        stacks = [room.members[name]["stack"] for name in "abcd"[:seats]]
        check(f"{seats} 人随机对局完赛",
              room.game["stage"] == "showdown" and result["winner"] in "abcd"[:seats]
              and guard < 30000,
              f"guard={guard}")
        check(f"{seats} 人结算守恒", abs(sum(stacks) - 100 * seats) < 1e-6,
              str(stacks))
        room.close()


def test_view_payload():
    async def run():
        room = make_room({"launch": 5, "payout": "rank"}, players=("a", "b"))
        await room.start_race()
        view = room.view_for("a")
        spectated = room.spectator_view("bob") if room.has_spectator("bob") else None
        room.add_spectator("bob", "a")
        spectated = room.spectator_view("bob")
        return view, spectated

    old = randomness.roll_die
    try:
        view, spectated = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("视图含规则与颜色", view["rules"]["launch"] == 5
          and view["rules"]["payout"] == "rank"
          and [p["color"] for p in view["players"]] == [0, 1])
    check("视图含飞机与选项", view["players"][0]["planes"] == [-1] * PLANES_PER_PLAYER
          and view["your_options"]["roll"] is True
          and view["players"][0]["in_race"])
    check("观战视图剥离操作", spectated is not None
          and "your_options" not in spectated
          and spectated["players"][0]["planes"] == [-1] * PLANES_PER_PLAYER)


def test_settlement_vote_and_restart():
    async def run():
        room = make_room({}, players=("a", "b"))
        await room.start()
        room.game["planes"]["a"] = [GOAL] * PLANES_PER_PLAYER
        await room.end_race("a")
        can_vote = room.view_for("a")["settlement"]["total"] == 2
        await room.cast_vote("a", "next", room.blind)
        executed = await room.cast_vote("b", "next", room.blind)
        again = room.game is not None and room.in_hand() \
            and room.game["race_no"] == 2
        await room.restart()
        restarted = room.in_hand() and room.game["race_no"] == 3
        room.close()
        return can_vote, executed, again, restarted

    old = randomness.roll_die
    try:
        can_vote, executed, again, restarted = asyncio.run(run())
    finally:
        randomness.roll_die = old
    check("完赛后进入结算投票", can_vote)
    check("过半数再来一局", executed == "next" and again)
    check("房主重开新局", restarted)


def main():
    test_geometry()
    test_steps_and_effects()
    test_movable_and_captures()
    test_launch_and_turn_flow()
    test_triple_six_penalty()
    test_capture_on_landing()
    test_win_champion_payout()
    test_rank_payout()
    test_bounce_move()
    test_leave_mid_race_finishes()
    test_full_random_race()
    test_view_payload()
    test_settlement_vote_and_restart()
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败用例：", "、".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
