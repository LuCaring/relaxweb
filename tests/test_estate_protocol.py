#!/usr/bin/env python3
"""小胖庄园真实 WebSocket 协议回归。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets
import chat_server as server


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def receive(ws, kind):
    async def read():
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind:
                return message
            if message["type"] == "estate_error":
                raise AssertionError(message)
    return await asyncio.wait_for(read(), 5)


async def main():
    clock = 2_000_000_000
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(server, "DB_FILE", str(Path(tmp) / "estate.db")), \
             patch.object(server.time, "time", return_value=clock) as now:
            server.init_db()
            with server.database() as conn, conn:
                conn.executemany(
                    "INSERT INTO users(username,password_hash,salt,created_at,coins) "
                    "VALUES (?,'','',0,?)", (("alice", 10000), ("bob", 0)),
                )
            token = server.create_session("alice")
            bob_token = server.create_session("bob")
            async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
                uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
                async with websockets.connect(uri) as a, websockets.connect(uri) as b, \
                           websockets.connect(uri) as other:
                    await send(a, type="get_estate")
                    denied = await receive(a, "estate_error")
                    assert denied["code"] == "auth_required"
                    await send(a, type="estate_set_skin", request_id="skin-anonymous-001", skin_id="xiaopang")
                    denied_skin = await receive(a, "estate_error")
                    assert denied_skin["code"] == "auth_required"
                    assert denied_skin["request_id"] == "skin-anonymous-001"
                    with server.database() as conn:
                        assert conn.execute("SELECT COUNT(*) FROM estate_profiles").fetchone()[0] == 0
                        assert conn.execute("SELECT COUNT(*) FROM estate_actions").fetchone()[0] == 0

                    for ws in (a, b):
                        await send(ws, type="resume", token=token)
                        await receive(ws, "resume_success")
                    await send(other, type="resume", token=bob_token)
                    await receive(other, "resume_success")
                    await send(other, type="get_estate")
                    bob_initial = await receive(other, "estate_state")
                    assert bob_initial["profile"]["skin_id"] == "berry"
                    assert bob_initial["coins"] == 0
                    await send(a, type="get_estate")
                    initial = await receive(a, "estate_state")
                    assert initial["profile"]["plot_count"] == 4
                    assert len(initial["plots"]) == 8
                    assert initial["profile"]["skin_id"] == "berry"
                    assert set(initial["catalog"]["skins"]) == {"berry", "xiaopang", "rose_mage"}
                    for skin_id, skin in initial["catalog"]["skins"].items():
                        assert skin["id"] == skin_id and skin["name"] and skin["description"]

                    for ws, skin_id in ((a, "xiaopang"), (b, "rose_mage")):
                        request_id = f"skin-{skin_id}-001"
                        await send(ws, type="estate_set_skin", request_id=request_id, skin_id=skin_id)
                        skin_a, skin_b = await asyncio.gather(
                            receive(a, "estate_state"), receive(b, "estate_state")
                        )
                        assert skin_a == skin_b
                        assert skin_a["profile"]["skin_id"] == skin_id
                        assert skin_a["request_id"] == request_id
                        assert skin_a["result"] == {"action": "set_skin", "skin_id": skin_id,
                                                    "request_id": request_id, "replayed": False}
                        assert skin_a["coins"] == initial["coins"]
                        assert skin_a["profile"]["xp"] == initial["profile"]["xp"]
                        assert skin_a["inventory"] == initial["inventory"]

                    # 不同账号不能收到换装广播；同一 request_id 也按账号隔离。
                    await send(other, type="get_estate")
                    bob_unchanged = await receive(other, "estate_state")
                    assert bob_unchanged == bob_initial
                    await send(other, type="estate_set_skin", request_id="skin-xiaopang-001",
                               skin_id="xiaopang", username="alice")
                    bob_selected = await receive(other, "estate_state")
                    assert bob_selected["profile"]["skin_id"] == "xiaopang"
                    assert not bob_selected["result"]["replayed"]
                    assert bob_selected["coins"] == 0
                    assert bob_selected["inventory"] == bob_initial["inventory"]
                    for ws in (a, b):
                        await send(ws, type="get_estate")
                        alice_unchanged = await receive(ws, "estate_state")
                        assert alice_unchanged["request_id"] is None
                        assert alice_unchanged["profile"] == skin_a["profile"]
                        assert alice_unchanged["coins"] == initial["coins"]

                    # 重放旧换装只重发原结果；完整快照必须仍是最新皮肤，不能回退。
                    await send(a, type="estate_set_skin", request_id="skin-xiaopang-001", skin_id="xiaopang")
                    skin_replay_a, skin_replay_b = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert skin_replay_a == skin_replay_b
                    assert skin_replay_a["result"]["replayed"]
                    assert skin_replay_a["result"]["skin_id"] == "xiaopang"
                    assert skin_replay_a["profile"] == skin_a["profile"]
                    assert skin_replay_a["profile"]["skin_id"] == "rose_mage"
                    await send(b, type="estate_set_skin", request_id="skin-xiaopang-001", skin_id="berry")
                    conflict = await receive(b, "estate_error")
                    assert conflict["code"] == "request_conflict"
                    assert conflict["state"]["profile"] == skin_a["profile"]

                    invalid_payloads = [{}] + [{"skin_id": value} for value in (
                        None, True, False, 0, 1.5, [], ["xiaopang"], {}, {"id": "xiaopang"},
                        "", "missing", "Xiaopang", " xiaopang", "xiaopang ", "__proto__",
                        "mint", "sky", "wisteria", "farmer",
                    )]
                    for index, payload in enumerate(invalid_payloads):
                        request_id = f"skin-invalid-{index:03}"
                        await send(a, type="estate_set_skin", request_id=request_id, **payload)
                        invalid = await receive(a, "estate_error")
                        assert invalid["code"] == "invalid_skin"
                        assert invalid["request_id"] == request_id
                        assert invalid["state"]["profile"] == skin_a["profile"]
                        assert invalid["state"]["coins"] == initial["coins"]
                        assert invalid["state"]["inventory"] == initial["inventory"]

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

                    # 再次选中当前皮肤也成功；有收获和买卖后，经济状态仍不受换装影响。
                    await send(b, type="estate_set_skin", request_id="skin-current-001", skin_id="rose_mage")
                    current_a, current_b = await asyncio.gather(
                        receive(a, "estate_state"), receive(b, "estate_state")
                    )
                    assert current_a == current_b
                    assert current_a["profile"]["skin_id"] == "rose_mage"
                    assert not current_a["result"]["replayed"]
                    assert current_a["coins"] == replay_a["coins"]
                    assert current_a["profile"]["xp"] == replay_a["profile"]["xp"]
                    assert current_a["inventory"] == replay_a["inventory"]

                    await send(a, type="get_finance")
                    finance = await receive(a, "finance")
                    assert [row["kind"] for row in finance["transactions"]] == [
                        "estate_sale", "estate_purchase"
                    ]
                    await send(other, type="get_estate")
                    bob_final = await receive(other, "estate_state")
                    assert bob_final["request_id"] is None
                    assert bob_final["profile"] == bob_selected["profile"]
                    assert bob_final["coins"] == 0
                    assert bob_final["inventory"] == bob_initial["inventory"]

                server.init_db()
                async with websockets.connect(uri) as c:
                    await send(c, type="resume", token=token)
                    await receive(c, "resume_success")
                    await send(c, type="get_estate")
                    persisted = await receive(c, "estate_state")
                    assert persisted["coins"] == 10010
                    assert persisted["profile"]["level"] == 1
                    assert persisted["profile"]["skin_id"] == "rose_mage"
                    assert persisted["profile"] == current_a["profile"]
                    assert persisted["plots"][0]["crop_id"] is None
                    await send(c, type="estate_set_skin", request_id="skin-xiaopang-001", skin_id="xiaopang")
                    reconnected_replay = await receive(c, "estate_state")
                    assert reconnected_replay["result"]["replayed"]
                    assert reconnected_replay["result"]["skin_id"] == "xiaopang"
                    assert reconnected_replay["profile"]["skin_id"] == "rose_mage"
                    assert reconnected_replay["version"] == persisted["version"]

    print("PASS estate WebSocket: auth, skin catalog/switch/errors, cross-account isolation, "
          "sync, lifecycle, concurrency, idempotency, ledger and reconnect persistence")


if __name__ == "__main__":
    asyncio.run(main())
