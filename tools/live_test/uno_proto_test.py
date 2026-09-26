"""UNO 协议级自动化测试：建房、发牌、三客户端驱动打完一局、结算投票与解散退款。"""
import asyncio
import json

import websockets

from local_config import URI
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def client():
    ws = await websockets.connect(URI + "/?client=game")
    queue = asyncio.Queue()

    async def reader():
        try:
            async for raw in ws:
                await queue.put(json.loads(raw))
        except Exception:
            pass

    asyncio.ensure_future(reader())

    async def send(d):
        await ws.send(json.dumps(d, ensure_ascii=False))

    async def expect(want, timeout=8):
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout)
            except asyncio.TimeoutError:
                return None
            if data.get("type") in want:
                return data

    return {"ws": ws, "send": send, "expect": expect}


def playable(card, active):
    if not active:
        return False
    if card["c"] == "w":
        return True
    return card["c"] == active["c"] or card["v"] == active["v"]


async def handle_view(cli, who, done, msg):
    """处理一帧私有视图；需要行动时发送动作。返回是否行动过。"""
    if msg is None:
        return False
    if msg["type"] == "room_closed":
        return False
    if msg["type"] == "hand_result" or msg.get("result"):
        done.set()
        return False
    opts = msg.get("your_options") or {}
    print(f"    [{who}] to_act={msg.get('to_act')} opts={opts} "
          f"hand={len(msg.get('your_hand') or [])} active={msg.get('active')}",
          flush=True)
    acted = False
    if opts.get("uno"):
        await cli["send"]({"type": "poker_action", "action": "uno"})
        acted = True
    if msg.get("to_act") == who:
        hand = msg.get("your_hand") or []
        active = msg.get("active") or {}
        drawn_only = opts.get("pass") and msg.get("your_drawn") is not None
        pick = None
        for index, card in enumerate(hand):
            if drawn_only and index != msg["your_drawn"]:
                continue
            if playable(card, active):
                pick = (index, card)
                break
        if pick is not None:
            payload = {"type": "poker_action", "action": "play", "card": pick[0]}
            if pick[1]["c"] == "w":
                payload["color"] = "r"
            await cli["send"](payload)
        elif opts.get("draw"):
            await cli["send"]({"type": "poker_action", "action": "draw"})
        elif opts.get("pass"):
            await cli["send"]({"type": "poker_action", "action": "pass"})
        acted = True
    return acted


async def drive(cli, who, done):
    """事件驱动 + 开局引导：先主动拉一次视图，之后只依赖实时广播。"""
    await cli["send"]({"type": "get_room"})
    msg = await cli["expect"]({"game_update", "hand_result", "room_closed"},
                              timeout=6)
    while not done.is_set():
        await handle_view(cli, who, done, msg)
        if done.is_set():
            return True
        msg = await cli["expect"]({"game_update", "hand_result", "room_closed"},
                                  timeout=40)
        if msg is None:
            return False
    return True


async def main():
    clients = []
    for name in ("玩家1", "玩家2", "玩家3"):
        cli = await client()
        await cli["send"]({"type": "login", "username": name, "password": "test123456"})
        msg = await cli["expect"]({"login_success"})
        assert msg, f"{name} login failed"
        # 清场：若仍在旧房间先退出
        await cli["send"]({"type": "get_room"})
        room_msg = await cli["expect"]({"game_update", "room_closed"}, timeout=4)
        if room_msg and room_msg["type"] == "game_update":
            await cli["send"]({"type": "leave_room"})
            await cli["expect"]({"room_closed"}, timeout=4)
        clients.append(cli)

    a, b, c = clients
    # 记录初始余额（库可能被历史测试弄脏，不能假设 1000）
    await a["send"]({"type": "get_finance"})
    fin0 = await a["expect"]({"finance"})
    coins_before = fin0["coins"] if fin0 else 1000
    await a["send"]({"type": "create_room", "game": "uno", "name": "UNO协议测试",
                     "buy_in": 100, "blind": 5})
    msg = await a["expect"]({"game_joined", "game_error"})
    room = (msg or {}).get("room") or {}
    check("创建UNO房间", msg and msg["type"] == "game_joined"
          and room.get("game_type") == "uno", str(room.get("game_type")))
    if msg is None or msg["type"] != "game_joined":
        return
    room_id = room["room_id"]
    for cli in (b, c):
        await cli["send"]({"type": "join_room", "room_id": room_id})
        joined = await cli["expect"]({"game_joined", "game_error"})
        assert joined and joined["type"] == "game_joined", str(joined)

    await a["send"]({"type": "start_game"})
    msg = None
    for _ in range(10):
        msg = await a["expect"]({"game_update", "game_error"})
        if msg and msg.get("your_hand"):
            break
    hand = (msg or {}).get("your_hand") or []
    active = (msg or {}).get("active") or {}
    check("UNO 发牌7张", len(hand) == 7, f"n={len(hand)}")
    check("UNO 首张数字牌", bool(active.get("card"))
          and active["card"]["v"].isdigit(), str(active.get("card")))
    check("UNO 7张手牌可渲染", all("c" in card and "v" in card for card in hand))

    done = asyncio.Event()
    await asyncio.gather(
        drive(a, "玩家1", done),
        drive(b, "玩家2", done),
        drive(c, "玩家3", done),
    )
    check("UNO 三家打完一局", done.is_set())

    settled = None
    for _ in range(10):
        msg = await a["expect"]({"game_update", "room_closed"})
        if msg and msg.get("settlement"):
            settled = msg
            break
    check("UNO 结算投票出现", settled is not None)
    if settled is None:
        return
    result = settled.get("result") or {}
    winner = result.get("winner")
    check("UNO 有一名赢家", winner in ("玩家1", "玩家2", "玩家3"), str(winner))
    players = {p["username"]: p for p in settled["players"]}
    gain = round(sum(result.get("payouts", {}).values()), 2)
    check("UNO 赢家收注", abs(players[winner]["stack"] - (100 + gain)) < 0.01,
          f"stack={players[winner]['stack']} gain={gain}")
    total = round(sum(p["stack"] for p in players.values()), 2)
    check("UNO 筹码守恒", abs(total - 300) < 0.01, f"total={total}")
    losers_ok = all(
        abs(players[name]["stack"] - (100 - result["payouts"].get(name, 0))) < 0.01
        for name in players if name != winner
    )
    check("UNO 输家按剩牌扣注", losers_ok, str(result.get("payouts")))

    await a["send"]({"type": "settle_vote", "choice": "dissolve"})
    await b["send"]({"type": "settle_vote", "choice": "dissolve"})
    msg = await a["expect"]({"room_closed"}, timeout=6)
    check("UNO 结算解散", msg is not None and msg["type"] == "room_closed")

    await asyncio.sleep(0.5)
    await a["send"]({"type": "get_finance"})
    fin = await a["expect"]({"finance"})
    expected = round(coins_before - 100 + players["玩家1"]["stack"], 2)
    check("UNO 解散退款入账", fin and abs(fin["coins"] - expected) < 0.01,
          f"coins={fin and fin['coins']} expected={expected}")

    for cli in clients:
        await cli["ws"].close()


asyncio.run(main())
failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
exit(1 if failed else 0)
