#!/usr/bin/env python3
"""掼蛋 4 人对局集成测试：python3 tools/live_test/guandan_test.py

四个测试账号走真实 WebSocket 协议：玩家1 创建掼蛋房间（自定义规则），
玩家2/3/4 加入，开局后按「最小单张」策略自动出牌，打完两手
（第一手结算后全员投「再来一局」，第二手后投「解散」）。

前置：bash tools/live_test/reset.sh；账号 玩家1~4 / test123456。
"""
import asyncio
import json
import os
import sys

import websockets

from local_config import URI
PASS = "test123456"
PLAYERS = ["玩家1", "玩家2", "玩家3", "玩家4"]
TIMEOUT = float(os.environ.get("GUANDAN_TEST_TIMEOUT", "90"))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def rank_value(rank, levels):
    if rank == 17:
        return (3, 2)
    if rank == 16:
        return (3, 1)
    if rank in levels:
        return (2, rank)
    return (1, rank)


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
        self.levels_after_first = None
        self.closed = asyncio.Event()
        self.action_errors = 0
        self.saw_full_hand = False

    async def send(self, payload):
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    def decide(self, view):
        """最小单张策略：自由出牌出最小单张；跟单张能压就压，否则过。"""
        hand = view.get("your_hand") or []
        if not hand:
            return None
        levels = set(view.get("levels") or [2, 2])
        values = [(i, rank_value(c["r"], levels)) for i, c in enumerate(hand)]
        if view.get("free_lead"):
            index, _ = min(values, key=lambda x: x[1])
            return {"type": "poker_action", "action": "play", "cards": [index]}
        standing = view.get("standing")
        if standing and standing.get("type") == "single":
            main = tuple(standing.get("main") or (9, 9))
            beats = [(i, v) for i, v in values if v > main]
            if beats:
                index, _ = min(beats, key=lambda x: x[1])
                return {"type": "poker_action", "action": "play", "cards": [index]}
        if (view.get("your_options") or {}).get("pass"):
            return {"type": "poker_action", "action": "pass"}
        return None

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
                    if room.get("game") == "guandan" and len(room.get("players", [])) < 4:
                        self.joining = True
                        await self.send({"type": "join_room", "room_id": room["id"]})
                        print(f"[bot] {self.user} joining #{room['id']}", flush=True)
                        break
        elif t == "hand_result":
            self.hand_results.append(data)
            print(f"[bot] {self.user} saw hand #{data.get('hand_no')} result: "
                  f"finish={data.get('finish')} gain={data.get('gain')} "
                  f"payouts={data.get('payouts')}", flush=True)
        elif t == "room_closed":
            print(f"[bot] {self.user} room closed: {data.get('reason')}", flush=True)
            self.closed.set()
        elif t == "game_update":
            view = data
            self.last_view = view
            self.saw_full_hand |= len(view.get("your_hand") or []) == 27
            if view.get("status") != "playing":
                return
            result = view.get("result")
            settlement = view.get("settlement")
            if result and settlement and view.get("hand_no") not in self.voted_hand:
                self.voted_hand.add(view.get("hand_no"))
                if self.levels_after_first is None:
                    self.levels_after_first = view.get("levels")
                await asyncio.sleep(0.3)
                choice = "next" if len(self.hand_results) < 2 else "dissolve"
                await self.send({"type": "settle_vote", "choice": choice,
                                 "blind": view.get("blind")})
                print(f"[bot] {self.user} voted {choice}", flush=True)
                return
            if view.get("to_act") != self.user:
                return
            await asyncio.sleep(0.15)
            action = self.decide(view)
            if action:
                await self.send(action)

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
                    "type": "create_room", "game": "guandan", "name": "掼蛋集成测试",
                    "buy_in": 100, "blind": 1,
                    "rules": {"wild": True, "bomb_cap": 8, "ace_strict": True},
                })
                await asyncio.wait_for(self.joined.wait(), 10)
                # 等 4 人到齐（用主 reader 维护的 last_view 计数）再开局
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
                    raise SystemExit(f"{self.user} 没找到可加入的掼蛋房间")
            await asyncio.wait_for(self.closed.wait(), TIMEOUT)
            read_task.cancel()


async def main():
    host = Bot("玩家1", "host")
    joiners = [Bot(name, "joiner") for name in PLAYERS[1:]]
    bots = [host] + joiners

    def started(bots_):
        return all(bot.first_view is not None for bot in bots_)

    tasks = [asyncio.create_task(bot.run()) for bot in bots]
    waits = [asyncio.create_task(bot.closed.wait()) for bot in bots]
    done, pending = await asyncio.wait(waits, timeout=TIMEOUT + 20)
    for task in pending:
        task.cancel()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    check("四人全部进房", started(bots))
    view = host.first_view or {}
    check("房间类型为掼蛋", view.get("game_type") == "guandan")
    check("自定义规则已存入房间", view.get("rules") == {
        "wild": True, "bomb_cap": 8, "ace_strict": True}, str(view.get("rules")))
    check("每人27张手牌下发", all(bot.saw_full_hand for bot in bots))
    hand1 = [data for bot in bots for data in bot.hand_results if data["hand_no"] == 1]
    check("第一手打完并有结算", len(hand1) >= 4, f"{len(hand1)} 份")
    if hand1:
        result = hand1[0]
        check("名次完整（头游~末游）", len(result.get("finish") or []) == 4,
              str(result.get("finish")))
        check("升级数合法(1~3)", result.get("gain") in (1, 2, 3), str(result.get("gain")))
        total_out = round(sum(result.get("payouts", {}).values()), 2)
        total_in = round(sum(result.get("gains", {}).values()), 2)
        check("金币守恒", abs(total_out - total_in) < 0.01 and total_out > 0,
              f"out={total_out} in={total_in}")
    check("第一手后级数推进", host.levels_after_first is not None
          and host.levels_after_first != [2, 2], str(host.levels_after_first))
    hand2 = [data for bot in bots for data in bot.hand_results if data["hand_no"] == 2]
    check("第二手正常重发并打完", len(hand2) >= 4, f"{len(hand2)} 份")
    if hand2:
        check("第二手头游先出仍能收尾", len(hand2[0].get("finish") or []) == 4)
    check("解散后房间关闭", all(bot.closed.is_set() for bot in bots))
    errors = sum(bot.action_errors for bot in bots)
    check("无 game_error", errors == 0, f"{errors} 条")

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
