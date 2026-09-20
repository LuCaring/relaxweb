"""掼蛋 · 双副牌四人组队房间。

build_deck / rank_value / enumerate_shapes / resolve_combo / beats /
find_moves 是纯函数，不依赖任何 IO，可直接单元测试；GuandanRoom 负责
一手牌的状态机、计时与视图。网络广播与托管持久化由宿主注入
（见 games/base.py）。

规则要点（江苏掼蛋的简化在线版）：
  - 两副牌 108 张，4 人按座位间隔分成两队，各自从 2 打到 A；
  - 级牌（双方当前级数）大于 A 小于王；本方红桃级牌可作逢人配（可选）；
  - 牌型：单张/对子/三张/三带二/顺子(5)/连对(≥3 对)/钢板(≥3 连三张)/
    同花顺/炸弹(≥4 同点)/天王炸(4 王，打出立即按双上结束)；
  - 头游方胜：双上升 3 级、搭档三游升 2 级、搭档末游升 1 级；
    打 A 默认需双上才能过 A（可选宽松：搭档不是末游即可）；
  - 金币：输家各付 底注×炸弹倍率（每炸弹×2 可封顶，双上再×2），赢家平分；
    简化无进贡还贡。
"""
import asyncio
import logging
import os
import random
import time
from collections import Counter

from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.guandan")

