#!/usr/bin/env python3
"""狼人杀引擎单元测试：python3 tests/test_werewolf.py

只测纯逻辑（不发网络请求）：
  - 规则清洗与板子解析（预设/自定义/非法配置）
  - 角色分发与视图可见性（狼人互识、局外人不泄露、死亡翻牌、观战视角）
  - 夜晚流程：守卫→狼人→女巫→预言家、守护/解药/毒药/查验、显式留白、
    连守禁制、女巫自救规则、平安夜
  - 白天流程：死讯公布、投票放逐、平票重投/无人出局、遗言队列、猎人开枪
    （被刀/被放逐可开枪，被毒默认不可）
  - 胜负判定：狼灭好人胜、屠边、屠城、人数兜底；离桌出局与胜负复判
  - 聊天定向：狼人夜晚 wolf 频道、死者 dead 频道、visible_chat 过滤
  - 金币结算（输方付底注、胜方均分、筹码守恒）与随机完整对局
"""
import asyncio
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games import randomness  # noqa: E402
from games.base import create_room  # noqa: E402
from games.werewolf import ROLES, is_wolf_role, resolve_board  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def make_room(rules=None, players=("a", "b", "c", "d", "e", "f"),
              buy_in=100, blind=5):
    room = create_room("werewolf", room_id=1, name="测试", owner=players[0],
                       buy_in=buy_in, blind=blind, rules=rules or {})
    for name in players:
        room.add_member(name, buy_in)

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def run_identity_shuffle(coro_fn):
    """固定洗牌为恒等变换：板子顺序即座位顺序，角色完全可预期。"""
    old = randomness.shuffle
    randomness.shuffle = lambda items: None
    try:
        return asyncio.run(coro_fn())
    finally:
        randomness.shuffle = old


def six_room(rules=None):
    """经典 6 人局：a/b 狼，c 预言家，d 女巫，e/f 平民。"""
    return make_room(rules)


async def skip_to_vote(room):
    """把当前夜晚全部空过并吸收遗言/开枪，推进到投票阶段（或对局结束）。"""
    g = room.game
    for _ in range(40):
        phase = g["phase"]
        if phase in ("vote", "showdown"):
            return
        if phase == "night":
            ability = ROLES[g["night_role"]]["ability"]
            for name in list(g.get("pending", [])):
                if ability == "witch":
                    await room.perform_action(name, "witch", {"save": False})
                else:
                    await room.perform_action(name, "night", {})
            if g["phase"] == "night" and g.get("night_role") == g["night_role"]:
                await room._advance_night()
        elif phase == "day":
            await room._advance_speech()
        elif phase == "last_words":
            await room._advance_last_words()
        elif phase == "shot":
            await room._close_shot()
        else:
            return


# ---- 规则与板子 ----
def test_sanitize_and_board():
    room = make_room({"board": "bad", "win_mode": "tucheng",
                      "witch_self_save": "no", "last_words": "sometimes",
                      "tie": "coin", "speak_seconds": 1, "vote_seconds": 99999})
    check("非法规则回落默认", room.rules["board"] is None
          and room.rules["win_mode"] == "bian"
          and room.rules["witch_self_save"] == "first"
          and room.rules["last_words"] == "first"
          and room.rules["tie"] == "revote"
          and room.rules["speak_seconds"] == 15
          and room.rules["vote_seconds"] == 600, str(room.rules))
    board = ["werewolf", "seer", "witch", "villager", "villager"]
    room = make_room({"board": board, "win_mode": "cheng",
                      "witch_self_save": "always", "guard_continuous": True,
                      "hunter_shot_on_poison": True,
                      "last_words": "none", "tie": "no_exile",
                      "speak_seconds": 45})
    check("合法自定义规则保留", room.rules["board"] == board
          and room.rules["win_mode"] == "cheng"
          and room.rules["witch_self_save"] == "always"
          and room.rules["guard_continuous"] is True
          and room.rules["hunter_shot_on_poison"] is True
          and room.rules["last_words"] == "none"
          and room.rules["tie"] == "no_exile"
          and room.rules["speak_seconds"] == 45)
    preset, error = resolve_board({"board": None}, 9)
    check("9 人预设板子 3 狼", preset is not None
          and sum(1 for k in preset if is_wolf_role(k)) == 3, str(preset))
    board, error = resolve_board({"board": ["werewolf"] * 4}, 5)
    check("自定义板子人数不符报错", board is None and "5 个角色" in error, error)
    board, error = resolve_board({"board": ["seer", "villager"]}, 2)
    check("无狼板子报错", board is None and "狼人" in error, error)
    board, error = resolve_board({"board": None}, 7)
    check("无预设人数要求自定义板子", board is None and "board" in error, error)


# ---- 分发与视图可见性 ----
def test_assignment_and_views():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        roles_ok = g["roles"] == {"a": "werewolf", "b": "werewolf",
                                  "c": "seer", "d": "witch",
                                  "e": "villager", "f": "villager"}
        wolf_view = room.view_for("a")
        seer_view = room.view_for("c")
        f_view = room.view_for("f")
        wolf_sees = [p["role"] for p in wolf_view["players"]
                     if p["username"] in ("a", "b")]
        villager_hidden = all(p["role"] is None for p in seer_view["players"]
                              if p["username"] != "c")
        room.add_spectator("bob", "a")
        spectated = room.spectator_view("bob")
        return (roles_ok, wolf_view["your_role"], wolf_sees, f_view,
                villager_hidden, spectated)

    (roles_ok, your_role, wolf_sees, f_view, villager_hidden,
     spectated) = run_identity_shuffle(run)
    check("板子按座位顺序分发", roles_ok, str(roles_ok))
    check("狼人视角带阵营与队友", your_role["faction"] == "wolf"
          and {t["username"] for t in your_role["teammates"]} == {"b"},
          str(your_role))
    check("狼队友在座位列表互相可见", wolf_sees == ["狼人", "狼人"], str(wolf_sees))
    check("好人看不到任何存活者身份", villager_hidden)
    check("平民视图无私有字段", "your_options" not in f_view
          and "check_log" not in f_view and "witch_stock" not in f_view)
    check("观战视角不泄露角色且无操作", spectated["spectator"] is True
          and all(p["role"] is None for p in spectated["players"])
          and "your_options" not in spectated and "your_role" not in spectated)


