"""协议级自动化测试：登录、房间生命周期、断线宽限、在线计数、离桌退款。"""
import asyncio
import json
import sys

import websockets

from local_config import URI
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


async def client(client_tag=""):
    path = URI + (f"/?client={client_tag}" if client_tag else "")
    ws = await websockets.connect(path)
    queue = asyncio.Queue()

    async def reader():
        try:
            async for raw in ws:
                await queue.put(json.loads(raw))
        except Exception:
            pass

    task = asyncio.ensure_future(reader())

    async def send(d):
        await ws.send(json.dumps(d, ensure_ascii=False))

    async def expect(want_types, timeout=8):
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout)
            except asyncio.TimeoutError:
                return None
            if data.get("type") in want_types:
                return data

    async def drain(seconds=1.2):
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)

    return {"ws": ws, "send": send, "expect": expect, "task": task, "drain": drain}


async def drive(cli, who, done):
    while not done.is_set():
        await asyncio.sleep(0.15)
        await cli["send"]({"type": "get_room"})
        msg = await cli["expect"]({"game_update", "hand_result", "room_closed"}, timeout=6)
        if msg is None:
            return False
        t = msg["type"]
        if t == "hand_result" or (t == "game_update" and msg.get("result")):
            done.set()
            return True
        if t == "room_closed":
            return False
        if msg.get("to_act") == who:
            opts = msg["your_options"] or {}
            if opts.get("check"):
                await cli["send"]({"type": "poker_action", "action": "check"})
            elif opts.get("call"):
                await cli["send"]({"type": "poker_action", "action": "call"})
            elif opts.get("allin"):
                await cli["send"]({"type": "poker_action", "action": "raise",
                                   "raise_to": opts["allin_to"]})
            else:
                await cli["send"]({"type": "poker_action", "action": "fold"})
    return True