TURN_TIMEOUT = float(os.environ.get("GUANDAN_TURN_TIMEOUT", "45"))
SETTLE_TIMEOUT = float(os.environ.get("GUANDAN_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)
RULE_BOMB_CAPS = (0, 4, 8, 16, 999)   # 0=不翻倍，999=不封顶

SUIT_CHARS = "♠♥♦♣"
RANK_CHARS = {11: "J", 12: "Q", 13: "K", 14: "A", 16: "小王", 17: "大王"}
COMBO_NAMES = {
    "single": "单张", "pair": "对子", "triple": "三张", "triple_pair": "三带二",
    "straight": "顺子", "pairs_seq": "连对", "triple_seq": "钢板",
    "flush_straight": "同花顺", "bomb": "炸弹", "king_bomb": "天王炸",
}
ROLE_NAMES = {1: "头游", 2: "二游", 3: "三游", 4: "末游"}
ACE = 14
LOW = 3            # 顺子/连对/钢板的最小点数（2 与王不可入列）
STRAIGHT_LEN = 5


def build_deck():
    """两副牌 108 张：2~A 各花色 ×8，小王×2、大王×2。"""
    deck = [{"r": rank, "s": suit}
            for rank in range(2, 15) for suit in range(4)
            for _ in range(2)]
    deck.extend({"r": 16, "s": 4} for _ in range(2))
    deck.extend({"r": 17, "s": 4} for _ in range(2))
    random.shuffle(deck)
    return deck


def rank_char(rank):
    return RANK_CHARS.get(rank, str(rank))


def card_label(card):
    if card["s"] == 4:
        return RANK_CHARS[card["r"]]
    return f"{SUIT_CHARS[card['s']]}{rank_char(card['r'])}"


def rank_value(rank, levels):
    """单张比较序：大王 > 小王 > 级牌（双方当前级）> A > K > … > 2。"""
    if rank == 17:
        return (3, 2)
    if rank == 16:
        return (3, 1)
    if rank in levels:
        return (2, rank)
    return (1, rank)


def is_wild(card, wild_rank):
    """逢人配：本方红桃级牌可以当任意非王牌使用。"""
    return bool(wild_rank) and card["s"] == 1 and card["r"] == wild_rank


def _main_char(shape):
    tier_key, point = shape["main"]
    if tier_key == 3:
        return "大王" if point == 2 else "小王"
    return rank_char(point)


def combo_label(shape):
    name = COMBO_NAMES[shape["type"]]
    if shape["type"] == "bomb":
        return f"炸弹 {_main_char(shape)}×{shape['len']}张"
    if shape["type"] == "king_bomb":
        return name
    return f"{name} {_main_char(shape)}"


def shape_sort_key(shape):
    """从大到小：天王炸 > 炸弹/同花顺按 tier > 其余按主牌点数。"""
    return (shape["type"] == "king_bomb", shape["tier"], shape["main"], shape["len"])


def _check_needs(needs, nat, wilds):
    """needs 的缺口是否能全由万能牌补上（万能牌不能补王）。"""
    used = 0
    for rank, need in needs.items():
        short = need - nat.get(rank, 0)
        if rank >= 16 and short > 0:
            return False
        if short > 0:
            used += short
            if used > wilds:
                return False
    return True


def _shape(kind, tier, main, length, needs, suit=None):
    return {"type": kind, "tier": tier, "main": main, "len": length,
            "needs": needs, "suit": suit}


def enumerate_shapes(nat, nat_suit, wilds, total, levels, wild_rank=None):
    """枚举一组牌在「张数恰好 = total」时的全部合法牌型。

    nat: 剔除万能牌后按点数计数；nat_suit: 按 (点数, 花色) 计数（同花顺用）；
    wilds: 万能牌张数。返回 shape 列表，needs 记录各点数需要的张数，
    realize 时自然牌优先、缺口用万能牌补足。
    """
    shapes = []

    def add(kind, tier, main, needs, suit=None):
        shapes.append(_shape(kind, tier, main, total, needs, suit))

    # ---- 炸弹/天王炸：不可代牌，红桃级牌可按本点数使用 ----
    if 4 <= total <= 8:
        for rank in range(2, ACE + 1):
            count = nat.get(rank, 0) + (wilds if rank == wild_rank else 0)
            if count >= total:
                add("bomb", total, rank_value(rank, levels), {rank: total})
    if total == 4 and nat.get(16, 0) == 2 and nat.get(17, 0) == 2:
        add("king_bomb", 99, (4, 0), {16: 2, 17: 2})

    # ---- 单张（万能牌单出按本方级牌本身）----
    if total == 1:
        for rank in nat:
            add("single", 0, rank_value(rank, levels), {rank: 1})
        if wilds:
            add("single", 0, rank_value(wild_rank, levels), {})

    if total == 2:
        for rank in nat:
            if _check_needs({rank: 2}, nat, wilds):
                add("pair", 0, rank_value(rank, levels), {rank: 2})
        # 万能牌配对子：只能补 2~A，王不能配
        for rank in range(2, ACE + 1):
            need_wilds = 2 - nat.get(rank, 0)
            if 0 < need_wilds <= wilds:
                add("pair", 0, rank_value(rank, levels), {rank: 2})

    if total == 3:
        for rank in range(2, ACE + 1):
            if _check_needs({rank: 3}, nat, wilds):
                add("triple", 0, rank_value(rank, levels), {rank: 3})

    if total == 5:
        for triple_rank in range(2, ACE + 1):
            for pair_rank in range(2, 18):
                if pair_rank == triple_rank:
                    continue
                needs = {triple_rank: 3, pair_rank: 2}
                if _check_needs(needs, nat, wilds):
                    add("triple_pair", 0, rank_value(triple_rank, levels), needs)

    # ---- 顺子(5) / 连对(≥3 对) / 钢板(≥2 组三张)：2 与王不可入列 ----
    if total == STRAIGHT_LEN:
        for top in range(LOW + STRAIGHT_LEN - 1, ACE + 1):
            window = list(range(top - STRAIGHT_LEN + 1, top + 1))
            needs = {rank: 1 for rank in window}
            if _check_needs(needs, nat, wilds):
                add("straight", 0, (1, top), needs)
    if total >= 6 and total % 2 == 0:
        pairs = total // 2
        if pairs >= 3:
            for top in range(LOW + pairs - 1, ACE + 1):
                window = list(range(top - pairs + 1, top + 1))
                needs = {rank: 2 for rank in window}
                if _check_needs(needs, nat, wilds):
                    add("pairs_seq", 0, (1, top), needs)
    if total >= 6 and total % 3 == 0:
        triples = total // 3
        if triples >= 2:
            for top in range(LOW + triples - 1, ACE + 1):
                window = list(range(top - triples + 1, top + 1))
                needs = {rank: 3 for rank in window}
                if _check_needs(needs, nat, wilds):
                    add("triple_seq", 0, (1, top), needs)

    # ---- 同花顺：5 张同花连顺，大于五张炸弹、小于六张炸弹 ----
    if total == STRAIGHT_LEN:
        for suit in range(4):
            for top in range(LOW + STRAIGHT_LEN - 1, ACE + 1):
                window = list(range(top - STRAIGHT_LEN + 1, top + 1))
                suit_need = {(rank, suit): 1 for rank in window}
                used = 0
                ok = True
                for rank in window:
                    short = 1 - nat_suit.get((rank, suit), 0)
                    if short > 0:
                        used += short
                        if used > wilds:
                            ok = False
                            break
                if ok:
                    add("flush_straight", 5.5, (1, top),
                        {rank: 1 for rank in window}, suit=suit)
    return shapes


def _display_ranks(shape):
    """牌面展示顺序：三带二先三张后对子，其余组合按点数分组。"""
    ranks = sorted(shape["needs"])
    if shape["type"] == "triple_pair":
        triple_rank = shape["main"][1]
        return [triple_rank] + [rank for rank in ranks if rank != triple_rank]
    return ranks


def _realize(shape, nats, wild_cards, levels):
    """给 shape 分配具体牌，并按牌型结构排列；缺口就地用万能牌补。"""
    if shape["type"] == "bomb":
        nats = nats + wild_cards
        wild_cards = []
    pool = {}
    if shape["suit"] is None:
        for card in nats:
            pool.setdefault(card["r"], []).append(card)
    else:
        suit = shape["suit"]
        for card in nats:
            pool.setdefault((card["r"], card["s"]), []).append(card)

    picked = []
    wild_index = 0
    for rank in _display_ranks(shape):
        need = shape["needs"][rank]
        key = rank if shape["suit"] is None else (rank, shape["suit"])
        natural = sorted(pool.get(key, [])[:need], key=lambda c: (c["s"], c["r"]))
        short = need - len(natural)
        if wild_index + short > len(wild_cards):
            return None
        # 白搭按所代点数归组，并在该组的自然牌之前展示。
        picked.extend(wild_cards[wild_index:wild_index + short])
        picked.extend(natural)
        wild_index += short

    # 万能牌单出时 needs 为空，仍需把这张牌放进结果。
    remaining = shape["len"] - len(picked)
    if remaining > len(wild_cards) - wild_index:
        return None
    picked.extend(wild_cards[wild_index:wild_index + remaining])
    return {"type": shape["type"], "tier": shape["tier"], "main": shape["main"],
            "len": shape["len"], "label": combo_label(shape), "cards": picked}


def resolve_combo(cards, wild_rank, levels, standing=None):
    """解析最强合法牌型；有 standing 时只选能接的牌型，无解返回 None。"""
    wild_cards = [c for c in cards if is_wild(c, wild_rank)]
    nats = [c for c in cards if not is_wild(c, wild_rank)]
    nat = Counter(c["r"] for c in nats)
    nat_suit = Counter((c["r"], c["s"]) for c in nats)
    shapes = enumerate_shapes(nat, nat_suit, len(wild_cards), len(cards), levels,
                              wild_rank)
    if standing is not None:
        shapes = [shape for shape in shapes if beats(shape, standing)]
    if not shapes:
        return None
    return _realize(max(shapes, key=shape_sort_key), nats, wild_cards, levels)


def beats(candidate, standing):
    """candidate 能否压过 standing。"""
    if standing["type"] == "king_bomb":
        return False
    if candidate["type"] == "king_bomb":
        return True
    if candidate["tier"] and standing["tier"]:
        if candidate["tier"] != standing["tier"]:
            return candidate["tier"] > standing["tier"]
        return (candidate["type"] == standing["type"]
                and candidate["main"] > standing["main"])
    if candidate["tier"]:
        return True
    if standing["tier"]:
        return False
    return (candidate["type"] == standing["type"]
            and candidate["len"] == standing["len"]
            and candidate["main"] > standing["main"])


def find_moves(hand, wild_rank, levels, standing):
    """整手牌的全部合法出法（standing 为 None 表示自由出牌）。

    返回 [(combo, cards)]，按从小到大排序，第一个即「最小出法」。
    """
    wild_cards = [c for c in hand if is_wild(c, wild_rank)]
    nats = [c for c in hand if not is_wild(c, wild_rank)]
    nat = Counter(c["r"] for c in nats)
    nat_suit = Counter((c["r"], c["s"]) for c in nats)
    moves = []
    seen = set()
    for count in range(1, len(hand) + 1):
        for shape in enumerate_shapes(nat, nat_suit, len(wild_cards), count, levels,
                                      wild_rank):
            combo = _realize(shape, nats, wild_cards, levels)
            if combo is None:
                continue
            combo = resolve_combo(combo["cards"], wild_rank, levels, standing)
            if combo is None:
                continue
            key = (combo["type"], combo["main"], combo["len"])
            if key in seen:
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


@register_room_type("guandan")
class GuandanRoom(BaseRoom):
    """一手牌的状态机：发牌 -> 轮流出牌/过 -> 定头游结算 -> 投票再来。"""

    max_seats = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.levels = {0: 2, 1: 2}
        self.game = None
        self.starter = None        # 上一局头游，下一局先出
        self.hand_seq = 0
        self.match_winner = None   # 过 A 的队伍；再开一局时清零重置级数
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}
        try:
            bomb_cap = int(rules.get("bomb_cap", 8))
        except (TypeError, ValueError):
            bomb_cap = 8
        if bomb_cap not in RULE_BOMB_CAPS:
            bomb_cap = 8
        return {
            "wild": bool(rules.get("wild", True)),
            "bomb_cap": bomb_cap,
            "ace_strict": bool(rules.get("ace_strict", True)),
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

    # ---- 队伍与规则 ----
    def team_of(self, username):
        g = self.game if isinstance(self.game, dict) else None
        if g and username in g.get("teams", {}):
            return g["teams"][username]
        try:
            return self.seating.index(username) % 2
        except ValueError:
            return 0

    def wild_rank_for(self, username):
        if not self.rules.get("wild", True):
            return None
        return self.levels[self.team_of(username)]

    def level_set(self):
        return {self.levels[0], self.levels[1]}

    def current_multiplier(self):
        g = self.game
        cap = self.rules.get("bomb_cap")
        if not g or not cap:
            return 1
        return min(2 ** g["bombs"], cap)

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
            "levels": [self.levels[0], self.levels[1]],
            "match_winner": self.match_winner,
            "players": [],
        }
        for name in self.seating:
            member = self.members[name]
            in_hand = bool(g and name in g.get("teams", {}))
            finished = g["finish"].index(name) + 1 if g and name in g["finish"] else 0
            view["players"].append(
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "avatar": self.player_avatar(name),
                    "stack": member["stack"],
                    "rating": self.player_rating(name),
                    "team": self.team_of(name),
                    "in_hand": in_hand,
                    "cards": len(g["hands"].get(name, [])) if in_hand else 0,
                    "finished": finished,
                    "passed": bool(g and name in g["passed"]),
                }
            )
        if g:
            standing = g.get("standing")
            view.update(
                {
                    "hand_no": g["hand_no"],
                    "to_act": g["to_act"],
                    "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                    if g["to_act"] else 0,
                    "free_lead": g["free_lead"],
                    "bombs": g["bombs"],
                    "multiplier": self.current_multiplier(),
                    "standing": None if not standing else {
                        "by": g["standing_by"],
                        "type": standing["type"],
                        "tier": standing["tier"],
                        "main": list(standing["main"]),
                        "len": standing["len"],
                        "label": standing["label"],
                        "cards": [{"r": c["r"], "s": c["s"]}
                                  for c in standing["cards"]],
                    },
                    "passed": sorted(g["passed"]),
                    "last_action": g.get("last_action"),
                    "result": g.get("result"),
                }
            )
            if username in g.get("teams", {}):
                view["my_team"] = g["teams"][username]
                view["your_hand"] = [
                    {"r": c["r"], "s": c["s"]} for c in g["hands"].get(username, [])
                ]
                view["your_options"] = {
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

    # ---- 一手牌状态机 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def note_leave(self, username):
        """掼蛋是固定 4 人的团体赛，有人中途离桌则本手作废。"""
        g = self.game
        mid_hand = self.in_hand() and username in g.get("teams", {})
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

    def next_unfinished(self, after):
        ring = self.game["ring"]
        start = ring.index(after) if after in ring else -1
        for step in range(1, len(ring) + 1):
            name = ring[(start + step) % len(ring)]
            if name in self.game["finish"] or name not in self.members:
                continue
            return name
        return None

    async def auto_action(self):
        g = self.game
        if self.paused or not g or not g.get("to_act") or not self.in_hand():
            return
        username = g["to_act"]
        if username not in g.get("teams", {}):
            await self.progress_game()
            return
        if g["free_lead"]:
            moves = find_moves(g["hands"][username], self.wild_rank_for(username),
                               self.level_set(), None)
            if moves:
                indices = indices_of(g["hands"][username], moves[0]["cards"])
                if indices is not None:
                    await self.perform_action(username, "play",
                                              {"cards": indices}, auto=True)
                    return
        await self.perform_action(username, "pass", {}, auto=True)

    async def start(self):
        """房主开局；掼蛋必须正好 4 名有筹码玩家。"""
        eligible = self.members_with_chips()
        if len(eligible) != 4:
            raise ValueError("掼蛋需要正好 4 名有筹码的玩家才能开局")
        self.status = "playing"
        self.starter = None
        await self.start_hand()

    async def start_hand(self):
        self.cancel_timers()
        eligible = self.members_with_chips()
        if len(eligible) != 4:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        if self.match_winner is not None:
            self.levels = {0: 2, 1: 2}
            self.match_winner = None
        self.begin_rating_hand(eligible)
        deck = build_deck()
        hands = {name: [deck.pop() for _ in range(27)] for name in eligible}
        teams = {name: index % 2 for index, name in enumerate(eligible)}
        leader = self.starter if self.starter in eligible else eligible[0]
        self.game = {
            "hand_no": self.hand_seq + 1,
            "stage": "play",
            "hands": hands,
            "teams": teams,
            "ring": list(eligible),
            "to_act": leader,
            "free_lead": True,
            "standing": None,
            "standing_by": None,
            "passed": set(),
            "finish": [],
            "bombs": 0,
            "deadline": time.time() + TURN_TIMEOUT,
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["hand_no"]
        self.game["last_action"] = {
            "username": leader,
            "nickname": self.display_name(leader),
            "text": "成为先出玩家，可出任意牌型",
        }
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or not self.in_hand():
            return
        if g.get("to_act") != username or username not in g.get("teams", {}):
            return
        if action == "pass":
            await self.pass_turn(username, auto)
        elif action == "play":
            await self.play_cards(username, data or {}, auto)

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
        combo = resolve_combo(cards, self.wild_rank_for(username), self.level_set(),
                              standing)
        if combo is None:
            return
        standing = g.get("standing")
        if not g["free_lead"] and (standing is None or not beats(combo, standing)):
            return
        for card in cards:
            g["hands"][username].remove(card)
        if combo["tier"] >= 4:
            g["bombs"] += 1
        g["standing"] = combo
        g["standing_by"] = username
        g["free_lead"] = False
        g["passed"] = set()
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + f"出 {combo['label']}",
        }
        logger.info("guandan %s@%s: %s %s",
                    username, self.id, combo["type"], combo["label"])
        if combo["type"] == "king_bomb":
            # 天王炸：本局立即结束，出牌者头游、对家二游
            partner = next(name for name, team in g["teams"].items()
                           if team == g["teams"][username] and name != username)
            await self.on_finish(username, auto, king_partner=partner)
            return
        if not g["hands"][username]:
            await self.on_finish(username, auto)
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
        logger.info("guandan %s@%s: pass", username, self.id)
        waiting = [name for name in g["ring"]
                   if name != g["standing_by"] and name not in g["finish"]
                   and name in self.members]
        if waiting and all(name in g["passed"] for name in waiting):
            # 一家出牌其余全过：轮到出牌者自由出牌
            leader = g["standing_by"]
            g["free_lead"] = True
            g["standing"] = None
            g["standing_by"] = None
            g["passed"] = set()
            if leader is None or leader not in self.members or leader in g["finish"]:
                leader = self.next_unfinished(leader or g["ring"][0])
            if leader is None:
                await self.end_hand(False)
                return
            g["to_act"] = leader
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
            return
        await self.advance_turn(username)

    async def advance_turn(self, acted):
        g = self.game
        nxt = self.next_unfinished(acted)
        if nxt is None:
            await self.end_hand(False)
            return
        g["to_act"] = nxt
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def on_finish(self, username, auto, king_partner=None):
        """有人出完：记录名次；双上/三游定局则收尾。"""
        g = self.game
        if king_partner is not None:
            g["finish"] = []
        g["finish"].append(username)
        role_name = ROLE_NAMES.get(len(g["finish"]), "")
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + f"出完，成为{role_name}",
        }
        if king_partner is not None:
            g["finish"].append(king_partner)
        team = g["teams"][username]
        teammates_done = sum(1 for name in g["finish"] if g["teams"][name] == team)
        if teammates_done == 2 or len(g["finish"]) >= 3:
            if len(g["finish"]) < 4:
                remaining = [name for name in g["ring"]
                             if name not in g["finish"] and name in self.members]
                g["finish"].extend(remaining)
            await self.end_hand(False)
            return
        await self.advance_turn(username)

    async def progress_game(self):
        """宿主在成员离开后调用：掼蛋有人离场本手作废。"""
        g = self.game
        if not g or not self.in_hand():
            return
        if g.get("broken") or any(name not in self.members for name in g["teams"]):
            await self.end_hand(True)
            return
        if not g.get("to_act") or g["to_act"] not in self.members:
            nxt = self.next_unfinished(g["ring"][0])
            if nxt is None:
                await self.end_hand(True)
                return
            g["to_act"] = nxt
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 结束与结算投票（与德州/UNO 一致的宿主协议）----
    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        return len(self.seating) >= 4 and all(
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
            raise ValueError("人数不足 4 人或有人筹码已输光，只能结算并解散房间")
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

    @staticmethod
    def level_gain(finish, teams):
        """头游方升级数：双上 3、搭档三游 2、搭档末游 1。"""
        head_team = teams[finish[0]]
        if teams.get(finish[1]) == head_team:
            return 3
        if teams.get(finish[2]) == head_team:
            return 2
        return 1

    async def end_hand(self, aborted):
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        g = self.game
        self.paused = False
        self.pause_remaining = 0.0
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        finish = list(g["finish"])
        levels_before = dict(self.levels)
        levels_after = dict(self.levels)
        payouts = {}
        gains = {}
        gain = 0
        match_win = None
        ratings = {}
        multiplier = self.current_multiplier()
        if not aborted:
            winner_team = g["teams"][finish[0]]
            gain = self.level_gain(finish, g["teams"])
            old = levels_before[winner_team]
            if old >= ACE:
                passed = gain == 3 if self.rules["ace_strict"] else gain >= 2
                if passed:
                    match_win = winner_team
                    self.match_winner = winner_team
            else:
                levels_after[winner_team] = min(ACE, old + gain)
            self.levels = levels_after
            # 金币：输家各付 底注×炸弹倍率（双上再×2），赢家平分（头游拿零头）
            unit = round(self.blind * multiplier * (2 if gain == 3 else 1), 2)
            loser_team = 1 - winner_team
            total = 0.0
            for name, team in g["teams"].items():
                if team != loser_team or name not in self.members:
                    continue
                pay = round(min(self.members[name]["stack"], unit), 2)
                if pay > 0:
                    payouts[name] = pay
                    total = round(total + pay, 2)
            share = round(total / 2, 2)
            head = next((n for n in finish
                         if g["teams"].get(n) == winner_team and n in self.members),
                        None)
            partner = next((n for n in finish
                            if n != head and g["teams"].get(n) == winner_team
                            and n in self.members), None)
            if head and partner:
                gains[head] = round(total - share, 2)
                gains[partner] = share
            elif head:
                gains[head] = total
            endings = {
                name: round(member["stack"] - payouts.get(name, 0)
                            + gains.get(name, 0), 2)
                for name, member in self.members.items()
            }
            ratings = self.settle_ratings(endings)
            for name, amount in endings.items():
                self.members[name]["stack"] = amount
            self.stacks_changed()
            self.starter = finish[0]
        else:
            self.starter = None
        g["result"] = {
            "ratings": ratings,
            "hand_no": g["hand_no"],
            "aborted": aborted,
            "finish": finish,
            "roles": {name: ROLE_NAMES.get(index + 1, "")
                      for index, name in enumerate(finish)},
            "teams": dict(g["teams"]),
            "levels_before": levels_before,
            "levels_after": levels_after,
            "gain": gain,
            "match_win": match_win,
            "bombs": g["bombs"],
            "multiplier": multiplier,
            "payouts": payouts,
            "gains": gains,
            "hands": {
                name: [{"r": c["r"], "s": c["s"]} for c in g["hands"].get(name, [])]
                for name in g["teams"]
            },
        }
        logger.info(
            "guandan hand #%d done in room %s, aborted=%s, payouts=%s",
            g["hand_no"], self.id, aborted, payouts,
        )
        self.enter_settlement()
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]}
        )
        await self.broadcast_views()
        await self.on_rooms_changed()

    async def restart(self):
        """重新开始：收回本局手牌重新发牌（级数保留）。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_hand()