# ---- 夜晚：守护 / 刀 / 救 / 毒 / 验 ----
def test_night_save_and_check():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        steps = [key for _, key in g["night"]["steps"]]
        step_order_ok = steps == ["werewolf", "witch", "seer"]
        partial = g["pending"] == ["a", "b"] \
            and "your_options" in room.view_for("a")
        await room.perform_action("a", "night", {"target": "e"})
        waiting_b = g["phase"] == "night" and g["night_role"] == "werewolf" \
            and g["night"]["kill"] == "e"
        await room.perform_action("b", "night", {"target": "e"})
        now_witch = g["night_role"] == "witch" and g["pending"] == ["d"]
        witch_opts = room.view_for("d").get("your_options", {})
        witch_sees_kill = witch_opts.get("kill_target", {}).get("username") == "e"
        await room.perform_action("d", "witch", {"save": True})
        now_seer = g["night_role"] == "seer" and g["pending"] == ["c"]
        await room.perform_action("c", "night", {"target": "a"})
        check_log = room.view_for("c")["check_log"]
        check_ok = bool(check_log) and check_log[0]["is_wolf"] is True \
            and check_log[0]["target"] == "a"
        peaceful = g["phase"] == "day" and g["day_no"] == 1 \
            and not g["last_night"] and len(g["alive"]) == 6
        history_ok = any("平安夜" in e["text"] for e in g["history"])
        return (step_order_ok, partial, waiting_b, now_witch, witch_sees_kill,
                now_seer, check_ok, peaceful, history_ok)

    (step_order_ok, partial, waiting_b, now_witch, witch_sees_kill, now_seer,
     check_ok, peaceful, history_ok) = run_identity_shuffle(run)
    check("夜晚苏醒次序 狼→女巫→预言家", step_order_ok, str(step_order_ok))
    check("狼人待决断时给出选项", partial)
    check("单狼提交后等待另一狼", waiting_b)
    check("全员提交后进入女巫阶段", now_witch)
    check("女巫能看到刀口", witch_sees_kill)
    check("女巫提交后进入预言家阶段", now_seer)
    check("查验结果只记给预言家且判定正确", check_ok, str(check_ok))
    check("解药救下刀口成平安夜", peaceful)
    check("历史记录平安夜", history_ok)


def test_night_kill_and_poison():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {"target": ""})   # 显式空刀
        await room.perform_action("b", "night", {"target": ""})
        await room.perform_action("d", "witch", {"save": False, "poison": "e"})
        await room.perform_action("c", "night", {})
        deaths_ok = g["last_night"] == {"e": "poison"} \
            and sorted(g["alive"]) == ["a", "b", "c", "d", "f"]
        view = room.view_for("a")
        hidden = {p["username"]: p["role"] for p in view["players"]}
        hidden_ok = hidden["e"] is None and hidden["c"] is None  # 出局不翻牌
        god = room.view_for("e")            # 出局者只看得到自己的身份
        god_roles = {p["username"]: p["role"] for p in god["players"]}
        god_view_ok = "god_view" not in god \
            and god_roles["e"] == "平民" and god_roles["c"] is None
        history_ok = all("（" not in e["text"] or e["kind"] != "dawn"
                         for e in g["history"])   # 死讯不再公布角色
        last_words_ok = g["phase"] == "last_words" \
            and g["last_words"]["current"] == "e"
        await room._advance_last_words()
        day_ok = g["phase"] == "day" and g["last_words"]["current"] is None
        god_after = room.view_for("e")      # 遗言说完即转上帝视角
        god_after_ok = god_after["god_view"] is True \
            and {p["username"]: p["role"] for p in god_after["players"]}["c"] \
            == "预言家"
        stock = room.view_for("d")["witch_stock"]
        return deaths_ok, hidden_ok, god_view_ok, history_ok, last_words_ok, \
            day_ok, god_after_ok, stock

    deaths_ok, hidden_ok, god_view_ok, history_ok, last_words_ok, day_ok, \
        god_after_ok, stock = run_identity_shuffle(run)
    check("空刀加毒药生效", deaths_ok, str(deaths_ok))
    check("出局不翻牌而存活者隐藏", hidden_ok, str(hidden_ok))
    check("遗言中只见自己身份，他人保密", god_view_ok)
    check("死讯不公布角色", history_ok)
    check("首夜死者进入遗言队列", last_words_ok)
    check("遗言耗尽进入白天", day_ok)
    check("遗言了结后进入上帝视角", god_after_ok)
    check("女巫药水库存扣除", stock == {"save": True, "poison": False},
          str(stock))


def test_single_actor_submit_locks():
    async def run():
        # 双预言家自定义板：第一名提交后本夜不能再改验
        rules = {"board": ["werewolf", "seer", "seer",
                           "villager", "villager", "villager"]}
        room = make_room(rules)
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {"target": ""})   # 唯一狼空刀
        seers = [n for n in g["alive"] if g["roles"][n] == "seer"]
        at_seer = g["night_role"] == "seer" and sorted(g["pending"]) == sorted(seers)
        await room.perform_action(seers[0], "night", {"target": "a"})
        first = g["night"]["checks"][seers[0]]
        await room.perform_action(seers[0], "night", {"target": "b"})
        locked = g["night"]["checks"][seers[0]] == first \
            and len(room.view_for(seers[0])["check_log"]) == 1
        await room.perform_action(seers[1], "night", {"target": "a"})
        both_done = g["phase"] == "day" and g["night"]["kill"] is None
        return at_seer, locked, both_done

    at_seer, locked, both_done = run_identity_shuffle(run)
    check("双预言家同时待决断", at_seer)
    check("单角色提交后锁定不可改验", locked)
    check("全员提交后进入白天且空刀", both_done)


