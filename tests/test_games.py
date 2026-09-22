#!/usr/bin/env python3
"""游戏引擎单元测试：python3 tests/test_games.py

只测纯逻辑（不发网络请求）：
  - 德州扑克牌力判定与边池分配（games/holdem.py 纯函数）
  - UNO 牌堆构成、出牌判定与一局流程（games/uno.py）
  - BaseRoom 计时器/暂停/成员管理（games/base.py）

新增游戏时请照此为它的纯函数补一组用例。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games.base import BaseRoom, ROOM_TYPES, create_room  # noqa: E402
from games.holdem import best7, build_side_pots, distribute_pots, evaluate5  # noqa: E402
from games.uno import build_deck, card_label, matches  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def card(rank, suit):
    return (rank, suit)


def test_evaluate():
    royal = [card(14, s) for s in (0, 1)] + [card(13, 0), card(12, 0), card(11, 0), card(10, 0)]
    check("皇家同花顺", best7(royal) == (8, 14))
    quads = [card(9, 0), card(9, 1), card(9, 2), card(9, 3), card(5, 0), card(6, 1), card(7, 2)]
    check("四条", best7(quads) == (7, 9, 7))
    wheel = [card(14, 0), card(5, 1), card(4, 0), card(3, 2), card(2, 1), card(9, 3), card(9, 0)]
    check("A5低顺", best7(wheel) == (4, 5))
    pair = [card(2, 0), card(2, 1), card(5, 0), card(6, 1), card(8, 2)]
    check("一对", evaluate5(pair) == (1, 2, 8, 6, 5))


def test_side_pots():
    committed = {"a": 100.0, "b": 100.0, "c": 40.0}
    folded = {"b"}
    pots = build_side_pots(committed, folded)
    # c 只有 40：主池 120（a/b/c），边池 120（仅 a）
    check("边池分层", len(pots) == 2 and pots[0]["amount"] == 120 and pots[1]["amount"] == 120,
          str(pots))
    hands = {"a": (1, 13), "b": (1, 12), "c": (3, 7)}
    payouts = distribute_pots(pots, hands)
    check("边池分配", payouts == {"c": 120.0, "a": 120.0}, str(payouts))


def test_room_lifecycle():
    async def run():
        room = create_room("holdem", room_id=1, name="测试", owner="a", buy_in=100, blind=5)
        room.add_member("a", 100)
        room.add_member("b", 100)
        views = []

        async def broadcast_views():
            views.append(1)

        room.broadcast_views = broadcast_views
        await room.start()
        started = room.in_hand() and room.status == "playing"
        room.pause()
        paused = room.paused and not room.timers
        room.resume()
        resumed = not room.paused
        left = room.remove_member("b")
        return started, paused, resumed, left

    started, paused, resumed, left = asyncio.run(run())
    check("开局发牌", started)
    check("暂停清计时器", paused)
    check("恢复", resumed)
    check("成员移除", left["stack"] == 90.0 and left["paid"] == 100.0,
          "开局后大盲注已提交，累计买入仍记 100")


def test_holdem_settlement_payload():
    """结算视图要能让前端摊开公共牌与每个人的手牌（含弃牌者）。"""

    async def play_out(folder):
        room = create_room("holdem", room_id=2, name="测试", owner="a", buy_in=100, blind=5)
        for name in ("a", "b", "c"):
            room.add_member(name, 100)

        async def noop(*_args, **_kwargs):
            return None

        room.broadcast_views = noop
        room.broadcast_payload = noop
        room.on_rooms_changed = noop
        await room.start()
        first = room.view_for("a")["players"]
        hand_bets = [p["hand_bet"] for p in first]
        streets = []
        guard = 0
        while room.in_hand() and guard < 300:
            guard += 1
            streets.append(room.game["stage"])
            who = room.game["to_act"]
            if who == folder:
                folder = None
                await room.perform_action(who, "fold")
                continue
            opts = room.legal_actions(who)
            await room.perform_action(who, "check" if opts["check"] else "call")
        result = room.game["result"]
        room.close()
        return room, result, hand_bets, streets

    room, result, hand_bets, _ = asyncio.run(play_out("a"))
    hands = {h["username"]: h for h in result["hands"]}
    check("结算含全部玩家手牌", sorted(hands) == ["a", "b", "c"], str(sorted(hands)))
    check("结算手牌都是两张", all(len(h["cards"]) == 2 for h in hands.values()))
    check("弃牌者仍在结算里且标记", hands["a"]["folded"] and not hands["b"]["folded"])
    check("公共牌五张", len(result["board"]) == 5, str(len(result["board"])))
    check("摊牌有牌型名", bool(hands["b"]["hand_name"]), str(hands["b"]["hand_name"]))
    check("弃牌者无牌型名", hands["a"]["hand_name"] == "")
    check("结算含每人投入", hands["b"]["committed"] > 0 and hands["a"]["committed"] == 0,
          str({n: h["committed"] for n, h in hands.items()}))
    check("底池守恒", round(sum(result["payouts"].values()), 2) == result["pot"],
          f"{result['payouts']} pot={result['pot']}")
    check("下注后座位可见本手投入", sum(hand_bets) == 15.0, str(hand_bets))

    # 全员弃牌到一人：没有摊牌也要能看到手牌
    room2, result2, _, _ = asyncio.run(play_out("a"))
    check("弃牌结束也有手牌数据", len(result2["hands"]) == 3)


def _holdem_room(room_id, names, blind=5):
    """建一个只测引擎的房间：宿主能力全部打成桩。"""

    async def noop(*_args, **_kwargs):
        return None

    room = create_room("holdem", room_id=room_id, name="测试", owner=names[0],
                       buy_in=100, blind=blind)
    room.views_seen = []

    async def record_views():
        room.views_seen.append(room.view_for(names[0]))

    room.broadcast_views = record_views
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    room.player_rating = lambda username: {"score": 1000, "tier": "白银"}
    room.record_ratings = lambda hand_id, starts, endings, stakes=None, statistics=None: {
        name: {"delta": 5, "initial": starts[name], "final": endings[name]}
        for name in endings
    }
    for name in names:
        room.add_member(name, 100)
    return room


async def _play_hand(room, folder=None):
    """把当前一手打完：指定 folder 则让他弃牌，否则一路过牌/跟注到摊牌。"""
    guard = 0
    while room.in_hand() and guard < 300:
        guard += 1
        who = room.game["to_act"]
        if who == folder:
            folder = None
            await room.perform_action(who, "fold")
            continue
        opts = room.legal_actions(who)
        await room.perform_action(who, "check" if opts["check"] else "call")


def test_holdem_hand_result_and_continue():
    """一手结束后弹层确认：10 秒倒计时自动开下一手，全员确认则立即开。"""

    async def run():
        room = _holdem_room(9, ("a", "b", "c"))
        await room.start()
        await _play_hand(room, "a")
        view = room.view_for("b")
        first = {"status": room.status, "ready": view.get("hand_ready"),
                 "settlement": view.get("settlement")}
        await room.mark_ready("b")
        middle = room.view_for("b")["hand_ready"]
        await room.mark_ready("c")
        await room.mark_ready("a")
        after_all = room.view_for("b")
        await _play_hand(room, "a")
        waiting = room.view_for("a").get("hand_ready")
        await room.continue_timeout()
        after_timeout = room.view_for("a")
        views = list(room.views_seen)
        room.close()
        return {
            "views": views,
            "first": first,
            "middle": middle,
            "all_ready": {"hand_no": after_all.get("hand_no"),
                          "ready": after_all.get("hand_ready")},
            "waiting": waiting,
            "timeout": {"hand_no": after_timeout.get("hand_no"),
                        "ready": after_timeout.get("hand_ready")},
        }

    out = asyncio.run(run())
    first = out["first"]
    check("一手结束进入继续确认", first["status"] == "playing" and bool(first["ready"]))
    check("继续确认不再走结算投票", first["settlement"] is None)
    check("倒计时 10 秒", 0 < first["ready"]["left"] <= 10, str(first["ready"]["left"]))
    check("弹层状态已广播给客户端",
          any(v.get("hand_ready") for v in out["views"]),
          f"共 {len(out['views'])} 次广播，必须有一次带 hand_ready")
    check("确认人数与总人数", first["ready"]["ready"] == [] and first["ready"]["total"] == 3)
    check("单人确认记录下来", out["middle"]["ready"] == ["b"], str(out["middle"]))
    check("全员确认立即开下一手",
          out["all_ready"]["hand_no"] == 2 and out["all_ready"]["ready"] is None,
          str(out["all_ready"]))
    check("等待中倒计时仍在", bool(out["waiting"]) and out["waiting"]["ready"] == [])
    check("倒计时到点自动开下一手",
          out["timeout"]["hand_no"] == 3 and out["timeout"]["ready"] is None,
          str(out["timeout"]))


def test_holdem_match_settlement_and_vote():
    """筹码不足盲注则对局结束：展示资产与段位分变化，投票过半数生效。"""

    async def run():
        dissolved = []
        room = _holdem_room(10, ("a", "b"))
        room.on_dissolve_requested = _dissolve_recorder(dissolved)
        rebuys = []
        room.on_rebuy_requested = _rebuy_stub(room, rebuys)
        await room.start()
        await _play_hand(room, "a")
        first_delta = dict(room.match_rating_delta)
        # b 只剩 1 枚（不足盲注 5），下一手打完必定触发对局结束
        room.members["b"]["stack"] = 1
        await room.start_next_hand()
        await _play_hand(room, "b")
        gate = room.view_for("a")
        await room.mark_ready("a")
        gate_mid = room.view_for("a")
        await room.mark_ready("b")
        view = room.view_for("a")
        result = view.get("match_result") or {}
        rows = {item["username"]: item for item in result.get("players", [])}
        snapshot = {
            "status": room.status,
            "gate": gate.get("hand_ready"),
            "gate_result": gate.get("match_result"),
            "gate_mid": gate_mid.get("hand_ready"),
            "reason": result.get("reason"),
            "can_next": result.get("can_next"),
            "rows": rows,
            "stacks": {name: room.members[name]["stack"] for name in room.seating},
            "first_delta": first_delta.get("a"),
            "votes": dict(room.votes),
            "rebuys": list(rebuys),
        }
        await room.cast_vote("a", "next", 2)
        mid = {"votes": dict(room.votes), "status": room.status}
        await room.cast_vote("b", "next", 2)
        after = {
            "status": room.status,
            "match_no": room.match_no,
            "blind": room.blind,
            "hand_no": room.view_for("a").get("hand_no"),
            "match_result": room.match_result,
            "delta": dict(room.match_rating_delta),
            "paid": {name: room.members[name]["paid"] for name in room.seating},
        }
        room.close()

        # 解散票：过半数即解散房间
        room2 = _holdem_room(11, ("a", "b"))
        dissolved2 = []
        room2.on_dissolve_requested = _dissolve_recorder(dissolved2)
        await room2.start()
        await _play_hand(room2, "a")
        room2.members["b"]["stack"] = 1
        await room2.start_next_hand()
        await _play_hand(room2, "b")
        await room2.continue_timeout()
        await room2.cast_vote("a", "dissolve", 5)
        await room2.cast_vote("b", "dissolve", 5)
        room2.close()
        return snapshot, mid, after, dissolved2

    snapshot, mid, after, dissolved2 = asyncio.run(run())
    rows = snapshot["rows"]
    stacks = snapshot["stacks"]
    check("盲注不足仍先展示最后一手倒计时",
          snapshot["gate"] and snapshot["gate"]["ends_match"]
          and 0 < snapshot["gate"]["left"] <= 10
          and snapshot["gate_result"] is None,
          str(snapshot["gate"]))
    check("结束本局需要全员确认", snapshot["gate_mid"]["ready"] == ["a"],
          str(snapshot["gate_mid"]))
    check("筹码不足盲注即对局结束", snapshot["status"] == "settled", snapshot["status"])
    check("结束原因写明筹码不足", "筹码不足" in (snapshot["reason"] or ""), str(snapshot["reason"]))
    check("结算列出每位玩家", sorted(rows) == ["a", "b"], str(sorted(rows)))
    for name in ("a", "b"):
        check(f"{name} 的资产等于当前筹码", rows[name]["stack"] == stacks[name],
              f"{rows[name]['stack']} vs {stacks[name]}")
        check(f"{name} 的盈亏等于资产减累计买入",
              rows[name]["net"] == round(stacks[name] - 100, 2),
              f"{rows[name]['net']}")
        check(f"{name} 结算只含本场盈亏", rows[name]["rating"]["tier"] == "白银"
              and not ({"cards", "hand_name", "folded"} & set(rows[name])))
    check("段位分按本局累计（两手各 +5）",
          snapshot["first_delta"] == 5 and rows["a"]["rating_delta"] == 10,
          f"{snapshot['first_delta']} / {rows['a']['rating_delta']}")
    check("对局结束前没有投票", snapshot["votes"] == {} and not snapshot["rebuys"])
    check("人数够时可以再来一局", snapshot["can_next"] is True)
    check("单人一票未过半不执行", len(mid["votes"]) == 1 and mid["status"] == "settled")
    check("过半数通过后开新的一局",
          after["status"] == "playing" and after["match_no"] == 2 and after["hand_no"] == 3,
          f"{after['status']} m{after['match_no']} h{after['hand_no']}")
    check("再来一局按票中盲注", after["blind"] == 2, str(after["blind"]))
    check("再来一局不继承上轮筹码", after["paid"] == {"a": 100.0, "b": 100.0}, str(after["paid"]))
    check("再来一局后清零本局段位累计", after["delta"] == {}, str(after["delta"]))
    check("新一局结算明细已清空", after["match_result"] is None)
    check("解散票过半数即解散房间", dissolved2 == ["结算解散"], str(dissolved2))


def _dissolve_recorder(sink):
    async def record(reason):
        sink.append(reason)
    return record


def _rebuy_stub(room, sink):
    """模拟宿主：结清上一轮后，每人以标准买入额重新入桌。"""
    async def rebuy():
        for name in list(room.seating):
            room.members[name]["stack"] = room.buy_in
            room.members[name]["paid"] = room.buy_in
        sink.append(len(room.seating))
    return rebuy


def test_registry():
    check("注册表包含holdem", "holdem" in ROOM_TYPES)
    check("注册表包含uno", "uno" in ROOM_TYPES)
    try:
        create_room("unknown")
        check("未注册类型报错", False)
    except ValueError:
        check("未注册类型报错", True)


def test_uno_deck_and_matches():
    deck = build_deck()
    check("UNO 牌堆108张", len(deck) == 108)
    colored = [c for c in deck if c["c"] != "w"]
    check("UNO 每色25张", all(
        sum(1 for c in colored if c["c"] == color) == 25 for color in "rygb"))
    wilds = [c for c in deck if c["c"] == "w"]
    check("UNO 万能牌8张", len(wilds) == 8
          and sum(1 for c in wilds if c["v"] == "wild") == 4)
    check("UNO 同色可出", matches("r", "5", {"c": "r", "v": "7"}))
    check("UNO 同数可出", matches("r", "5", {"c": "b", "v": "5"}))
    check("UNO 万能可出", matches("r", "5", {"c": "w", "v": "wd4"}))
    check("UNO 不匹配不可出", not matches("r", "5", {"c": "g", "v": "9"}))
    check("UNO 牌面名称", card_label({"c": "r", "v": "d2"}) == "红+2"
          and card_label({"c": "w", "v": "wild"}) == "换色")


def test_uno_lifecycle():
    async def run():
        room = create_room("uno", room_id=2, name="UNO测试", owner="a",
                           buy_in=100, blind=5)
        room.add_member("a", 100)
        room.add_member("b", 100)
        room.add_member("c", 100)

        async def broadcast_views():
            pass

        async def broadcast_payload(payload):
            pass

        room.broadcast_views = broadcast_views
        room.broadcast_payload = broadcast_payload
        await room.start()
        g = room.game
        dealt = room.in_hand() and all(len(h) == 7 for h in g["hands"].values())
        first_number = g["discard"][-1]["v"].isdigit()
        order = g["order"]

        # a 出一张同色牌：弃牌堆/当前色更新，轮到下家
        g["hands"]["a"] = [{"c": "r", "v": "3"}, {"c": "b", "v": "8"}]
        g["color"], g["value"] = "r", "5"
        g["idx"] = order.index("a")
        g["to_act"] = "a"
        await room.perform_action("a", "play", {"card": 0})
        played = (g["discard"][-1] == {"c": "r", "v": "3"}
                  and g["color"] == "r" and g["to_act"] != "a")

        # b 摸到不可出的牌：自动轮到 c
        g["hands"]["b"] = [{"c": "g", "v": "2"}]
        g["color"], g["value"] = "r", "5"
        g["idx"] = order.index("b")
        g["to_act"] = "b"
        g["deck"].append({"c": "g", "v": "9"})
        await room.perform_action("b", "draw", {})
        draw_pass = g["to_act"] == "c" and len(g["hands"]["b"]) == 2

        # c 摸到可出的牌：进入二选一，选择保留并跳过
        g["hands"]["c"] = [{"c": "g", "v": "6"}]
        g["color"], g["value"] = "r", "5"
        g["idx"] = order.index("c")
        g["to_act"] = "c"
        g["deck"].append({"c": "r", "v": "4"})
        await room.perform_action("c", "draw", {})
        drawn_pending = g["drawn_state"] == "c" and g["to_act"] == "c"
        await room.perform_action("c", "pass", {})
        kept = g["to_act"] != "c" and g["drawn_state"] is None

        # +2：下家摸 2 且被跳过（b 此前已有 2 张手牌）
        g["hands"]["a"] = [{"c": "r", "v": "d2"}, {"c": "b", "v": "3"}]
        g["color"], g["value"] = "r", "5"
        g["idx"] = order.index("a")
        g["to_act"] = "a"
        await room.perform_action("a", "play", {"card": 0})
        victim = order[(order.index("a") + 1) % len(order)]
        d2 = (len(g["hands"][victim]) == 4 and g["to_act"]
              == order[(order.index("a") + 2) % len(order)])

        # 剩 1 张进入 UNO 窗口：补喊后解除
        pending = "a" in g["uno_pending"]
        await room.perform_action("a", "uno", {})
        called = "a" not in g["uno_pending"]
        return dealt, first_number, played, draw_pass, drawn_pending, kept, d2, pending, called

    (dealt, first_number, played, draw_pass, drawn_pending, kept, d2,
     pending, called) = asyncio.run(run())
    check("UNO 开局发牌", dealt)
    check("UNO 首张为数字牌", first_number)
    check("UNO 出牌换手", played)
    check("UNO 摸牌不出自动过", draw_pass)
    check("UNO 摸牌可出二选一", drawn_pending)
    check("UNO 保留摸牌跳过", kept)
    check("UNO +2 罚摸跳过", d2)
    check("UNO 剩1张待喊", pending)
    check("UNO 补喊解除", called)


def test_uno_settlement():
    async def run():
        room = create_room("uno", room_id=3, name="UNO结算", owner="a",
                           buy_in=100, blind=5)
        room.add_member("a", 100)
        room.add_member("b", 100)

        async def broadcast_views():
            pass

        async def broadcast_payload(payload):
            pass

        async def on_rooms_changed():
            pass

        room.broadcast_views = broadcast_views
        room.broadcast_payload = broadcast_payload
        room.on_rooms_changed = on_rooms_changed
        await room.start()
        g = room.game
        order = g["order"]
        # a 一次性出完手牌获胜：b 剩 3 张按每张 5 赔付
        g["hands"]["a"] = [{"c": "r", "v": "3"}]
        g["hands"]["b"] = [{"c": "g", "v": "2"}, {"c": "g", "v": "7"},
                           {"c": "y", "v": "1"}]
        g["color"], g["value"] = "r", "9"
        g["idx"] = order.index("a")
        g["to_act"] = "a"
        await room.perform_action("a", "play", {"card": 0})
        stacks = (room.members["a"]["stack"], room.members["b"]["stack"])
        settled = (not room.in_hand() and room.status == "playing"
                   and "settle" in room.timers)
        # 结算投票：全员投「再来一局」后重新发牌
        await room.cast_vote("a", "next", 5)
        await room.cast_vote("b", "next", 5)
        redealt = room.in_hand() and all(
            len(h) == 7 for h in room.game["hands"].values())
        return stacks, settled, redealt

    stacks, settled, redealt = asyncio.run(run())
    check("UNO 出完即胜", stacks == (115.0, 85.0), str(stacks))
    check("UNO 胜后进入结算投票", settled)
    check("UNO 投票通过重发牌", redealt)


test_evaluate()
test_side_pots()
test_room_lifecycle()
test_holdem_settlement_payload()
test_holdem_hand_result_and_continue()
test_holdem_match_settlement_and_vote()
test_registry()
test_uno_deck_and_matches()
test_uno_lifecycle()
test_uno_settlement()
failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
