"""游戏房间基础设施：房间基类、计时器与注册表。

并发模型与宿主 chat_server 相同：整个服务跑在单一 asyncio 事件循环里，
房间的可变状态只在事件循环内的同步代码段中修改；延迟逻辑一律通过
BaseRoom.schedule（内部为 loop.call_later + ensure_future）回到事件循环，
因此不需要加锁。

新增游戏的方法：
    1. 在 games/ 下新建模块，实现 BaseRoom 子类；
    2. 实现抽象/钩子方法（start/perform_action/view_for/summary 等）；
    3. 在类上用 @register_room_type("游戏id") 注册；
    4. 宿主 chat_server 的开房参数校验即自动放行该游戏。

引擎不要直接依赖传输层与数据库：广播、展示名、托管持久化都由宿主
通过 attach（见 chat_server.attach_host）注入的属性完成，因此玩法
代码可以脱离网络独立测试。
"""
import asyncio
import math
import uuid
from collections import deque

ROOM_TYPES = {}


def parse_amount(value):
    """宽松解析正金额；非法/非正值返回 None。引擎与宿主共用。"""
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def register_room_type(game_type):
    """类装饰器：把房间实现注册进游戏厅。"""
    def wrapper(cls):
        ROOM_TYPES[game_type] = cls
        cls.game_type = game_type
        return cls
    return wrapper


def create_room(game_type, **kwargs):
    """按游戏类型实例化房间；未注册的类型抛 ValueError。"""
    try:
        cls = ROOM_TYPES[game_type]
    except KeyError:
        raise ValueError(f"未注册的游戏类型：{game_type}") from None
    return cls(**kwargs)


