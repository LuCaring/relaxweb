#!/usr/bin/env python3
"""休闲庄园真实 WebSocket 协议回归。"""
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
import time

import server.database as storage

server = create_app()


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def receive(ws, kind):
    async def read():
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind:
                return message
    return await asyncio.wait_for(read(), 5)


async def main():
    clock = 2_000_000_000
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(storage, "DB_FILE", str(Path(tmp) / "estate.db")), \
             patch.object(time, "time", return_value=clock) as now:
            init_db(server.database)
            with server.database() as conn, conn:
                conn.execute(
                    "INSERT INTO users(username,password_hash,salt,created_at,coins) "
                    "VALUES ('alice','','',0,19)"
                )
            token = server.accounts.create_session("alice")
            async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
                uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
                async with websockets.connect(uri) as a, websockets.connect(uri) as b:
                    await send(a, type="get_estate")
                    denied = await receive(a, "estate_error")
                    assert denied["code"] == "auth_required"

                    for ws in (a, b):
                        await send(ws, type="resume", token=token)
                        await receive(ws, "resume_success")
                    await send(a, type="get_estate")
                    initial = await receive(a, "estate_state")
                    assert initial["profile"]["plot_count"] == 4
                    assert len(initial["plots"]) == 12

                    # Use the real wallet: insufficient funds must keep the domain
                    # message and roll back ownership, inventory, ledger and replay keys.
                    for purchase in (
                        dict(type="estate_buy", request_id="buy-wheat-001",
                             kind="seed", item_id="wheat", quantity=1),
                        dict(type="estate_buy_skin", request_id="poor-skin-001", skin_id="steve"),
                    ):
                        await send(a, **purchase)
                        rejected = await receive(a, "estate_error")
                        assert rejected["code"] == "insufficient_coins", rejected["code"]
                        assert rejected["message"] == "金币不足", rejected["message"]
                        assert rejected["request_id"] == purchase["request_id"]
                    await send(a, type="get_estate")
                    unchanged = await receive(a, "estate_state")
                    for field in ("coins", "inventory", "skins", "version"):
                        assert unchanged[field] == initial[field], field
                    with server.database() as conn, conn:
                        assert conn.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0] == 0
                        assert conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0] == 0
                        conn.execute("UPDATE users SET coins=10000 WHERE username='alice'")

                    await send(a, type="estate_buy", request_id="buy-wheat-001",
                               kind="seed", item_id="wheat", quantity=1)
                    bought_a, bought_b = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert bought_a["coins"] == bought_b["coins"] == 9980
                    assert not bought_a["result"]["replayed"]

                    await send(a, type="estate_plant", request_id="plant-wheat-01",
                               plot_id=0, crop_id="wheat")
                    planted_a, _ = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    ready_at = planted_a["result"]["ready_at"]
                    now.return_value = ready_at

                    await asyncio.gather(
                        send(a, type="estate_harvest", request_id="harvest-a-001", plot_id=0),
                        send(b, type="estate_harvest", request_id="harvest-b-001", plot_id=0),
                    )
                    first_a, first_b = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert first_a["result"]["crop_id"] == "wheat"
                    assert first_b["result"]["crop_id"] == "wheat"
                    rejected = await receive(b, "estate_error")
                    assert rejected["code"] == "plot_empty"

                    await send(a, type="estate_sell", request_id="sell-wheat-001",
                               item_id="crop:wheat", quantity=1)
                    sold_a, _ = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert sold_a["coins"] == 10010

                    await send(a, type="estate_sell", request_id="sell-wheat-001",
                               item_id="crop:wheat", quantity=1)
                    replay_a, _ = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert replay_a["result"]["replayed"]
                    assert replay_a["coins"] == 10010

                    await send(a, type="get_finance")
                    finance = await receive(a, "finance")
                    assert [row["kind"] for row in finance["transactions"][:2]] == [
                        "estate_sale", "estate_purchase"
                    ]

                init_db(server.database)
                async with websockets.connect(uri) as c:
                    await send(c, type="resume", token=token)
                    await receive(c, "resume_success")
                    await send(c, type="get_estate")
                    persisted = await receive(c, "estate_state")
                    assert persisted["coins"] == 10010
                    assert persisted["profile"]["level"] == 1
                    assert persisted["plots"][0]["crop_id"] is None

    print("PASS estate WebSocket: auth, sync, lifecycle, concurrency, idempotency, ledger and persistence")


if __name__ == "__main__":
    asyncio.run(main())
