#!/usr/bin/env python3
"""斗地主协议回归：python3 tests/test_doudizhu_protocol.py

走真实 create_app 的房间协议（假 socket，不发网络）：
  - 开房规则清洗、3 人加入与开局校验
  - 叫分定地主、底牌并入、poker_action 出牌/不出
  - 结算广播 hand_result、settle_vote 再来一局
  - 观战视图剥离操作字段
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import create_app  # noqa: E402
from server.schema import init_db  # noqa: E402
import server.database as storage  # noqa: E402


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))

    def last(self, kind):
        return next(message for message in reversed(self.messages)
                    if message["type"] == kind)


def card(rank, suit=0):
    return {"r": rank, "s": suit}


async def run():
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(storage, "DB_FILE", str(Path(tmp) / "doudizhu.db")):
        app = create_app(disconnect_grace=0)
        init_db(app.database)
        with app.database() as conn, conn:
            for name in ("p0", "p1", "p2", "watcher"):
                conn.execute(
                    "INSERT INTO users(username,password_hash,salt,created_at,coins)"
                    " VALUES (?, '', '', 0, 1000)", (name,))
        sockets = {}
        for name in ("p0", "p1", "p2", "watcher"):
            socket = Socket()
            sockets[name] = socket
            app.hub.clients[socket] = {
                "user": {"username": name, "nickname": name, "role": "user"},
                "send_lock": asyncio.Lock(),
            }

        async def handle(username, message_type, **data):
            state = app.hub.clients[sockets[username]]
            await app.room_protocol.handlers()[message_type](
                sockets[username], state, {"type": message_type, **data})

        # 开房：规则原样传给房间类清洗
        await handle("p0", "create_room", game="doudizhu", name="斗地主",
                     buy_in=100, blind=5,
                     rules={"bid_mode": "bid", "bottom_visible": True,
                            "bomb_cap": 4, "spring": True})
        room = app.rooms.find_user_room("p0")
        assert room and room.game_type == "doudizhu", "开房成功"
        assert room.rules == {"bid_mode": "bid", "bottom_visible": True,
                              "bomb_cap": 4, "spring": True}, room.rules
        await handle("p1", "join_room", room_id=room.id)
        await handle("p2", "join_room", room_id=room.id)
        assert len(room.seating) == 3, "三人入座"

        await handle("p0", "start_game")
        g = room.game
        assert g["stage"] == "bid" and all(
            len(h) == 17 for h in g["hands"].values()), "发牌 17 张进入叫分"

        # 叫分：p0 叫 2，p1/p2 不叫 → p0 地主，底牌并入 20 张
        await handle("p0", "poker_action", action="bid", score=2)
        await handle("p1", "poker_action", action="pass")
        await handle("p2", "poker_action", action="pass")
        assert g["landlord"] == "p0" and g["stage"] == "play", "p0 成为地主"
        assert len(g["hands"]["p0"]) == 20, "地主 20 张"
        view = room.view_for("p0")
        assert view["bottom"] == [{"r": c["r"], "s": c["s"]}
                                  for c in g["bottom"]], "明底牌可见"
        assert view["your_options"]["play"] is True, "地主先出"

        # 换成已知牌走完整一手：p0 出 9，两家不要，p0 再出 10 出完 → 地主胜
        g["hands"]["p0"] = [card(9), card(10)]
        g["hands"]["p1"] = [card(4)]
        g["hands"]["p2"] = [card(5)]
        g["bottom"] = []
        for socket in sockets.values():
            socket.messages.clear()
        await handle("p0", "poker_action", action="play", cards=[0])
        assert g["standing"]["label"].startswith("单张"), "出牌成功"
        assert g["to_act"] in ("p1", "p2"), "轮到农民"
        await handle("p1", "poker_action", action="pass")
        await handle("p2", "poker_action", action="pass")
        await handle("p0", "poker_action", action="play", cards=[0])
        assert not room.in_hand(), "对局结束"
        result = sockets["p0"].last("hand_result")
        assert result["landlord"] == "p0" and result["winners"] == ["p0"], "地主胜"
        # 倍率 = 叫分 2 × 春天 2（农民没出过牌）= 4，农民各付 20
        assert result["multiplier"] == 4, f"倍率 {result['multiplier']}"
        assert room.members["p1"]["stack"] == 80, "农民按倍数赔付"

        # 结算投票：再来一局
        for username in ("p0", "p1", "p2"):
            await handle(username, "settle_vote", choice="next", blind=5)
        assert room.in_hand() and room.game["stage"] == "bid", "投票重发"

        # 观战：剥离操作字段
        await handle("p0", "poker_action", action="bid", score=1)
        await handle("p1", "poker_action", action="pass")
        await handle("p2", "poker_action", action="pass")
        await handle("watcher", "join_room", room_id=room.id, spectate=True)
        spec_view = sockets["watcher"].last("game_joined")["room"]
        assert spec_view.get("spectator"), "观战进入"
        assert "your_options" not in spec_view, "观战无操作字段"
        print("doudizhu 协议回归全部通过")


if __name__ == "__main__":
    asyncio.run(run())
