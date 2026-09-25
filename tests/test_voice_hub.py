#!/usr/bin/env python3
"""语音聊天室（VoiceHub）单元测试：python3 tests/test_voice_hub.py

不依赖真实 LiveKit：VoiceService 照 tests/test_voice.py 的 make_service
stub 掉 mint_token/_kick_room 并记录调用，覆盖：
  - 加入/切换频道的成员变化与 voice_plan 映射（vh-<频道>）；
  - 与游戏房间互斥：join 被拒、recheck 把成员拉出并发 token:null 断开；
  - 临时重命名：权限/长度/限频校验，空置超 TTL 恢复默认名；
  - 分频道文字聊天：定向投递、1 秒限频、历史上限；
  - 断线宽限：到点二次确认，已认证连接仍在则不移除；
  - 主动离开：立即收到无 token 的 voice_update。
"""
import asyncio
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.transport import ConnectionHub  # noqa: E402
from server.voice_hub import VoiceHub  # noqa: E402
from server.voice_livekit import VoiceService  # noqa: E402


class Socket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(json.loads(payload))


class FakeAccounts:
    def display_name(self, username):
        return f"昵称{username}"


class FakeRooms:
    def __init__(self):
        self.in_room = set()

    def user_in_room(self, username):
        return username in self.in_room


class FakeHub(ConnectionHub):
    """真实连接锁之上的记录层：send_to_user 同时记录签发/断开消息。"""

    def __init__(self):
        super().__init__()
        self.sent = {}

    async def send_to_user(self, username, payload):
        self.sent.setdefault(username, []).append(payload)
        await super().send_to_user(username, payload)


def make_voice_service(hub):
    """照 tests/test_voice.py 的 make_service：stub 掉真实 LiveKit 依赖。"""
    conf = {"enabled": True, "url": "ws://lk.test:7880",
            "api_url": "http://lk.test:7880", "api_key": "devkey",
            "api_secret": "devsecret", "token_ttl": 600}
    with patch("server.voice_livekit.voice_config", return_value=conf):
        service = VoiceService(hub)
    service._load_api = lambda: object()

    def fake_mint(username, plan):
        return f"token:{username}:{sorted(plan)}" if plan else None

    service.mint_token = fake_mint
    kicks = []

    async def fake_kick(room_name, username):
        kicks.append((room_name, username))

    service._kick_room = fake_kick
    return service, kicks


class VoiceHubTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = FakeHub()
        self.rooms = FakeRooms()
        self.voice, self.kicks = make_voice_service(self.hub)
        # 小 TTL/宽限，便于在测试里真实等到定时器触发
        self.vh = VoiceHub(self.hub, FakeAccounts(), self.rooms, self.voice,
                           rename_ttl=0.05, disconnect_grace=0.05)
        self.addAsyncCleanup(self.vh.aclose)

    def connect(self, username):
        ws = Socket()
        state = {"send_lock": asyncio.Lock(),
                 "user": {"username": username, "nickname": f"昵称{username}",
                          "avatar": f"{username}.png"}}
        self.hub.clients[ws] = state
        return ws, state

    def anon_state(self, user):
        """同一账号的新请求 state：不共享连接上的限频键。"""
        return {"send_lock": asyncio.Lock(), "user": user}

    async def wait_timer(self):
        await asyncio.sleep(0.15)

    def last_message(self, ws, kind):
        matched = [m for m in ws.messages if m["type"] == kind]
        self.assertTrue(matched, f"没有收到 {kind}：{ws.messages}")
        return matched[-1]

    async def test_join_default_channel_plan_snapshot_and_history(self):
        ws, state = self.connect("alice")
        await self.vh.handle_join(ws, state, {})
        facade = self.vh._facade
        self.assertEqual(facade.id, "voice-hub")
        self.assertEqual(facade.spectators, [])
        self.assertEqual(facade.seating, ["alice"])
        self.assertEqual(facade.voice_plan("alice"), {"vh-default": True})
        self.assertEqual(facade.voice_plan("stranger"), {})
        self.assertEqual(self.hub.sent["alice"][-1]["room"], "vh-default")
        self.assertTrue(self.hub.sent["alice"][-1]["can_publish"])
        issued_before = len(self.hub.sent["alice"])
        # 订阅后的个性化快照与空聊天历史
        await self.vh.handle_state(ws, state, {})
        self.assertEqual(len(self.hub.sent["alice"]), issued_before + 1,
                         "重新订阅必须补发语音 token")
        self.assertEqual(self.hub.sent["alice"][-1]["room"], "vh-default")
        snapshot = self.last_message(ws, "voice_hub_state")
        self.assertEqual(snapshot["my_channel"], "default")
        self.assertTrue(snapshot["voice_enabled"])
        self.assertEqual([channel["id"] for channel in snapshot["channels"]],
                         ["default", "1", "2", "3", "4", "5"])
        default = snapshot["channels"][0]
        self.assertEqual(default["name"], "默认频道")
        self.assertFalse(default["custom"])
        self.assertEqual(default["members"],
                         [{"username": "alice", "nickname": "昵称alice",
                           "avatar": "alice.png"}])
        self.assertEqual(self.last_message(ws, "voice_hub_chat_history"),
                         {"type": "voice_hub_chat_history", "channel": "default",
                          "messages": []})
        self.assertIn(ws, self.vh.subscribers)
        # 未登录守卫：不做任何事
        await self.vh.handle_state(ws, {"user": None}, {})
        await self.vh.handle_unsubscribe(ws, {"user": None}, {})
        self.assertIn(ws, self.vh.subscribers)
        await self.vh.handle_unsubscribe(ws, state, {})
        self.assertNotIn(ws, self.vh.subscribers)

    async def test_switch_channel_moves_member_and_kicks_old_livekit_room(self):
        ws, state = self.connect("alice")
        await self.vh.handle_join(ws, state, {"channel": "default"})
        # 频道 1 里已有历史消息：切换后应把该频道历史补给加入者
        self.vh.channels["1"]["chat"].append(
            {"username": "bob", "nickname": "昵称bob", "text": "频道1见", "time": 123})
        await self.vh.handle_join(ws, state, {"channel": "1"})
        self.assertEqual(self.vh._facade.voice_plan("alice"), {"vh-1": True})
        self.assertEqual(self.vh._facade.seating, ["alice"])
        self.assertEqual(self.vh.channels["default"]["members"], {})
        self.assertEqual(list(self.vh.channels["1"]["members"]), ["alice"])
        self.assertIn(("vh-default", "alice"), self.kicks)
        self.assertEqual(self.hub.sent["alice"][-1]["room"], "vh-1")
        self.assertEqual(self.last_message(ws, "voice_hub_chat_history"),
                         {"type": "voice_hub_chat_history", "channel": "1",
                          "messages": [{"username": "bob", "nickname": "昵称bob",
                                        "text": "频道1见", "time": 123}]})
        # 不存在的频道被拒且不影响现状
        await self.vh.handle_join(ws, state, {"channel": "9"})
        self.assertEqual(ws.messages[-1],
                         {"type": "voice_hub_error", "message": "频道不存在"})
        self.assertEqual(self.vh._facade.voice_plan("alice"), {"vh-1": True})

    async def test_game_room_blocks_join_and_recheck_removes_members(self):
        ws, state = self.connect("alice")
        self.rooms.in_room.add("alice")
        await self.vh.handle_join(ws, state, {})
        self.assertEqual(ws.messages[-1],
                         {"type": "voice_hub_error",
                          "message": "游戏中不能加入语音聊天室"})
        self.assertNotIn("alice", self.hub.sent)
        self.assertEqual(self.vh._facade.seating, [])
        # 已在频道的成员进入游戏房间后，recheck 立即拉出并显式断开
        bob, bob_state = self.connect("bob")
        await self.vh.handle_join(bob, bob_state, {})
        self.rooms.in_room.add("bob")
        await self.vh.recheck()
        self.assertNotIn("bob", self.vh.channels["default"]["members"])
        self.assertEqual(self.vh._facade.seating, [])
        disconnect = self.hub.sent["bob"][-1]
        self.assertEqual(disconnect["type"], "voice_update")
        self.assertIsNone(disconnect["token"])
        self.assertIsNone(disconnect["room"])
        self.assertIn(("vh-default", "bob"), self.kicks)

    async def test_rename_validation_custom_flag_and_cooldown(self):
        ws, state = self.connect("alice")
        await self.vh.handle_rename(ws, state, {"name": "新名字"})
        self.assertEqual(ws.messages[-1]["message"], "加入频道后才能重命名")
        await self.vh.handle_join(ws, state, {})
        await self.vh.handle_rename(ws, state, {"name": "新名字"})
        self.assertEqual(ws.messages[-1]["message"], "默认频道不能重命名")
        await self.vh.handle_join(ws, state, {"channel": "2"})
        # 空 / 13 字被拒；每次用独立 state 避免共享限频键
        for bad in ("   ", "字" * 13):
            await self.vh.handle_rename(ws, self.anon_state(state["user"]),
                                        {"name": bad})
            self.assertEqual(ws.messages[-1]["message"], "频道名需要 1~12 个字")
        self.assertEqual(self.vh.channels["2"]["name"], "频道2")
        # 正常改名：快照 custom=True 且显示新名
        fresh = self.anon_state(state["user"])
        await self.vh.handle_rename(ws, fresh, {"name": "  老友局  "})
        self.assertEqual(self.vh.channels["2"]["name"], "老友局")
        await self.vh.handle_state(ws, state, {})
        channel = next(c for c in self.last_message(ws, "voice_hub_state")["channels"]
                       if c["id"] == "2")
        self.assertEqual(channel["name"], "老友局")
        self.assertTrue(channel["custom"])
        # 5 秒限频：同一请求 state 的第二次改名被拒
        await self.vh.handle_rename(ws, fresh, {"name": "另一个名字"})
        self.assertEqual(ws.messages[-1],
                         {"type": "voice_hub_error", "message": "操作太频繁，请稍后再试"})
        self.assertEqual(self.vh.channels["2"]["name"], "老友局")

    async def test_empty_channel_restores_default_name_only_after_ttl(self):
        ws, state = self.connect("alice")
        await self.vh.handle_join(ws, state, {"channel": "2"})
        await self.vh.handle_rename(ws, self.anon_state(state["user"]),
                                    {"name": "老友局"})
        # 有人时绝不恢复：TTL 到点只是 no-op
        await self.wait_timer()
        self.assertEqual(self.vh.channels["2"]["name"], "老友局")
        self.assertTrue(self.vh.channels["2"]["custom"])
        # 全员离开，空置超过 TTL 后恢复默认名
        await self.vh.handle_leave(ws, state, {})
        self.assertTrue(self.vh.channels["2"]["custom"])
        await self.wait_timer()
        self.assertEqual(self.vh.channels["2"]["name"], "频道2")
        self.assertFalse(self.vh.channels["2"]["custom"])
        self.assertEqual(self.vh._reset_timers, {})

    async def test_chat_channel_scoped_rate_limited_and_history_capped(self):
        alice, alice_state = self.connect("alice")
        bob, bob_state = self.connect("bob")
        carol, carol_state = self.connect("carol")
        await self.vh.handle_join(alice, alice_state, {})
        await self.vh.handle_join(bob, bob_state, {})
        await self.vh.handle_join(carol, carol_state, {"channel": "1"})
        # 1 秒内第二条被限频；消息只投递给同频道成员
        await self.vh.handle_chat(alice, alice_state, {"text": "第一条"})
        await self.vh.handle_chat(alice, alice_state, {"text": "第二条"})
        self.assertEqual(alice.messages[-1],
                         {"type": "voice_hub_error", "message": "发送太快了"})
        message = self.last_message(bob, "voice_hub_chat")
        self.assertEqual(message["channel"], "default")
        self.assertEqual(message["username"], "alice")
        self.assertEqual(message["nickname"], "昵称alice")
        self.assertEqual(message["text"], "第一条")
        self.assertIsInstance(message["time"], int)
        self.assertFalse([m for m in carol.messages if m["type"] == "voice_hub_chat"])
        self.assertEqual(len(self.vh.channels["default"]["chat"]), 1)
        # 历史上限 30 条：绕开限频连发后只留最后 30 条
        user = alice_state["user"]
        for index in range(35):
            await self.vh.handle_chat(alice, self.anon_state(user),
                                      {"text": f"m{index}"})
        chat = self.vh.channels["default"]["chat"]
        self.assertEqual(len(chat), 30)
        self.assertEqual(chat[0]["text"], "m5")
        self.assertEqual(chat[-1]["text"], "m34")
        # 纯空白不记录
        await self.vh.handle_chat(alice, self.anon_state(user), {"text": "   "})
        self.assertEqual(len(self.vh.channels["default"]["chat"]), 30)

    async def test_disconnect_grace_expires_and_reconnect_is_protected(self):
        ws, state = self.connect("alice")
        await self.vh.handle_join(ws, state, {})
        self.hub.clients.pop(ws)                    # 连接关闭
        await self.vh.disconnect(ws, state)
        self.assertIn("alice", self.vh.channels["default"]["members"])
        await self.wait_timer()
        self.assertNotIn("alice", self.vh.channels["default"]["members"])
        self.assertEqual(self.vh._facade.seating, [])
        self.assertEqual(self.vh._expire_timers, {})
        self.assertIn(("vh-default", "alice"), self.kicks)
        # 同账号仍有已认证连接（宽限期内重连）则不移除
        bob, bob_state = self.connect("bob")
        await self.vh.handle_join(bob, bob_state, {})
        self.hub.clients.pop(bob)
        await self.vh.disconnect(bob, bob_state)
        self.connect("bob")                          # 重连，已认证
        await self.wait_timer()
        self.assertIn("bob", self.vh.channels["default"]["members"])
        # 未认证的连接不算「已重连」
        carol, carol_state = self.connect("carol")
        await self.vh.handle_join(carol, carol_state, {})
        self.hub.clients.pop(carol)
        await self.vh.disconnect(carol, carol_state)
        self.hub.clients[Socket()] = {"send_lock": asyncio.Lock(), "user": None}
        await self.wait_timer()
        self.assertNotIn("carol", self.vh.channels["default"]["members"])

    async def test_leave_sends_voice_disconnect_and_updates_members(self):
        ws, state = self.connect("alice")
        await self.vh.handle_join(ws, state, {"channel": "3"})
        await self.vh.handle_leave(ws, state, {})
        self.assertEqual(self.vh.channels["3"]["members"], {})
        disconnect = self.hub.sent["alice"][-1]
        self.assertEqual(disconnect["type"], "voice_update")
        self.assertIsNone(disconnect["token"])
        self.assertIsNone(disconnect["room"])
        self.assertFalse(disconnect["can_publish"])
        self.assertIn(("vh-3", "alice"), self.kicks)


if __name__ == "__main__":
    unittest.main()
