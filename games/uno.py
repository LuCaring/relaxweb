"""UNO · 经典出牌房间。

build_deck / matches / card_label 是纯函数，不依赖任何 IO，可直接单元
测试；UnoRoom 负责一局牌的状态机、计时与视图。网络广播与托管持久化
由宿主注入（见 games/base.py）。

金币规则：blind 是「每张剩牌的赔付单位」，一手结束赢家按各家剩牌数
收注（封顶为对方当前筹码）；没有底池，筹码只在结算时移动。
一手打完进入与德州相同的结算投票：过半数选「再来一局」或「解散」。
"""
import asyncio
import logging
import os
import time

from games import randomness
from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.uno")

TURN_TIMEOUT = float(os.environ.get("UNO_TURN_TIMEOUT", "30"))
UNO_WINDOW = 2.0  # 漏喊 UNO 的保护期；到期只开放质疑，不自动罚牌。
SETTLE_TIMEOUT = float(os.environ.get("UNO_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)

COLORS = ("r", "y", "g", "b")
COLOR_NAMES = {"r": "红", "y": "黄", "g": "绿", "b": "蓝"}
VALUE_NAMES = {"skip": "禁止", "rev": "反转", "d2": "+2", "wild": "换色", "wd4": "+4"}


def build_deck():
    """标准 108 张：每色 0×1、1-9×2、禁止/反转/+2 各×2，换色与+4 各 4 张。"""
    deck = []
    for color in COLORS:
        deck.append({"c": color, "v": "0"})
        for value in ("1", "2", "3", "4", "5", "6", "7", "8", "9",
                      "skip", "rev", "d2"):
            deck.extend({"c": color, "v": value} for _ in range(2))
    deck.extend({"c": "w", "v": "wild"} for _ in range(4))
    deck.extend({"c": "w", "v": "wd4"} for _ in range(4))
    randomness.shuffle(deck)
    return deck


def is_number(card):
    return card["v"].isdigit()


def matches(color, value, card):
    """出牌合法性：万能牌任意时候可出，同色或同数即可出。"""
    if card["c"] == "w" or card["v"] == "wild" or card["v"] == "wd4":
        return True
    return card["c"] == color or card["v"] == value


def hand_playable(hand, color, value):
    return any(matches(color, value, card) for card in hand)


def card_label(card):
    color = COLOR_NAMES.get(card["c"], "万能")
    if is_number(card):
        return f"{color}{card['v']}"
    if card["c"] == "w":
        return VALUE_NAMES[card["v"]]
    return f"{color}{VALUE_NAMES[card['v']]}"


@register_room_type("uno")
class UnoRoom(BaseRoom):
    """一局牌的状态机：发牌 -> 轮流出牌 -> 出完结算 -> 投票再来/解散。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.game = None        # 一局牌的状态字典；None = 未开局/结算投票中
        self.starter = None     # 上一局的先手，用于轮转
        self.hand_seq = 0
        self.pause_remaining = 0.0
        self.uno_pause_remaining = {}
        self.votes = {}         # 结算投票：username -> {"choice", "blind"}

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())
        if g:
            self.uno_pause_remaining = {name: max(0, deadline - time.time()) for name, deadline in g["uno_deadlines"].items()}

    def on_resumed(self):
        g = self.game
        if g and g.get("to_act"):
            g["deadline"] = time.time() + (self.pause_remaining or TURN_TIMEOUT)
            self.schedule_turn_timer()
        if g and g.get("uno_pending"):
            g["uno_deadlines"] = {name: time.time() + self.uno_pause_remaining.get(name, 0) for name in g["uno_pending"]}
            self.schedule_uno_timer()
        self.uno_pause_remaining = {}
        self.pause_remaining = 0.0

    # ---- 视图 ----
    def summary(self):
        data = super().summary()
        data["hand_no"] = self.game["hand_no"] if self.game else 0
        return data

    def view_for(self, username):
        g = self.game if isinstance(self.game, dict) else None
        view = {
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
            "players": [],
        }
        for name in self.seating:
            member = self.members[name]
            in_hand = bool(g and name in g["order"])
            view["players"].append(
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "stack": member["stack"],
                    "rating": self.player_rating(name),
                    "in_hand": in_hand,
                    "cards": len(g["hands"][name]) if in_hand else 0,
                    "uno": bool(g and name in g["uno_pending"]),
                    "uno_left": self.uno_left(name) if g and name in g["uno_pending"] else 0,
                }
            )
        if g:
            view.update(
                {
                    "hand_no": g["hand_no"],
                    "direction": g["direction"],
                    "active": {"c": g["color"], "v": g["value"], "card": g["discard"][-1]},
                    "deck_left": len(g["deck"]),
                    "to_act": g["to_act"],
                    "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                    if g["to_act"]
                    else 0,
                    "last_action": g.get("last_action"),
                    "action_event": g.get("action_event"),
                    "result": g.get("result"),
                }
            )
            if username in g["hands"]:
                view["your_hand"] = list(g["hands"][username])
                if g["drawn_state"] == username:
                    view["your_drawn"] = g["drawn_index"]
                # 补喊与质疑不受当前回合限制；服务器再次校验保护期。
                view["your_options"] = {
                    "draw": not self.paused and g["to_act"] == username and g["drawn_state"] != username,
                    "pass": not self.paused and g["drawn_state"] == username,
                    "uno": not self.paused and username in g["uno_pending"],
                    "challenge": [name for name in g["order"] if name != username
                                  and name in g["uno_pending"] and self.uno_left(name) <= 0]
                                 if not self.paused and self.in_hand() else [],
                }
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    # ---- 一局牌状态机 ----
    def in_hand(self):
        """是否有一局牌正在进行（结算展示/投票阶段不算）。"""
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def note_leave(self, username):
        """离桌时把该玩家移出本局，返回是否处于一局牌中间。"""
        g = self.game
        mid_hand = bool(g and username in g["order"])
        if not mid_hand:
            return False
        index = g["order"].index(username)
        g["order"].pop(index)
        g["hands"].pop(username, None)
        self.clear_uno(username)
        if g["drawn_state"] == username:
            g["drawn_state"] = None
            g["drawn_index"] = None
        if g["order"] and index < g["idx"]:
            g["idx"] -= 1
        g["idx"] %= max(1, len(g["order"]))
        return True

    def pending_refunds(self):
        """房间关闭时应退给每人的筹码（UNO 没有进行中的投入）。"""
        return {name: member["stack"] for name, member in self.members.items()}

    def schedule_turn_timer(self):
        g = self.game
        if not g or not g.get("to_act"):
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    def draw_cards(self, username, count):
        """给玩家摸 n 张；牌堆见底时把弃牌堆洗回。返回实际摸到的牌。"""
        g = self.game
        got = []
        for _ in range(count):
            if not g["deck"] and len(g["discard"]) > 1:
                top = g["discard"].pop()
                randomness.shuffle(g["discard"])
                g["deck"] = g["discard"]
                g["discard"] = [top]
            if not g["deck"]:
                break
            card = g["deck"].pop()
            g["hands"][username].append(card)
            got.append(card)
            if len(g["hands"][username]) != 1:
                self.clear_uno(username)
        return got

    def peek_next(self, step):
        g = self.game
        index = (g["idx"] + step * g["direction"]) % len(g["order"])
        return g["order"][index]

    async def auto_action(self):
        g = self.game
        if self.paused or not g or not g.get("to_act"):
            return
        if g["to_act"] not in g["order"]:
            await self.advance(1)
            return
        if g["drawn_state"] == g["to_act"]:
            await self.perform_action(g["to_act"], "pass", auto=True)
        else:
            await self.perform_action(g["to_act"], "draw", auto=True)

    async def advance(self, step):
        """轮转到下家：step 决定跨几人（跳过/罚摸时为 2）。"""
        g = self.game
        self.cancel_timer("turn")
        g["drawn_state"] = None
        g["drawn_index"] = None
        g["idx"] = (g["idx"] + step * g["direction"]) % len(g["order"])
        g["to_act"] = g["order"][g["idx"]]
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or g.get("stage") == "showdown":
            return
        data = data or {}
        if username not in g["order"]:
            return
        if action == "challenge_uno":
            await self.challenge_uno(username, data.get("target"))
            return
        if action == "uno":
            await self.call_uno(username)
            return
        if g.get("to_act") != username or username not in g["order"]:
            return
        if action == "play":
            await self.play_card(username, data, auto)
        elif action == "draw":
            await self.draw_turn(username, auto)
        elif action == "pass":
            await self.pass_turn(username, auto)

    async def call_uno(self, username):
        g = self.game
        if not g or username not in g["uno_pending"]:
            return
        self.clear_uno(username)
        self.record_event("uno", username)
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": "喊出 UNO！",
        }
        logger.info("uno %s@%s: call", username, self.id)
        await self.broadcast_views()

    async def play_card(self, username, data, auto):
        g = self.game
        hand = g["hands"][username]
        try:
            index = int(data.get("card"))
        except (TypeError, ValueError):
            return
        if not 0 <= index < len(hand):
            return
        if g["drawn_state"] == username and index != g["drawn_index"]:
            return
        card = hand[index]
        if not matches(g["color"], g["value"], card):
            return
        nickname = self.display_name(username)
        hand.pop(index)
        g["discard"].append(card)
        if card["c"] == "w":
            color = str(data.get("color") or "r")
            color = color if color in COLORS else "r"
            g["color"] = color
        else:
            g["color"] = card["c"]
        g["value"] = card["v"]
        text = f"出 {card_label(card)}"
        step = 1
        victim = None
        drawn = []
        if card["v"] == "skip":
            victim = self.peek_next(1)
            step = 2
            text += "，下家被跳过"
        elif card["v"] == "rev":
            g["direction"] *= -1
            step = 2 if len(g["order"]) == 2 else 1
            text += "，方向反转"
        elif card["v"] == "d2":
            victim = self.peek_next(1)
            drawn = self.draw_cards(victim, 2)
            step = 2
            text += f"，{self.display_name(victim)} 摸 {len(drawn)} 张"
        elif card["v"] == "wd4":
            victim = self.peek_next(1)
            drawn = self.draw_cards(victim, 4)
            step = 2
            text += f"，{self.display_name(victim)} 摸 {len(drawn)} 张并跳过，改 {COLOR_NAMES[g['color']]}"
        if card["c"] == "w" and card["v"] != "wd4":
            text += f"，改 {COLOR_NAMES[g['color']]}"
        self.record_event("play", username, card=dict(card), target=victim, count=len(drawn), color=g["color"], direction=g["direction"])
        if len(hand) == 1:
            g["uno_pending"].add(username)
            g["uno_deadlines"][username] = time.time() + UNO_WINDOW
            self.schedule_uno_timer()
            text += "（剩 1 张）"
        g["last_action"] = {
            "username": username,
            "nickname": nickname,
            "text": ("超时自动" if auto else "") + text,
        }
        if not hand:
            await self.end_hand(username, auto)
            return
        logger.info("uno %s@%s: play %s", username, self.id, card_label(card))
        await self.advance(step)

    async def draw_turn(self, username, auto):
        g = self.game
        if g["drawn_state"] == username:
            return
        drawn = self.draw_cards(username, 1)
        self.record_event("draw", username, target=username, count=len(drawn))
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + "摸牌",
        }
        logger.info("uno %s@%s: draw %d", username, self.id, len(drawn))
        card = drawn[0] if drawn else None
        if card and matches(g["color"], g["value"], card):
            g["drawn_state"] = username
            g["drawn_index"] = len(g["hands"][username]) - 1
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
        else:
            await self.advance(1)

    async def pass_turn(self, username, auto):
        g = self.game
        if g["drawn_state"] != username:
            return
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + "留下摸的牌",
        }
        logger.info("uno %s@%s: pass", username, self.id)
        await self.advance(1)

    def record_event(self, kind, username, **details):
        """只广播公开动作，不暴露摸到的牌；序号用于前端重绘去重。"""
        g = self.game
        g["event_seq"] += 1
        g["action_event"] = {"id": g["event_seq"], "kind": kind, "username": username, **details}

    def uno_left(self, username):
        if self.paused:
            return self.uno_pause_remaining.get(username, 0)
        return max(0, self.game["uno_deadlines"].get(username, 0) - time.time())

    def clear_uno(self, username):
        self.game["uno_pending"].discard(username)
        self.game["uno_deadlines"].pop(username, None)
        self.uno_pause_remaining.pop(username, None)
        self.schedule_uno_timer()

    def schedule_uno_timer(self):
        self.cancel_timer("uno")
        if self.paused or not self.in_hand():
            return
        remaining = [self.uno_left(name) for name in self.game["uno_pending"]]
        future = [left for left in remaining if left > 0]
        if future:
            self.schedule("uno", min(future), self.uno_timeout)

    async def uno_timeout(self):
        # 保护期结束只广播可质疑状态，不替玩家自动质疑。
        if self.paused or not self.in_hand():
            return
        self.schedule_uno_timer()
        await self.broadcast_views()

    async def challenge_uno(self, username, target):
        g = self.game
        valid = (isinstance(target, str) and username != target
                 and target in g["uno_pending"] and self.uno_left(target) <= 0
                 and len(g["hands"].get(target, [])) == 1)
        if valid:
            # 在第一个 await 前消耗资格，补喊/重复质疑按服务端处理顺序裁决。
            self.clear_uno(target)
            drawn = self.draw_cards(target, 2)
            self.record_event("challenge", username, target=target, count=len(drawn))
            g["last_action"] = {
                "username": username, "nickname": self.display_name(username),
                "text": f"质疑 {self.display_name(target)} 漏喊 UNO，罚摸 {len(drawn)} 张",
            }
        # 过早/重复/已补喊：返回最新状态，客户端解除等待，不重复罚牌。
        await self.broadcast_views()

    # ---- 结束与结算投票（与德州扑克一致的宿主协议）----
    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        """全员还有筹码且人数足时才能再来一局。"""
        return len(self.seating) >= 2 and all(
            member["stack"] > 0 for member in self.members.values()
        )

    async def settle_timeout(self):
        if self.paused or self.in_hand() or self.status != "playing":
            return
        await self.execute_decision("next", self.blind)

    async def cast_vote(self, username, choice, blind):
        """记录一票；过半数（或全员已投）即执行，返回执行结果或 None。"""
        if self.in_hand() or self.status != "playing":
            return None
        if choice == "next" and not self.can_continue(blind):
            raise ValueError("有人筹码不足下一局门槛，只能结算并解散房间")
        self.votes[username] = {
            "choice": choice,
            "blind": blind if choice == "next" else None,
        }
        live = {name: vote for name, vote in self.votes.items() if name in self.members}
        total = len(self.seating)
        next_votes = sum(1 for v in live.values() if v["choice"] == "next")
        dissolve_votes = sum(1 for v in live.values() if v["choice"] == "dissolve")
        executed = None
        if next_votes > total / 2:
            executed = "next"
        elif dissolve_votes > total / 2:
            executed = "dissolve"
        elif all(name in live for name in self.seating):
            executed = "next" if next_votes >= dissolve_votes else "dissolve"
        if executed == "next":
            preferred = [
                v["blind"] for v in live.values()
                if v["choice"] == "next" and v["blind"] in BLIND_PRESETS
            ]
            preferred.sort(key=preferred.count, reverse=True)
            await self.execute_decision("next", preferred[0] if preferred else self.blind)
        elif executed == "dissolve":
            await self.execute_decision("dissolve", None)
        return executed

    async def execute_decision(self, choice, blind):
        self.cancel_timer("settle")
        if choice == "next":
            self.blind = blind or self.blind
            self.votes = {}
            await self.start_hand()
            await self.on_rooms_changed()
        else:
            if self.on_dissolve_requested:
                await self.on_dissolve_requested("结算解散")

    async def start(self):
        """房主开局；人数不足时抛 ValueError（消息可直接展示给玩家）。"""
        if len(self.members_with_chips()) < 2:
            raise ValueError("至少需要两名有筹码的玩家才能开局")
        self.status = "playing"
        self.starter = None
        await self.start_hand()

    async def start_hand(self):
        self.cancel_timers()
        self.uno_pause_remaining = {}
        eligible = self.members_with_chips()
        if len(eligible) < 2:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        self.begin_rating_hand(eligible)
        deck = build_deck()
        hands = {name: [deck.pop() for _ in range(7)] for name in eligible}
        # 翻一张数字牌做起始牌（万能/功能牌沉底）
        for i in range(len(deck) - 1, -1, -1):
            if is_number(deck[i]):
                deck[i], deck[-1] = deck[-1], deck[i]
                break
        first = deck.pop()
        if self.starter in eligible:
            starter_u = eligible[(eligible.index(self.starter) + 1) % len(eligible)]
        else:
            starter_u = eligible[0]
        self.starter = starter_u
        order = eligible[eligible.index(starter_u):] + eligible[: eligible.index(starter_u)]
        self.game = {
            "hand_no": self.hand_seq + 1,
            "stage": "play",
            "deck": deck,
            "discard": [first],
            "hands": hands,
            "order": order,
            "color": first["c"],
            "value": first["v"],
            "direction": 1,
            "idx": 0,
            "to_act": starter_u,
            "deadline": time.time() + TURN_TIMEOUT,
            "drawn_state": None,
            "drawn_index": None,
            "uno_pending": set(),
            "uno_deadlines": {},
            "event_seq": 0,
            "action_event": None,
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["hand_no"]
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def progress_game(self):
        """宿主在成员离开后调用：有人离场后收尾或推进。"""
        g = self.game
        if not g or g.get("stage") == "showdown":
            return
        if len(g["order"]) <= 1:
            await self.end_hand(g["order"][0] if g["order"] else None, False)
            return
        if g["to_act"] not in g["order"]:
            await self.advance(1)
        else:
            await self.broadcast_views()

    async def end_hand(self, winner, reveal=True):
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        self.cancel_timer("uno")
        g = self.game
        penalties = {}
        payouts = {}
        total = 0.0
        endings = {name: member["stack"] for name, member in self.members.items()}
        for name in g["order"]:
            if name == winner:
                continue
            count = len(g["hands"][name])
            pay = round(min(self.members[name]["stack"], self.blind * count), 2)
            penalties[name] = count
            if pay > 0:
                payouts[name] = pay
                total = round(total + pay, 2)
                endings[name] = round(endings[name] - pay, 2)
        if winner and winner in self.members:
            endings[winner] = round(endings[winner] + total, 2)
        ratings = self.settle_ratings(endings)
        for name, amount in endings.items():
            self.members[name]["stack"] = amount
        self.stacks_changed()
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        g["drawn_state"] = None
        g["uno_pending"] = set()
        g["uno_deadlines"] = {}
        self.uno_pause_remaining = {}
        g["result"] = {
            "ratings": ratings,
            "hand_no": g["hand_no"],
            "winner": winner,
            "winner_name": self.display_name(winner) if winner else "",
            "payouts": payouts,
            "penalties": penalties,
            "cards": {name: list(g["hands"][name]) for name in g["order"]},
        }
        logger.info(
            "uno hand #%d done in room %s, winner %s, collected %.2f",
            g["hand_no"],
            self.id,
            winner,
            total,
        )
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]}
        )
        await self.broadcast_views()
        await self.on_rooms_changed()
        self.enter_settlement()

    async def restart(self):
        """重新开始：UNO 没有进行中的投入，直接重新发牌。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_hand()