def test_witch_rules_and_guard():
    async def run():
        # 5 人自定义板：a 守卫 b 狼 c 女巫 d 预言家 e 平民
        rules = {"board": ["guard", "werewolf", "witch", "seer", "villager"],
                 "witch_self_save": "never"}
        room = make_room(rules, players=("a", "b", "c", "d", "e"))
        await room.start()
        g = room.game
        # 守卫守 e，狼刀 e → 被守护，平安夜
        await room.perform_action("a", "night", {"target": "e"})
        await room.perform_action("b", "night", {"target": "e"})
        await room.perform_action("c", "witch", {"save": False})
        await room.perform_action("d", "night", {})
        guard_saved = not g["last_night"] and g["phase"] == "day"
        while g["phase"] == "day":
            await room._advance_speech()
        await room._phase_timeout()          # 无人投票 → 无人出局 → 第 2 夜
        night2 = g["day_no"] == 2 and g["phase"] == "night"
        # 守卫不能连守同一人：提交 e 被拒绝，改守 d 成功
        await room.perform_action("a", "night", {"target": "e"})
        rejected = g["night"]["protect"].get("a") != "e"
        await room.perform_action("a", "night", {"target": "d"})
        protect_ok = g["night"]["protect"].get("a") == "d"
        # 狼刀女巫 c：自救规则 never → 不能救
        await room.perform_action("b", "night", {"target": "c"})
        witch_opts = room.view_for("c").get("your_options", {})
        self_save_blocked = bool(witch_opts) \
            and witch_opts["kill_target"]["username"] == "c" \
            and witch_opts["can_save"] is False
        await room.perform_action("c", "witch", {"save": True})
        refused = "c" not in g["night"]["witch"]
        await room.perform_action("c", "witch", {"save": False})
        await room.perform_action("d", "night", {})
        witch_dead = g["last_night"].get("c") == "kill" and "c" not in g["alive"]
        return (guard_saved, night2, rejected, protect_ok, self_save_blocked,
                refused, witch_dead)

    (guard_saved, night2, rejected, protect_ok, self_save_blocked, refused,
     witch_dead) = run_identity_shuffle(run)
    check("守护抵消刀口", guard_saved)
    check("平安进入第二天夜晚", night2)
    check("连守同一人被拒绝", rejected)
    check("改守他人成功", protect_ok)
    check("never 自救规则下女巫看不到救人选项", self_save_blocked)
    check("违规自救提交被拒绝", refused)
    check("女巫被刀身亡", witch_dead)


def test_witch_always_self_save():
    async def run():
        rules = {"board": ["guard", "werewolf", "witch", "seer", "villager"],
                 "witch_self_save": "always"}
        room = make_room(rules, players=("a", "b", "c", "d", "e"))
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {})
        await room.perform_action("b", "night", {"target": "c"})
        opts = room.view_for("c").get("your_options", {})
        can = bool(opts) and opts["can_save"] is True
        await room.perform_action("c", "witch", {"save": True})
        await room.perform_action("d", "night", {})
        saved = not g["last_night"] \
            and room.view_for("c")["witch_stock"] == {"save": False,
                                                      "poison": True}
        return can, saved

    can, saved = run_identity_shuffle(run)
    check("always 自救规则允许女巫自救", can and saved)


def test_guard_self_and_last_words_timer():
    async def run():
        room = make_room({"board": ["guard", "werewolf", "witch", "seer",
                                    "villager", "villager"]})
        await room.start()
        options = room.view_for("a")["your_options"]
        offered = "a" in [row["username"] for row in options["targets"]]
        await room.perform_action("a", "night", {"target": "a"})
        accepted = room.game["night"]["protect"].get("a") == "a"
        await room.perform_action("b", "night", {"target": "f"})
        await room.perform_action("c", "witch", {"save": False})
        await room.perform_action("d", "night", {})
        g = room.game
        remaining = g["deadline"] - time.time()
        scheduled = room.timers["phase"].when() - asyncio.get_running_loop().time()
        result = (offered, accepted, g["phase"], remaining, scheduled,
                  room.view_for("f")["turn_left"])
        room.close()
        return result

    offered, accepted, phase, remaining, scheduled, visible = \
        run_identity_shuffle(run)
    check("守卫可守护自己", offered and accepted)
    check("遗言有完整 20 秒", phase == "last_words"
          and 18 < remaining <= 20 and 18 < scheduled <= 20
          and 18 < visible <= 20)


def test_skip_hunter_shot():
    async def run():
        room = make_room({"board": ["werewolf", "werewolf", "hunter", "seer",
                                    "villager", "villager"]})
        await room.start()
        await skip_to_vote(room)
        for voter in list(room.game["alive"]):
            await room.perform_action(voter, "vote", {"target": "c"})
        if room.game["phase"] == "last_words":
            await room._advance_last_words()
        was_shot = room.game["phase"] == "shot"
        await room.perform_action("c", "shoot", {"target": ""})
        result = (was_shot, room.game["phase"], room.game["shot_pending"])
        room.close()
        return result

    was_shot, phase, pending = run_identity_shuffle(run)
    check("猎人放弃开枪立即推进", was_shot and phase == "night" and pending is None)


