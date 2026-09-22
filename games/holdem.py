"""德州扑克 · 无限注房间。

evaluate5 / best7 / build_side_pots / distribute_pots 是纯函数，
不依赖任何 IO，可直接单元测试；HoldemRoom 负责一手牌的状态机、
计时与视图。网络广播与托管持久化由宿主注入（见 games/base.py）。
"""
import asyncio
import logging
import os
import time
from itertools import combinations

from games import randomness
from games.base import BaseRoom, parse_amount, register_room_type

logger = logging.getLogger("live-chat.holdem")

TURN_TIMEOUT = float(os.environ.get("HOLDEM_TURN_TIMEOUT", "45"))
SETTLE_TIMEOUT = float(os.environ.get("HOLDEM_SETTLE_TIMEOUT", "90"))
CONTINUE_TIMEOUT = float(os.environ.get("HOLDEM_CONTINUE_TIMEOUT", "10"))
BLIND_PRESETS = (1, 2, 5, 10)

HAND_NAMES = ["高牌", "一对", "两对", "三条", "顺子", "同花", "葫芦", "四条", "同花顺"]


def hand_name(score):
    if score[0] == 8 and score[1] == 14:
        return "皇家同花顺"
    return HAND_NAMES[score[0]]


def evaluate5(cards):
    """返回可比较的牌型分值元组，越大越强。cards: [(rank 2-14, suit 0-3), ...]"""
    ranks = sorted((c[0] for c in cards), reverse=True)
    suits = [c[1] for c in cards]
    uniq = sorted(set(ranks), reverse=True)
    straight_high = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight_high = 5
    is_flush = len(set(suits)) == 1
    if is_flush and straight_high:
        return (8, straight_high)
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    pattern = [c for _, c in groups]
    tie = [r for r, _ in groups]
    if pattern == [4, 1]:
        return (7, *tie)
    if pattern == [3, 2]:
        return (6, *tie)
    if is_flush:
        return (5, *ranks)
    if straight_high:
        return (4, straight_high)
    if pattern == [3, 1, 1]:
        return (3, *tie)
    if pattern == [2, 2, 1]:
        return (2, *tie)
    if pattern == [2, 1, 1, 1]:
        return (1, *tie)
    return (0, *ranks)


def best7(cards7):
    return max(evaluate5(list(group)) for group in combinations(cards7, 5))


def build_side_pots(committed, folded):
    """按投入分层构造边池：[{amount, eligible}]，eligible 为未弃牌且投入达层的玩家。"""
    levels = sorted({v for v in committed.values() if v > 0})
    pots = []
    prev = 0.0
    for level in levels:
        amount = 0.0
        eligible = []
        for name, chips in committed.items():
            if chips > prev:
                amount += min(chips, level) - prev
                if chips >= level and name not in folded:
                    eligible.append(name)
        if amount > 0:
            pots.append({"amount": round(amount, 2), "eligible": eligible})
        prev = level
    return pots


def distribute_pots(pots, hands):
    """按牌型把每个池分给符合条件的最大玩家，浮点尾差给第一个赢家。"""
    payouts = {}
    for pot in pots:
        best = max(hands[name] for name in pot["eligible"])
        winners = [name for name in pot["eligible"] if hands[name] == best]
        share = round(pot["amount"] / len(winners), 2)
        paid = 0.0
        for index, name in enumerate(winners):
            amount = share
            if index == len(winners) - 1:
                amount = round(pot["amount"] - paid, 2)
            paid = round(paid + amount, 2)
            payouts[name] = round(payouts.get(name, 0) + amount, 2)
    return payouts


def new_deck():
    deck = [(rank, suit) for rank in range(2, 15) for suit in range(4)]
    randomness.shuffle(deck)
    return deck


