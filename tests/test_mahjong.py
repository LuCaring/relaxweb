#!/usr/bin/env python3
"""国标麻将引擎单元测试：python3 tests/test_mahjong.py

只测纯逻辑（不发网络请求）：
  - 牌墙构成（144/136 张、花牌开关）
  - 和牌形式识别（标准/七对/连七对/十三幺/九莲宝灯）
  - 番种计分：常用番种、就高不就低（显式不计）、情景番种
  - 听牌集合
  - 房间流程：摸打、碰、点炮胡、自摸胡、起和门槛、荒庄、离桌作废、庄位轮转
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games.base import ROOM_TYPES, create_room  # noqa: E402
from games.mahjong import (  # noqa: E402
    DRAGONS, FLOWER_MIN, WINDS, best_score, build_wall, tile_name, waits_for,
    win_forms,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ---- 牌编码快捷方式 ----
def m(n):        # n: 1-9 万
    return n - 1


def s(n):        # 条
    return 9 + n - 1


def p(n):        # 筒
    return 18 + n - 1


def h(name):     # 东南西北中发白
    return {"东": 27, "南": 28, "西": 29, "北": 30, "中": 31, "发": 32, "白": 33}[name]


def base_ctx(**kwargs):
    ctx = {
        "win_tile": None, "zimo": False, "haidi": False, "gang_draw": False,
        "qianggang": False, "juezhang": False, "menqing": True,
        "all_exposed": False, "round_wind": WINDS[0], "seat_wind": WINDS[0],
        "flowers": 0,
    }
    ctx.update(kwargs)
    return ctx


def fan_names(fans):
    return [name for name, _ in fans]


def score(tiles, melds=None, **ctx_kwargs):
    result = best_score(tiles, melds or [], base_ctx(**ctx_kwargs))
    return result[0], result[1]


def test_wall():
    wall = build_wall(True)
    check("花牌牌墙144张", len(wall) == 144)
    counts = {}
    for t in wall:
        counts[t] = counts.get(t, 0) + 1
    check("数字/字牌各4张", all(counts.get(c) == 4 for c in range(34)))
    check("每张花牌1张", all(counts.get(c) == 1 for c in range(34, 42)))
    plain = build_wall(False)
    check("关花牌墙136张", len(plain) == 136
          and all(t < FLOWER_MIN for t in plain))


def test_forms():
    std = [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9), p(1), p(2), p(3), s(5), s(5)]
    forms = win_forms(std, [])
    check("标准形式可和", any(f["type"] == "std" for f in forms))
    check("13张不成和", not win_forms(std[:13], []))
    qidui = [m(1), m(1), m(2), m(2), m(3), m(3), p(5), p(5), p(6), p(6), s(8), s(8), s(9), s(9)]
    check("七对可和", any(f["type"] == "qidui" for f in win_forms(qidui, [])))
    lian = [m(n) for n in (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7)]
    forms = win_forms(lian, [])
    check("连七对可和", any(f["type"] == "lianqidui" for f in forms))
    check("连七对不误判普通七对优先（两种形式都列出）",
          any(f["type"] == "qidui" for f in forms))
    yao = [m(1), m(9), s(1), s(9), p(1), p(9), h("东"), h("南"), h("西"),
           h("北"), h("中"), h("发"), h("白")]
    check("十三幺可和", any(f["type"] == "shisanyao"
                            for f in win_forms(yao + [m(1)], [])))
    check("十三幺缺一不成", not any(f["type"] == "shisanyao"
                                    for f in win_forms(yao + [m(5)], [])))
    jiulian = [m(1)] * 3 + [m(n) for n in range(2, 9)] + [m(9)] * 3 + [m(9)]
    check("九莲宝灯（9万和）", any(f["type"] == "jiulian"
                                  for f in win_forms(jiulian, [])))
    jiulian1 = [m(1)] * 4 + [m(n) for n in range(2, 9)] + [m(9)] * 3
    check("九莲宝灯（1万和）", any(f["type"] == "jiulian"
                                  for f in win_forms(jiulian1, [])))
    check("非九莲形态不成九莲宝灯",
          not any(f["type"] == "jiulian"
                  for f in win_forms([m(1), m(1), m(1), m(2), m(2), m(3), m(3),
                                      m(4), m(5), m(6), m(7), m(8), m(9), m(9)], [])))


def test_basic_fans():
    # 平和：四顺 + 数牌将，门清自摸
    fans, total = score([m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9),
                         p(1), p(2), p(3), s(5), s(5)], win_tile=s(5), zimo=True)
    names = fan_names(fans)
    check("平和计入", "平和" in names, str(names))
    check("门清自摸三件套", {"自摸", "门前清", "不求人"} <= set(names), str(names))
    check("缺一门不计（三花色）", "缺一门" not in names)
    check("单钓将计入", "单钓将" in names, str(names))

    # 碰碰和：一副明刻 + 三副暗刻（不构成四暗刻）
    peng = [{"type": "peng", "tiles": [p(7)] * 3, "from": "x"}]
    fans, _ = score([m(1), m(1), m(1), m(3), m(3), m(3), p(5), p(5), p(5),
                     s(9), s(9)], melds=peng, win_tile=s(9), zimo=True,
                    menqing=False)
    names = fan_names(fans)
    check("碰碰和计入", "碰碰和" in names, str(names))
    check("三暗刻计入", "三暗刻" in names, str(names))
    check("双暗刻被三暗刻不计", "双暗刻" not in names, str(names))
    check("幺九刻计入", "幺九刻" in names, str(names))
    check("断幺不计（有 333/555/777）", "断幺" not in names, str(names))
    check("单钓将计入（9万对）", "单钓将" in names, str(names))

    # 四暗刻：不计碰碰和
    fans, _ = score([m(1), m(1), m(1), m(3), m(3), m(3), p(5), p(5), p(5),
                     p(7), p(7), p(7), s(9), s(9)], win_tile=s(9), zimo=True)
    names = fan_names(fans)
    check("四暗刻计入", "四暗刻" in names, str(names))
    check("四暗刻不计碰碰和", "碰碰和" not in names, str(names))

    # 清一色
    fans, _ = score([m(1), m(2), m(3), m(2), m(3), m(4), m(5), m(6), m(7),
                     m(7), m(8), m(9), m(6), m(6)], win_tile=m(6), zimo=False)
    check("清一色计入", "清一色" in fan_names(fans))
    # 混一色
    fans, _ = score([m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9),
                     h("中"), h("中"), h("中"), m(3), m(3)], win_tile=m(3))
    names = fan_names(fans)
    check("混一色计入", "混一色" in names, str(names))
    check("清一色不与混一色同现", "清一色" not in names)
    check("箭刻计入", "箭刻" in names, str(names))


def test_special_fans():
    # 大三元（自摸 1万 单钓）：88，箭刻被不计
    fans, total = score([h("中"), h("中"), h("中"), h("发"), h("发"), h("发"),
                         h("白"), h("白"), h("白"), m(1), m(1), m(2), m(3), m(1)],
                        win_tile=m(1), zimo=True)
    names = fan_names(fans)
    check("大三元计入", "大三元" in names, str(names))
    check("大三元不计箭刻", "箭刻" not in names, str(names))
    check("大三元手暗刻可加计三暗刻", "三暗刻" in names, str(names))
    check("混一色/字一色同时判定", "字一色" not in names and "混一色" in names,
          str(names))

    # 清幺九：88，碰碰和/混幺九被不计
    fans, _ = score([m(1), m(1), m(1), m(9), m(9), m(9), p(1), p(1), p(1),
                     p(9), p(9), p(9), s(1), s(1)], win_tile=s(1))
    names = fan_names(fans)
    check("清幺九计入", "清幺九" in names, str(names))
    check("清幺九不计碰碰和", "碰碰和" not in names, str(names))
    check("清幺九不计混幺九", "混幺九" not in names, str(names))

    # 十三幺：门清/不求人被不计，单钓计入
    yao = [m(1), m(9), s(1), s(9), p(1), p(9), h("东"), h("南"), h("西"),
           h("北"), h("中"), h("发"), h("白")]
    fans, total = score(yao + [m(1)], win_tile=m(1), zimo=True)
    names = fan_names(fans)
    check("十三幺计入", "十三幺" in names, str(names))
    check("十三幺不计门前清/不求人",
          "门前清" not in names and "不求人" not in names, str(names))
    check("十三幺计单钓将", "单钓将" in names, str(names))

    # 双暗杠：6分，不计暗杠单次与双暗刻
    melds = [
        {"type": "angang", "tiles": [s(5)] * 4, "from": None},
        {"type": "angang", "tiles": [s(9)] * 4, "from": None},
    ]
    fans, _ = score([m(1), m(2), m(3), m(4), m(5), m(6), p(5), p(5)], melds=melds,
                    win_tile=p(5), zimo=True)
    names = fan_names(fans)
    check("双暗杠计入", "双暗杠" in names, str(names))
    check("双暗杠不计暗杠", "暗杠" not in names, str(names))
    check("双暗杠不计双暗刻", "双暗刻" not in names, str(names))

    # 无番和：无任何结构番种
    fans, total = score([m(1), m(2), m(3), m(6), m(7), m(8), p(2), p(3), p(4),
                         s(7), s(8), s(9), h("白"), h("白")],
                        win_tile=p(4))
    names = fan_names(fans)
    check("无番和计入", "无番和" in names, str(names))
    check("无番和时无字不计", "无字" not in names and "断幺" not in names,
          str(names))


def test_situational_fans():
    tiles = [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9),
             p(1), p(2), p(3), s(5), s(5)]
    fans, _ = score(tiles, win_tile=s(5), zimo=True, haidi=True)
    check("妙手回春=自摸海底", "妙手回春" in fan_names(fans)
          and "海底捞月" not in fan_names(fans))
    fans, _ = score(tiles, win_tile=s(5), zimo=False, haidi=True)
    check("海底捞月=点炮海底", "海底捞月" in fan_names(fans))
    fans, _ = score(tiles, win_tile=s(5), zimo=True, gang_draw=True)
    check("杠上开花", "杠上开花" in fan_names(fans))
    fans, _ = score(tiles, win_tile=s(5), zimo=False, qianggang=True)
    check("抢杠胡", "抢杠胡" in fan_names(fans))
    fans, _ = score(tiles, win_tile=s(5), zimo=False, juezhang=True)
    check("和绝张", "和绝张" in fan_names(fans))
    fans, _ = score(tiles, win_tile=s(5), zimo=True, flowers=3)
    check("花牌每张1分", fan_names(fans).count("花牌") == 3)
    # 圈风刻 + 门风刻（东风圈东家碰东风）
    tiles = [h("东"), h("东"), h("东"), m(1), m(2), m(3), m(4), m(5), m(6),
             p(7), p(8), p(9), s(5), s(5)]
    fans, _ = score(tiles, win_tile=s(5), round_wind=h("东"), seat_wind=h("东"))
    names = fan_names(fans)
    check("圈风刻与门风刻同刻并计",
          names.count("圈风刻") == 1 and names.count("门风刻") == 1, str(names))
    # 全求人：四副全明 + 点炮
    melds = [
        {"type": "peng", "tiles": [m(1)] * 3, "from": "x"},
        {"type": "chi", "tiles": [m(4), m(5), m(6)], "from": "x"},
        {"type": "chi", "tiles": [m(7), m(8), m(9)], "from": "x"},
        {"type": "chi", "tiles": [p(1), p(2), p(3)], "from": "x"},
    ]
    fans, _ = score([s(5), s(5)], melds=melds, win_tile=s(5), zimo=False,
                    menqing=False, all_exposed=True)
    check("全求人", "全求人" in fan_names(fans))


def test_wait_shapes():
    base = [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9)]
    # 坎张：2/4筒等 3筒
    fans, _ = score(base + [p(2), p(4), s(5), s(5), p(3)], win_tile=p(3))
    check("坎张计入", "坎张" in fan_names(fans), str(fan_names(fans)))
    # 边张：1/2筒等 3筒
    fans, _ = score(base + [p(1), p(2), s(5), s(5), p(3)], win_tile=p(3))
    check("边张（12等3）", "边张" in fan_names(fans), str(fan_names(fans)))
    # 边张：8/9万等 7万
    fans, _ = score([m(8), m(9), m(1), m(2), m(3), p(1), p(2), p(3),
                     s(1), s(2), s(3), s(5), s(5), m(7)], win_tile=m(7))
    check("边张（89等7）", "边张" in fan_names(fans), str(fan_names(fans)))
    # 两面听不计边坎钓
    fans, _ = score(base + [p(2), p(3), s(5), s(5), p(4)], win_tile=p(4))
    check("两面听不计边坎钓",
          not {"边张", "坎张", "单钓将"} & set(fan_names(fans)),
          str(fan_names(fans)))


def test_waits():
    hand = [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8), m(9), p(1), p(2), s(5), s(5)]
    waits = waits_for(hand, [])
    check("听3筒", p(3) in waits, str(tile_name(w) for w in waits))
    check("听张不超过4种", len(waits) <= 4, str(waits))
    check("14张不算听牌", waits_for(hand + [m(1)], []) == [])


def make_room(rules=None):
    room = create_room("mahjong", room_id=99, name="麻将测试", owner="a",
                       buy_in=200, blind=1, rules=rules or {})
    for name in ("a", "b", "c", "d"):
        room.add_member(name, 200)

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def force_hand(room, name, tiles, win_tile=None, flowers=()):
    g = room.game
    g["hands"][name] = list(tiles)
    g["flowers"][name] = list(flowers)
    g["last_draw"] = win_tile if win_tile is not None else (
        tiles[-1] if g["to_act"] == name else None)


def test_chi_claim():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        # a（庄）打 3万；b 是 a 的下家，手里 4万5万可吃；c/d 不可吃
        force_hand(room, "a", [m(3), m(1), m(1), m(2), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发"), h("白"), h("北")])
        force_hand(room, "b", [m(4), m(5), m(1), m(1), m(2), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发")])
        force_hand(room, "c", [m(4), m(5), m(1), m(2), m(3), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发")])
        force_hand(room, "d", [m(4), m(5), m(1), m(2), m(3), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发")])
        await room.perform_action("a", "discard", {"index": 0})
        options = g["claim"]["options"] if g["claim"] else {}
        only_next = options.get("b", {}).get("chi")
        others = any(options.get(n, {}).get("chi") for n in ("c", "d"))
        pair = only_next[0]
        await room.perform_action("b", "claim", {"kind": "chi", "tiles": pair})
        meld = g["melds"]["b"][0] if g["melds"]["b"] else {}
        chi_ok = (meld.get("type") == "chi"
                  and sorted(meld.get("tiles", [])) == sorted(pair + [m(3)])
                  and g["to_act"] == "b" and g["phase"] == "discard"
                  and g["discards"]["a"] == []
                  and len(g["hands"]["b"]) == 11)
        # 吃后打牌继续正常（声明结算是元组/字符串混用，曾在此抛异常）
        tile_out = g["hands"]["b"][0]
        await room.perform_action("b", "discard", {"index": 0})
        # 下家可能对这张牌有吃碰响应，因此只要求牌落河且流程推进
        advanced = (g["discards"]["b"] == [tile_out]
                    and g["phase"] in ("claim", "discard")
                    and (g["phase"] == "claim" or g["to_act"] == "c"))
        return bool(only_next), others, chi_ok, advanced

    has_chi, others, chi_ok, advanced = asyncio.run(run())
    check("只有下家可吃", has_chi and not others)
    check("吃牌成副露并轮到出牌", chi_ok)
    check("吃后正常继续", advanced)


def test_gang_claim_and_draw():
    async def run(replacement_flowers):
        room = make_room()
        await room.start()
        g = room.game
        # a 打 5万，b 手里三张 5万可明杠
        force_hand(room, "a", [m(5), m(1), m(2), m(3), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发"), h("白"), h("北")])
        force_hand(room, "b", [m(5), m(5), m(5), m(1), m(2), m(3), p(1), p(2),
                               p(3), s(1), s(2), s(3), h("中")])
        force_hand(room, "c", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(5)])
        force_hand(room, "d", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(9)])
        # 杠从牌尾补牌；分别验证普通补牌和连续补花，不依赖随机牌尾。
        consumed = 1 + len(replacement_flowers)
        g["wall"][-consumed:] = [p(9), *replacement_flowers]
        wall_before = len(g["wall"])
        await room.perform_action("a", "discard", {"index": 0})
        can_gang = g["claim"]["options"]["b"].get("gang")
        await room.perform_action("b", "claim", {"kind": "gang"})
        meld = g["melds"]["b"][0] if g["melds"]["b"] else {}
        gang_ok = (bool(can_gang) and meld.get("type") == "gang"
                   and len(meld.get("tiles", [])) == 4
                   and g["to_act"] == "b" and g["phase"] == "discard"
                   and g["gang_draw"] and len(g["hands"]["b"]) == 11
                   and len(g["wall"]) == wall_before - consumed
                   and g["flowers"]["b"] == list(reversed(replacement_flowers)))
        # 暗杠：把 b 手牌换成四张相同
        force_hand(room, "b", [s(7)] * 4 + [m(1), m(2), m(3), p(1), p(2), p(3)])
        g["gang_draw"] = False
        g["wall"][-consumed:] = [p(9), *replacement_flowers]
        wall2 = len(g["wall"])
        await room.perform_action("b", "angang", {"index": 0})
        angang_ok = (len(g["melds"]["b"]) == 2
                     and g["melds"]["b"][1]["type"] == "angang"
                     and len(g["hands"]["b"]) == 7
                     and len(g["wall"]) == wall2 - consumed
                     and g["flowers"]["b"] == list(reversed(replacement_flowers)))
        return gang_ok, angang_ok

    for flowers in ((), (34, 35)):
        gang_ok, angang_ok = asyncio.run(run(flowers))
        suffix = "（连续补花）" if flowers else ""
        check("明杠成副露并补牌" + suffix, gang_ok)
        check("暗杠成副露并补牌" + suffix, angang_ok)


def test_rules_sanitize():
    room = make_room({"min_fan": 4, "flowers": False, "chow": False,
                      "dianpao_full": False, "junk": 1})
    check("规则清洗", room.rules == {"min_fan": 4, "flowers": False, "chow": False,
                                     "dianpao_full": False}, str(room.rules))
    room = make_room({"min_fan": 99})
    check("非法起和回退8", room.rules["min_fan"] == 8)


def test_discard_and_peng():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        check("开局每人13张+庄家摸牌14张",
              len(g["hands"]["a"]) == 14 and all(
                  len(g["hands"][n]) == 13 for n in ("b", "c", "d")))
        # a（庄）打 5万，b 手里两张 5万可碰；c/d 无动作
        force_hand(room, "a", [m(5), m(1), m(2), m(3), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发"), h("白"), h("北")])
        force_hand(room, "b", [m(5), m(5), m(1), m(2), m(3), p(1), p(2), p(3),
                               s(1), s(2), s(3), h("中"), h("发")])
        force_hand(room, "c", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(5)])
        force_hand(room, "d", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(9)])
        await room.perform_action("a", "discard", {"index": 0})
        claim_open = g["phase"] == "claim" and "b" in g["claim"]["options"] \
            and g["claim"]["options"]["b"].get("peng")
        await room.perform_action("b", "claim", {"kind": "peng"})
        peng_ok = (len(g["melds"]["b"]) == 1
                   and g["melds"]["b"][0]["type"] == "peng"
                   and g["phase"] == "discard" and g["to_act"] == "b"
                   and len(g["hands"]["b"]) == 11
                   and g["discards"]["a"] == [])
        # b 打一张无人有动作的牌（发），直接轮到 c 摸牌
        await room.perform_action("b", "discard", {"index": 10})
        advanced = g["to_act"] == "c" and g["phase"] == "discard" \
            and len(g["hands"]["c"]) == 14
        return room, claim_open, peng_ok, advanced

    room, claim_open, peng_ok, advanced = asyncio.run(run())
    check("打出可碰牌进入声明窗", claim_open)
    check("碰后进入打牌阶段", peng_ok)
    check("无人响应自动过到下家摸牌", advanced)


def test_hu_on_discard():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        # b 听七对（单骑 7条）：七对24 + 门前清2 ≥ 8 起和
        force_hand(room, "a", [s(7), m(1), m(2), m(3), s(1), s(2), s(3),
                               p(1), p(2), p(3), h("北"), h("南"), h("西"), h("东")])
        force_hand(room, "b", [m(1), m(1), m(2), m(2), m(3), m(3), m(4), m(4),
                               p(5), p(5), p(6), p(6), s(7)])
        force_hand(room, "c", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(5)])
        force_hand(room, "d", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), p(1), p(2), p(3), s(9)])
        await room.perform_action("a", "discard", {"index": 0})
        options_ok = g["phase"] == "claim" and g["claim"]["options"]["b"].get("hu")
        stacks_before = {n: room.members[n]["stack"] for n in "abcd"}
        await room.perform_action("b", "claim", {"kind": "hu"})
        result = g["result"]
        settled = not room.in_hand() and result and result["winner"] == "b"
        # 点炮包三家：庄家出牌付 3 份
        unit = result["unit"] if result else 0
        payouts_ok = result and result["payouts"] == {"a": round(unit * 3, 2)}
        stacks_ok = room.members["a"]["stack"] == stacks_before["a"] - unit * 3 \
            and room.members["b"]["stack"] == stacks_before["b"] + unit * 3
        dealer_moved = room.dealer_idx == 1
        return options_ok, settled, result, payouts_ok, stacks_ok, dealer_moved

    (options_ok, settled, result, payouts_ok, stacks_ok,
     dealer_moved) = asyncio.run(run())
    check("点炮胡选项可达起和", options_ok)
    check("胡牌进入结算", settled, str(result and result.get("fans")))
    check("点炮包三家付3份", bool(payouts_ok), str(result and result["payouts"]))
    check("筹码结算正确", stacks_ok)
    check("结算载荷带段位明细", "ratings" in result)
    check("非庄胡牌后下家坐庄", dealer_moved)
    names = fan_names([(f["name"], f["value"]) for f in result["fans"]]) \
        if result else []
    check("七对/门前清计入", {"七对", "门前清"} <= set(names), str(names))


def test_hu_below_min_fan():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        # 确保固定手牌夹具不会继承开局随机发到的花牌加分。
        g["flowers"]["b"] = [34, 35, 36, 37]
        # b 的固定手牌不含花牌加分，不足 8 分起和。
        force_hand(room, "a", [s(5), m(1), m(2), m(3), s(1), s(2), s(3),
                               p(1), p(2), p(3), h("北"), h("南"), h("西"), h("东")])
        force_hand(room, "b", [m(1), m(2), m(3), m(4), m(5), m(6), p(7), p(8),
                               p(9), p(3), p(3), p(3), s(5)])
        force_hand(room, "c", [m(1), m(9), s(1), s(9), p(1), p(9), h("中"),
                               h("发"), h("白"), m(5), m(6), m(7), m(8)])
        force_hand(room, "d", [m(1), m(9), s(1), s(9), p(1), p(9), h("中"),
                               h("发"), h("白"), m(5), m(6), m(7), p(9)])
        await room.perform_action("a", "discard", {"index": 0})
        no_claim = g["phase"] == "discard"
        advanced = g["to_act"] == "b" and len(g["hands"]["b"]) == 14
        return no_claim and advanced

    check("不足起和分不能胡", asyncio.run(run()))


def test_zimo():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        # a（庄）自摸：清一色大牌
        win_tile = m(9)
        force_hand(room, "a", [m(1), m(2), m(3), m(4), m(5), m(6), m(7), m(8),
                               m(9), m(2), m(3), m(4), m(6), m(6)], win_tile)
        opts = room.options_for("a")
        zimo_ok = opts["zimo"]
        before = {n: room.members[n]["stack"] for n in "abcd"}
        await room.perform_action("a", "hu", {})
        result = g["result"]
        stacks_after = {n: room.members[n]["stack"] for n in "abcd"}
        zimo_paid = all(
            stacks_after[n] == before[n] - result["unit"] for n in ("b", "c", "d"))
        a_gained = stacks_after["a"] == before["a"] + result["unit"] * 3
        dealer_stayed = room.dealer_idx == 0 and result["dealer_repeat"]
        return zimo_ok, result, zimo_paid, a_gained, dealer_stayed

    zimo_ok, result, zimo_paid, a_gained, dealer_stayed = asyncio.run(run())
    check("自摸选项可达起和", zimo_ok)
    check("自摸三家各付一份", zimo_paid and a_gained, str(result["payouts"]))
    check("庄家自摸连庄", dealer_stayed)
    check("清一色计入", "清一色" in fan_names(
        [(f["name"], f["value"]) for f in result["fans"]]))


def test_draw_game_and_abort():
    async def run():
        room = make_room()
        await room.start()
        g = room.game
        force_hand(room, "a", [m(1), m(2), m(3), s(1), s(2), s(3),
                               p(1), p(2), p(3), h("北"), h("南"), h("西"), h("东")])
        force_hand(room, "b", [m(5), m(6), m(7), s(5), s(6), s(7), p(5), p(6),
                               p(7), h("北"), h("南"), h("西"), h("东")])
        force_hand(room, "c", [m(5), m(6), m(7), s(5), s(6), s(7), p(5), p(6),
                               p(7), h("北"), h("南"), h("西"), h("东")])
        force_hand(room, "d", [m(5), m(6), m(7), s(5), s(6), s(7), p(5), p(6),
                               p(7), h("北"), h("南"), h("西"), h("东")])
        g["wall"] = []
        await room.perform_action("a", "discard", {"index": 0})
        drawn = not room.in_hand() and g["result"]["draw_game"] \
            and g["result"]["dealer_repeat"]
        stacks_ok = all(m["stack"] == 200 for m in room.members.values())
        # 离桌作废
        for name in ("a", "b", "c", "d"):
            await room.cast_vote(name, "next", 1)
        g = room.game
        force_hand(room, "a", [m(1)] * 14)
        mid = room.in_hand()
        room.remove_member("c")
        await room.progress_game()
        aborted = mid and not room.in_hand() and g["result"]["aborted"]
        # 只剩 3 人不能开局
        try:
            await room.start()
            blocked = False
        except ValueError:
            blocked = True
        return drawn, stacks_ok, aborted, blocked

    drawn, stacks_ok, aborted, blocked = asyncio.run(run())
    check("牌墙摸空荒庄连庄", drawn)
    check("荒庄不计分", stacks_ok)
    check("离桌本手作废", aborted)
    check("人数不足不能开局", blocked)


def test_registry():
    check("注册表包含mahjong", "mahjong" in ROOM_TYPES)
    room = create_room("mahjong", room_id=1, name="x", owner="a",
                       buy_in=100, blind=5)
    check("mahjong房间4人上限", room.max_seats == 4)


test_wall()
test_forms()
test_basic_fans()
test_special_fans()
test_situational_fans()
test_wait_shapes()
test_waits()
test_chi_claim()
test_gang_claim_and_draw()
test_rules_sanitize()
test_discard_and_peng()
test_hu_on_discard()
test_hu_below_min_fan()
test_zimo()
test_draw_game_and_abort()
test_registry()
failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
