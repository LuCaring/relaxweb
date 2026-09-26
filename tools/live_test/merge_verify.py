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


async def finance(cli):
    await cli["send"]({"type": "get_finance"})
    msg = await cli["expect"]({"finance"})
    return [(t["kind"], round(t["amount"], 2), t["detail"]) for t in msg["transactions"]], msg["coins"]


async def main():
    c1 = await make_cli()
    await c1["send"]({"type": "login", "username": "玩家1", "password": "test123456"})
    await c1["expect"]({"login_success"})
    c2 = await make_cli()
    await c2["send"]({"type": "login", "username": "玩家2", "password": "test123456"})
    await c2["expect"]({"login_success"})

    await c1["send"]({"type": "create_bet", "question": "合并测试", "options": ["能", "不能"]})
    await c1["expect"]({"bet_created", "bet_update"})
    await asyncio.sleep(2.2)
    await c2["send"]({"type": "place_bet", "option_index": 0, "amount": 10})
    await c2["expect"]({"bet_placed"})
    await asyncio.sleep(2.2)
    await c1["send"]({"type": "place_bet", "option_index": 1, "amount": 10})
    await c1["expect"]({"bet_placed"})
    await asyncio.sleep(2.2)
    await c1["send"]({"type": "settle_bet", "correct_index": 0})
    await c1["expect"]({"bet_settled"})
    rows1, coins1 = await finance(c1)
    rows2, coins2 = await finance(c2)
    m1 = [r for r in rows1 if r[0] == "bet_result"]
    m2 = [r for r in rows2 if r[0] == "bet_result"]
    s1 = [r for r in rows1 if r[0] == "bet_stake"]
    s2 = [r for r in rows2 if r[0] == "bet_stake"]
    print(f"1. 玩家1(输) 合并: {m1} 残留={len(s1)} 余额={coins1}", flush=True)
    print(f"2. 玩家2(赢) 合并: {m2} 残留={len(s2)} 余额={coins2}", flush=True)

    await c1["send"]({"type": "create_bet", "question": "流局测试", "options": ["能", "不能"]})
    await c1["expect"]({"bet_created", "bet_update"})
    await asyncio.sleep(2.2)
    await c2["send"]({"type": "place_bet", "option_index": 0, "amount": 10})
    await c2["expect"]({"bet_placed"})
    await asyncio.sleep(2.2)
    await c1["send"]({"type": "cancel_bet"})
    await c1["expect"]({"bet_cancelled"})
    rows2, coins2 = await finance(c2)
    left = [r for r in rows2 if r[0] == "bet_stake" and "流局测试" in r[2]]
    print(f"3. 流局后条目删除: {not left} 余额={coins2}", flush=True)

    await c1["send"]({"type": "create_room", "name": "流水测试房", "buy_in": 100, "blind": 5})
    msg = await c1["expect"]({"game_joined"})
    room_id = msg["room"]["room_id"]
    rows1, _ = await finance(c1)
    print(f"4. 买入未结算条目: {[r for r in rows1 if r[0] == 'game_buyin']}", flush=True)
    await asyncio.sleep(1.2)
    await c2["send"]({"type": "join_room", "room_id": room_id})
    await c2["expect"]({"game_joined"})
    await c1["send"]({"type": "start_game"})
    await c1["expect"]({"game_update"})
    await asyncio.sleep(0.5)
    await c2["send"]({"type": "leave_room"})
    await c2["expect"]({"room_closed"})
    rows2, coins2 = await finance(c2)
    traces = [r for r in rows2 if "流水测试房" in r[2]]
    print(f"5. 未开局流局离桌: 无痕迹={not traces} 余额={coins2}", flush=True)

    done = asyncio.Event()

    async def drive(cli, who):
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

    await asyncio.gather(drive(c1, "玩家1"), drive(c2, "玩家2"))
    await c1["send"]({"type": "leave_room"})
    await c1["expect"]({"room_closed"})
    rows1, coins1 = await finance(c1)
    merged = [r for r in rows1 if r[0] == "game_result" and "流水测试房" in r[2]]
    buyleft = [r for r in rows1 if r[0] == "game_buyin" and "流水测试房" in r[2]]
    print(f"6. 打完离桌合并: {merged} 残留买入行={len(buyleft)} 余额={coins1}", flush=True)

    await asyncio.sleep(1.2)
    await c1["send"]({"type": "create_room", "name": "解散测试房", "buy_in": 50, "blind": 1})
    await c1["expect"]({"game_joined"})
    await asyncio.sleep(0.5)
    await c1["send"]({"type": "leave_room"})
    await c1["expect"]({"room_closed"})
    rows1, coins1 = await finance(c1)
    traces = [r for r in rows1 if "解散测试房" in r[2]]
    print(f"7. 未开局解散: 无痕迹={not traces} 余额={coins1}", flush=True)


asyncio.run(main())
