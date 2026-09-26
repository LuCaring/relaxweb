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

                    # 行情完全由服务端本地模型生成：读快照 → 买入 → 落库。
                    await send(c, type="estate_market_trade", request_id="market-ore-proto-1",
                               symbol="XORE", side="buy", quantity="2")
                    ore = await receive(c, "estate_state")
                    assert ore["result"]["symbol"] == "XORE", ore["result"]
                    assert ore["result"]["market"]["symbol"] == "XORE"
                    assert ore["result"]["market"]["shares"] == 2
                    assert [row["shares"] for row in ore["result"]["market"]["positions"]] == [2]
                    await send(c, type="estate_market_get", symbol="XORE")
                    quote = await receive(c, "estate_market_state")
                    assert quote["market"]["symbol"] == "XORE", quote["market"]["symbol"]
                    assert len(quote["market"]["symbols"]) == 3
                    await send(c, type="estate_market_get")
                    quote = await receive(c, "estate_market_state")
                    assert quote["market"]["symbol"] == "XTIDE", quote["market"]["symbol"]
                    assert quote["market"]["available"] is True
                    assert quote["market"]["anchor"] == 1000, quote["market"]["anchor"]
                    quoted = quote["market"]["price"]
                    assert 625 <= quoted <= 1600, quoted
                    assert len(quote["market"]["candles"]["minute"]) >= 1
                    await send(c, type="estate_market_trade", request_id="market-buy-proto-1",
                               side="buy", quantity="0.5")

                    filled = await receive(c, "estate_state")
                    assert filled["result"]["action"] == "market_trade"
                    assert filled["result"]["market"]["shares"] == 0.5
                    # 买的是 XTIDE，XORE 那 2 份不受影响，说明标的确实是分开的
                    assert filled["result"]["market"]["symbol"] == "XTIDE"
                    assert filled["result"]["average_price"] > quoted, filled["result"]["average_price"]
                    assert filled["result"]["market"]["book"]["asks"][0]["price"] > 0

                    # 限价委托：低于现价挂单resting，撤单后回到空簿。
                    await send(c, type="estate_market_order", request_id="market-limit-proto-1",
                               side="buy", price=str(int(quoted) - 5), quantity="2")
                    placed = await receive(c, "estate_state")
                    assert placed["result"]["action"] == "market_order"
                    assert placed["result"]["filled"] == 0, placed["result"]
                    order = placed["result"]["market"]["orders"][0]
                    assert order["remaining"] == 2
                    await send(c, type="estate_market_cancel", request_id="market-cancel-proto-1",
                               order_id=order["id"])
                    cancelled = await receive(c, "estate_state")
                    assert cancelled["result"]["action"] == "market_cancel"
                    assert cancelled["result"]["market"]["orders"] == []
                    assert cancelled["result"]["market"]["fills"], "成交流水应包含刚才那一笔"
                    assert filled["coins"] < persisted["coins"]
                    with server.database() as conn, conn:
                        assert conn.execute("SELECT COUNT(*) FROM estate_market_ticks "
                                            "WHERE symbol='XTIDE'").fetchone()[0] == 1
                        assert conn.execute("SELECT COUNT(*) FROM estate_market_symbols"
                                            ).fetchone()[0] == 3
                        inventory = conn.execute("SELECT inventory_milli FROM estate_market_symbols "
                                                 "WHERE symbol='XTIDE'").fetchone()[0]
                        assert inventory == -500, inventory
                        # 把锚顶到拆股阈值，下一次行情访问应当触发并播报一次拆股
                        conn.execute("UPDATE estate_market_symbols SET anchor_cents=200000,"
                                     "price_cents=200000 WHERE symbol='XTIDE'")
                    now.return_value = ready_at + 60
                    await send(c, type="estate_market_get")
                    # 拆股公告在一次请求里先于行情快照发出
                    announced = await receive(c, "system")
                    assert "1:2 拆股" in announced["text"], announced["text"]
                    quote = await receive(c, "estate_market_state")
                    assert quote["market"]["split_count"] == 1, quote["market"]["split_count"]
                    assert quote["market"]["shares"] == 1.0, quote["market"]["shares"]
                    # 第二次访问不再重复播报
                    await send(c, type="estate_market_get")
                    await receive(c, "estate_market_state")
                    with server.database() as conn, conn:
                        assert conn.execute("SELECT announced_splits FROM estate_market_symbols "
                                            "WHERE symbol='XTIDE'").fetchone()[0] == 1

    print("PASS estate WebSocket: auth, sync, lifecycle, market, idempotency, ledger and persistence")


if __name__ == "__main__":
    asyncio.run(main())