def test_hunter_shot_on_exile():
    async def run():
        # 猎人不是唯一神职：放逐他不会触发屠边
        rules = {"board": ["werewolf", "werewolf", "hunter", "seer",
                           "villager", "villager"]}
        room = make_room(rules, players=("a", "b", "c", "d", "e", "f"))
        await room.start()
        g = room.game
        await skip_to_vote(room)
        for voter, target in (("a", "c"), ("b", "c"), ("c", "a"),
                              ("d", "c"), ("e", "c"), ("f", "a")):
            await room.perform_action(voter, "vote", {"target": target})
        exiled = "c" not in g["alive"] and g["phase"] in ("last_words", "shot")
        if g["phase"] == "last_words":
            await room._advance_last_words()
        shot_phase = g["phase"] == "shot" and g["shot_pending"] == "c"
        await room.perform_action("c", "shoot", {"target": "a"})
        shot_ok = "a" not in g["alive"] \
            and any("开枪" in e["text"] for e in g["history"])
        continue_ok = g["phase"] == "night" and g["day_no"] == 2 \
            and sorted(g["alive"]) == ["b", "d", "e", "f"]
        return exiled, shot_phase, shot_ok, continue_ok

    exiled, shot_phase, shot_ok, continue_ok = run_identity_shuffle(run)
    check("投票放逐猎人", exiled)
    check("放逐后进入开枪阶段", shot_phase)
    check("猎人开枪带走狼人", shot_ok)
    check("开枪后进入下一夜", continue_ok)


def test_hunter_poison_default_no_shot():
    async def run():
        rules = {"board": ["werewolf", "hunter", "witch", "seer", "villager"]}
        room = make_room(rules, players=("a", "b", "c", "d", "e"))
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {})              # 空刀
        await room.perform_action("c", "witch", {"save": False,
                                                 "poison": "b"})  # 毒猎人
        await room.perform_action("d", "night", {})
        # 首夜死者先进遗言阶段，再进白天
        await room._advance_last_words()
        no_shot = g["phase"] == "day" and g["shot_pending"] is None \
            and "b" not in g["alive"]
        rules2 = dict(rules, hunter_shot_on_poison=True)
        room2 = make_room(rules2, players=("a", "b", "c", "d", "e"))
        await room2.start()
        g2 = room2.game
        await room2.perform_action("a", "night", {})
        await room2.perform_action("c", "witch", {"save": False,
                                                  "poison": "b"})
        await room2.perform_action("d", "night", {})
        await room2._advance_last_words()
        shot = g2["phase"] == "shot" and g2["shot_pending"] == "b"
        await room2.perform_action("b", "shoot", {"target": "c"})
        shot_hit = "c" not in g2["alive"]
        return no_shot, shot, shot_hit

    no_shot, shot, shot_hit = run_identity_shuffle(run)
    check("默认被毒不能开枪，直接进白天", no_shot)
    check("规则放开后被毒可开枪", shot)
    check("被毒猎人的枪仍然生效", shot_hit)


# ---- 投票与平票 ----
def test_vote_and_tie():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        await skip_to_vote(room)
        vote_phase = g["phase"] == "vote"
        votes = {"a": "b", "b": "a", "c": "a", "d": "b", "e": "a", "f": "b"}
        vote_locked = False
        for voter, target in votes.items():
            await room.perform_action(voter, "vote", {"target": target})
            if voter == "a":               # 重复提交被拒绝，票锁定在首次选择
                await room.perform_action("a", "vote", {"target": "c"})
                vote_locked = g["vote"].get("a") == "b"
        revote = g["vote_round"] == 2 and set(g["candidates"]) == {"a", "b"} \
            and g["vote"] == {}
        votes2 = {"a": "b", "b": "a", "c": "b", "d": "a", "e": "b", "f": "a"}
        for voter, target in votes2.items():
            await room.perform_action(voter, "vote", {"target": target})
        no_exile = g["phase"] == "night" and g["day_no"] == 2 \
            and len(g["alive"]) == 6
        room2 = make_room({"tie": "no_exile"})
        await room2.start()
        g2 = room2.game
        await skip_to_vote(room2)
        for voter, target in votes.items():
            await room2.perform_action(voter, "vote", {"target": target})
        straight = g2["phase"] == "night" and g2["day_no"] == 2 \
            and len(g2["alive"]) == 6
        return vote_phase, vote_locked, revote, no_exile, straight

    vote_phase, vote_locked, revote, no_exile, straight = \
        run_identity_shuffle(run)
    check("白天结束进入投票", vote_phase)
    check("重复投票被拒绝且首票锁定", vote_locked)
    check("平票进入候选人重投", revote)
    check("重投仍平无人出局", no_exile)
    check("no_exile 规则平票直接跳过", straight)


# ---- 胜负与结算 ----
def test_win_good_and_payout():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        await skip_to_vote(room)
        for voter in list(g["alive"]):
            await room.perform_action(voter, "vote", {"target": "b"})
        first_exile = "b" not in g["alive"]
        await skip_to_vote(room)             # 遗言 → 第 2 夜空刀 → 投票
        for voter in list(g["alive"]):
            await room.perform_action(voter, "vote", {"target": "a"})
        finished = g["phase"] == "showdown" and g["result"]["winner"] == "good"
        stacks = {name: room.members[name]["stack"] for name in "abcdef"}
        payout_ok = abs(stacks["a"] - 95.0) < 1e-6 \
            and abs(stacks["b"] - 95.0) < 1e-6 \
            and all(abs(stacks[name] - 102.5) < 1e-6 for name in "cdef")
        roles_revealed = {"狼人", "预言家"} <= set(g["result"]["roles"].values())
        return first_exile, finished, stacks, payout_ok, roles_revealed

    first_exile, finished, stacks, payout_ok, roles_revealed = \
        run_identity_shuffle(run)
    check("第一只狼被放逐对局继续", first_exile)
    check("狼人全灭好人获胜", finished)
    check("输方付底注、胜方均分", payout_ok, str(stacks))
    check("结算面板全翻牌", roles_revealed)


