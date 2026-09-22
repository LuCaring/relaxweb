#!/usr/bin/env python3
"""钓鱼和矿场真实 WebSocket 闭环。"""
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

server = create_app()


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def receive(ws, kind="estate_state"):
    async with asyncio.timeout(5):
        while True:
            data = json.loads(await ws.recv())
            if data["type"] == kind:
                return data
            if data["type"] == "estate_error":
                raise AssertionError(data)


def winning_trace(pattern):
    tension, progress, trace = .18, .08, []
    for index in range(360):
        force = pattern[min(len(pattern) - 1, index // 10)]
        held = tension < .64
        trace.append(held)
        if held:
            tension += .026 * (.68 + force)
            progress += .013 * (1.12 - force * .3)
        else:
            tension = max(0, tension - .045)
            progress = max(0, progress - .0035 * (.5 + force))
        if progress >= 1:
            return trace
    return trace


async def action(ws, request_id, message_type, **payload):
    await send(ws, type=message_type, request_id=request_id, **payload)
    return await receive(ws)


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(storage, "DB_FILE", str(Path(tmp) / "activities.db")):
            init_db(server.database)
            with server.database() as conn, conn:
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES ('alice','','',0,10000)")
            token = server.accounts.create_session("alice")
            async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
                uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
                async with websockets.connect(uri) as ws:
                    await send(ws, type="resume", token=token); await receive(ws, "resume_success")
                    await action(ws, "rod-buy-0001", "estate_buy_tool", tool_type="rod")
                    await action(ws, "pick-buy-001", "estate_buy_tool", tool_type="pickaxe")
                    await action(ws, "bait-buy-001", "estate_buy", kind="bait", item_id="worm", quantity=1)
                    fishing = await action(ws, "fish-start-01", "estate_start_fishing", bait_id="worm")
                    session = fishing["result"]
                    caught = await action(ws, "fish-finish-1", "estate_finish_fishing",
                                          session_id=session["session_id"], trace=winning_trace(session["pattern"]))
                    assert caught["result"]["outcome"] == "caught"
                    mining = await action(ws, "mine-start-01", "estate_start_mining", mine_level=1)
                    initial_durability = caught["tools"]["pickaxe"]["durability"]
                    run = mining["result"]
                    cell = await action(ws, "mine-cell-001", "estate_mine_cell", run_id=run["run_id"], cell=0)
                    assert "outcome" in cell["result"]
                    ended = await action(ws, "mine-end-0001", "estate_finish_mining", run_id=run["run_id"])
                    assert ended["result"]["finished"]
                    assert ended["profile"]["warehouse_reserved"] == 0
                    assert ended["tools"]["rod"]["durability"] == 19
                    assert ended["tools"]["pickaxe"]["durability"] == initial_durability - 1
    print("PASS fishing/mining WebSocket: tools, bait, verified catch, hidden cell and settlement")


if __name__ == "__main__":
    asyncio.run(main())
