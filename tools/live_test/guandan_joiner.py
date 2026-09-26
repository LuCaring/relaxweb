#!/usr/bin/env python3
"""掼蛋加入器：python3 tools/live_test/guandan_joiner.py [人数，默认3]

N 个机器人等待并加入掼蛋房间，按「最小单张」策略跟牌，
直到房间关闭。供 GUI 冒烟测试与手动联调当陪玩。
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).parent))
from guandan_test import Bot, PLAYERS  # noqa: E402

from local_config import URI


async def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    bots = [Bot(name, "joiner") for name in PLAYERS[1:1 + count]]
    tasks = [asyncio.create_task(bot.run()) for bot in bots]
    await asyncio.gather(*tasks, return_exceptions=True)
    print("[joiner] all bots exited", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
