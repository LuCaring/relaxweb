#!/usr/bin/env python3
"""骗子酒馆引擎单元测试：python3 tests/test_liarsbar.py

只测纯逻辑（不发网络请求）：
  - 牌堆构成与翻牌判定（games/liarsbar.py 纯函数）
  - 轮盘概率：弹巢递增 / 每次重转
  - 房间流程：出牌与选项门禁、决斗翻牌（说谎/真话）、阵亡淘汰、
    下一轮先手、超时托管、离桌完赛、金币结算（冠军通吃 / 按出局
    顺序递增）、随机完整对局与筹码守恒
"""
import asyncio
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games import randomness  # noqa: E402
from games.base import create_room  # noqa: E402
from games.liarsbar import (  # noqa: E402
    JOKER, build_deck, is_truthful, ranking_of, shot_denominator, sort_hand,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


class FixedDice:
    """按脚本依次掷点；脚本耗尽后抛错，避免测试悄悄走偏。"""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self, sides=6):
        if not self.values:
            raise AssertionError("骰子脚本耗尽")
        value = self.values.pop(0)
        assert 1 <= value <= sides, f"脚本点数 {value} 超出 d{sides}"
        return value


def make_room(rules=None, players=("a", "b"), buy_in=100, blind=5):
    room = create_room("liarsbar", room_id=1, name="测试", owner=players[0],
                       buy_in=buy_in, blind=blind, rules=rules or {})
    for name in players:
        room.add_member(name, buy_in)

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def run_with_dice(values, coro_fn):
    """在受控骰子下跑协程，结束后恢复全局随机源。"""
    old = randomness.roll_die
    randomness.roll_die = FixedDice(values)
    try:
        return asyncio.run(coro_fn())
    finally:
        randomness.roll_die = old


async def start_and_rig(room, hands, table_card="K"):
    """开局（首个骰点 1 -> 桌面牌 K）并把手牌固定成指定局面。"""
    await room.start()
    g = room.game
    g["table_card"] = table_card
    for name, cards in hands.items():
        g["hands"][name] = sort_hand(cards)
    return g


def test_deck_and_truthful():
    deck = build_deck(4, 5, "wild")
    counts = Counter(deck)
    check("4 人经典牌堆 6/6/6+2 小丑",
          len(deck) == 20 and counts["K"] == 6 and counts["Q"] == 6
          and counts["A"] == 6 and counts[JOKER] == 2, str(counts))
    counts = Counter(build_deck(2, 5, "wild"))
    check("2 人牌堆 3/3/2+2 小丑",
          counts["K"] == 3 and counts["Q"] == 3 and counts["A"] == 2
          and counts[JOKER] == 2, str(counts))
    counts = Counter(build_deck(3, 4, "none"))
    check("无小丑纯 K/Q/A", len(counts) == 3 and sum(counts.values()) == 12,
          str(counts))
    check("小丑百搭算真话", is_truthful([JOKER, "K"], "K", "wild"))
    check("无小丑模式小丑是说谎", not is_truthful([JOKER], "K", "none"))
    check("全部桌面牌是真话", is_truthful(["K", "K"], "K", "wild"))
    check("混入他牌是说谎", not is_truthful(["K", "Q"], "K", "wild"))
    check("手牌排序 K>Q>A>小丑", sort_hand(["A", JOKER, "K", "Q"]) == ["K", "Q", "A", JOKER])


def test_shot_denominator():
    check("递增弹巢 1/6→1/5→…→1/1",
          [shot_denominator(6, s, False) for s in range(6)] == [6, 5, 4, 3, 2, 1])
    check("重转恒为 1/6",
          [shot_denominator(6, s, True) for s in range(6)] == [6] * 6)
    check("幸存数溢出时兜底为 1", shot_denominator(4, 9, False) == 1)
    check("排名：幸存者第一、出局从晚到早",
          ranking_of(["a"], ["b", "c"]) == ["a", "c", "b"]
          and ranking_of([], ["b"]) == ["b"])


def test_rules_sanitize():
    room = make_room({"chambers": 5, "cards": 99, "max_play": 1,
                      "jokers": "weird", "payout": "rich", "respin": 1})
    check("非法规则回落默认", room.rules == {
        "chambers": 6, "cards": 5, "max_play": 3, "jokers": "wild",
        "respin": True, "payout": "champion"}, str(room.rules))
    room = make_room({"chambers": 4, "cards": 6, "max_play": 2,
                      "jokers": "none", "payout": "rank"})
    check("合法规则原样保留", room.rules["chambers"] == 4
          and room.rules["cards"] == 6 and room.rules["max_play"] == 2
          and room.rules["jokers"] == "none" and room.rules["payout"] == "rank")