def test_win_bian_vs_cheng():
    async def run():
        # 屠边：5 人板 a/b 狼，c 预言家（唯一神职），d/e 民 → 刀掉预言家即狼胜
        board = ["werewolf", "werewolf", "seer", "villager", "villager"]
        room = make_room({"board": board}, players=("a", "b", "c", "d", "e"))
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {"target": "c"})
        await room.perform_action("b", "night", {"target": "c"})
        await room.perform_action("c", "night", {})   # 夜里还活着，须先行动
        bian = g["phase"] == "showdown" and g["result"]["winner"] == "wolf" \
            and "屠边" in g["result"]["reason"]
        # 屠城：6 人板刀掉预言家只死一人，对局继续（屠边此时已终局）
        room2 = make_room({"board": ["werewolf", "werewolf", "seer",
                                     "villager", "villager", "villager"],
                           "win_mode": "cheng"},
                          players=("a", "b", "c", "d", "e", "f"))
        await room2.start()
        g2 = room2.game
        await room2.perform_action("a", "night", {"target": "c"})
        await room2.perform_action("b", "night", {"target": "c"})
        await room2.perform_action("c", "night", {})
        survived = g2["phase"] in ("day", "last_words") and "c" not in g2["alive"]
        for _ in range(10):
            phase = g2["phase"]
            if phase == "showdown":
                break
            if phase == "last_words":
                await room2._advance_last_words()
            elif phase == "day":
                await room2._advance_speech()
            elif phase == "vote":
                for voter in list(g2["alive"]):
                    await room2.perform_action(voter, "vote", {"target": "d"})
            elif phase == "night":
                wolves = [n for n in g2["alive"]
                          if is_wolf_role(g2["roles"][n])]
                for w in wolves:
                    targets = [n for n in g2["alive"]
                               if not is_wolf_role(g2["roles"][n])]
                    if targets:
                        await room2.perform_action(w, "night",
                                                   {"target": targets[0]})
            else:
                break
        cheng_end = g2["phase"] == "showdown" \
            and g2["result"]["winner"] == "wolf"
        return bian, survived, cheng_end

    bian, survived, cheng_end = run_identity_shuffle(run)
    check("屠边局神职全灭狼人立即获胜", bian)
    check("屠城局神职全灭对局继续", survived)
    check("屠城局进行到人数兜底狼胜", cheng_end)


# ---- 离桌与胜负复判 ----
def test_leave_mid_night():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {"target": "e"})
        await room.perform_action("b", "night", {"target": "e"})
        await room.perform_action("d", "witch", {"save": True})   # 救下 e，避免人数兜底
        mid = room.note_leave("c")           # 预言家在验人阶段离桌
        await room.progress_game()
        advanced = g["phase"] == "day" and "c" not in g["alive"]
        room2 = six_room()
        await room2.start()
        g2 = room2.game
        left1 = room2.note_leave("a")
        await room2.progress_game()
        still = g2["phase"] == "night" and g2["result"] is None
        left2 = room2.note_leave("b")
        await room2.progress_game()
        good_win = g2["phase"] == "showdown" \
            and g2["result"]["winner"] == "good"
        return mid, advanced, left1 and left2, still, good_win

    mid, advanced, left_both, still, good_win = run_identity_shuffle(run)
    check("夜晚离桌视作出局", mid)
    check("离桌后夜晚流程自动推进", advanced)
    check("两只狼先后离桌", left_both)
    check("第一只狼离桌对局继续", still)
    check("狼人全部离桌好人获胜", good_win)


async def skip_night(room):
    """空过当前夜晚所有分段（显式空刀/不救/不验），停在白天或后续阶段。"""
    g = room.game
    while g["phase"] == "night":
        ability = ROLES[g["night_role"]]["ability"]
        for name in list(g.get("pending", [])):
            if ability == "witch":
                await room.perform_action(name, "witch", {"save": False})
            else:
                await room.perform_action(name, "night", {})
        if g["phase"] == "night":
            await room._advance_night()


