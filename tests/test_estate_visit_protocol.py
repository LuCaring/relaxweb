#!/usr/bin/env python3
"""休闲庄园拜访与偷菜 WebSocket 端到端回归。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets
import chat_server as server
from estate import ensure_estate


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def receive(ws, kind):
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if message["type"] == kind:
            return message


async def main():
    clock = 2_000_000_000
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(server, "DB_FILE", str(Path(tmp) / "visits.db")), \
         patch.object(server.time, "time", return_value=clock):
        server.clients.clear(); server.estate_channels.clear(); server.init_db()
        with server.database() as conn, conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO users(username,password_hash,salt,created_at,coins) "
                             "VALUES (?,?,?,0,10000)", (name, "", ""))
                ensure_estate(conn, name, clock)
                conn.execute("UPDATE estate_profiles SET level=3 WHERE username=?", (name,))
                conn.execute("UPDATE estate_profiles SET plot_count=12 WHERE username=?", (name,))
            conn.execute("UPDATE estate_plots SET crop_id='wheat',planted_at=?,ready_at=? "
                         "WHERE username='bob' AND plot_index=9", (clock - 400, clock - 1))
        alice_token = server.create_session("alice")
        bob_token = server.create_session("bob")
        async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
            uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
            async with websockets.connect(uri) as alice:
                await send(alice, type="resume", token=alice_token); await receive(alice, "resume_success")
                await send(alice, type="estate_list_visits", request_id="visit-list-001")
                listing = await receive(alice, "estate_visit_list")
                assert listing["estates"] == [{"username": "bob", "mature_plots": 1}]
                await send(alice, type="estate_enter_visit", request_id="visit-enter-001", owner_username="bob")
                visit = await receive(alice, "estate_visit_state")
                assert visit["owner_username"] == "bob" and len(visit["plots"]) == 12
                await send(alice, type="estate_visit_move", x=300, y=440,
                           direction="right", walking=True)
                await send(alice, type="estate_steal_crop", request_id="visit-steal-001",
                           owner_username="bob", plot_id=9)
                stolen = await receive(alice, "estate_steal_result")
                assert stolen["result"]["crop_id"] == "wheat"
            async with websockets.connect(uri) as bob:
                await send(bob, type="resume", token=bob_token); await receive(bob, "resume_success")
                await send(bob, type="get_estate")
                await receive(bob, "estate_state")
                notices = await receive(bob, "estate_notifications")
                assert notices["notifications"][0]["visitor_username"] == "alice"
    print("PASS estate visits: directory, offline entry, theft and deferred notification")


if __name__ == "__main__":
    asyncio.run(main())