class BaseRoom:
    """游戏房间基类：与具体玩法无关的成员、聊天、计时器与生命周期。

    由宿主注入的属性（默认值保证引擎可脱离宿主独立实例化测试）：
        broadcast_payload(payload)   同一消息发给房间所有成员连接
        broadcast_views()            给每个成员连接发送其私有视图
        on_rooms_changed()           房间列表发生变化时通知宿主广播
        display_name(username)       用户名 -> 展示昵称
        player_avatar(username)      读取公开头像 URL
        set_escrow(username, amount) 筹码变动后同步托管（宿主写数据库）
        player_rating(username)     读取公开段位
        record_ratings(id, starts, endings, stakes=None, statistics=None)
                                    同步提交积分、筹码与可选德扑下注流水/统计
        on_dissolve_requested(reason)    async，房间解散（含结算解散、流局）
        on_rebuy_requested()             async，对局结束「再来一局」：结清本轮后按买入额
                                         重置筹码，余额不足者由宿主负责离桌
    """

    max_seats = 9

    def __init__(self, room_id, name, owner, buy_in, blind, rules=None):
        self.id = room_id
        self.name = name
        self.owner = owner
        self.buy_in = buy_in
        self.blind = blind
        self.rules = rules if isinstance(rules, dict) else {}
        self.status = "waiting"          # waiting | playing | settled（对局结束待投票）
        self.paused = False
        self.seating = []                # 座位顺序即加入顺序
        self.members = {}                # username -> {"stack": float, "paid": float}
        self.spectators = {}             # username -> 被观看的成员；观战者不买入、不占座
        self.chat = deque(maxlen=30)     # 房间聊天，内存态，随房间销毁
        self.timers = {}                 # key -> asyncio.TimerHandle

        self.broadcast_payload = None
        self.broadcast_views = None
        self.on_rooms_changed = None
        self.on_dissolve_requested = None   # async (reason) -> None，宿主注入
        self.on_rebuy_requested = None      # async () -> None，宿主注入
        self.display_name = lambda username: username
        self.player_avatar = lambda username: ""
        self.set_escrow = lambda username, amount: None
        self.player_rating = lambda username: None
        self.record_ratings = lambda hand_id, starts, endings, stakes=None, statistics=None: {}
        self.rating_hand_id = None
        self.rating_starts = {}
        self.rating_results = {}
        self.match_rating_delta = {}     # 本局累计段位分变化，随每手结算累加

    def begin_rating_hand(self, names):
        """在扣盲注/发牌之前固定本金；重开只替换未完成的快照。"""
        self.rating_hand_id = uuid.uuid4().hex
        self.rating_starts = {name: self.members[name]["stack"] for name in names}
        self.rating_results = {}

    def settle_ratings(self, endings=None, stakes=None, statistics=None):
        """积分、可选统计与下注流水同事务提交；stakes 仅在德扑整手结束时提供。"""
        if endings is None:
            endings = {name: member["stack"] for name, member in self.members.items()}
        pending = {name: amount for name, amount in endings.items()
                   if name in self.rating_starts and name not in self.rating_results}
        if pending or stakes is not None:
            extra = {}
            if stakes is not None:
                extra["stakes"] = stakes
            if statistics is not None:
                extra["statistics"] = statistics
            fresh = self.record_ratings(self.rating_hand_id, self.rating_starts, pending, **extra)
            self.rating_results.update(fresh)
            for name, info in fresh.items():
                self.match_rating_delta[name] = (
                    self.match_rating_delta.get(name, 0) + int(info.get("delta", 0)))
        return dict(self.rating_results)

    def settle_leaving_rating(self, username):
        """先结算再移除成员；玩法可覆盖以附加离桌统计摘要。"""
        return self.settle_ratings({username: self.members[username]["stack"]})

    async def finish_pending_settlement(self):
        """离桌/解散前完成已结束但尚未持久化的手牌；默认无待重试结算。"""

    def reset_match_rating(self):
        """开始新的一局对局时清零累计段位分变化。"""
        self.match_rating_delta = {}

    # ---- 成员与筹码 ----
    def has_member(self, username):
        return username in self.members

    def add_member(self, username, buy_in, buyin_ref=None):
        self.seating.append(username)
        self.members[username] = {
            "stack": buy_in,
            "paid": buy_in,
            "buyin_ref": buyin_ref or f"room:{self.id}:{username}",
        }

    def remove_member(self, username):
        member = self.members.pop(username, None)
        self.seating = [name for name in self.seating if name != username]
        return member

    def member_paid(self, username):
        """该成员在本房间的累计买入（含多次重新买入），用于结算净额。"""
        member = self.members.get(username)
        if not member:
            return self.buy_in
        return round(member.get("paid", self.buy_in), 2)

    def add_chips(self, username, amount):
        """买入/重新买入：宿主扣完金币后调用，筹码与累计买入同步增加。"""
        member = self.members.get(username)
        if not member or amount <= 0:
            return 0.0
        member["stack"] = round(member["stack"] + amount, 2)
        member["paid"] = round(member.get("paid", 0.0) + amount, 2)
        return amount

    def members_with_chips(self):
        return [name for name in self.seating if self.members[name]["stack"] > 0]

    # ---- 观战 ----
    def has_spectator(self, username):
        return username in self.spectators

    def add_spectator(self, username, watched=None):
        """记录观战者及其观看目标；目标缺省取首位成员。"""
        if watched not in self.members:
            watched = self.seating[0] if self.seating else self.owner
        self.spectators[username] = watched
        return watched

    def remove_spectator(self, username):
        return self.spectators.pop(username, None)

    def spectator_view(self, username):
        """观战者视图：被看玩家的第一视角，但剥离一切可操作字段。"""
        watched = self.spectators.get(username)
        if watched not in self.members:
            watched = self.add_spectator(username)
        view = self.view_for(watched)
        view.pop("your_options", None)
        view["spectator"] = True
        view["watching"] = watched
        return view

    def stacks_changed(self):
        """筹码发生变动后调用；宿主注入的 set_escrow 负责持久化托管。"""
        for name in self.members:
            self.set_escrow(name, self.members[name]["stack"])

    # ---- 计时器 ----
    def schedule(self, key, delay, coro_fn, *args):
        """安排 key 计时器：delay 秒后在事件循环里执行 coro_fn(*args)。"""
        self.cancel_timer(key)
        loop = asyncio.get_running_loop()

        def fire():
            self.timers.pop(key, None)
            asyncio.ensure_future(coro_fn(*args))

        self.timers[key] = loop.call_later(max(0.05, delay), fire)

    def cancel_timer(self, key):
        handle = self.timers.pop(key, None)
        if handle:
            handle.cancel()

    def cancel_timers(self):
        for key in tuple(self.timers):
            self.cancel_timer(key)

    # ---- 暂停/恢复（模板方法）----
    def pause(self):
        if self.paused:
            return
        self.paused = True
        self.cancel_timers()
        self.on_paused()

    def resume(self):
        if not self.paused:
            return
        self.paused = False
        self.on_resumed()

    def on_paused(self):
        """子类钩子：保存进行中的状态（如剩余出牌时间）。"""

    def on_resumed(self):
        """子类钩子：重新安排被暂停打断的计时器。"""

    def close(self):
        """房间关闭：清掉所有计时器；聊天随对象一起被回收。"""
        self.cancel_timers()

    # ---- 房间聊天钩子（默认全公开；子类可按阶段定向，如狼人杀）----
    def chat_route(self, username, message):
        """返回 None 公开广播；"" 拦截该消息；子频道名则定向给 chat_audience。"""
        return None

    def chat_audience(self, channel):
        """定向频道的接收者列表；None 表示回退为全员广播。"""
        return None

    def visible_chat(self, username):
        """历史消息按查看者过滤（含定向频道的消息）。"""
        return list(self.chat)

    # ---- 视图 ----
    def summary(self):
        """房间列表条目（公开信息）。"""
        return {
            "id": self.id,
            "name": self.name,
            "game": self.game_type,
            "owner": self.owner,
            "owner_name": self.display_name(self.owner),
            "buy_in": self.buy_in,
            "blind": self.blind,
            "status": self.status,
            "hand_no": 0,
            "players": [
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "stack": self.members[name]["stack"],
                    "rating": self.player_rating(name),
                }
                for name in self.seating
            ],
        }

    def view_for(self, username):
        """成员私有视图；子类应叠加玩法状态。"""
        return {
            "type": "game_update",
            "room_id": self.id,
            "name": self.name,
            "game_type": self.game_type,
            "paused": self.paused,
            "owner": self.owner,
            "owner_name": self.display_name(self.owner),
            "buy_in": self.buy_in,
            "blind": self.blind,
            "status": self.status,
            "players": self.summary()["players"],
        }