# ---- 聊天定向 ----
def test_chat_channels():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        night_wolf = room.chat_route("a", {})
        night_good = room.chat_route("c", {})
        await skip_night(room)
        day_speaker = g["speech"]["current"]
        day_speaker_route = room.chat_route(day_speaker, {})
        day_other = next(name for name in g["alive"]
                         if name != day_speaker)
        day_muted_route = room.chat_route(day_other, {})
        speech_visible = room.view_for(day_other)["speech_current"] \
            == day_speaker
        await skip_to_vote(room)
        vote_route = room.chat_route("a", {})
        for voter in list(g["alive"]):
            await room.perform_action(voter, "vote", {"target": "f"})
        # f 刚被放逐、遗言轮次中：仍在局内，身份保密
        involved_view = room.view_for("f")
        involved_ok = "god_view" not in involved_view \
            and {p["username"]: p["role"] for p in involved_view["players"]}["c"] is None
        while g["phase"] == "last_words":
            await room._advance_last_words()
        if g["phase"] == "shot":
            await room._close_shot()
        # f 遗言了结：转上帝视角旁观——全场公开、狼频道旁听、不得再发言
        settled_ok = room.view_for("f")["god_view"] is True
        dead_channel = room.chat_route("f", {})
        queued_route_ok = False
        g["last_words"]["queue"] = ["f"]      # 模拟遗言还没轮到：仍算局内
        queued_route_ok = room.chat_route("f", {}) == "dead" \
            and "god_view" not in room.view_for("f")
        g["last_words"]["queue"] = []
        night2_wolf = room.chat_route("a", {})
        audience_wolf = room.chat_audience("wolf")
        audience_dead = room.chat_audience("dead")
        god_voice_ok = room.view_for("f")["voice"] == {"channel": "wolf",
                                                       "can_speak": False}
        room.chat.append({"channel": "wolf", "text": "刀e", "username": "a"})
        room.chat.append({"channel": "dead", "text": "冤枉", "username": "f"})
        room.chat.append({"text": "大家好", "username": "c"})
        room.add_spectator("watcher", "a")
        spectator_route = room.chat_route("watcher", {})
        spectator_sees = [m["text"] for m in room.visible_chat("watcher")]
        wolf_sees = [m["text"] for m in room.visible_chat("a")]
        good_sees = [m["text"] for m in room.visible_chat("e")]
        dead_sees = [m["text"] for m in room.visible_chat("f")]
        return (night_wolf, night_good, day_speaker_route, day_muted_route,
                speech_visible, vote_route, involved_ok, settled_ok,
                dead_channel, queued_route_ok, night2_wolf, audience_wolf,
                audience_dead, god_voice_ok, wolf_sees, good_sees, dead_sees,
                spectator_route, spectator_sees)

    (night_wolf, night_good, day_speaker_route, day_muted_route,
     speech_visible, vote_route, involved_ok, settled_ok,
     dead_channel, queued_route_ok, night2_wolf, audience_wolf,
     audience_dead, god_voice_ok, wolf_sees, good_sees, dead_sees,
     spectator_route, spectator_sees) = \
        run_identity_shuffle(run)
    check("夜晚狼人走 wolf 频道", night_wolf == "wolf" and night2_wolf == "wolf")
    check("夜晚好人被禁言", night_good == "")
    check("白天仅当前发言人可公开发言",
          day_speaker_route is None and day_muted_route == "",
          f"speaker={day_speaker_route} other={day_muted_route}")
    check("发言顺序对全体可见", speech_visible)
    check("投票阶段存活者公开发言", vote_route is None)
    check("遗言未了结的死者保密且可走 dead 频道", involved_ok and queued_route_ok)
    check("遗言了结的死者转上帝视角且被禁言", settled_ok and dead_channel == "")
    check("wolf 频道听众含了结死亡的旁听者", set(audience_wolf) == {"a", "b", "f"},
          str(audience_wolf))
    check("dead 频道听众只有死者", audience_dead == ["f"], str(audience_dead))
    check("了结死亡的夜晚可听狼频道语音", god_voice_ok, str(god_voice_ok))
    check("狼人可见狼频道与公开消息", "刀e" in wolf_sees and "大家好" in wolf_sees
          and "冤枉" not in wolf_sees, str(wolf_sees))
    check("好人只见公开消息", "大家好" in good_sees and "刀e" not in good_sees
          and "冤枉" not in good_sees, str(good_sees))
    check("旁观死者可见狼频道/dead 频道与公开消息", "刀e" in dead_sees
          and "冤枉" in dead_sees and "大家好" in dead_sees, str(dead_sees))
    check("观战者看不到死者频道且不能冒充死者发言",
          spectator_route == "" and spectator_sees == ["大家好"],
          str(spectator_sees))


def test_dead_spectator_and_shot_options():
    async def run():
        board = ["werewolf", "hunter", "seer", "witch", "villager", "villager"]

        # 猎人被刀：遗言+开枪未了结时仍在局内（可开枪、不公开身份），
        # 收枪后转上帝视角
        room = make_room({"board": board})
        await room.start()
        g = room.game
        await room.perform_action("a", "night", {"target": "b"})
        await room.perform_action("d", "witch", {"save": False})
        await room.perform_action("c", "night", {})
        words_ok = g["last_words"]["current"] == "b" \
            and "god_view" not in room.view_for("b")
        await room._advance_last_words()       # b 的遗言结束 → 开枪阶段
        shot_view = room.view_for("b")
        shot_ok = g["phase"] == "shot" \
            and shot_view["your_options"]["kind"] == "shot" \
            and [t["username"] for t in shot_view["your_options"]["targets"]] \
            == ["a", "c", "d", "e", "f"] \
            and "god_view" not in shot_view \
            and room.chat_route("b", {}) == "dead"
        await room.perform_action("b", "shoot", {"target": ""})   # 收枪
        day_ok = g["phase"] == "day"
        god_view = room.view_for("b")
        settled_ok = god_view["god_view"] is True \
            and room.chat_route("b", "") == "" \
            and god_view["voice"] == {"channel": "day", "can_speak": False}

        # 死亡无遗言（last_words=none）：出局即旁观
        room2 = make_room({"board": board, "last_words": "none"})
        await room2.start()
        g2 = room2.game
        await room2.perform_action("a", "night", {"target": "f"})
        await room2.perform_action("d", "witch", {"save": False})
        await room2.perform_action("c", "night", {})
        instant_ok = g2["phase"] == "day" \
            and room2.view_for("f")["god_view"] is True \
            and room2.chat_route("f", {}) == ""
        return words_ok, shot_ok, day_ok, settled_ok, instant_ok

    words_ok, shot_ok, day_ok, settled_ok, instant_ok = run_identity_shuffle(run)
    check("猎人死亡时仍保密且无上帝视角", words_ok)
    check("开枪阶段猎人保留行动选项与死者频道", shot_ok)
    check("收枪后转上帝视角且被禁言", day_ok and settled_ok)
    check("死亡无遗言立即转旁观视角", instant_ok)


