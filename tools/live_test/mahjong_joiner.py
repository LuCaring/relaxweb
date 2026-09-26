#!/usr/bin/env python3
"""麻将陪玩机器人：python3 tools/live_test/mahjong_joiner.py [玩家名]

自动寻找玩家1的麻将房间加入并陪打（能碰就碰、能杠就杠、能胡就胡），
结算时投「再来一局」。用于浏览器手动联调时补齐 4 家。
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

from local_config import URI
PASS = "test123456"


class Joiner:
    def __init__(self, user):
        self.user = user
        self.ws = None
        self.joined = asyncio.Event()
        self.joining = False
        self.voted = set()

    async def send(self, payload):
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def handle(self, data):
        t = data.get("type")
        if t == "room_list":
            if not self.joined.is_set() and not self.joining:
                for room in data.get("rooms", []):
                    if room.get("game") == "mahjong" and len(room.get("players", [])) < 4:
                        self.joining = True
                        await self.send({"type": "join_room", "room_id": room["id"]})
                        print(f"[{self.user}] joining #{room['id']}", flush=True)
                        break
        elif t == "game_joined":
            print(f"[{self.user}] joined {data.get('room', {}).get('room_id')}", flush=True)
            self.joined.set()
        elif t == "game_update":
            view = data
            if view.get("status") != "playing":
                return
            result = view.get("result")
            settlement = view.get("settlement")
            if result and settlement and view.get("hand_no") not in self.voted:
                self.voted.add(view.get("hand_no"))
                await self.send({"type": "settle_vote", "choice": "next",
                                 "blind": view.get("blind")})
                return
            options = view.get("your_options") or {}
            claim = options.get("claim")
            if view.get("phase") == "claim" and claim:
                if claim.get("hu"):
                    await self.send({"type": "poker_action", "action": "claim", "kind": "hu"})
                elif claim.get("gang"):
                    await self.send({"type": "poker_action", "action": "claim", "kind": "gang"})
                elif claim.get("peng"):
                    await self.send({"type": "poker_action", "action": "claim", "kind": "peng"})
                elif claim.get("chi"):
                    await self.send({"type": "poker_action", "action": "claim",
                                     "kind": "chi", "tiles": claim["chi"][0]})
                else:
                    await self.send({"type": "poker_action", "action": "pass"})
                return
            if view.get("to_act") != self.user or view.get("phase") != "discard":
                return
            if options.get("zimo"):
                await self.send({"type": "poker_action", "action": "hu"})
                return
            if options.get("angang"):
                index = (view.get("your_hand") or []).index(options["angang"][0])
                await self.send({"type": "poker_action", "action": "angang", "index": index})
                return
            if options.get("bugang"):
                index = (view.get("your_hand") or []).index(options["bugang"][0])
                await self.send({"type": "poker_action", "action": "bugang", "index": index})
                return
            if options.get("discard"):
                hand = view.get("your_hand") or []
                if hand:
                    await self.send({"type": "poker_action", "action": "discard",
                                     "index": len(hand) // 2})

    async def run(self):
        async with websockets.connect(URI + "/?client=game") as ws:
            self.ws = ws
            await self.send({"type": "login", "username": self.user, "password": PASS})
            started = asyncio.Event()

            async def reader():
                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("type") == "login_success":
                        started.set()
                    await self.handle(data)

            task = asyncio.create_task(reader())
            await asyncio.wait_for(started.wait(), 10)
            for _ in range(240):
                if self.joined.is_set():
                    break
                await self.send({"type": "list_rooms"})
                await asyncio.sleep(1)
            await asyncio.sleep(3600)


if __name__ == "__main__":
    user = sys.argv[1] if len(sys.argv) > 1 else "玩家2"
    try:
        asyncio.run(Joiner(user).run())
    except KeyboardInterrupt:
        pass
