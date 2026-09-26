"""德州扑克测试机器人：自动入座、跟注/看牌、结算投票「再来一局」。

用法: python3 poker_bot.py 玩家2 [raise]
  raise  参数存在时偶尔加注一次，便于观察座位下注金额展示。
"""
import asyncio
import json
import sys

import websockets

from local_config import URI
USER = sys.argv[1] if len(sys.argv) > 1 else "玩家2"
AGGRESSIVE = len(sys.argv) > 2
PASS = "test123456"


async def main():
    async with websockets.connect(URI) as ws:
        joined = False
        raised = set()

        async def send(d):
            await ws.send(json.dumps(d, ensure_ascii=False))

        await send({"type": "login", "username": USER, "password": PASS})
        async for raw in ws:
            data = json.loads(raw)
            t = data.get("type")
            if t == "login_success":
                print(f"[bot {USER}] logged in", flush=True)
                await send({"type": "get_room"})
                await send({"type": "list_rooms"})
            elif t == "room_list":
                if joined:
                    continue
                for room in data.get("rooms", []):
                    if room.get("game") != "holdem":
                        continue
                    if all(p["username"] != USER for p in room["players"]):
                        await send({"type": "join_room", "room_id": room["id"]})
                        break
            elif t == "game_joined":
                joined = True
                print(f"[bot {USER}] joined room", flush=True)
            elif t == "room_closed":
                joined = False
                await send({"type": "list_rooms"})
            elif t == "game_update":
                if data.get("settlement"):
                    votes = data["settlement"].get("votes") or {}
                    if USER not in votes and data["settlement"].get("can_next"):
                        await send({"type": "settle_vote", "choice": "next",
                                    "blind": data["settlement"].get("blind") or 5})
                        print(f"[bot {USER}] voted next", flush=True)
                    continue
                if data.get("to_act") != USER:
                    continue
                opts = data.get("your_options") or {}
                hand = data.get("hand_no")
                if AGGRESSIVE and hand not in raised and opts.get("can_raise"):
                    raised.add(hand)
                    target = min(opts["raise_min"] * 2, opts["raise_max"])
                    await send({"type": "poker_action", "action": "raise",
                                "raise_to": target})
                    print(f"[bot {USER}] raised to {target}", flush=True)
                elif opts.get("check"):
                    await send({"type": "poker_action", "action": "check"})
                elif opts.get("call"):
                    await send({"type": "poker_action", "action": "call"})
                elif opts.get("allin"):
                    await send({"type": "poker_action", "action": "raise",
                                "raise_to": opts["allin_to"]})
                else:
                    await send({"type": "poker_action", "action": "fold"})
                print(f"[bot {USER}] acted hand={hand}", flush=True)


asyncio.run(main())
