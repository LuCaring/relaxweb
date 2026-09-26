"""测试机器人：登录、加入房间、自动行动（check > call > 全下跟注）。"""
import asyncio
import json
import sys

import websockets

from local_config import URI
USER = sys.argv[1] if len(sys.argv) > 1 else "玩家2"
PASS = "test123456"


async def main():
    async with websockets.connect(URI) as ws:
        joined = False

        async def send(d):
            await ws.send(json.dumps(d, ensure_ascii=False))

        await send({"type": "login", "username": USER, "password": PASS})
        async for raw in ws:
            data = json.loads(raw)
            t = data.get("type")
            if t == "login_success":
                print(f"[bot] {USER} logged in", flush=True)
                await send({"type": "get_room"})
                await send({"type": "list_rooms"})
            elif t == "room_list":
                if joined:
                    continue
                for room in data.get("rooms", []):
                    if all(p["username"] != USER for p in room["players"]):
                        await send({"type": "join_room", "room_id": room["id"]})
                        break
            elif t == "game_joined":
                joined = True
                print(f"[bot] {USER} joined room", flush=True)
            elif t == "room_closed":
                joined = False
            elif t == "game_update":
                if data.get("to_act") == USER and data.get("your_options"):
                    opts = data["your_options"]
                    if opts.get("check"):
                        await send({"type": "poker_action", "action": "check"})
                    elif opts.get("call"):
                        await send({"type": "poker_action", "action": "call"})
                    elif opts.get("allin"):
                        await send({"type": "poker_action", "action": "raise",
                                    "raise_to": opts["allin_to"]})
                    else:
                        await send({"type": "poker_action", "action": "fold"})
                    print(f"[bot] {USER} acted", flush=True)


asyncio.run(main())