def test_play_flow_and_options():
    async def run():
        room = make_room()
        g = await start_and_rig(room, {"a": ["Q", "Q", "A"], "b": ["K", "K"]})
        first = room.view_for("a")
        second = room.view_for("b")
        # 先手没有可质疑对象，只能出牌
        ok_first = g["to_act"] == "a" and first["your_options"]["can_challenge"] is False \
            and first["your_options"]["max_play"] == 3
        # 别人的视图只有自己的手牌，且轮不到时不给操作
        hidden = second.get("your_hand") == ["K", "K"] \
            and "your_options" not in second
        await room.perform_action("a", "play", {"cards": [0, 1]})
        played = g["last_play"]["cards"] == ["Q", "Q"] \
            and g["hands"]["a"] == ["A"] and g["piles"]["a"] == 2 \
            and g["to_act"] == "b"
        view_b = room.view_for("b")
        can_challenge = view_b["your_options"]["can_challenge"] is True \
            and view_b["your_options"]["challenge"] == "a"
        # 超出上限 / 越界 / 负数下标都无效
        await room.perform_action("b", "play", {"cards": [0, 1, 2]})
        await room.perform_action("b", "play", {"cards": [5]})
        await room.perform_action("b", "play", {"cards": [-1]})
        invalid_ok = g["last_play"]["username"] == "a" and g["to_act"] == "b"
        return ok_first, hidden, played, can_challenge, invalid_ok

    ok1, ok2, ok3, ok4, ok5 = run_with_dice([1], run)
    check("先手只能出牌且选项正确", ok1)
    check("手牌只对本人可见", ok2)
    check("暗打出牌移除手牌并轮转", ok3)
    check("上家暗牌可被质疑", ok4)
    check("非法出牌参数被拒绝", ok5)


def test_lie_duel_kills_liar():
    async def run():
        room = make_room({"payout": "champion"})
        g = await start_and_rig(room, {"a": ["Q"], "b": ["K", "K"]})
        await room.perform_action("a", "play", {"cards": [0]})   # 谎称 K
        await room.perform_action("b", "challenge")
        duel = g["last_duel"]
        duel_ok = g["stage"] == "reveal" and g["to_act"] is None \
            and duel["truthful"] is False and duel["shooter"] == "a" \
            and duel["cards"] == ["Q"] and duel["hit"] is True
        removed = g["order"] == ["b"] and g["eliminated"] == ["a"] \
            and g["next_starter"] == "b"
        await room.after_reveal()
        finished = g["stage"] == "showdown" and g["result"]["winner"] == "b"
        stacks = (room.members["a"]["stack"], room.members["b"]["stack"])
        return duel_ok, removed, finished, stacks, g["result"]["payouts"]

    duel_ok, removed, finished, stacks, payouts = run_with_dice([1, 1], run)
    check("说谎被质疑：说谎者开枪阵亡", duel_ok)
    check("阵亡移出存活序列", removed)
    check("独存即完赛", finished)
    check("冠军通吃赔付", payouts == {"a": 5} and stacks == (95.0, 105.0),
          f"{payouts} {stacks}")


def test_truth_duel_escalates_and_new_round():
    async def run():
        room = make_room()
        g = await start_and_rig(room, {"a": ["K", "K"], "b": ["Q"]})
        await room.perform_action("a", "play", {"cards": [0]})   # 真话
        await room.perform_action("b", "challenge")              # 误质疑
        first = g["last_duel"]
        survived1 = first["truthful"] is True and first["shooter"] == "b" \
            and first["hit"] is False and g["guns"]["b"]["survived"] == 1 \
            and first["odds"] == "1/6"
        await room.after_reveal()
        round2 = g["stage"] == "play" and g["round_no"] == 2 \
            and g["to_act"] == "b" and g["next_starter"] == "b" \
            and g["last_duel"] is None and len(g["hands"]["b"]) == 5
        # 第二轮 b（幸存者）先手，误质疑 a 的真话再挨一枪：1/5
        g["hands"]["b"] = ["Q"]
        g["hands"]["a"] = ["K"]
        await room.perform_action("b", "play", {"cards": [0]})
        await room.perform_action("a", "challenge")
        second = g["last_duel"]
        survived2 = second["shooter"] == "b" and second["hit"] is False \
            and second["odds"] == "1/5" and g["guns"]["b"]["survived"] == 2 \
            and room.gun_label(2) == "1/4"
        return survived1, round2, survived2

    survived1, round2, survived2 = run_with_dice([1, 2, 1, 2], run)
    check("误质疑：质疑者开枪空弹幸存", survived1)
    check("换轮重发且幸存者先手", round2)
    check("弹巢递增 1/6→1/5→1/4", survived2)


