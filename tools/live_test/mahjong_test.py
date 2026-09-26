#!/usr/bin/env python3
"""国标麻将 4 人对局集成测试：python3 tools/live_test/mahjong_test.py

四个测试账号走真实 WebSocket 协议：玩家1 创建麻将房间（自定义规则），
玩家2/3/4 加入，开局后自动摸打（能碰就碰、能杠就杠、能胡就胡），
打完两手（第一手结算后全员投「再来一局」，第二手后投「解散」）。
随机对局大概率以荒庄流局收尾，同样验证计分外的一切流程。

前置：bash tools/live_test/restart_all.sh；账号 玩家1~4 / test123456。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from local_config import URI
PASS = "test123456"
PLAYERS = ["玩家1", "玩家2", "玩家3", "玩家4"]
TIMEOUT = float(os.environ.get("MAHJONG_TEST_TIMEOUT", "240"))
MAX_HANDS = 2

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


class Bot:
    def __init__(self, user, role):
        self.user = user
        self.role = role            # "host" | "joiner"
        self.ws = None
        self.room_id = None
        self.joined = asyncio.Event()
        self.joining = False
        self.hand_results = []
        self.voted_hand = set()
        self.first_view = None
        self.last_view = None
        self.closed = asyncio.Event()
        self.action_errors = 0
        self.my_wins = 0
        self.claimed = 0
        self.tenpai_seen = False
        self.max_hand = 0
        self.playing_view = None

    async def send(self, payload):
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def handle(self, data):
        t = data.get("type")
        if t == "game_error":
            message = data.get("message") or ""
            if message == "你不在任何房间中":
                return   # host 启动时的防御性退房，属预期
            self.action_errors += 1
            print(f"[bot] {self.user} game_error: {message}", flush=True)
        elif t == "game_joined":
            room = data.get("room") or {}
            self.room_id = room.get("room_id")
            self.first_view = room
            self.last_view = room
            print(f"[bot] {self.user} joined #{self.room_id} "
                  f"({room.get('game_type')}) rules={room.get('rules')}", flush=True)
            self.joined.set()
        elif t == "room_list":
            if self.role == "joiner" and not self.joined.is_set() and not self.joining:
                for room in data.get("rooms", []):
                    if room.get("game") == "mahjong" and len(room.get("players", [])) < 4:
                        self.joining = True
                        await self.send({"type": "join_room", "room_id": room["id"]})
                        print(f"[bot] {self.user} joining #{room['id']}", flush=True)
                        break
        elif t == "hand_result":
            self.hand_results.append(data)
            if data.get("winner"):
                print(f"[bot] {self.user} saw hand #{data.get('hand_no')}: "
                      f"winner={data.get('winner')} fan={data.get('fan_total')} "
                      f"payouts={data.get('payouts')}", flush=True)
            else:
                print(f"[bot] {self.user} saw hand #{data.get('hand_no')}: "
                      f"{'荒庄' if data.get('draw_game') else '作废'}", flush=True)
        elif t == "room_closed":
            print(f"[bot] {self.user} room closed: {data.get('reason')}", flush=True)
            # 开局前房主的防御性退房也会收到这条消息，别当成自己的房间关了
            if self.joined.is_set():
                self.closed.set()
        elif t == "game_update":
            view = data
            self.last_view = view
            if view.get("status") == "playing" and self.playing_view is None:
                self.playing_view = view
            if view.get("status") != "playing":
                return
            result = view.get("result")
            settlement = view.get("settlement")
            if result and settlement and view.get("hand_no") not in self.voted_hand:
                self.voted_hand.add(view.get("hand_no"))
                await asyncio.sleep(0.3)
                choice = "next" if len(self.hand_results) < MAX_HANDS else "dissolve"
                await self.send({"type": "settle_vote", "choice": choice,
                                 "blind": view.get("blind")})
                print(f"[bot] {self.user} voted {choice}", flush=True)
                return
            options = view.get("your_options") or {}
            claim = options.get("claim")
            if view.get("phase") == "claim" and claim:
                await asyncio.sleep(0.15)
                if claim.get("hu"):
                    await self.send({"type": "poker_action", "action": "claim",
                                     "kind": "hu"})
                    self.my_wins += 1
                elif claim.get("gang"):
                    await self.send({"type": "poker_action", "action": "claim",
                                     "kind": "gang"})
                    self.claimed += 1
                elif claim.get("peng"):
                    await self.send({"type": "poker_action", "action": "claim",
                                     "kind": "peng"})
                    self.claimed += 1
                elif claim.get("chi"):
                    pair = claim["chi"][0]
                    await self.send({"type": "poker_action", "action": "claim",
                                     "kind": "chi", "tiles": pair})
                    self.claimed += 1
                else:
                    await self.send({"type": "poker_action", "action": "pass"})
                return
            if view.get("to_act") != self.user or view.get("phase") != "discard":
                return
            if view.get("tenpai"):
                self.tenpai_seen = True
            self.max_hand = max(self.max_hand, len(view.get("your_hand") or []))
            await asyncio.sleep(0.1)
            if options.get("zimo"):
                await self.send({"type": "poker_action", "action": "hu"})
                self.my_wins += 1
                print(f"[bot] {self.user} 自摸胡!", flush=True)
                return
            if options.get("angang"):
                code = options["angang"][0]
                index = (view.get("your_hand") or []).index(code)
                await self.send({"type": "poker_action", "action": "angang",
                                 "index": index})
                print(f"[bot] {self.user} 暗杠", flush=True)
                return
            if options.get("bugang"):
                code = options["bugang"][0]
                index = (view.get("your_hand") or []).index(code)
                await self.send({"type": "poker_action", "action": "bugang",
                                 "index": index})
                print(f"[bot] {self.user} 补杠", flush=True)
                return
            if options.get("discard"):
                hand = view.get("your_hand") or []
                if hand:
                    # 出中间一张，减少无意破坏牌型的极端情况
                    index = len(hand) // 2
                    await self.send({"type": "poker_action", "action": "discard",
                                     "index": index})

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

            read_task = asyncio.create_task(reader())
            await asyncio.wait_for(started.wait(), 10)
            if self.role == "host":
                await self.send({"type": "leave_room"})   # 清掉残留房间（若有）
                await asyncio.sleep(0.5)
                await self.send({
                    "type": "create_room", "game": "mahjong", "name": "麻将集成测试",
                    "buy_in": 100, "blind": 1,
                    "rules": {"min_fan": 8, "flowers": True, "chow": True,
                              "dianpao_full": True},
                })
                await asyncio.wait_for(self.joined.wait(), 10)
                for _ in range(120):
                    players = ((self.last_view or {}).get("players")) or []
                    if len(players) == 4:
                        await self.send({"type": "start_game"})
                        print("[bot] 玩家1 started game", flush=True)
                        break
                    await asyncio.sleep(0.5)
            else:
                for _ in range(30):
                    if self.joined.is_set():
                        break
                    await self.send({"type": "list_rooms"})
                    await asyncio.sleep(0.5)
                if not self.joined.is_set():
                    raise SystemExit(f"{self.user} 没找到可加入的麻将房间")
            await asyncio.wait_for(self.closed.wait(), TIMEOUT)
            read_task.cancel()


async def main():
    bots = [Bot("玩家1", "host")] + [Bot(name, "joiner") for name in PLAYERS[1:]]

    runs = [asyncio.create_task(bot.run()) for bot in bots]
    await asyncio.gather(*runs, return_exceptions=True)
    return bots


bots = asyncio.run(main())
host = bots[0]
first = host.playing_view or {}
last = host.last_view or {}

check("四人加入且规则回显", len(first.get("players") or []) == 4
      and (first.get("rules") or {}).get("min_fan") == 8
      and (first.get("rules") or {}).get("flowers") is True,
      str(first.get("rules")))
check("开局视图含圈风与庄家", first.get("round_wind") == "东" and first.get("dealer"),
      f"{first.get('round_wind')} / {first.get('dealer')}")
# 吃碰后暗牌会减少，因此只要求「看到过接近满手」且庄家首轮 14 张
check("各家正常摸牌（庄家 14 张，其余 ≥10 张）",
      bots[0].max_hand == 14 and all(b.max_hand >= 10 for b in bots),
      str([(b.user, b.max_hand) for b in bots]))
check("两手全部产生结果", len(host.hand_results) >= MAX_HANDS,
      f"{len(host.hand_results)} hands")
kinds = {"win" if h.get("winner") else ("draw" if h.get("draw_game") else "abort")
         for h in host.hand_results}
print(f"[stats] hand kinds: {kinds}, claims/wins by bots: "
      f"{[(b.user, b.claimed, b.my_wins, b.tenpai_seen) for b in bots]}")
check("存在正常结束的手（胡牌或荒庄）",
      kinds <= {"win", "draw"} and bool(host.hand_results), str(kinds))
check("胡牌手带番种明细与结算", all(
    (not h.get("winner")) or (h.get("fans") and h.get("fan_total", 0) >= 8
                              and h.get("payouts") is not None)
    for h in host.hand_results))
check("无人收到 game_error", all(b.action_errors == 0 for b in bots))
check("房间最终解散", host.closed.is_set())

failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
