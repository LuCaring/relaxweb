#!/usr/bin/env python3
"""斗地主引擎单元测试：python3 tests/test_doudizhu.py

只测纯逻辑（不发网络请求）：
  - 牌堆构成（games/doudizhu.py 纯函数）
  - 牌型解析与压制关系（含飞机、四带二、王炸）
  - 一局流程：叫分定地主/流局重发/出牌/结算/春天反春
  - 炸弹封顶、离桌作废、投票重发
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games.base import ROOM_TYPES, create_room  # noqa: E402
from games.doudizhu import (  # noqa: E402
    beats, build_deck, find_moves, resolve_combo,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def card(rank, suit=0):
    return {"r": rank, "s": suit}


def joker(small=True):
    return {"r": 16 if small else 17, "s": 4}


def combo(cards):
    return resolve_combo(cards)


def test_deck():
    deck = build_deck()
    check("斗地主牌堆54张", len(deck) == 54)
    ranks = {}
    for c in deck:
        ranks[c["r"]] = ranks.get(c["r"], 0) + 1
    check("3~2 每点数4张", all(ranks.get(r) == 4 for r in range(3, 16)), str(ranks))
    check("大小王各1张", ranks.get(16) == 1 and ranks.get(17) == 1)


def test_resolve():
    check("单张", combo([card(7)])["type"] == "single")
    check("对子", combo([card(7), card(7, 1)])["type"] == "pair")
    check("三张", combo([card(7), card(7, 1), card(7, 2)])["type"] == "triple")
    to = combo([card(7), card(7, 1), card(7, 2), card(3)])
    check("三带一", to["type"] == "triple_one" and to["main"] == 7)
    tp = combo([card(7), card(7, 1), card(7, 2), card(3), card(3, 1)])
    check("三带二", tp["type"] == "triple_pair" and tp["main"] == 7)
    check("四张同点是炸弹不是三带一",
          combo([card(7), card(7, 1), card(7, 2), card(7, 3)])["type"] == "bomb")
    check("最小顺子34567",
          combo([card(3), card(4, 1), card(5, 2), card(6, 0), card(7, 1)])["type"] == "straight")
    check("最大顺子10JQKA",
          combo([card(10), card(11, 1), card(12, 2), card(13, 0), card(14, 1)])["type"] == "straight")
    check("2不能入顺", combo([card(2), card(3), card(4), card(5), card(6)]) is None)
    check("A后不能接2", combo([card(10), card(11), card(12), card(13), card(14), card(15)]) is None)
    ps = combo([card(3), card(3, 1), card(4, 0), card(4, 1), card(5, 0), card(5, 1)])
    check("三连对", ps["type"] == "pairs_seq" and ps["len"] == 6)
    check("两连对不算连对",
          combo([card(3), card(3, 1), card(4, 0), card(4, 1)]) is None)
    check("王不能入连对", combo([card(3), card(3, 1), card(4, 0), card(4, 1),
                                 joker(True), joker(False)]) is None)
    pl = combo([card(3), card(3, 1), card(3, 2), card(4, 0), card(4, 1), card(4, 2)])
    check("飞机不带翅", pl["type"] == "plane" and pl["main"] == 4)
    pls = combo([card(3), card(3, 1), card(3, 2), card(4, 0), card(4, 1),
                 card(4, 2), card(5), card(6)])
    check("飞机带单", pls["type"] == "plane_single" and pls["main"] == 4
          and pls["len"] == 8)
    plp = combo([card(3), card(3, 1), card(3, 2), card(4, 0), card(4, 1),
                 card(4, 2), card(5), card(5, 1), card(6), card(6, 1)])
    check("飞机带对", plp["type"] == "plane_pair" and plp["main"] == 4)
    # 飞机带单的翅膀不能取牌身上的点数：333444+5 只缺 1 张凑不出 2 翅
    check("翅膀不能含牌身点数",
          combo([card(3), card(3, 1), card(3, 2), card(4, 0), card(4, 1),
                 card(4, 2), card(5)]) is None)
    ft = combo([card(9), card(9, 1), card(9, 2), card(9, 3), card(3), card(4)])
    check("四带二", ft["type"] == "four_two" and ft["main"] == 9)
    ftp = combo([card(9), card(9, 1), card(9, 2), card(9, 3),
                 card(3), card(3, 1), card(4), card(4, 1)])
    check("四带两对", ftp["type"] == "four_two_pairs" and ftp["main"] == 9)
    check("四带两对不能同对",
          combo([card(9), card(9, 1), card(9, 2), card(9, 3)] + [card(3)] * 4)
          is None)
    check("炸弹", combo([card(9), card(9, 1), card(9, 2), card(9, 3)])["type"] == "bomb")
    check("王炸", combo([joker(True), joker(False)])["type"] == "rocket")


def test_beats():
    s9 = {"type": "straight", "tier": 0, "main": 9, "len": 5}
    s10 = {"type": "straight", "tier": 0, "main": 10, "len": 5}
    s10x = {"type": "straight", "tier": 0, "main": 10, "len": 6}
    p = {"type": "pair", "tier": 0, "main": 15, "len": 2}
    b4 = {"type": "bomb", "tier": 1, "main": 3, "len": 4}
    b5 = {"type": "bomb", "tier": 1, "main": 4, "len": 4}
    rk = {"type": "rocket", "tier": 2, "main": 17, "len": 2}
    pls4 = {"type": "plane_single", "tier": 0, "main": 4, "len": 8}
    pls5 = {"type": "plane_single", "tier": 0, "main": 5, "len": 8}
    pls4x = {"type": "plane_single", "tier": 0, "main": 4, "len": 12}
    check("大顺压小顺", beats(s10, s9))
    check("不同长度不能压", not beats(s10x, s9))
    check("对2不能压顺子", not beats(p, s9))
    check("炸弹压顺子", beats(b4, s10))
    check("炸弹压飞机带单", beats(b4, pls4))
    check("大炸弹压小炸弹", beats(b5, b4))
    check("小炸弹不能压大炸弹", not beats(b4, b5))
    check("等长飞机大压小", beats(pls5, pls4))
    check("飞机长度不同不能压", not beats(pls4x, pls4))
    check("王炸压一切", beats(rk, b5) and beats(rk, pls5))
    check("王炸不可被压", not beats(b5, rk) and not beats(rk, rk))


def test_find_moves():
    hand = [card(3), card(3, 1), card(7), card(9), card(14)]
    free = find_moves(hand, None)
    check("自由出牌含最小单张", free[0]["type"] == "single"
          and free[0]["main"] == 3)
    check("自由出牌含对子", any(m["type"] == "pair" and m["main"] == 3 for m in free))
    standing = {"type": "single", "tier": 0, "main": 8, "len": 1}
    follow = find_moves(hand, standing)
    check("跟牌只给压得过的", follow and all(beats(m, standing) for m in follow)
          and [m["main"] for m in follow] == [9, 14])
    bomb_standing = {"type": "bomb", "tier": 1, "main": 5, "len": 4}
    check("炸弹压顶时无解", find_moves(hand, bomb_standing) == [])
    jokers = [joker(True), joker(False)]
    rocket_moves = find_moves(jokers, bomb_standing)
    check("王炸可接炸弹", len(rocket_moves) == 1
          and rocket_moves[0]["type"] == "rocket")


def make_room(rules=None):
    room = create_room("doudizhu", room_id=99, name="斗地主测试", owner="a",
                       buy_in=100, blind=5, rules=rules or {})
    for name in ("a", "b", "c"):
        room.add_member(name, 100)

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def set_hands(room, hands, bottom=None):
    g = room.game
    for name, cards in hands.items():
        g["hands"][name] = list(cards)
    if bottom is not None:
        g["bottom"] = list(bottom)


def test_bid_and_lifecycle():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        dealt = (room.in_hand() and g["stage"] == "bid"
                 and all(len(h) == 17 for h in g["hands"].values())
                 and len(g["bottom"]) == 3)
        leader_ok = g["to_act"] == "a"
        # a 叫 2，b、c 都不叫 → a 地主，倍率含叫分 2
        await room.perform_action("a", "bid", {"score": 2})
        await room.perform_action("b", "pass", {})
        await room.perform_action("c", "pass", {})
        landlord_ok = (g["landlord"] == "a" and g["stage"] == "play"
                       and g["bid_score"] == 2 and len(g["hands"]["a"]) == 20
                       and g["to_act"] == "a" and g["free_lead"])
        # 换成已知牌：农民全程不压，地主连出成春天
        set_hands(room, {
            "a": [card(8), card(8, 1), card(3)],
            "b": [card(4)],
            "c": [card(5)],
        }, bottom=[])
        await room.perform_action("a", "play", {"cards": [0]})
        played = (g["standing"]["label"].startswith("单张")
                  and g["to_act"] == "b")
        await room.perform_action("b", "pass", {})
        await room.perform_action("c", "pass", {})
        free_again = g["free_lead"] and g["to_act"] == "a"
        await room.perform_action("a", "play", {"cards": [0]})
        await room.perform_action("b", "pass", {})
        await room.perform_action("c", "pass", {})
        await room.perform_action("a", "play", {"cards": [0]})
        result = g["result"]
        settled = not room.in_hand() and g["stage"] == "showdown"
        return room, dealt, leader_ok, landlord_ok, played, free_again, \
            settled, result

    (room, dealt, leader_ok, landlord_ok, played, free_again, settled,
     result) = asyncio.run(run())
    check("开局每人17张留3张底牌", dealt)
    check("首局房主先叫分", leader_ok)
    check("叫2分当地主拿底牌", landlord_ok)
    check("出牌后轮下家", played)
    check("两家不过回到出牌者自由出", free_again)
    check("地主先出完即获胜进入结算", settled)
    check("地主胜", result["landlord"] == "a" and result["winners"] == ["a"],
          str(result))
    # 农民一次没出过 = 春天：倍率 = 叫分 2 × 春天 2 = 4
    check("春天计入倍数", result["spring"] and result["multiplier"] == 4,
          str(result["multiplier"]))
    check("农民各付20地主赢40", result["payouts"] == {"b": 20, "c": 20}
          and result["gains"] == {"a": 40}, str(result))
    check("筹码更新", room.members["a"]["stack"] == 140
          and room.members["b"]["stack"] == 80)
    check("结算载荷带段位明细", "ratings" in result)
    check("下一局地主先叫", room.starter == "a")

    async def vote():
        for name in ("a", "b", "c"):
            await room.cast_vote(name, "next", 5)
        return room.in_hand() and all(len(h) == 17 for h in room.game["hands"].values())

    check("投票通过重发牌", asyncio.run(vote()))


def test_bid_pass_around_and_rules():
    async def run():
        room = make_room({"bottom_visible": True, "bomb_cap": 4})
        check("规则清洗生效", room.rules == {
            "bid_mode": "bid", "bottom_visible": True, "bomb_cap": 4,
            "spring": True})
        await room.start()
        # 全家不叫 → 自动重发，首叫轮流到 b
        await room.perform_action("a", "pass", {})
        await room.perform_action("b", "pass", {})
        await room.perform_action("c", "pass", {})
        g = room.game
        redealt = (room.in_hand() and g["hand_no"] == 2 and g["to_act"] == "b"
                   and all(len(h) == 17 for h in g["hands"].values()))
        # b 叫 1，c 加叫 2，a 不叫 → 全部表态完后 c 地主
        await room.perform_action("b", "bid", {"score": 1})
        await room.perform_action("c", "bid", {"score": 2})
        await room.perform_action("a", "pass", {})
        landlord = g["landlord"] == "c" and g["bid_score"] == 2
        # 地主 c 出 3，农民 a 用 4 一压即出完 → 农民胜
        set_hands(room, {"a": [card(4)], "b": [card(6)],
                         "c": [card(3), card(15)]})
        await room.perform_action("c", "play", {"cards": [0]})
        await room.perform_action("a", "play", {"cards": [0]})
        result = g["result"]
        peasants_win = (result["peasants_win"]
                        and result["winners"] == ["a", "b"])
        # 地主只出了一手 = 反春：倍率 = 叫分 2 × 反春 2 = 4，地主包赔两家各 20
        spring_ok = result["spring"] and result["multiplier"] == 4
        payouts_ok = result["payouts"] == {"c": 40}
        gains_ok = result["gains"] == {"a": 20, "b": 20}
        stacks_ok = room.members["c"]["stack"] == 60
        # 只剩 2 人不能开局
        room.remove_member("a")
        try:
            await room.start()
            restart_blocked = False
        except ValueError:
            restart_blocked = True
        return redealt, landlord, peasants_win, spring_ok, payouts_ok, \
            gains_ok, stacks_ok, restart_blocked

    (redealt, landlord, peasants_win, spring_ok, payouts_ok, gains_ok,
     stacks_ok, restart_blocked) = asyncio.run(run())
    check("全家不叫自动重发且首叫轮换", redealt)
    check("加叫者成为地主", landlord)
    check("农民出完农民胜", peasants_win)
    check("反春翻倍生效", spring_ok)
    check("地主包赔两家", payouts_ok and gains_ok and stacks_ok)
    check("人数不足不能开局", restart_blocked)


def test_spring_and_bombs():
    async def run_spring():
        room = make_room({"bid_mode": "random"})
        await room.start()
        g = room.game
        landlord = g["landlord"]
        ring = g["ring"]
        start = ring.index(landlord)
        after = [ring[(start + 1) % 3], ring[(start + 2) % 3]]
        g["hands"][landlord] = [card(8), card(8, 1)]
        g["hands"][after[0]] = [card(4)]
        g["hands"][after[1]] = [card(5)]
        await room.perform_action(landlord, "play", {"cards": [0]})
        await room.perform_action(after[0], "pass", {})
        await room.perform_action(after[1], "pass", {})
        await room.perform_action(landlord, "play", {"cards": [0]})
        return room.game["result"], after

    result, peasants = asyncio.run(run_spring())
    check("随机地主模式底分×1", result["bid_score"] == 1)
    check("春天翻倍", result["spring"] and result["multiplier"] == 2,
          str(result["multiplier"]))
    check("春天农民各付10", all(result["payouts"].get(n) == 10 for n in peasants),
          str(result))
    async def run_bombs():
        room = make_room({"bomb_cap": 4})
        await room.start()
        # a 不叫，b 叫 3 直接当地主（c 无需表态）
        await room.perform_action("a", "pass", {})
        await room.perform_action("b", "bid", {"score": 3})
        g = room.game
        set_hands(room, {
            "b": [card(9), card(10), card(8)],
            "c": [card(5), card(5, 1), card(5, 2), card(5, 3), card(6), card(7)],
            "a": [card(4)],
        })
        await room.perform_action("b", "play", {"cards": [0]})     # 单张 9
        await room.perform_action("c", "play", {"cards": [0, 1, 2, 3]})  # 炸弹
        await room.perform_action("a", "pass", {})
        await room.perform_action("b", "pass", {})
        await room.perform_action("c", "play", {"cards": [0]})     # 自由出 6
        await room.perform_action("a", "pass", {})
        await room.perform_action("b", "play", {"cards": [0]})     # 8 压 6
        await room.perform_action("c", "pass", {})
        await room.perform_action("a", "pass", {})
        # b 自由出 10 后出完 → 地主胜
        await room.perform_action("b", "play", {"cards": [0]})
        return room.game["result"]

    bombs = asyncio.run(run_bombs())
    check("叫3分直接当地主", bombs["bid_score"] == 3)
    # 倍率 = 3 × min(2^1, 4) = 6；地主出过 3 次不构成反春，农民各付 30
    check("炸弹翻倍封顶生效", bombs["multiplier"] == 6
          and bombs["payouts"] == {"a": 30, "c": 30}
          and bombs["gains"] == {"b": 60},
          f"{bombs['multiplier']} {bombs['payouts']} {bombs['gains']}")


def test_abort_and_misc():
    async def run():
        room = make_room()
        await room.start()
        await room.perform_action("a", "bid", {"score": 1})
        mid_hand = room.in_hand()
        stacks_before = {n: m["stack"] for n, m in room.members.items()}
        room.remove_member("b")
        await room.progress_game()
        aborted = (mid_hand and not room.in_hand()
                   and room.game["result"]["aborted"]
                   and not room.game["result"]["payouts"])
        stacks_same = all(room.members[n]["stack"] == s
                          for n, s in stacks_before.items() if n != "b")
        return aborted, stacks_same

    aborted, stacks_same = asyncio.run(run())
    check("离桌本局作废", aborted)
    check("作废不动筹码", stacks_same)


def test_registry():
    check("注册表包含doudizhu", "doudizhu" in ROOM_TYPES)
    check("doudizhu房间3人上限", create_room("doudizhu", room_id=1, name="x",
                                             owner="a", buy_in=100, blind=5)
          .max_seats == 3)


test_deck()
test_resolve()
test_beats()
test_find_moves()
test_bid_and_lifecycle()
test_bid_pass_around_and_rules()
test_spring_and_bombs()
test_abort_and_misc()
test_registry()
failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
