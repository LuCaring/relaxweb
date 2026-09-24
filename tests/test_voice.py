#!/usr/bin/env python3
"""LiveKit 语音适配层单元测试：python3 tests/test_voice.py

只测纯逻辑与签发差分（不起 LiveKit 服务器）：
  - werewolf voice_plan：白天/狼夜/死亡/观战 → LiveKit 房间与发言权
  - VoiceService 差分签发：计划变化换发、未变化不重发、半衰期续签、
    离桌踢出、禁用时空操作
  - 真实 mint_token（livekit-api 已安装）：JWT 可解码且授予对应房间权限
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games.base import create_room  # noqa: E402
from games import randomness  # noqa: E402
from server.voice_livekit import VoiceService, plan_key, voice_config  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def make_room():
    room = create_room("werewolf", room_id=42, name="语音测试", owner="a",
                       buy_in=100, blind=5, rules={})
    for name in ("a", "b", "c", "d", "e", "f"):
        room.add_member(name, 100)
    room.add_spectator("watcher", "a")

    async def noop(*_args, **_kwargs):
        return None

    room.broadcast_views = noop
    room.broadcast_payload = noop
    room.on_rooms_changed = noop
    return room


def run_identity_shuffle(coro_fn):
    old = randomness.shuffle
    randomness.shuffle = lambda items: None
    try:
        return asyncio.run(coro_fn())
    finally:
        randomness.shuffle = old


class FakeHub:
    def __init__(self):
        self.sent = {}

    async def send_to_user(self, username, payload):
        self.sent.setdefault(username, []).append(payload)


def make_service(hub, ttl=600, enabled=True):
    conf = {"enabled": enabled, "url": "ws://lk.test:7880",
            "api_url": "http://lk.test:7880",
            "api_key": "devkey", "api_secret": "devsecret" if enabled else "",
            "token_ttl": ttl}
    with patch("server.voice_livekit.voice_config", return_value=conf):
        service = VoiceService(hub)
    service._load_api = lambda: object()   # 差分测试不需要真实 API

    def fake_mint(username, plan):
        return f"token:{username}:{sorted(plan)}" if plan else None

    service.mint_token = fake_mint
    kicks = []

    async def fake_kick(room_name, username):
        kicks.append((room_name, username))

    service._kick_room = fake_kick
    return service, kicks


# ---- werewolf voice_plan ----
def test_voice_plan():
    async def run():
        room = make_room()
        await room.start()
        room.game["phase"] = "day"           # start 直接进第一夜；从白天开始观察
        day = room.voice_plan("a")
        spectator_day = room.voice_plan("watcher")
        room.game["phase"] = "night"
        room.game["voice_epoch"] += 1
        room.game["alive"] = [n for n in room.game["alive"] if n != "e"]
        room.game["last_night"] = {"e": "kill"}
        dead = room.voice_plan("e")
        night_wolf = room.voice_plan("a")
        night_good = room.voice_plan("c")
        spectator_night = room.voice_plan("watcher")
        room.game["phase"] = "last_words"
        room.game["last_words"]["current"] = "e"
        room.game["voice_epoch"] += 1
        words_speaker = room.voice_plan("e")
        words_listener = room.voice_plan("c")
        return (day, spectator_day, dead, night_wolf, night_good,
                spectator_night, words_speaker, words_listener)

    (day, spectator_day, dead, night_wolf, night_good, spectator_night,
     words_speaker, words_listener) = \
        run_identity_shuffle(run)
    check("白天存活者可进 day 频道发言", day == {"ww42-m1-v1-day": True}, str(day))
    check("夜晚狼人切到 wolf 频道", night_wolf == {"ww42-m1-v2-wolf": True},
          str(night_wolf))
    check("夜晚好人无频道", night_good == {}, str(night_good))
    check("死者无频道", dead == {}, str(dead))
    check("观战者白天只听不说", spectator_day == {"ww42-m1-v1-day": False},
          str(spectator_day))
    check("观战者夜晚无频道", spectator_night == {}, str(spectator_night))
    check("遗言时仅发言者可发布，其他人可听", words_speaker
          == {"ww42-m1-v3-day": True} and words_listener
          == {"ww42-m1-v3-day": False})
    check("plan_key 稳定可比较", plan_key({"ww42-day": True})
          == plan_key({"ww42-day": True}) and plan_key({"a": True})
          != plan_key({"a": False}))


# ---- VoiceService 差分 ----
def test_service_diff():
    async def run():
        hub = FakeHub()
        service, kicks = make_service(hub)
        room = make_room()
        await room.start()
        room.game["phase"] = "day"           # start 直接进第一夜；测试从白天开始
        await service.sync_room(room)
        first = {name: len(msgs) for name, msgs in hub.sent.items()}
        # 计划未变：不重发
        await service.sync_room(room)
        unchanged = {name: len(msgs) for name, msgs in hub.sent.items()}
        day_wolf = hub.sent["a"][-1]
        day_watch = hub.sent["watcher"][-1]
        # 入夜 + e 出局：狼人换发到 wolf，好人收到断开
        room.game["phase"] = "night"
        room.game["voice_epoch"] += 1
        room.game["alive"] = [n for n in room.game["alive"] if n != "e"]
        await service.sync_room(room)
        night = {name: len(msgs) for name, msgs in hub.sent.items()}
        wolf_msg = hub.sent["a"][-1]
        good_msg = hub.sent["c"][-1]
        # 半衰期：把签发时间拨老，未变计划也应续签
        hub.sent.clear()
        state = service.issued[room.id]
        service.issued[room.id] = {n: (k, t - 400) for n, (k, t) in state.items()}
        await service.sync_room(room)
        refreshed = all(len(msgs) >= 1 for msgs in hub.sent.values())
        # 新页面/观战者加入时，未变的计划也可立即补发。
        before_force = len(hub.sent["a"])
        await service.sync_user(room, "a", force=True)
        forced = len(hub.sent["a"]) == before_force + 1
        # 离桌：被移除的人触发踢出
        room.remove_member("f")
        room.game["alive"] = [n for n in room.game["alive"] if n != "f"]
        await service.sync_room(room)
        return (first, unchanged, night, day_wolf, day_watch, wolf_msg,
                good_msg, refreshed, forced, kicks)

    (first, unchanged, night, day_wolf, day_watch, wolf_msg, good_msg,
     refreshed, forced, kicks) = run_identity_shuffle(run)
    check("开局后全员+观战者各签发一次", set(first) == {"a", "b", "c", "d", "e", "f", "watcher"}
          and all(count == 1 for count in first.values()), str(first))
    check("计划未变不重复签发", unchanged == first, str(unchanged))
    check("白天狼人也只在 day 频道", day_wolf["room"] == "ww42-m1-v1-day"
          and day_wolf["can_publish"] is True, str(day_wolf))
    check("白天观战者只听不说", day_watch["room"] == "ww42-m1-v1-day"
          and day_watch["can_publish"] is False, str(day_watch))
    check("入夜后计划变化触发换发", all(night[name] > first[name]
                                    for name in ("a", "c", "watcher")), str(night))
    check("狼人夜晚收到 wolf 房间 token",
          wolf_msg["room"] == "ww42-m1-v2-wolf" and wolf_msg["can_publish"] is True
          and wolf_msg["url"] == "ws://lk.test:7880" and wolf_msg["token"], str(wolf_msg))
    check("好人夜晚收到断开指令", good_msg["room"] is None
          and good_msg["token"] is None, str(good_msg))
    check("半衰期后未变计划也续签", refreshed)
    check("重进页面可强制补发授权", forced)
    check("阶段切换踢出旧频道", ("ww42-m1-v1-day", "a") in kicks
          and ("ww42-m1-v1-day", "f") in kicks, str(kicks))


def test_service_disabled():
    async def run():
        hub = FakeHub()
        service, _kicks = make_service(hub, enabled=False)
        room = make_room()
        await room.start()
        await service.sync_room(room)
        real_mint = VoiceService.mint_token(service, "a", {"ww42-day": True})
        return not hub.sent and service.enabled is False and real_mint is None

    ok = asyncio.run(run())
    check("禁用时整体空操作", ok)


def test_mint_token_real():
    """真实签发（livekit-api 已随 uv 安装）：JWT 可解码且授予对应房间。"""
    conf = dict(voice_config(), enabled=True, api_key="devkey",
                api_secret="devsecret", url="ws://127.0.0.1:7880", token_ttl=600)
    with patch("server.voice_livekit.voice_config", return_value=conf):
        service = VoiceService(FakeHub())
    if not service.enabled:
        check("真实签发依赖 livekit-api（未安装则跳过）", True, "livekit-api missing")
        return
    token = service.mint_token("alice", {"ww42-wolf": True})
    import jwt
    claims = jwt.decode(token, "devsecret", algorithms=["HS256"])
    grants = claims.get("video", {})
    check("JWT 携带身份与房间授权", claims.get("sub") == "alice"
          and grants.get("room") == "ww42-wolf" and grants.get("roomJoin") is True
          and grants.get("canPublish") is True and grants.get("canSubscribe") is True,
          json.dumps(grants))
    empty = service.mint_token("alice", {})
    check("空计划签发为 None（客户端断开）", empty is None)


def test_admin_cleanup():
    """使用真实 SDK 请求类型验证管理接口由实例调用。"""
    async def run():
        conf = dict(voice_config(), enabled=True, api_key="devkey",
                    api_secret="devsecret", api_url="http://127.0.0.1:7880")
        with patch("server.voice_livekit.voice_config", return_value=conf):
            service = VoiceService(FakeHub())
        removed, deleted = [], []

        class FakeRoomService:
            async def remove_participant(self, request):
                removed.append((request.room, request.identity))

            async def delete_room(self, request):
                deleted.append(request.room)

        class FakeClient:
            room = FakeRoomService()

            async def aclose(self):
                pass

        service._client = FakeClient()
        await service._kick_room("ww42-m1-v1-day", "alice")
        service.rooms_seen[42] = {"ww42-m1-v1-day", "ww42-m1-v2-wolf"}
        await service.close_room(42)
        await service.aclose()
        return removed, deleted

    removed, deleted = asyncio.run(run())
    check("LiveKit 管理接口可踢人与删房", removed
          == [("ww42-m1-v1-day", "alice")]
          and set(deleted) == {"ww42-m1-v1-day", "ww42-m1-v2-wolf"})


def main():
    test_voice_plan()
    test_service_diff()
    test_service_disabled()
    test_mint_token_real()
    test_admin_cleanup()
    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败用例：", "、".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
