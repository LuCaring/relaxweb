"""UNO 测试机器人：登录、找 UNO 房间加入、事件驱动出牌。

用法：python3 uno_bot.py [用户名]   （默认玩家2，密码 test123456）
"""
import asyncio
import json
import sys

import websockets

from local_config import URI
USER = sys.argv[1] if len(sys.argv) > 1 else "玩家2"
PASS = "test123456"


def playable(card, active):
    if not active:
        return False
    if card["c"] == "w":
        return True
    return card["c"] == active["c"] or card["v"] == active["v"]


async def main():
    async with websockets.connect(URI + "/?client=game") as ws:
        state = {"joined": False, "in_room": False}

        async def send(d):
            await ws.send(json.dumps(d, ensure_ascii=False))

        async def act(data):
            opts = data.get("your_options") or {}
            await asyncio.sleep(0.4)
            if opts.get("uno"):
                await send({"type": "poker_action", "action": "uno"})
            if data.get("to_act") != USER:
                return
            hand = data.get("your_hand") or []
            active = data.get("active") or {}
            drawn_only = opts.get("pass") and data.get("your_drawn") is not None
            pick = None
            for index, card in enumerate(hand):
                if drawn_only and index != data["your_drawn"]:
                    continue
                if playable(card, active):
                    pick = (index, card)
                    break
            if pick is not None:
                payload = {"type": "poker_action", "action": "play", "card": pick[0]}
                if pick[1]["c"] == "w":
                    payload["color"] = "r"
                await send(payload)
                print(f"[bot] {USER} played {pick[1]}", flush=True)
            elif opts.get("draw"):
                await send({"type": "poker_action", "action": "draw"})
                print(f"[bot] {USER} drew", flush=True)
            elif opts.get("pass"):
                await send({"type": "poker_action", "action": "pass"})
                print(f"[bot] {USER} passed", flush=True)

        await send({"type": "login", "username": USER, "password": PASS})
        async for raw in ws:
            data = json.loads(raw)
            t = data.get("type")
            if t == "login_success":
                print(f"[bot] {USER} logged in", flush=True)
                await send({"type": "get_room"})
                if not state["in_room"]:
                    await send({"type": "list_rooms"})
            elif t == "room_closed":
                state = {"joined": False, "in_room": False}
                await send({"type": "list_rooms"})
            elif t == "game_joined":
                state = {"joined": True, "in_room": True}
                print(f"[bot] {USER} joined room", flush=True)
            elif t == "room_list":
                if state["in_room"]:
                    continue
                for room in data.get("rooms", []):
                    if room.get("game") != "uno":
                        continue
                    if all(p["username"] != USER for p in room["players"]):
                        await send({"type": "join_room", "room_id": room["id"]})
                        break
            elif t == "game_update":
                if data.get("result"):
                    if not state.get("voted"):
                        state["voted"] = True
                        await send({"type": "settle_vote", "choice": "next",
                                    "blind": 5})
                        print(f"[bot] {USER} voted next", flush=True)
                    continue
                state["voted"] = False
                await act(data)


asyncio.run(main())
