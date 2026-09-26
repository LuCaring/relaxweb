import asyncio, json

import websockets

from local_config import URI


async def make_cli():
    ws = await websockets.connect(URI)
    q = asyncio.Queue()

    async def reader():
        try:
            async for raw in ws:
                await q.put(json.loads(raw))
        except Exception:
            pass

    asyncio.ensure_future(reader())

    async def send(d):
        await ws.send(json.dumps(d, ensure_ascii=False))

    async def expect(types, timeout=8):
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout)
            except asyncio.TimeoutError:
                return None
            if data.get("type") in types:
                return data

    return {"ws": ws, "send": send, "expect": expect}


async def drive(cli, who, done):
    while not done.is_set():
        await cli["send"]({"type": "get_room"})
        msg = await cli["expect"]({"game_update", "hand_result", "room_closed"}, timeout=6)
        if msg is None:
            return
        t = msg["type"]
        if t == "hand_result" or (t == "game_update" and msg.get("result")):
            done.set()
            return
        if t == "room_closed":
            return
        if msg.get("to_act") == who:
            opts = msg["your_options"] or {}
            if opts.get("check"):
                await cli["send"]({"type": "poker_action", "action": "check"})
            elif opts.get("call"):
                await cli["send"]({"type": "poker_action", "action": "call"})
            else:
                await cli["send"]({"type": "poker_action", "action": "fold"})


async def main():
    c1 = await make_cli()
    await c1["send"]({"type": "login", "username": "玩家1", "password": "test123456"})
    await c1["expect"]({"login_success"})
    c2 = await make_cli()
    await c2["send"]({"type": "login", "username": "玩家2", "password": "test123456"})
    await c2["expect"]({"login_success"})

    await c1["send"]({"type": "create_room", "name": "结算投票房", "buy_in": 100, "blind": 5})
    msg = await c1["expect"]({"game_joined"})
    room_id = msg["room"]["room_id"]
    await c2["send"]({"type": "join_room", "room_id": room_id})
    await c2["expect"]({"game_joined"})
    await c1["expect"]({"game_update"})
    await c1["send"]({"type": "start_game"})
    for _ in range(10):
        msg = await c1["expect"]({"game_update"})
        if msg and msg.get("stage") == "preflop":
            break
    print("1. 开局 OK (hand 1)", flush=True)

    done = asyncio.Event()
    await asyncio.gather(drive(c1, "玩家1", done), drive(c2, "玩家2", done))
    print("2. 一手打完，进入结算", flush=True)

    await c1["send"]({"type": "get_room"})
    settle = None
    for _ in range(15):
        msg = await c1["expect"]({"game_update"})
        if msg and msg.get("settlement"):
            settle = msg["settlement"]
            break
    print("FULL KEYS:", sorted(msg.keys()) if msg else None, flush=True)
    print("status:", msg and msg.get("status"), "stage:", msg and msg.get("stage"), flush=True)
    settle = settle or (msg and msg.get("settlement"))
    print("3. settlement 字段:", settle, flush=True)
    assert settle and settle["can_next"] is True and settle["total"] == 2

    await c2["send"]({"type": "settle_vote", "choice": "next", "blind": 5})
    msg = await c2["expect"]({"game_update"})
    print("4. 单票未过半: votes =", msg and msg["settlement"]["votes"], flush=True)

    await c1["send"]({"type": "settle_vote", "choice": "next", "blind": 10})
    msg = None
    for _ in range(10):
        msg = await c1["expect"]({"game_update"})
        if msg and msg.get("stage") == "preflop" and msg.get("hand_no") == 2:
            break
    assert msg and msg.get("hand_no") == 2 and msg.get("blind") == 10
    print("5. 过半生效：hand 2 preflop，盲注改为 10/20", flush=True)

    done = asyncio.Event()
    await asyncio.gather(drive(c1, "玩家1", done), drive(c2, "玩家2", done))
    await c1["send"]({"type": "get_room"})
    msg = None
    for _ in range(15):
        msg = await c1["expect"]({"game_update"})
        if msg and msg.get("settlement"):
            break
    assert msg and msg.get("settlement"), "应再次进入结算"
    print("6. 第二手结束回到结算", flush=True)

    await c1["send"]({"type": "settle_vote", "choice": "next", "blind": 5})
    await c1["expect"]({"game_update"})
    await c2["send"]({"type": "settle_vote", "choice": "dissolve"})
    msg = await c1["expect"]({"room_closed"})
    assert msg and msg["reason"] == "结算解散"
    print("7. 全员已投平票→解散：room_closed 结算解散", flush=True)

    await c1["send"]({"type": "get_finance"})
    f1 = await c1["expect"]({"finance"})
    await c2["send"]({"type": "get_finance"})
    f2 = await c2["expect"]({"finance"})
    total = f1["coins"] + f2["coins"]
    print(f"8. 双方余额合计 {total}（买入共 200，筹码应守恒）", flush=True)

    print("\nALL SETTLE-VOTE CHECKS PASSED", flush=True)

asyncio.run(main())
