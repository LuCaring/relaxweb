"""斗地主 · 经典三人类房间。

build_deck / enumerate_shapes / resolve_combo / beats / find_moves 是纯
函数，不依赖任何 IO，可直接单元测试；DoudizhuRoom 负责叫分、一手牌的
状态机、计时与视图。网络广播与托管持久化由宿主注入（见 games/base.py）。

规则要点（经典三人斗地主的线上版）：
  - 一副牌 54 张，3 人各 17 张，留 3 张底牌；地主拿底牌、共 20 张、先出；
  - 叫地主默认叫分竞叫：按座次叫 1/2/3 分或不过，叫 3 直接定地主，
    全不过自动重新发牌；可选随机指定地主（不叫分，底分 ×1）；
  - 牌型：单张/对子/三张/三带一/三带二/顺子(≥5)/连对(≥3 对)/
    飞机(≥2 连三)/飞机带单/飞机带对/四带二/四带两对/炸弹/王炸；
    2 与王不可入顺、连对、飞机的牌身；
  - 倍数 = 叫分 × 2^炸弹数（王炸也算，可封顶）× 春天 ×2（可关）；
    农民趁地主只出一手就获胜为反春，同样 ×2；
  - 金币：地主对两家各 底注×倍数，赢家通吃对应份额，赔付不超过剩余筹码。
"""
import asyncio
import logging
import os
import time
from collections import Counter

from games import randomness
from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.doudizhu")