@register_room_type("holdem")
class HoldemRoom(BaseRoom):
    """一手牌的状态机：发牌 -> 四条街下注 -> 摊牌/结束 -> 间歇后下一手。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.game = None        # 一手牌的状态字典；None = 未开局
        self.dealer = None
        self.hand_seq = 0
        self.pause_remaining = 0.0
        self.votes = {}         # 对局结束投票：username -> {"choice", "blind"}
        self.ready = {}         # 每手结束的「继续下一手」确认：username -> True
        self.ready_deadline = 0.0
        self.ready_match_reason = None  # 非空表示倒计时结束后进入整局结算
        self.match_no = 1       # 第几局对局（对局结束投票后再来一局时 +1）
        self.match_result = None

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())

    def on_resumed(self):
        g = self.game
        if g and "pending_settlement" in g:
            self.schedule("settlement", 2, self.retry_settlement)
        elif g and g.get("to_act"):
            g["deadline"] = time.time() + (self.pause_remaining or TURN_TIMEOUT)
            self.schedule_turn_timer()
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
        dealer_u = g.get("dealer") if g else None
        for name in self.seating:
            member = self.members[name]
            view["players"].append(
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "stack": member["stack"],
                    "rating": self.player_rating(name),
                    "bet": round(g["street_committed"].get(name, 0), 2) if g else 0,
                    "hand_bet": round(g["committed"].get(name, 0), 2) if g else 0,
                    "folded": bool(g and name in g["folded"]),
                    "allin": bool(g and name in g["allin"]),
                    "in_hand": bool(g and name in g["order"]),
                    "dealer": name == dealer_u,
                }
            )
        if g:
            pot = round(sum(g["committed"].values()), 2)
            if g.get("result"):
                pot = g["result"].get("pot", pot)
            view.update(
                {
                    "hand_no": g["hand_no"],
                    "stage": g["stage"],
                    "board": [{"r": r, "s": s} for r, s in g["board"]],
                    "pot": pot,
                    "current_bet": g["current_bet"],
                    "to_act": g["to_act"],
                    "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                    if g["to_act"]
                    else 0,
                    "dealer": dealer_u,
                    "last_action": g.get("last_action"),
                    "result": g.get("result"),
                }
            )
            if username in g["holes"]:
                view["your_hole"] = [{"r": r, "s": s} for r, s in g["holes"][username]]
                if g["to_act"] == username:
                    view["your_options"] = self.legal_actions(username)
        if self.status == "playing" and not self.in_hand() and self.ready_deadline:
            # 一手刚结束：展示牌型与筹码加减；最后一手确认后进入整局结算。
            view["hand_ready"] = {
                "ready": sorted(name for name, done in self.ready.items() if done),
                "total": len(self.seating),
                "left": round(max(0.0, self.ready_deadline - time.time()), 1),
                "hand_no": g["hand_no"] if g else 0,
                "ends_match": bool(self.ready_match_reason),
            }
        if self.status == "settled" and self.match_result:
            # 对局结束：展示整局盈亏与段位分变化，投票再来一局/解散。
            view["match_result"] = {
                **self.match_result,
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": len(self.seating) >= 2,
            }
        return view

    # ---- 一手牌状态机 ----
    def in_hand(self):
        """是否有一手牌正在进行（摊牌/结算阶段不算）。"""
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def note_leave(self, username):
        """离桌时把进行中的玩家标记为弃牌，返回是否处于一手牌中间。"""
        g = self.game
        mid_hand = bool(
            g and g.get("stage") != "showdown"
            and username in g["order"] and username not in g["folded"]
        )
        if mid_hand:
            g["folded"].add(username)
        return mid_hand

    def hand_statistics(self, names, *, leaving=False, showdown=False):
        """构造结算摘要，不修改进行中的状态；数据库失败后可安全重试。"""
        g = self.game
        if not g or "stats" not in g:
            return None  # 旧手工快照无完整行动数据：沿用积分结算，但不推测统计
        result = {}
        for name in names:
            if name not in g["stats"]:
                continue
            stats = g["stats"][name]
            folded = name in g["folded"] or leaving
            reason = stats["fold_reason"]
            if folded and reason is None:
                reason = "leave" if leaving else "manual"
            result[name] = {
                **stats,
                "big_blind": g["big_blind"],
                "folded": folded,
                "fold_reason": reason,
                "showdown": bool(showdown and not folded and stats["saw_flop"]),
                "settlement_reason": "leave" if leaving else "completed",
            }
        return result

    def settle_leaving_rating(self, username):
        endings = {username: self.members[username]["stack"]}
        return self.settle_ratings(endings, statistics=self.hand_statistics(endings, leaving=True))

    def pending_refunds(self):
        """房间关闭时应退给每人的筹码（含未结手牌中已投入部分）。"""
        refunds = {name: member["stack"] for name, member in self.members.items()}
        g = self.game
        if g:
            for name, amount in g.get("committed", {}).items():
                if name in refunds and amount:
                    refunds[name] = round(refunds[name] + amount, 2)
        return refunds

    def legal_actions(self, username):
        g = self.game
        member = self.members[username]
        to_call = round(g["current_bet"] - g["street_committed"].get(username, 0), 2)
        can_raise = username not in g["acted"]
        raise_max = round(g["street_committed"].get(username, 0) + member["stack"], 2)
        return {
            "fold": True,
            "check": to_call <= 0,
            "call": 0 < to_call <= member["stack"],
            "call_amount": round(min(to_call, member["stack"]), 2),
            "can_raise": can_raise and member["stack"] > 0,
            "raise_min": round(g["current_bet"] + g["min_raise"], 2),
            "raise_max": raise_max,
            "allin": member["stack"] > 0,
            "allin_to": raise_max,
        }

    def schedule_turn_timer(self):
        g = self.game
        if not g or not g.get("to_act"):
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    async def auto_action(self):
        if self.paused or not self.game or not self.game.get("to_act"):
            return
        username = self.game["to_act"]
        if self.game["street_committed"].get(username, 0) >= self.game["current_bet"]:
            action = "check"
        else:
            action = "fold"
        await self.perform_action(username, action, auto=True)

    async def enter_settlement(self, match_reason=None):
        """一手结束后展示 10 秒结果；随后开下一手或进入整局结算。"""
        self.votes = {}
        self.ready = {}
        self.ready_match_reason = match_reason
        self.ready_deadline = time.time() + CONTINUE_TIMEOUT
        self.schedule("continue", CONTINUE_TIMEOUT, self.continue_timeout)
        await self.broadcast_views()

    def can_continue(self):
        """对局继续条件：至少两人，且人人筹码够一个盲注（不足者只能结算）。"""
        return len(self.seating) >= 2 and all(
            member["stack"] >= self.blind for member in self.members.values()
        )

    async def mark_ready(self, username):
        """记录结果页确认；全员确认后开下一手或进入整局结算。"""
        if self.in_hand() or self.status != "playing" or username not in self.members:
            return False
        if self.ready.get(username):
            return False
        self.ready[username] = True
        if all(self.ready.get(name) for name in self.seating):
            await self.finish_hand_ready()
        else:
            await self.broadcast_views()
        return True

    async def continue_timeout(self):
        """10 秒未全员确认：自动开下一手或进入整局结算。"""
        if self.paused or self.in_hand() or self.status != "playing":
            return
        await self.finish_hand_ready()

    async def finish_hand_ready(self):
        reason = self.ready_match_reason
        if reason:
            await self.enter_match_settlement(reason)
        else:
            await self.start_next_hand()

    async def start_next_hand(self):
        self.cancel_timer("continue")
        self.ready = {}
        self.ready_deadline = 0.0
        self.ready_match_reason = None
        await self.start_hand()
        await self.on_rooms_changed()

    async def enter_match_settlement(self, reason):
        """对局结束：进入结算投票（再来一局会先结清并重置筹码）。"""
        self.cancel_timer("continue")
        self.ready = {}
        self.ready_deadline = 0.0
        self.ready_match_reason = None
        self.votes = {}
        self.status = "settled"
        self.match_result = self.build_match_result(reason)
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)
        await self.broadcast_views()
        await self.on_rooms_changed()

    def build_match_result(self, reason):
        """对局结束明细：每人的资产（筹码）、累计买入与盈亏、本局段位分变化。"""
        players = []
        for name in self.seating:
            member = self.members[name]
            paid = self.member_paid(name)
            players.append({
                "username": name,
                "nickname": self.display_name(name),
                "stack": member["stack"],
                "paid": paid,
                "net": round(member["stack"] - paid, 2),
                "rating": self.player_rating(name),
                "rating_delta": self.match_rating_delta.get(name, 0),
            })
        return {
            "reason": reason,
            "match_no": self.match_no,
            "blind": self.blind,
            "buy_in": self.buy_in,
            "hand_no": self.hand_seq,
            "players": players,
        }

    async def settle_timeout(self):
        """投票超时：不强制扣钱，按当前筹码结算并解散房间。"""
        if self.paused or self.in_hand() or self.status != "settled":
            return
        await self.execute_decision("dissolve", None, "投票超时，结算解散")

    async def cast_vote(self, username, choice, blind):
        """对局结束后投票：过半数（或全员已投）即执行，返回执行结果或 None。"""
        if self.in_hand() or self.status != "settled" or username not in self.members:
            return None
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

    async def execute_decision(self, choice, blind, reason=None):
        self.cancel_timer("settle")
        if choice == "next":
            self.blind = blind or self.blind
            self.votes = {}
            self.match_result = None
            self.status = "playing"
            self.match_no += 1
            self.ready_match_reason = None
            self.reset_match_rating()
            if self.on_rebuy_requested:
                await self.on_rebuy_requested()
            if len(self.members_with_chips()) < 2:
                self.status = "settled"
                if self.on_dissolve_requested:
                    await self.on_dissolve_requested(reason or "人数不足，结算解散")
                return
            await self.start_hand()
            await self.on_rooms_changed()
        else:
            if self.on_dissolve_requested:
                await self.on_dissolve_requested(reason or "结算解散")

    async def start(self):
        """房主开局；筹码不足时抛 ValueError（消息可直接展示给玩家）。"""
        if len(self.members_with_chips()) < 2:
            raise ValueError("至少需要两名有筹码的玩家才能开局")
        self.status = "playing"
        self.dealer = None
        self.match_no = 1
        self.match_result = None
        self.votes = {}
        self.ready = {}
        self.ready_deadline = 0.0
        self.ready_match_reason = None
        self.reset_match_rating()
        await self.start_hand()

    def commit_chips(self, username, amount):
        g = self.game
        member = self.members[username]
        amount = round(min(amount, member["stack"]), 2)
        member["stack"] = round(member["stack"] - amount, 2)
        g["committed"][username] = round(g["committed"].get(username, 0) + amount, 2)
        g["street_committed"][username] = round(
            g["street_committed"].get(username, 0) + amount, 2
        )
        if member["stack"] <= 0:
            g["allin"].add(username)
        return amount

    def is_pending(self, g, username):
        return (
            username in g["order"]
            and username not in g["folded"]
            and username not in g["allin"]
            and (
                g["street_committed"].get(username, 0) < g["current_bet"]
                or username not in g["acted"]
            )
        )

    def next_pending_after(self, anchor):
        g = self.game
        order = g["order"]
        start = order.index(anchor) if anchor in order else -1
        for step in range(1, len(order) + 1):
            candidate = order[(start + step) % len(order)]
            if self.is_pending(g, candidate):
                return candidate
        return None

    async def start_hand(self):
        await self.finish_pending_settlement()
        self.cancel_timer("settle")
        eligible = self.members_with_chips()
        if len(eligible) < 2:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            self.ready = {}
            self.ready_deadline = 0.0
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        self.begin_rating_hand(eligible)
        blind = self.blind
        big_blind = round(blind * 2, 2)
        previous_dealer = self.dealer
        if previous_dealer in eligible:
            dealer_u = eligible[(eligible.index(previous_dealer) + 1) % len(eligible)]
        else:
            dealer_u = eligible[0]
        self.dealer = dealer_u
        order = eligible[eligible.index(dealer_u) + 1:] + eligible[: eligible.index(dealer_u) + 1]
        deck = new_deck()
        holes = {name: [deck.pop(), deck.pop()] for name in order}
        self.game = {
            "hand_no": self.hand_seq + 1,
            "stage": "preflop",
            "deck": deck,
            "board": [],
            "holes": holes,
            "order": order,
            "committed": {name: 0.0 for name in order},
            "street_committed": {name: 0.0 for name in order},
            "folded": set(),
            "allin": set(),
            "acted": set(),
            "current_bet": big_blind,
            "min_raise": big_blind,
            "big_blind": big_blind,  # 统计必须使用本手盲注，不能读取后续对局设置
            "stats": {name: {"vpip": False, "pfr": False, "saw_flop": False,
                             "fold_reason": None, "aggressive_actions": 0,
                             "call_actions": 0} for name in order},
            "dealer": dealer_u,
            "to_act": None,
            "deadline": 0,
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["hand_no"]
        g = self.game
        if len(order) == 2:
            small_u, big_u = dealer_u, order[0]
        else:
            small_u, big_u = order[0], order[1]
        self.commit_chips(small_u, blind)
        self.commit_chips(big_u, big_blind)
        first = self.next_pending_after(big_u)
        if first is None:
            await self.advance_street()
            return
        g["to_act"] = first
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused:
            return
        if "pending_settlement" in g:
            if username in self.members:
                await self.finish_pending_settlement()
            return  # 重发操作只重试原结算，不再接受下注或更改弃牌/摊牌口径
        if g.get("to_act") != username:
            return
        raise_to = parse_amount((data or {}).get("raise_to"))
        options = self.legal_actions(username)
        nickname = self.display_name(username)
        previous_bet = g["current_bet"]
        previous_committed = g["street_committed"][username]
        text = ""
        if action == "fold":
            g["folded"].add(username)
            text = "弃牌"
        elif action == "check" and options["check"]:
            g["acted"].add(username)
            text = "看牌"
        elif action == "call" and options["call"]:
            paid = self.commit_chips(username, options["call_amount"])
            g["acted"].add(username)
            text = "全下跟注" if username in g["allin"] else f"跟注 {paid:.2f}"
        elif action == "raise":
            if not options["can_raise"]:
                return
            allin_to = options["allin_to"]
            raise_min = options["raise_min"]
            target = round(min(max(raise_to or raise_min, min(raise_min, allin_to)), allin_to), 2)
            self.commit_chips(username, round(target - g["street_committed"][username], 2))
            if target > g["current_bet"]:
                if target - g["current_bet"] >= g["min_raise"]:
                    g["min_raise"] = round(target - g["current_bet"], 2)
                    g["acted"] = {username}
                else:
                    g["acted"].add(username)
                g["current_bet"] = target
            else:
                g["acted"].add(username)
            text = f"全下 {target:.2f}" if username in g["allin"] else f"加注到 {target:.2f}"
        else:
            return
        # 只统计验证成功的行为。raise 按钮也可能实际是短码全下跟注。
        stats = g["stats"][username]
        committed = g["street_committed"][username]
        if action == "fold" and stats["fold_reason"] is None:
            stats["fold_reason"] = "timeout" if auto else "manual"
        if committed > previous_committed:
            raised = committed > previous_bet
            if g["stage"] == "preflop":
                stats["vpip"] = True
                stats["pfr"] = stats["pfr"] or raised
            elif raised:
                stats["aggressive_actions"] += 1
            else:
                stats["call_actions"] += 1
        g["last_action"] = {
            "username": username,
            "nickname": nickname,
            "text": ("超时自动" if auto else "") + text,
        }
        logger.info("poker %s@%s: %s %s", username, self.id, action, text)
        await self.progress_game()

    async def progress_game(self):
        self.cancel_timer("turn")
        g = self.game
        if not g:
            return
        alive = [name for name in g["order"] if name not in g["folded"]]
        if len(alive) == 1:
            await self.end_hand(reveal=False)
            return
        nxt = self.next_pending_after(g.get("to_act") or g["dealer"])
        if nxt is None:
            if g["stage"] == "river":
                await self.end_hand(reveal=True)
            else:
                await self.advance_street()
            return
        g["to_act"] = nxt
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def advance_street(self):
        g = self.game
        if g["stage"] == "preflop":
            g["board"].extend([g["deck"].pop() for _ in range(3)])
            g["stage"] = "flop"
            for name in g["order"]:
                if name not in g["folded"]:
                    g["stats"][name]["saw_flop"] = True
        elif g["stage"] == "flop":
            g["board"].append(g["deck"].pop())
            g["stage"] = "turn"
        elif g["stage"] == "turn":
            g["board"].append(g["deck"].pop())
            g["stage"] = "river"
        else:
            await self.end_hand(reveal=True)
            return
        g["acted"] = set()
        g["current_bet"] = 0
        g["min_raise"] = round(self.blind * 2, 2)
        for name in g["order"]:
            g["street_committed"][name] = 0.0
        nxt = self.next_pending_after(g["dealer"])
        if nxt is None:
            if g["stage"] == "river":
                await self.end_hand(reveal=True)
            else:
                await self.broadcast_views()
                await self.advance_street()
            return
        g["to_act"] = nxt
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def finish_pending_settlement(self):
        if self.game and "pending_settlement" in self.game:
            await self.end_hand(self.game["pending_settlement"])

    async def retry_settlement(self):
        if self.paused:
            return
        try:
            await self.finish_pending_settlement()
        except Exception:
            # end_hand 已重新安排重试；异常不能成为无人处理的后台任务。
            logger.exception("poker settlement retry failed in room %s", self.id)

    async def end_hand(self, reveal):
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        g = self.game
        reveal = g.setdefault("pending_settlement", reveal)
        g["to_act"] = None
        g["deadline"] = 0
        stakes = dict(g["committed"])
        pot = round(sum(stakes.values()), 2)
        alive = [name for name in g["order"] if name not in g["folded"]]
        hands = {}
        if reveal:
            hands = {name: best7(g["holes"][name] + g["board"]) for name in alive}
        pots = build_side_pots(g["committed"], g["folded"])
        payouts = distribute_pots(pots, hands) if hands else {alive[0]: pot}
        endings = {name: round(member["stack"] + payouts.get(name, 0), 2)
                   for name, member in self.members.items()}
        try:
            ratings = self.settle_ratings(endings, stakes=stakes,
                statistics=self.hand_statistics(endings, showdown=reveal))
        except Exception:
            # 动作已经消费，冻结牌局并自动重试；离桌/重开/解散也须先完成本次结算。
            if not self.paused:
                self.schedule("settlement", 2, self.retry_settlement)
            raise
        self.cancel_timer("settlement")
        g.pop("pending_settlement")
        for name, amount in payouts.items():
            if amount > 0 and name in self.members:
                # 失败重试使用已提交的最终筹码，避免二次发奖。只更新获奖者，
                # 不能用旧的离桌结算覆写同名玩家在本手期间重新买入的筹码。
                final = round(self.members[name]["stack"] + amount, 2)
                self.members[name]["stack"] = ratings.get(name, {}).get("final", final)
        # 段位/统计事务已写入最终托管；不能再做易失败的重复写库，
        # 否则已提交的本手仍停在进行中，重开/解散可能再次退还投入。
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        g["committed"] = {}
        # 结算页要摊开所有人的手牌；牌型名只给没弃牌的人（公共牌不满 5 张时无意义）
        shown = hands
        if len(g["board"]) == 5:
            shown = {name: best7(g["holes"][name] + g["board"]) for name in alive}
        g["result"] = {
            "ratings": ratings,
            "board": [{"r": r, "s": s} for r, s in g["board"]],
            "pot": pot,
            "reveal": [
                {
                    "username": name,
                    "cards": [{"r": r, "s": s} for r, s in g["holes"][name]],
                    "hand_name": hand_name(hands[name]) if name in hands else "",
                }
                for name in alive
            ]
            if reveal
            else [],
            "payouts": payouts,
            "hands": [
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "cards": [{"r": r, "s": s} for r, s in g["holes"][name]],
                    "hand_name": hand_name(shown[name]) if name in shown else "",
                    "folded": name in g["folded"],
                    "committed": round(stakes.get(name, 0), 2),
                    "payout": round(payouts.get(name, 0), 2),
                }
                for name in g["order"]
            ],
        }
        logger.info(
            "poker hand #%d done in room %s, pot %.2f, winners %s",
            g["hand_no"],
            self.id,
            pot,
            {k: v for k, v in payouts.items() if v > 0},
        )
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]}
        )
        await self.broadcast_views()
        await self.on_rooms_changed()
        if self.can_continue():
            await self.enter_settlement()
        else:
            # 最后一手仍完整展示 10 秒；确认/倒计时结束后再转入整局结算。
            reason = ("有玩家筹码不足盲注，本局结束" if len(self.seating) >= 2
                      else "人数不足，本局结束")
            await self.enter_settlement(reason)

    async def restart(self):
        """重新开始：本手已投入的筹码退回各家，随后重新发一手。"""
        await self.finish_pending_settlement()
        g = self.game
        if g:
            for name, amount in g.get("committed", {}).items():
                member = self.members.get(name)
                if member and amount > 0:
                    member["stack"] = round(member["stack"] + amount, 2)
            self.stacks_changed()
        self.game = None
        self.paused = False
        self.votes = {}
        self.cancel_timer("continue")
        self.ready = {}
        self.ready_deadline = 0.0
        self.ready_match_reason = None
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_hand()
