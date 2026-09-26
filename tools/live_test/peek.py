"""注册一个观察账号并打印房间视图，用于排查结算字段。"""
import asyncio
import json
import sys

import websockets

from local_config import URI
USER = "观察员"
CODE = sys.argv[1] if len(sys.argv) > 1 else ""


async def main():
    async with websockets.connect(URI) as ws:
        async def send(d):
            await ws.send(json.dumps(d, ensure_ascii=False))

        await send({"type": "login", "username": USER, "password": "test123456"})
        while True:
            data = json.loads(await ws.recv())
            t = data.get("type")
            if t == "login_failed":
                await send({"type": "register", "username": USER,
                            "password": "test123456", "invite_code": CODE})
            elif t == "register_success":
                await send({"type": "login", "username": USER, "password": "test123456"})
            elif t == "login_success":
                break
        await send({"type": "get_room"})
        while True:
            data = json.loads(await ws.recv())
            if data.get("type") != "game_update":
                continue
            print("room:", data.get("name"), "status:", data.get("status"),
                  "blind:", data.get("blind"), "stage:", data.get("stage"))
            print("settlement:", json.dumps(data.get("settlement"), ensure_ascii=False))
            for p in data.get("players", []):
                print("   ", p["nickname"], "stack", p["stack"], "bet", p["bet"],
                      "folded", p["folded"], "allin", p["allin"], "in_hand", p["in_hand"])
            result = data.get("result") or {}
            if result:
                print("board:", json.dumps(result.get("board"), ensure_ascii=False))
                print("reveal:", json.dumps(result.get("reveal"), ensure_ascii=False))
                print("hands:", json.dumps(result.get("hands"), ensure_ascii=False))
            await send({"type": "get_room"})


asyncio.run(main())