def test_rank_payout():
    async def run():
        room = make_room({"payout": "rank"}, players=("a", "b", "c"))
        g = await start_and_rig(room, {"a": ["K"], "b": ["Q", "Q", "Q", "Q", "Q"],
                                 "c": ["Q", "Q", "Q", "Q", "Q"]})
        await room.perform_action("a", "play", {"cards": [0]})   # 真话
        await room.perform_action("b", "challenge")              # b 误质疑
        first_dead = g["eliminated"] == ["b"] and g["order"] == ["a", "c"] \
            and g["next_starter"] == "c"
        await room.after_reveal()
        # 第二轮 c 先手说谎，a 质疑命中 -> c 阵亡
        g["hands"]["c"] = ["Q"]
        g["hands"]["a"] = ["K"]
        await room.perform_action("c", "play", {"cards": [0]})
        await room.perform_action("a", "challenge")
        await room.after_reveal()
        result = g["result"]
        order = [row["username"] for row in result["ranking"]]
        stacks = {name: room.members[name]["stack"] for name in "abc"}
        return first_dead, order, stacks, result["payouts"]

    first_dead, order, stacks, payouts = run_with_dice([1, 1, 1, 1], run)
    check("阵亡后由下家先手", first_dead)
    check("按出局顺序排名", order == ["a", "c", "b"], str(order))
    check("按出局顺序递增赔付（第2名1份、第3名2份）",
          payouts == {"c": 5, "b": 10}
          and stacks == {"a": 115.0, "b": 90.0, "c": 95.0},
          f"{payouts} {stacks}")


def test_respin_keeps_odds():
    async def run():
        room = make_room({"respin": True})
        g = await start_and_rig(room, {"a": ["Q", "Q", "Q", "Q", "Q"], "b": ["K"]})
        for _ in range(2):
            await room.perform_action("a", "play", {"cards": [0]})   # 说谎
            await room.perform_action("b", "challenge")
            await room.after_reveal()
            if g["stage"] == "showdown":
                break
            g["hands"]["a"] = ["Q"]      # 幸存者 a 先手继续说谎
            g["hands"]["b"] = ["K"]
        return g["guns"]["a"]["survived"], room.gun_label(1)

    survived, label = run_with_dice([1, 6, 1, 6, 1], run)
    check("重转模式命中率恒定 1/6", survived >= 2 and label == "1/6",
          f"survived={survived} label={label}")


def test_auto_action():
    async def run():
        room = make_room()
        g = await start_and_rig(room, {"a": ["Q"], "b": ["K"]})
        # 无上家可质疑且无真牌：托管出一张
        await room.auto_action()
        auto_played = g["last_play"]["username"] == "a" \
            and g["last_play"]["cards"] == ["Q"] and g["to_act"] == "b"
        # b 有真牌：托管打真牌而不是质疑
        await room.auto_action()
        auto_truth = g["last_play"]["cards"] == ["K"] and g["to_act"] == "a"
        # a 手空且无真牌：托管质疑
        g["hands"]["a"] = []
        await room.auto_action()
        auto_challenge = g["stage"] == "reveal" \
            and g["last_duel"]["challenger"] == "a"
        return auto_played, auto_truth, auto_challenge

    ok1, ok2, ok3 = run_with_dice([1, 6], run)
    check("托管：无真牌时自动出一张", ok1)
    check("托管：有真牌先打真牌", ok2)
    check("托管：无手牌自动质疑", ok3)


def test_leave_mid_match_finishes():
    async def run():
        room = make_room()
        g = await start_and_rig(room, {"a": ["Q"], "b": ["K"]})
        await room.perform_action("a", "play", {"cards": [0]})
        mid = room.note_leave("a")          # 存活玩家离桌 = 认输出局
        cleared = g["last_play"] is None    # 离桌者的暗牌不能再被质疑
        room.remove_member("a")
        await room.progress_game()
        return mid, cleared, g["stage"], g["result"]["winner"]

    mid, cleared, stage, winner = run_with_dice([1], run)
    check("存活者离桌视作出局并直接完赛",
          mid and cleared and stage == "showdown" and winner == "b")