TURN_TIMEOUT = float(os.environ.get("DOUDIZHU_TURN_TIMEOUT", "45"))
SETTLE_TIMEOUT = float(os.environ.get("DOUDIZHU_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)
RULE_BOMB_CAPS = (0, 4, 8, 16, 999)   # 0=炸弹不翻倍，999=不封顶
BID_SCORES = (1, 2, 3)
BID_MODES = ("bid", "random")

SUIT_CHARS = "♠♥♦♣"
RANK_CHARS = {11: "J", 12: "Q", 13: "K", 14: "A", 15: "2", 16: "小王", 17: "大王"}
COMBO_NAMES = {
    "single": "单张", "pair": "对子", "triple": "三张", "triple_one": "三带一",
    "triple_pair": "三带二", "straight": "顺子", "pairs_seq": "连对",
    "plane": "飞机", "plane_single": "飞机带单", "plane_pair": "飞机带对",
    "four_two": "四带二", "four_two_pairs": "四带两对",
    "bomb": "炸弹", "rocket": "王炸",
}
ACE = 14          # 顺子/连对/飞机牌身的最大点数（2 与王不可入列）
DEUCE = 15
LOW = 3
MIN_STRAIGHT = 5
MIN_PAIRS_SEQ = 3
MIN_PLANE = 2


def build_deck():
    """一副牌 54 张：3~2 各花色 ×4，小王、大王各 1。"""
    deck = [{"r": rank, "s": suit}
            for rank in range(LOW, DEUCE + 1) for suit in range(4)]
    deck.append({"r": 16, "s": 4})
    deck.append({"r": 17, "s": 4})
    randomness.shuffle(deck)
    return deck


def rank_char(rank):
    return RANK_CHARS.get(rank, str(rank))


def card_label(card):
    if card["s"] == 4:
        return RANK_CHARS[card["r"]]
    return f"{SUIT_CHARS[card['s']]}{rank_char(card['r'])}"


def combo_label(shape):
    name = COMBO_NAMES[shape["type"]]
    if shape["type"] == "rocket":
        return name
    if shape["type"] == "bomb":
        return f"炸弹 {rank_char(shape['main'])}"
    if shape["type"] in ("plane", "plane_single", "plane_pair"):
        return f"{name} {rank_char(shape['main'])}"
    if shape["type"] in ("straight", "pairs_seq"):
        return f"{name} {rank_char(shape['main'])}"
    return f"{name} {rank_char(shape['main'])}"


def shape_sort_key(shape):
    """从小到大：王炸 > 炸弹 > 普通牌型按点数。"""
    return (shape["tier"], shape["main"], shape["len"])


def _windows(counts, length, need):
    """点数 3~A 内所有连窗：窗内每个点数都要有 need 张。"""
    tops = []
    for top in range(LOW + length - 1, ACE + 1):
        if all(counts.get(rank, 0) >= need
               for rank in range(top - length + 1, top + 1)):
            tops.append(top)
    return tops


def _kicker_combos(counts, banned, need, single_only=False):
    """从 banned 之外的牌里凑 need 张的所有组合，返回 [{rank: 张数}]。

    single_only 用于「带两单」允许凑成一对的变体；同一 rank 最多用到
    手里的张数（王只有一张）。池子按点数升序，保证与前端枚举一致。
    """
    pool = sorted(rank for rank, count in counts.items()
                  if rank not in banned and count > 0)
    results = []

    def walk(index, left, chosen):
        if left == 0:
            results.append(dict(chosen))
            return
        if index >= len(pool):
            return
        rank = pool[index]
        limit = min(counts[rank], left)
        if single_only:
            limit = min(limit, need)
        for take in range(limit, -1, -1):
            if take:
                chosen[rank] = take
            walk(index + 1, left - take, chosen)
            chosen.pop(rank, None)

    walk(0, need, {})
    return results


def enumerate_shapes(counts, total):
    """枚举一组牌在「张数恰好 = total」时的全部合法牌型。

    counts: 按点数计数（王是独立点数，各最多 1 张）。返回 shape 列表，
    picks 记录各点数需要的张数，realize 时据此分配具体牌。
    """
    shapes = []

    def add(kind, tier, main, picks):
        shapes.append({"type": kind, "tier": tier, "main": main,
                       "len": total, "picks": picks})

    counts = {rank: count for rank, count in counts.items() if count > 0}

    # ---- 炸弹 / 王炸 ----
    if total == 4:
        for rank, count in counts.items():
            if count == 4:
                add("bomb", 1, rank, {rank: 4})
    if total == 2 and counts.get(16) and counts.get(17):
        add("rocket", 2, 17, {16: 1, 17: 1})

    # ---- 单张/对子/三张 ----
    if total == 1:
        for rank in counts:
            add("single", 0, rank, {rank: 1})
    if total == 2:
        for rank, count in counts.items():
            if count >= 2:
                add("pair", 0, rank, {rank: 2})
    if total == 3:
        for rank, count in counts.items():
            if count >= 3:
                add("triple", 0, rank, {rank: 3})

    # ---- 三带一 / 三带二 ----
    if total == 4:
        for rank in sorted(counts):
            if counts[rank] >= 3:
                for kickers in _kicker_combos(counts, {rank}, 1):
                    picks = {rank: 3, **kickers}
                    add("triple_one", 0, rank, picks)
    if total == 5:
        for rank in sorted(counts):
            if counts[rank] >= 3:
                for other in sorted(counts):
                    if other != rank and counts[other] >= 2:
                        add("triple_pair", 0, rank, {rank: 3, other: 2})

    # ---- 顺子 / 连对：2 与王不可入列 ----
    if MIN_STRAIGHT <= total <= 12:
        for top in _windows(counts, total, 1):
            add("straight", 0, top,
                {rank: 1 for rank in range(top - total + 1, top + 1)})
    if total >= 2 * MIN_PAIRS_SEQ and total % 2 == 0 and total <= 24:
        pairs = total // 2
        for top in _windows(counts, pairs, 2):
            add("pairs_seq", 0, top,
                {rank: 2 for rank in range(top - pairs + 1, top + 1)})

    # ---- 飞机（≥2 连三张）：牌身不含 2 与王 ----
    if total >= 3 * MIN_PLANE and total % 3 == 0:
        triples = total // 3
        for top in _windows(counts, triples, 3):
            add("plane", 0, top,
                {rank: 3 for rank in range(top - triples + 1, top + 1)})
    if total >= 4 * MIN_PLANE and total % 4 == 0:
        triples = total // 4
        if MIN_PLANE <= triples <= 5:
            for top in _windows(counts, triples, 3):
                body = range(top - triples + 1, top + 1)
                for kickers in _kicker_combos(counts, set(body), triples):
                    add("plane_single", 0, top, {**{r: 3 for r in body}, **kickers})
    if total >= 5 * MIN_PLANE and total % 5 == 0:
        triples = total // 5
        if MIN_PLANE <= triples <= 4:
            for top in _windows(counts, triples, 3):
                body = range(top - triples + 1, top + 1)
                for rank in sorted(counts):
                    if rank not in body and counts[rank] >= 2:
                        for other in sorted(counts):
                            if other != rank and other not in body \
                                    and counts[other] >= 2:
                                add("plane_pair", 0, top,
                                    {**{r: 3 for r in body}, rank: 2, other: 2})

    # ---- 四带二 / 四带两对 ----
    if total == 6:
        for rank in sorted(counts):
            if counts[rank] >= 4:
                for kickers in _kicker_combos(counts, {rank}, 2, single_only=True):
                    add("four_two", 0, rank, {rank: 4, **kickers})
    if total == 8:
        for rank in sorted(counts):
            if counts[rank] >= 4:
                pairs = [other for other in sorted(counts)
                         if other != rank and counts[other] >= 2]
                for first in range(len(pairs)):
                    for second in range(first + 1, len(pairs)):
                        add("four_two_pairs", 0, rank,
                            {rank: 4, pairs[first]: 2, pairs[second]: 2})
    return shapes


def _realize(shape, pool):
    """给 shape 分配具体牌，按点数从大到小排列；张数不足返回 None。"""
    picked = []
    for rank, need in shape["picks"].items():
        cards = pool.get(rank, [])
        if len(cards) < need:
            return None
        picked.extend(cards[:need])
    if len(picked) != shape["len"]:
        return None
    picked.sort(key=lambda c: (c["r"], c["s"]), reverse=True)
    return {"type": shape["type"], "tier": shape["tier"], "main": shape["main"],
            "len": shape["len"], "label": combo_label(shape), "cards": picked}


def resolve_combo(cards):
    """解析所选牌的最强合法牌型；不构成牌型返回 None。"""
    counts = Counter(card["r"] for card in cards)
    shapes = enumerate_shapes(counts, len(cards))
    if not shapes:
        return None
    pool = {}
    for card in cards:
        pool.setdefault(card["r"], []).append(card)
    best = max(shapes, key=shape_sort_key)
    return _realize(best, pool)


def beats(candidate, standing):
    """candidate 能否压过 standing。"""
    if standing["type"] == "rocket":
        return False
    if candidate["type"] == "rocket":
        return True
    if candidate["tier"] and standing["tier"]:
        return candidate["main"] > standing["main"]
    if candidate["tier"]:
        return True
    if standing["tier"]:
        return False
    return (candidate["type"] == standing["type"]
            and candidate["len"] == standing["len"]
            and candidate["main"] > standing["main"])


def find_moves(hand, standing):
    """整手牌的全部合法出法（standing 为 None 表示自由出牌）。

    返回 [combo]，从小到大排序，第一个即「最小出法」。
    """
    counts = Counter(card["r"] for card in hand)
    pool = {}
    for card in hand:
        pool.setdefault(card["r"], []).append(card)
    moves = []
    seen = set()
    for count in range(1, len(hand) + 1):
        for shape in enumerate_shapes(counts, count):
            if standing is not None and not beats(shape, standing):
                continue
            key = (shape["type"], shape["main"], shape["len"])
            if key in seen:
                continue
            combo = _realize(shape, pool)
            if combo is None:
                continue
            seen.add(key)
            moves.append(combo)
    moves.sort(key=shape_sort_key)
    return moves


def indices_of(hand, cards):
    """把 find_moves/resolve 给出的牌换回手牌下标；等值牌可互换。"""
    used = set()
    indices = []
    for card in cards:
        for index, own in enumerate(hand):
            if index not in used and own == card:
                used.add(index)
                indices.append(index)
                break
        else:
            return None
    return indices


@register_room_type("doudizhu")
class DoudizhuRoom(BaseRoom):
    """一局的状态机：发牌 -> 叫分定地主 -> 轮流出牌 -> 定胜负结算 -> 投票再来。"""

    max_seats = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.game = None
        self.starter = None        # 上一局地主，下一局先叫分
        self.bid_round = 0         # 连续流局的次数：首叫玩家轮流让牌
        self.hand_seq = 0
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}
        try:
            bomb_cap = int(rules.get("bomb_cap", 16))
        except (TypeError, ValueError):
            bomb_cap = 16
        if bomb_cap not in RULE_BOMB_CAPS:
            bomb_cap = 16
        bid_mode = rules.get("bid_mode")
        if bid_mode not in BID_MODES:
            bid_mode = "bid"
        return {
            "bid_mode": bid_mode,
            "bottom_visible": bool(rules.get("bottom_visible", False)),
            "bomb_cap": bomb_cap,
            "spring": bool(rules.get("spring", True)),
        }

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())

    def on_resumed(self):
        g = self.game
        if g and g.get("to_act"):
            g["deadline"] = time.time() + (self.pause_remaining or TURN_TIMEOUT)
            self.schedule_turn_timer()
        self.pause_remaining = 0.0

    # ---- 倍数 ----
    def bomb_multiplier(self):
        g = self.game
        cap = self.rules.get("bomb_cap")
        if not g or not g.get("bombs") or not cap:
            return 1
        return min(2 ** g["bombs"], cap)

    def current_multiplier(self):
        g = self.game
        if not g:
            return 1
        multiplier = g.get("bid_score", 1) * self.bomb_multiplier()
        if self.rules.get("spring", True) and g.get("spring"):
            multiplier *= 2
        return multiplier

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
            "rules": dict(self.rules),
            "players": [],
        }
        for name in self.seating:
            member = self.members[name]
            in_hand = bool(g and name in g.get("hands", {}))
            view["players"].append(
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "avatar": self.player_avatar(name),
                    "stack": member["stack"],
                    "rating": self.player_rating(name),
                    "in_hand": in_hand,
                    "cards": len(g["hands"].get(name, [])) if in_hand else 0,
                    "landlord": bool(g and g.get("landlord") == name),
                    "passed": bool(g and name in g.get("passed", ())),
                }
            )
        if g:
            standing = g.get("standing")
            bottom_shown = (self.rules.get("bottom_visible")
                            or g["stage"] != "bid")
            view.update(
                {
                    "hand_no": g["hand_no"],
                    "stage": g["stage"],
                    "to_act": g["to_act"],
                    "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                    if g["to_act"] else 0,
                    "free_lead": g["free_lead"],
                    "landlord": g.get("landlord"),
                    "bid_score": g.get("bid_score", 1),
                    "bid_highest": g.get("bid_highest", 0),
                    "bid_highest_by": g.get("bid_highest_by"),
                    "bombs": g["bombs"],
                    "multiplier": self.current_multiplier(),
                    "bottom": [{"r": c["r"], "s": c["s"]}
                               for c in g.get("bottom", [])] if bottom_shown else [],
                    "standing": None if not standing else {
                        "by": g["standing_by"],
                        "type": standing["type"],
                        "main": standing["main"],
                        "len": standing["len"],
                        "label": standing["label"],
                        "cards": [{"r": c["r"], "s": c["s"]}
                                  for c in standing["cards"]],
                    },
                    "table_plays": {
                        name: {
                            "type": play["type"],
                            "label": play["label"],
                            "cards": [{"r": c["r"], "s": c["s"]}
                                      for c in play["cards"]],
                        }
                        for name, play in g["table_plays"].items()
                    },
                    "passed": sorted(g["passed"]),
                    "last_action": g.get("last_action"),
                    "result": g.get("result"),
                }
            )
            if username in g.get("hands", {}):
                view["your_hand"] = [
                    {"r": c["r"], "s": c["s"]} for c in g["hands"].get(username, [])
                ]
                if g["stage"] == "bid":
                    scores = []
                    if g["to_act"] == username and not self.paused:
                        scores = [s for s in BID_SCORES if s > g["bid_highest"]]
                    view["your_options"] = {"bid": scores, "play": False,
                                            "pass": bool(scores)}
                else:
                    view["your_options"] = {
                        "bid": [],
                        "play": not self.paused and g["to_act"] == username,
                        "pass": not self.paused and g["to_act"] == username
                        and not g["free_lead"],
                    }
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    # ---- 一局状态机 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def note_leave(self, username):
        """斗地主是固定 3 人对局，有人中途离桌则本局作废。"""
        g = self.game
        mid_hand = self.in_hand() and username in g.get("hands", {})
        if mid_hand:
            g["broken"] = True
        return mid_hand

    def pending_refunds(self):
        return {name: member["stack"] for name, member in self.members.items()}

    def schedule_turn_timer(self):
        g = self.game
        if not g or not g.get("to_act"):
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    async def auto_action(self):
        g = self.game
        if self.paused or not g or not g.get("to_act") or not self.in_hand():
            return
        username = g["to_act"]
        if username not in g.get("hands", {}):
            await self.progress_game()
            return
        if g["stage"] == "bid":
            await self.perform_action(username, "pass", {}, auto=True)
            return
        if g["free_lead"]:
            moves = find_moves(g["hands"][username], None)
            if moves:
                indices = indices_of(g["hands"][username], moves[0]["cards"])
                if indices is not None:
                    await self.perform_action(username, "play",
                                              {"cards": indices}, auto=True)
                    return
        await self.perform_action(username, "pass", {}, auto=True)

    async def start(self):
        """房主开局；斗地主必须正好 3 名有筹码玩家。"""
        eligible = self.members_with_chips()
        if len(eligible) != 3:
            raise ValueError("斗地主需要正好 3 名有筹码的玩家才能开局")
        self.status = "playing"
        self.starter = None
        self.bid_round = 0
        await self.start_hand()

    async def start_hand(self):
        self.cancel_timers()
        eligible = self.members_with_chips()
        if len(eligible) != 3:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        self.begin_rating_hand(eligible)
        deck = build_deck()
        hands = {name: [deck.pop() for _ in range(17)] for name in eligible}
        bottom = [deck.pop() for _ in range(3)]
        first = self.starter if self.starter in eligible else eligible[0]
        first = eligible[(eligible.index(first) + self.bid_round) % len(eligible)]
        self.game = {
            "hand_no": self.hand_seq + 1,
            "stage": "bid" if self.rules["bid_mode"] == "bid" else "play",
            "hands": hands,
            "bottom": bottom,
            "ring": list(eligible),
            "landlord": None,
            "bid_score": 1,
            "bid_highest": 0,
            "bid_highest_by": None,
            "bid_acted": set(),
            "to_act": first,
            "free_lead": True,
            "standing": None,
            "standing_by": None,
            "table_plays": {},
            "passed": set(),
            "bombs": 0,
            "spring": False,
            "plays": {},
            "deadline": time.time() + TURN_TIMEOUT,
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["hand_no"]
        if self.rules["bid_mode"] == "random":
            landlord = randomness.choice(eligible)
            await self.assign_landlord(landlord, 1)
            return
        self.game["last_action"] = {
            "username": first,
            "nickname": self.display_name(first),
            "text": "先叫分，可叫 1/2/3 分或不过",
        }
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def assign_landlord(self, username, score):
        g = self.game
        g["landlord"] = username
        g["bid_score"] = score
        g["hands"][username].extend(g["bottom"])
        g["stage"] = "play"
        g["to_act"] = username
        g["free_lead"] = True
        g["passed"] = set()
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.bid_round = 0
        self.starter = username
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": f"以 {score} 分成为地主，拿底牌先出牌"
            if score > 1 else "成为地主，拿底牌先出牌",
        }
        logger.info("doudizhu %s@%s: landlord=%s score=%s",
                    username, self.id, username, score)
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or not self.in_hand():
            return
        if g.get("to_act") != username or username not in g.get("hands", {}):
            return
        if g["stage"] == "bid":
            if action == "bid":
                await self.place_bid(username, data or {}, auto)
            elif action == "pass":
                await self.pass_bid(username, auto)
            return
        if action == "pass":
            await self.pass_turn(username, auto)
        elif action == "play":
            await self.play_cards(username, data or {}, auto)

    async def place_bid(self, username, data, auto):
        g = self.game
        g["bid_acted"].add(username)
        try:
            score = int(data.get("score"))
        except (TypeError, ValueError):
            return
        if score not in BID_SCORES or score <= g["bid_highest"]:
            return
        g["bid_highest"] = score
        g["bid_highest_by"] = username
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + f"叫 {score} 分",
        }
        logger.info("doudizhu %s@%s: bid %s", username, self.id, score)
        if score == 3:
            await self.assign_landlord(username, score)
            return
        await self.advance_bid(username)

    async def pass_bid(self, username, auto):
        g = self.game
        g["bid_acted"].add(username)
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + "不叫",
        }
        logger.info("doudizhu %s@%s: no bid", username, self.id)
        acted_all = all(name in g["bid_acted"]
                        for name in g["ring"] if name in self.members)
        if g["bid_highest_by"] is None and acted_all:
            self.bid_round += 1
            await self.start_hand()      # 全不过：重新发牌，首叫轮流
            return
        await self.advance_bid(username)

    async def advance_bid(self, acted):
        g = self.game
        index = g["ring"].index(acted) if acted in g["ring"] else -1
        for step in range(1, len(g["ring"]) + 1):
            name = g["ring"][(index + step) % len(g["ring"])]
            if name not in self.members or name in g["bid_acted"]:
                continue
            g["to_act"] = name
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
            return
        # 全部表态完毕：最高叫分者当地主
        if g["bid_highest_by"]:
            await self.assign_landlord(g["bid_highest_by"], g["bid_highest"])

    def take_cards(self, username, indices):
        """按手牌下标取牌；任何下标非法或重复时返回 None。"""
        hand = self.game["hands"][username]
        if not isinstance(indices, list):
            return None
        picked = []
        seen = set()
        for index in indices:
            if type(index) is not int:
                return None
            if not 0 <= index < len(hand) or index in seen:
                return None
            seen.add(index)
            picked.append(hand[index])
        return picked

    async def play_cards(self, username, data, auto):
        g = self.game
        cards = self.take_cards(username, data.get("cards") or [])
        if not cards:
            return
        standing = None if g["free_lead"] else g.get("standing")
        combo = resolve_combo(cards)
        if combo is None:
            return
        if not g["free_lead"]:
            if standing is None or not beats(combo, standing):
                return
        for card in cards:
            g["hands"][username].remove(card)
        if combo["tier"]:
            g["bombs"] += 1
        g["plays"][username] = g["plays"].get(username, 0) + 1
        g["standing"] = combo
        g["standing_by"] = username
        g["table_plays"][username] = combo
        g["free_lead"] = False
        g["passed"] = set()
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + f"出 {combo['label']}",
        }
        logger.info("doudizhu %s@%s: %s %s",
                    username, self.id, combo["type"], combo["label"])
        if not g["hands"][username]:
            await self.end_hand(False, winner=username)
            return
        await self.advance_turn(username)

    async def pass_turn(self, username, auto):
        g = self.game
        if g["free_lead"]:
            return
        g["passed"].add(username)
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + "不出",
        }
        logger.info("doudizhu %s@%s: pass", username, self.id)
        others = [name for name in g["ring"]
                  if name != g["standing_by"] and name in self.members]
        if others and all(name in g["passed"] for name in others):
            # 两家都不要：轮到出牌者自由出牌
            leader = g["standing_by"]
            g["free_lead"] = True
            g["standing"] = None
            g["standing_by"] = None
            g["table_plays"] = {}
            g["passed"] = set()
            if leader is None or leader not in self.members:
                leader = g["ring"][0]
            g["to_act"] = leader
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
            return
        await self.advance_turn(username)

    async def advance_turn(self, acted):
        g = self.game
        index = g["ring"].index(acted) if acted in g["ring"] else -1
        for step in range(1, len(g["ring"]) + 1):
            name = g["ring"][(index + step) % len(g["ring"])]
            if name in self.members:
                g["to_act"] = name
                g["deadline"] = time.time() + TURN_TIMEOUT
                self.schedule_turn_timer()
                await self.broadcast_views()
                return
        await self.end_hand(True, winner=None)

    async def progress_game(self):
        """宿主在成员离开后调用：斗地主有人离场本局作废。"""
        g = self.game
        if not g or not self.in_hand():
            return
        if g.get("broken") or any(name not in self.members for name in g["hands"]):
            await self.end_hand(True, winner=None)
            return
        if not g.get("to_act") or g["to_act"] not in self.members:
            nxt = g["ring"][0] if g["ring"][0] in self.members else None
            if nxt is None:
                await self.end_hand(True, winner=None)
                return
            g["to_act"] = nxt
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 结束与结算投票（与掼蛋/德州一致的宿主协议）----
    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        return len(self.seating) >= 3 and all(
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
            raise ValueError("人数不足 3 人或有人筹码已输光，只能结算并解散房间")
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

    async def end_hand(self, aborted, winner=None):
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        g = self.game
        self.paused = False
        self.pause_remaining = 0.0
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        payouts = {}
        gains = {}
        ratings = {}
        spring = False
        landlord = g.get("landlord")
        multiplier = self.current_multiplier()
        peasants_win = None
        if not aborted and landlord:
            peasants = [name for name in g["ring"] if name != landlord]
            landlord_won = winner == landlord
            peasants_win = not landlord_won
            # 春天：地主胜且两家农民一次都没出过；反春：农民胜且地主只出了一手
            if self.rules.get("spring", True):
                if landlord_won and all(g["plays"].get(n, 0) == 0 for n in peasants):
                    spring = True
                elif not landlord_won and g["plays"].get(landlord, 0) <= 1:
                    spring = True
            if spring:
                multiplier *= 2
                g["spring"] = True
            unit = round(self.blind * multiplier, 2)
            if landlord_won:
                total = 0.0
                for name in peasants:
                    if name not in self.members:
                        continue
                    pay = round(min(self.members[name]["stack"], unit), 2)
                    if pay > 0:
                        payouts[name] = pay
                        total = round(total + pay, 2)
                if landlord in self.members and total:
                    gains[landlord] = total
            else:
                remaining = self.members[landlord]["stack"]
                for name in peasants:
                    if name not in self.members or remaining <= 0:
                        continue
                    pay = round(min(unit, remaining), 2)
                    if pay > 0:
                        payouts[landlord] = round(
                            payouts.get(landlord, 0) + pay, 2)
                        gains[name] = pay
                        remaining = round(remaining - pay, 2)
            endings = {
                name: round(member["stack"] - payouts.get(name, 0)
                            + gains.get(name, 0), 2)
                for name, member in self.members.items()
            }
            ratings = self.settle_ratings(endings)
            for name, amount in endings.items():
                self.members[name]["stack"] = amount
            self.stacks_changed()
            self.starter = landlord
        else:
            self.starter = None
        winners = [winner] if winner and not aborted else []
        if peasants_win:
            winners = [name for name in g["ring"]
                       if name != landlord and name in self.members]
        g["result"] = {
            "ratings": ratings,
            "hand_no": g["hand_no"],
            "aborted": aborted,
            "landlord": landlord,
            "peasants_win": peasants_win,
            "winners": winners,
            "bid_score": g.get("bid_score", 1),
            "bombs": g["bombs"],
            "spring": spring,
            "multiplier": multiplier,
            "payouts": payouts,
            "gains": gains,
            "bottom": [{"r": c["r"], "s": c["s"]} for c in g.get("bottom", [])],
            "hands": {
                name: [{"r": c["r"], "s": c["s"]} for c in g["hands"].get(name, [])]
                for name in g.get("ring", [])
            },
        }
        logger.info(
            "doudizhu hand #%d done in room %s, aborted=%s, payouts=%s",
            g["hand_no"], self.id, aborted, payouts,
        )
        self.enter_settlement()
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]}
        )
        await self.broadcast_views()
        await self.on_rooms_changed()

    async def restart(self):
        """重新开始：收回本局手牌重新发牌。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_hand()