# ---- 白天依次发言 ----
def test_day_speech_order_and_mute():
    async def run():
        # 平安夜：全员存活 → 从座次首位开始依次发言，默认每人 30 秒
        room = six_room()
        await room.start()
        g = room.game
        await skip_night(room)
        queue_ok = g["speech"]["queue"] == ["b", "c", "d", "e", "f"] \
            and g["speech"]["current"] == "a"
        view = room.view_for("c")
        view_ok = view["speech_current"] == "a" and "your_options" not in view
        timer_ok = 28 < g["deadline"] - time.time() <= 30
        # 发言人离桌：话筒立即交给下一位
        left = room.note_leave("a")
        await room.progress_game()
        advanced = g["speech"]["current"] == "b"
        for _ in range(5):                     # b..f 说完进入投票
            await room._advance_speech()
        vote_ok = g["phase"] == "vote" and g["speech"]["current"] is None

        # 夜晚死人后从死者下一位开始；发言中离桌立即交给下一位
        room2 = six_room({"speak_seconds": 20})
        await room2.start()
        g2 = room2.game
        await room2.perform_action("a", "night", {"target": "e"})
        await room2.perform_action("b", "night", {"target": "e"})
        await room2.perform_action("d", "witch", {"save": False})
        await room2.perform_action("c", "night", {})
        await room2._advance_last_words()      # e 的遗言结束 → 白天
        day2 = g2["phase"] == "day" and g2["last_night"] == {"e": "kill"}
        rotated_ok = g2["speech"]["current"] == "f" \
            and g2["speech"]["queue"] == ["a", "b", "c", "d"]
        rotated_timer_ok = 18 < g2["deadline"] - time.time() <= 20
        return (queue_ok, view_ok, timer_ok, vote_ok, advanced, day2,
                rotated_ok, rotated_timer_ok)

    (queue_ok, view_ok, timer_ok, vote_ok, advanced, day2, rotated_ok,
     rotated_timer_ok) = run_identity_shuffle(run)
    check("平安夜从座次首位开始依次发言", queue_ok)
    check("发言顺序与当前发言人写入视图", view_ok)
    check("默认发言时限 30 秒", timer_ok)
    check("发言人离桌立即换下一位", advanced)
    check("全员说完自动进入投票", vote_ok)
    check("第二天从死者下一位开始发言", day2 and rotated_ok)
    check("自定义发言时限生效", rotated_timer_ok)


def test_speech_voice_gate_and_end():
    async def run():
        room = six_room()
        room.voice_wait = True               # 模拟宿主注入：语音服务已启用
        await room.start()
        g = room.game
        await skip_night(room)               # 平安夜直达白天
        view = room.view_for("c")
        # 首位发言人进入等待：倒计时不启动，视图带 awaiting_voice
        await_ok = g["phase"] == "day" and g["speech"]["current"] == "a" \
            and g["speech"]["awaiting_voice"] is True and g["deadline"] == 0 \
            and view["awaiting_voice"] is True and view["turn_left"] == 0
        # 非发言人报告连接无效；发言人在其他阶段报告也无效
        await room.perform_action("b", "speech_ready")
        await room.perform_action("a", "vote", {"target": "b"})
        still_ok = g["deadline"] == 0 and g["speech"]["awaiting_voice"] is True
        # 发言人连上语音：开表
        await room.perform_action("a", "speech_ready")
        armed_ok = g["speech"]["awaiting_voice"] is False \
            and 28 < g["deadline"] - time.time() <= 30
        # 提前结束发言：话筒立即交给下一位，并重新等待语音
        await room.perform_action("a", "speech_end")
        next_ok = g["speech"]["current"] == "b" \
            and g["speech"]["awaiting_voice"] is True and g["deadline"] == 0
        # 等待超时兜底：自动开表
        await room._voice_wait_timeout()
        timeout_ok = g["speech"]["awaiting_voice"] is False \
            and 28 < g["deadline"] - time.time() <= 30
        # 遗言阶段同样等语音：造一具尸体走完到遗言
        room2 = six_room()
        room2.voice_wait = True
        await room2.start()
        g2 = room2.game
        await room2.perform_action("a", "night", {"target": "e"})
        await room2.perform_action("b", "night", {"target": "e"})
        await room2.perform_action("d", "witch", {"save": False})
        await room2.perform_action("c", "night", {})
        words_ok = g2["phase"] == "last_words" \
            and g2["last_words"]["current"] == "e" \
            and g2["last_words"]["awaiting_voice"] is True \
            and g2["deadline"] == 0
        await room2.perform_action("e", "speech_ready")
        words_armed_ok = g2["last_words"]["awaiting_voice"] is False \
            and 18 < g2["deadline"] - time.time() <= 20
        await room2.perform_action("e", "speech_end")
        words_next_ok = g2["phase"] == "day" \
            and g2["last_words"]["current"] is None
        return (await_ok, still_ok, armed_ok, next_ok, timeout_ok, words_ok,
                words_armed_ok, words_next_ok)

    (await_ok, still_ok, armed_ok, next_ok, timeout_ok, words_ok,
     words_armed_ok, words_next_ok) = run_identity_shuffle(run)
    check("语音等待期间不启动发言倒计时", await_ok)
    check("非发言人报告连接无效", still_ok)
    check("发言人连接语音后开始倒计时", armed_ok)
    check("结束发言立即交给下一位", next_ok)
    check("等待超时自动开始倒计时", timeout_ok)
    check("遗言阶段同样等待语音连接", words_ok)
    check("遗言连接语音后开表并可提前结束", words_armed_ok and words_next_ok)


