#!/usr/bin/env python3
"""竞猜封盘回归：临时库、随机本地端口，不依赖手动联调工具或实际用户数据。

覆盖：旧表结构迁移、开盘设置封盘时间（默认不封盘）、到点自动封盘、
发起者立即封盘、封盘后拒绝新投注、重启后恢复封盘状态。
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets
from server.app import create_app
from server.schema import init_db

from server.betting import bet_public_state
import server.database as storage

server = create_app()


async def send(ws, **data):
    await ws.send(json.dumps(data))


async def next_message(ws, timeout=5):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def receive(ws, kind):
    async def read():
        while True:
            message = await next_message(ws)
            if message["type"] == kind:
                return message
    return await asyncio.wait_for(read(), 5)


async def receive_error(ws):
    async def read():
        while True:
            message = await next_message(ws)
            if message["type"].endswith("_error"):
                return message
    return await asyncio.wait_for(read(), 5)


async def wait_closed_update(ws):
    async def read():
        while True:
            message = await next_message(ws)
            if message["type"] == "bet_update" and message["bet"]:
                if message["bet"]["closed_at"]:
                    return message["bet"]
    return await asyncio.wait_for(read(), 5)


def create_user(name, coins=1000.0):
    with server.database() as conn, conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,salt,created_at,coins) "
            "VALUES (?, '', '', 0, ?)",
            (name, coins),
        )
    return server.accounts.create_session(name)


async def connect_and_resume(host, token):
    ws = await websockets.connect(host)
    await send(ws, type="resume", token=token)
    await receive(ws, "resume_success")
    return ws


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(storage, "DB_FILE", str(Path(tmp) / "bet.db")):
            init_db(server.database)
            # 旧库迁移：缺列的 bets 表补上 close_delay / closed_at
            old = Path(tmp) / "old.db"
            conn = sqlite3.connect(old)
            conn.execute(
                "CREATE TABLE bets (id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, "
                "options TEXT, creator TEXT, status TEXT DEFAULT 'open', correct_index INTEGER, "
                "created_at INTEGER, settled_at INTEGER)"
            )
            conn.commit()
            conn.close()
            with patch.object(storage, "DB_FILE", str(old)):
                init_db(server.database)
            cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(bets)")}
            assert {"close_delay", "closed_at"} <= cols, cols

            alice = create_user("alice")
            bob = create_user("bob")
            async with websockets.serve(server.handler, "127.0.0.1", 0) as host:
                uri = f"ws://127.0.0.1:{host.sockets[0].getsockname()[1]}"
                async with websockets.connect(uri) as a, websockets.connect(uri) as b:
                    await send(a, type="resume", token=alice)
                    await receive(a, "resume_success")
                    await send(b, type="resume", token=bob)
                    await receive(b, "resume_success")

                    # 默认不封盘
                    await send(a, type="create_bet", question="能吃到火锅吗",
                               options=["能", "不能"], close_minutes=0)
                    await receive(a, "bet_created")
                    state = (await receive(b, "bet_update"))["bet"]
                    assert state["close_delay"] == 0 and state["closes_at"] is None
                    assert state["closed_at"] is None

                    await send(b, type="place_bet", option_index=0, amount=50)
                    placed = await receive(b, "bet_placed")
                    assert placed["amount"] == 50
                    await receive(b, "bet_update")  # place_bet 的广播

                    # 设置 60 分钟封盘：closes_at = created_at + 3600
                    await asyncio.sleep(2.1)  # create_bet 限频 2s
                    server.betting.active_bet = None
                    await send(a, type="create_bet", question="今晚下雨吗",
                               options=["下", "不下"], close_minutes=60)
                    await receive(a, "bet_created")
                    state = (await receive(b, "bet_update"))["bet"]
                    assert state["close_delay"] == 60
                    assert state["closes_at"] == state["created_at"] + 3600
                    await send(b, type="place_bet", option_index=1, amount=20)
                    await receive(b, "bet_placed")
                    await receive(b, "bet_update")  # place_bet 的广播

                    # 时间越过封盘点：即使 closed_at 还没落库，投注也被拒
                    server.betting.active_bet["created_at"] -= 3601
                    await asyncio.sleep(1.1)  # place_bet 限频 1s
                    await send(b, type="place_bet", option_index=0, amount=20)
                    error = await receive_error(b)
                    assert "封盘" in error["message"], error

                    # 定时任务到点自动封盘（watcher 每秒巡检）
                    watcher = asyncio.create_task(server.betting.bet_close_watcher())
                    try:
                        closed = await wait_closed_update(b)
                        assert closed["closed_at"] and closed["pot"] == 20
                        with server.database() as conn:
                            row = conn.execute(
                                "SELECT closed_at, status FROM bets WHERE id = ?",
                                (closed["id"],),
                            ).fetchone()
                        assert row[0] == closed["closed_at"] and row[1] == "open"
                    finally:
                        watcher.cancel()

                    # 发起者手动「立即封盘」；非发起者不行
                    await asyncio.sleep(2.1)  # 上一个 create_bet 的限频冷却已过，此处稳一下
                    server.betting.active_bet = None
                    await send(a, type="create_bet", question="下一局谁赢",
                               options=["红方", "蓝方"], close_minutes=1)
                    await receive(a, "bet_created")
                    await receive(b, "bet_update")
                    await send(b, type="close_bet")
                    error = await receive_error(b)
                    assert "只有发起者" in error["message"], error
                    await asyncio.sleep(2.1)  # close_bet 限频 2s
                    await send(a, type="close_bet")
                    closed = await wait_closed_update(b)
                    await send(b, type="place_bet", option_index=0, amount=20)
                    error = await receive_error(b)
                    assert "封盘" in error["message"], error

                    # 重启恢复：load_open_bet 带回封盘字段
                    server.betting.active_bet = server.betting.load_open_bet()
                    assert server.betting.active_bet["closed_at"] == closed["closed_at"]
                    assert server.betting.active_bet["close_delay"] == 1
                    public = bet_public_state(server.betting.active_bet)
                    assert public["closed_at"] == closed["closed_at"]
                    server.betting.active_bet = None
    print("PASS bet close: migration, default open, timed close, manual close, join rejected, resume")


if __name__ == "__main__":
    asyncio.run(main())
