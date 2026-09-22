#!/usr/bin/env python3
"""真实 WebSocket 回归：临时库、随机本地端口，不依赖 live-test 或实际用户数据。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets
from server.app import create_app
from server.schema import init_db

import server.database as storage
import rewards

server = create_app()


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def receive(ws, kind):
    async def read():
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind:
                return message
            if message["type"].endswith("_error"):
                raise AssertionError(message)
    return await asyncio.wait_for(read(), 5)


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(storage, "DB_FILE", str(Path(tmp) / "protocol.db")):
            init_db(server.database)
            with server.database() as conn, conn:
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES ('alice', '', '', 0, 1000)")
            token = server.accounts.create_session("alice")
            async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
                uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
                async with websockets.connect(uri) as a, websockets.connect(uri) as b:
                    await send(a, type="daily_checkin")
                    assert (await receive(a, "rewards_error"))["message"] == "请先登录"
                    for ws in (a, b):
                        await send(ws, type="resume", token=token)
                        await receive(ws, "resume_success")
                    await asyncio.gather(send(a, type="daily_checkin"), send(b, type="daily_checkin"))
                    claims = await asyncio.gather(receive(a, "checkin_result"), receive(b, "checkin_result"))
                    assert sorted(c["awarded"] for c in claims) == [0, 5]
                    assert all(c["tickets"] == 5 for c in claims)
                    with patch.object(rewards, "pick_prize", return_value=75):
                        await asyncio.gather(send(a, type="draw_lottery", request_id="retry-0001"),
                                             send(b, type="draw_lottery", request_id="retry-0001"))
                        draws = await asyncio.gather(receive(a, "lottery_result"), receive(b, "lottery_result"))
                    assert sorted(d["replayed"] for d in draws) == [False, True]
                    assert all(d["amount"] == 75 and d["tickets"] == 4 and d["coins"] == 1075 for d in draws)
                    await send(a, type="get_finance")
                    finance = await receive(a, "finance")
                    assert finance["coins"] == 1075
                    assert [t["kind"] for t in finance["transactions"]] == ["lottery_win"]
                # Retry after an actual disconnect and schema reinitialization.
                init_db(server.database)
                async with websockets.connect(uri) as c:
                    await send(c, type="resume", token=token)
                    assert (await receive(c, "resume_success"))["coins"] == 1075
                    await send(c, type="draw_lottery", request_id="retry-0001")
                    retry = await receive(c, "lottery_result")
                    assert retry["replayed"] and retry["amount"] == 75 and retry["tickets"] == 4
                    await send(c, type="get_daily_rewards")
                    state = await receive(c, "daily_rewards")
                    assert state["checked_in"] and state["tickets"] == 4 and len(state["history"]) == 1
    print("PASS real WebSocket: authentication, concurrent check-in, duplicate draw, coin ledger, disconnect retry and persistence")


if __name__ == "__main__":
    asyncio.run(main())