# ---- 结算投票 ----
def test_settlement_vote():
    async def run():
        room = six_room()
        await room.start()
        g = room.game
        await skip_to_vote(room)
        for voter in list(g["alive"]):
            await room.perform_action(voter, "vote", {"target": "b"})
        await skip_to_vote(room)
        for voter in list(g["alive"]):
            await room.perform_action(voter, "vote", {"target": "a"})
        settled = g["phase"] == "showdown"
        view = room.view_for("a")
        can_vote = view["settlement"]["total"] == 6
        await room.cast_vote("a", "next", room.blind)
        await room.cast_vote("b", "next", room.blind)
        await room.cast_vote("c", "next", room.blind)
        executed = await room.cast_vote("d", "next", room.blind)   # 4 票 > 6/2
        again = room.in_hand() and room.game["match_no"] == 2
        return settled, can_vote, executed, again

    settled, can_vote, executed, again = run_identity_shuffle(run)
    check("完赛进入结算阶段", settled and can_vote)
    check("过半数投票再来一局", executed == "next" and again)


# ---- 随机完整对局 ----
async def submit_night_randomly(room, rng):
    g = room.game
    for _ in range(8):
        if g["phase"] != "night":
            return
        role = g.get("night_role")
        pending = list(g.get("pending", []))
        if not role or not pending:
            await room._advance_night()
            continue
        ability = ROLES[role]["ability"]
        for name in pending:
            if rng.random() < 0.1:
                continue                    # 留白走超时默认路径
            if ability == "kill":
                targets = [t for t in g["alive"]
                           if not is_wolf_role(g["roles"][t])]
                target = rng.choice(targets) if targets else None
                await room.perform_action(name, "night",
                                          {"target": target or ""})
            elif ability == "check":
                targets = [t for t in g["alive"] if t != name]
                target = rng.choice(targets) if targets else None
                await room.perform_action(name, "night",
                                          {"target": target or ""})
            elif ability == "protect":
                forbidden = None if room.rules["guard_continuous"] \
                    else g["last_protect"].get(name)
                targets = [t for t in g["alive"]
                           if t != name and t != forbidden]
                target = rng.choice(targets) if targets else None
                await room.perform_action(name, "night",
                                          {"target": target or ""})
            elif ability == "witch":
                opts = room._your_options(name)
                decision = {"save": bool(opts["can_save"])
                            and rng.random() < 0.4}
                if opts["can_poison"] and rng.random() < 0.12:
                    decision["poison"] = rng.choice(
                        opts["poison_targets"])["username"]
                await room.perform_action(name, "witch", decision)
        if g["phase"] == "night" and g.get("night_role") == role \
                and g.get("pending"):
            await room._advance_night()


async def drive_random_match(room, rng, limit=8000):
    guard = 0
    while room.in_hand() and guard < limit:
        guard += 1
        g = room.game
        phase = g["phase"]
        if phase == "night":
            await submit_night_randomly(room, rng)
        elif phase == "day":
            await room._advance_speech()
        elif phase == "vote":
            voters = [n for n in g["alive"] if n not in g["vote"]]
            if voters and rng.random() < 0.85:
                for voter in voters:
                    candidates = list(g["candidates"] or g["alive"])
                    await room.perform_action(voter, "vote",
                                              {"target": rng.choice(candidates)})
            else:
                await room._phase_timeout()
        elif phase == "last_words":
            await room._phase_timeout()
        elif phase == "shot":
            shooter = g["shot_pending"]
            opts = room._your_options(shooter) if shooter else None
            if opts and opts["targets"] and rng.random() < 0.7:
                await room.perform_action(
                    shooter, "shoot",
                    {"target": rng.choice(opts["targets"])["username"]})
            else:
                await room._phase_timeout()
        else:
            break
    return guard


def test_full_random_match():
    rng = random.Random(20260924)
    scenarios = [
        ("6 人预设板", {}, ("a", "b", "c", "d", "e", "f")),
        ("9 人预设板", {}, tuple("abcdefghi")),
        ("12 人预设板", {}, tuple("abcdefghijkl")),
        ("5 人自定义板", {"board": ["guard", "werewolf", "witch", "seer",
                                "villager"],
                       "witch_self_save": "always", "guard_continuous": True,
                       "hunter_shot_on_poison": True,
                       "last_words": "all", "tie": "no_exile",
                       "win_mode": "cheng"},
         ("a", "b", "c", "d", "e")),
    ]
    for label, rules, players in scenarios:
        room = make_room(rules, players=players)
        old_shuffle = randomness.shuffle
        randomness.shuffle = rng.shuffle

        async def run():
            await room.start()
            return await drive_random_match(room, rng)

        try:
            guard = asyncio.run(run())
        finally:
            randomness.shuffle = old_shuffle
        result = room.game["result"]
        stacks = [room.members[name]["stack"] for name in players]
        check(f"{label}随机对局完赛",
              room.game["phase"] == "showdown" and result is not None
              and guard < 8000,
              f"guard={guard} phase={room.game['phase']}")
        check(f"{label}结算守恒", abs(sum(stacks) - 100 * len(stacks)) < 1e-6,
              str(stacks))
        winner_side = result["winner"]
        winner_count = sum(
            1 for key in room.game["roles"].values()
            if is_wolf_role(key) == (winner_side == "wolf"))
        check(f"{label}胜方为完整阵营", winner_count > 0)
        room.close()


def main():
    test_sanitize_and_board()
    test_assignment_and_views()
    test_night_save_and_check()
    test_night_kill_and_poison()
    test_single_actor_submit_locks()
    test_witch_rules_and_guard()
    test_witch_always_self_save()
    test_guard_self_and_last_words_timer()
    test_skip_hunter_shot()
    test_hunter_shot_on_exile()
    test_hunter_poison_default_no_shot()
    test_vote_and_tie()
    test_win_good_and_payout()
    test_win_bian_vs_cheng()
    test_leave_mid_night()
    test_chat_channels()
    test_dead_spectator_and_shot_options()
    test_day_speech_order_and_mute()
    test_speech_voice_gate_and_end()
    test_settlement_vote()
    test_full_random_match()
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败用例：", "、".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