async def main():
    # 1. 登录
    c1 = await client()
    await c1["send"]({"type": "login", "username": "玩家1", "password": "test123456"})
    msg = await c1["expect"]({"login_success", "auth_error"})
    assert msg["type"] == "login_success", msg
    check("登录", True, f"coins={msg['coins']}")

    # 2. 建房
    await c1["send"]({"type": "create_room", "name": "协议测试房", "buy_in": 100, "blind": 5})
    msg = await c1["expect"]({"game_joined", "game_error"})
    assert msg["type"] == "game_joined", msg
    room_id = msg["room"]["room_id"]
    check("创建房间", True, f"room={room_id}, coins_after_buyin={msg['room']['players'][0]['stack']}")

    # 3. 玩家2 加入
    c2 = await client()
    await c2["send"]({"type": "login", "username": "玩家2", "password": "test123456"})
    await c2["expect"]({"login_success"})
    await c2["send"]({"type": "join_room", "room_id": room_id})
    msg = await c2["expect"]({"game_joined", "game_error"})
    assert msg["type"] == "game_joined", msg
    check("加入房间", True, "玩家2 joined")
    await c1["expect"]({"game_update"})

    # 4. 开始游戏
    await c1["send"]({"type": "start_game"})
    msg = None
    for _ in range(10):
        msg = await c1["expect"]({"game_update"})
        if msg and msg.get("stage") == "preflop":
            break
    assert msg and msg.get("stage") == "preflop", msg
    check("开局发牌", True, f"stage={msg.get('stage')} to_act={msg.get('to_act')}")

    # 4.5 暂停/恢复（牌局进行中）
    await c1["drain"](1.2)
    paused = resumed = False
    for _ in range(6):
        await c1["send"]({"type": "pause_game", "paused": True})
        msg = await c1["expect"]({"game_update"}, timeout=4)
        if msg and msg.get("paused") is True:
            paused = True
            break
    await c1["drain"](1.0)
    for _ in range(6):
        await c1["send"]({"type": "pause_game", "paused": False})
        msg = await c1["expect"]({"game_update"}, timeout=4)
        if msg and msg.get("paused") is False:
            resumed = True
            break
    check("暂停/恢复", paused and resumed, f"paused={paused} resumed={resumed}")

    # 5. 双方自动行动直到一手结束
    done = asyncio.Event()
    outcomes = await asyncio.gather(
        drive(c1, "玩家1", done), drive(c2, "玩家2", done)
    )
    check("完整打完一手", any(outcomes))

    # 5.5 房间聊天：广播给成员、带昵称
    await c1["send"]({"type": "room_chat", "text": "大家好"})
    msg = await c2["expect"]({"room_chat"})
    check("房间聊天广播", msg is not None and msg["text"] == "大家好"
          and msg["username"] == "玩家1", str(msg and msg.get("text")))

    # 5.7 结算投票：过半生效开下一手
    await c1["send"]({"type": "settle_vote", "choice": "next", "blind": 5})
    await c1["expect"]({"game_update"})
    await c2["send"]({"type": "settle_vote", "choice": "next", "blind": 5})
    msg = None
    for _ in range(15):
        msg = await c2["expect"]({"game_update"})
        if msg and msg.get("stage") == "preflop" and (msg.get("hand_no") or 0) >= 2:
            break
    check("结算投票再来一局", msg is not None and (msg.get("hand_no") or 0) >= 2,
          f"hand_no={msg and msg.get('hand_no')}")
    # 把第二手打完，回到结算
    done = asyncio.Event()
    await asyncio.gather(drive(c1, "玩家1", done), drive(c2, "玩家2", done))
    check("第二手打完", done.is_set())

    # 6. 金币联动：买入/结算后余额正确持久化（两手随机牌局，净收益不定）
    await c1["send"]({"type": "get_finance"})
    msg = await c1["expect"]({"finance"})
    kinds = {tx["kind"] for tx in msg["transactions"]}
    check("金币明细", "game_buyin" in kinds and 800 <= msg["coins"] <= 1000,
          f"kinds={sorted(kinds)} coins={msg['coins']}（进行中筹码全在托管）")

    # 7. 在线计数：client=game 的连接不计入
    cg = await client(client_tag="game")
    await cg["send"]({"type": "login", "username": "玩家3", "password": "test123456"})
    msg = await cg["expect"]({"login_success"})
    msg = await c1["expect"]({"online"})
    check("游戏页连接不计入在线观众", msg is not None and msg["count"] == 2,
          f"count={msg and msg['count']}（2 个普通连接，玩家3 只在游戏页）")

    # 8. 断线宽限：玩家2 断开 3 秒后重连，座位保留
    token2 = None
    await asyncio.sleep(0.5)
    print("c2 ws open:", c2["ws"].state.name, flush=True)
    await c2["send"]({"type": "login", "username": "玩家2", "password": "test123456"})
    msg = await c2["expect"]({"login_success"})
    assert msg, "c2 second login got no reply"
    token2 = msg["token"]
    await c2["ws"].close()
    await asyncio.sleep(3)
    c2b = await client()
    await c2b["send"]({"type": "resume", "token": token2})
    await c2b["expect"]({"resume_success"})
    await c2b["send"]({"type": "get_room"})
    msg = await c2b["expect"]({"game_update", "room_closed"})
    in_room = msg is not None and msg["type"] == "game_update"
    check("断线宽限保座", in_room,
          "3秒后重连仍在房间" if in_room else f"lost: {msg}")

    # 9. 玩家2 离桌退款
    await c2b["send"]({"type": "leave_room"})
    msg = await c2b["expect"]({"room_closed", "game_error"})
    left = msg is not None and msg["type"] == "room_closed"
    check("离桌结算", left, str(msg and msg.get("reason", "")))
    await c2b["send"]({"type": "get_finance"})
    msg = await c2b["expect"]({"finance"})
    has_net = any(tx["kind"] == "game_result" for tx in msg["transactions"])
    no_buyin = not any(
        tx["kind"] == "game_buyin" and "协议测试房" in tx["detail"]
        for tx in msg["transactions"]
    )
    check("离桌退款入账", (has_net or msg["coins"] == 1000) and no_buyin,
          f"balance={msg['coins']} net_row={has_net} buyin行已合并={no_buyin}")

    # 10. 宽限超时：玩家2 断开并不回来（不改全局 30s，太慢，跳过实际等待）
    # 11. 房主流局解散：所有人退回当前筹码
    await c1["send"]({"type": "leave_room"})
    msg = await c1["expect"]({"room_closed"})
    check("房主解散退款", msg is not None and msg["type"] == "room_closed",
          str(msg and msg.get("reason", "")))
    await c1["send"]({"type": "get_finance"})
    msg = await c1["expect"]({"finance"})
    check("解散退款入账", msg["coins"] >= 100,
          f"balance={msg['coins']}")

    for cli in (c1, c2, c2b, cg):
        try:
            await cli["ws"].close()
        except Exception:
            pass

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