def test_view_and_spectator():
    async def run():
        room = make_room({"chambers": 4, "payout": "rank", "jokers": "none"})
        await start_and_rig(room, {"a": ["K"], "b": ["K"]})
        view = room.view_for("a")
        room.add_spectator("bob", "a")
        spectated = room.spectator_view("bob")
        return view, spectated

    view, spectated = run_with_dice([1], run)
    check("视图含规则与桌面牌", view["rules"]["chambers"] == 4
          and view["rules"]["payout"] == "rank"
          and view["rules"]["jokers"] == "none" and view["table_card"] == "K")
    check("视图含手牌与命中率", view["your_hand"] == ["K"]
          and view["players"][0]["gun"] == "1/4"
          and view["players"][0]["alive"])
    check("观战视角剥离操作", "your_options" not in spectated
          and spectated["spectator"] is True
          and spectated["watching"] == "a")


def play_full_match(room, guard_limit=40000):
    """随机完整对局：能质疑时三成概率质疑（无真牌必质疑），否则随机出牌。"""

    async def run():
        await room.start()
        guard = 0
        while room.in_hand() and guard < guard_limit:
            guard += 1
            g = room.game
            if g["stage"] == "reveal":
                await room.after_reveal()
                continue
            who = g["to_act"]
            hand = g["hands"][who]
            can_challenge = g["last_play"] is not None
            truthful = [i for i, card in enumerate(hand)
                        if is_truthful([card], g["table_card"], room.rules["jokers"])]
            if not hand or (can_challenge and (not truthful or random.random() < 0.35)):
                await room.perform_action(who, "challenge")
            else:
                count = random.randint(1, min(room.rules["max_play"], len(hand)))
                picks = random.sample(range(len(hand)), count)
                await room.perform_action(who, "play", {"cards": picks})
        return guard

    return asyncio.run(run())


def test_full_random_match():
    for seats in (2, 3, 4, 5, 6):
        room = make_room({}, players=tuple("abcdef"[:seats]))
        guard = play_full_match(room)
        result = room.game["result"]
        stacks = [room.members[name]["stack"] for name in "abcdef"[:seats]]
        check(f"{seats} 人随机对局完赛",
              room.game["stage"] == "showdown"
              and result["winner"] in "abcdef"[:seats]
              and len(result["ranking"]) == seats and guard < 40000,
              f"guard={guard}")
        check(f"{seats} 人结算守恒", abs(sum(stacks) - 100 * seats) < 1e-6,
              str(stacks))
        room.close()
    room = make_room({"chambers": 4, "cards": 4, "max_play": 2,
                      "jokers": "none", "respin": True, "payout": "rank"},
                     players=("a", "b", "c"))
    guard = play_full_match(room)
    check("自定义规则随机对局完赛",
          room.game["stage"] == "showdown" and guard < 40000, f"guard={guard}")
    room.close()


def test_settlement_vote_and_restart():
    async def run():
        room = make_room()
        g = await start_and_rig(room, {"a": ["Q"], "b": ["K"]})
        await room.perform_action("a", "play", {"cards": [0]})
        await room.perform_action("b", "challenge")
        await room.after_reveal()           # a 阵亡，b 独存
        can_vote = room.view_for("b")["settlement"]["total"] == 2
        await room.cast_vote("a", "next", room.blind)
        executed = await room.cast_vote("b", "next", room.blind)
        again = room.in_hand() and room.game["match_no"] == 2 \
            and room.game["round_no"] == 1
        await room.restart()
        restarted = room.in_hand() and room.game["match_no"] == 3
        room.close()
        return can_vote, executed, again, restarted

    can_vote, executed, again, restarted = run_with_dice([1, 1, 1, 1], run)
    check("完赛后进入结算投票", can_vote)
    check("过半数再来一局", executed == "next" and again)
    check("房主重开新场", restarted)


def main():
    test_deck_and_truthful()
    test_shot_denominator()
    test_rules_sanitize()
    test_play_flow_and_options()
    test_lie_duel_kills_liar()
    test_truth_duel_escalates_and_new_round()
    test_rank_payout()
    test_respin_keeps_odds()
    test_auto_action()
    test_leave_mid_match_finishes()
    test_view_and_spectator()
    test_full_random_match()
    test_settlement_vote_and_restart()
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败用例：", "、".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
