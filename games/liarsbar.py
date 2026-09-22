"""骗子酒馆 · 说谎与轮盘房间。

build_deck / is_truthful / shot_denominator / ranking_of 是纯函数，不依赖
任何 IO，可直接单元测试；LiarsBarRoom 负责一局淘汰赛的状态机、计时与
视图。网络广播与托管持久化由宿主注入（见 games/base.py）。

规则要点（同名游戏 Liar's Bar 的线上版）：
  - 2–6 人围桌，各执一把左轮（弹巢可调，1 发子弹）轮流对质；
  - 每轮重新发牌：随机桌面牌 K/Q/A，每人发固定张数（可选是否加入
    2 张小丑——小丑百搭，翻开时永远算桌面牌）；
  - 轮到出牌时暗打 1..上限 张声称是桌面牌，或质疑上家刚才那一手；
  - 质疑翻开上家最后打出的牌：全部合规则质疑者对自己开枪，否则
    说谎者开枪；命中率 1/剩余弹巢（默认连中递增，可选每次重转）；
  - 开枪未死则换轮重新发牌（幸存者先手），阵亡者淘汰（下家先手）；
  - 只剩一人时对局结束，金币结算二选一：冠军通吃（每家付一份底注）
    或按出局顺序递增（先出局者多付），赔付不超过各自剩余筹码。
"""
import logging
import os
import time

from games import randomness
from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.liarsbar")

TURN_TIMEOUT = float(os.environ.get("LIARSBAR_TURN_TIMEOUT", "45"))
REVEAL_TIMEOUT = float(os.environ.get("LIARSBAR_REVEAL_TIMEOUT", "5"))
SETTLE_TIMEOUT = float(os.environ.get("LIARSBAR_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)

TABLE_CARDS = ("K", "Q", "A")
JOKER = "R"
CHAMBER_CHOICES = (4, 6, 8)
CARD_CHOICES = (4, 5, 6)
MAX_PLAY_CHOICES = (2, 3)
PAYOUT_CHOICES = ("champion", "rank")
JOKER_CHOICES = ("wild", "none")
CARD_NAMES = {"K": "K 国王", "Q": "Q 王后", "A": "A 尖儿", "R": "小丑"}
CARD_ORDER = {"K": 3, "Q": 2, "A": 1, "R": 0}   # 手牌展示顺序


def build_deck(player_count, cards, jokers):
    """按存活人数构建并洗匀牌堆：K/Q/A 尽量均分，百搭模式加 2 张小丑。

    4 人 5 张时正好是经典的 6/6/6 + 2 小丑 = 20 张。
    """
    total = player_count * cards
    joker_count = 2 if jokers == "wild" else 0
    plain = max(0, total - joker_count)
    base, extra = divmod(plain, len(TABLE_CARDS))
    deck = []
    for idx, rank in enumerate(TABLE_CARDS):
        deck.extend([rank] * (base + (1 if idx < extra else 0)))
    deck.extend([JOKER] * joker_count)
    randomness.shuffle(deck)
    return deck


def is_truthful(cards, table_card, jokers):
    """翻牌判定：每张都是桌面牌（或百搭模式下的小丑）才算真话。"""
    wild = jokers == "wild"
    return all(c == table_card or (wild and c == JOKER) for c in cards)


def shot_denominator(chambers, survived, respin):
    """开枪命中率的分母：重转恒为弹巢容量，否则随幸存次数递减。"""
    if respin:
        return chambers
    return max(1, chambers - survived)


def ranking_of(alive, eliminated):
    """最终排名：幸存者第一，其余按出局顺序从晚到早。"""
    winner = alive[0] if alive else None
    order = [winner] if winner else []
    return order + list(reversed(eliminated))


def sort_hand(cards):
    """手牌按 K > Q > A > 小丑排序，便于客户端直接展示。"""
    return sorted(cards, key=lambda c: CARD_ORDER.get(c, -1), reverse=True)


@register_room_type("liarsbar")
class LiarsBarRoom(BaseRoom):
    """一局淘汰赛的状态机：开局 -> 轮次出牌/质疑 -> 决斗 -> 淘汰至独存。"""

    max_seats = 6

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.game = None
        self.hand_seq = 0        # 完成的淘汰赛场次；结算落库沿用宿主约定
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}

        def pick(key, choices, default):
            try:
                value = rules.get(key, default)
            except (TypeError, ValueError):
                return default
            return value if value in choices else default

        return {
            "chambers": pick("chambers", CHAMBER_CHOICES, 6),
            "cards": pick("cards", CARD_CHOICES, 5),
            "max_play": pick("max_play", MAX_PLAY_CHOICES, 3),
            "jokers": pick("jokers", JOKER_CHOICES, "wild"),
            "respin": bool(rules.get("respin", False)),
            "payout": pick("payout", PAYOUT_CHOICES, "champion"),
        }

    # ---- 基础查询 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def max_play_now(self, username):
        """本次实际可打出的张数上限（受手牌数限制）。"""
        hand = self.game["hands"].get(username, [])
        return min(self.rules["max_play"], len(hand))

    def gun_label(self, survived):
        """座位上展示的开枪命中率文案。"""
        denom = shot_denominator(self.rules["chambers"], survived,
                                 self.rules["respin"])
        return f"1/{denom}"

    # ---- 视图 ----
    def summary(self):
        data = super().summary()
        data["hand_no"] = self.game["round_no"] if self.game else 0
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
            alive = bool(g and name in g["order"])
            in_match = bool(g and name in g["hands"])
            survived = g["guns"][name]["survived"] if g and name in g["guns"] else 0
            view["players"].append({
                "username": name,
                "nickname": self.display_name(name),
                "avatar": self.player_avatar(name),
                "stack": member["stack"],
                "rating": self.player_rating(name),
                "in_match": in_match,
                "alive": alive,
                "survived": survived,
                "gun": self.gun_label(survived),
                "pile": g["piles"].get(name, 0) if g else 0,
            })
        if g:
            view.update({
                "match_no": g["match_no"],
                "round_no": g["round_no"],
                "hand_no": g["round_no"],      # 通用音效/回放以 hand_no 为准
                "stage": g["stage"],
                "table_card": g["table_card"],
                "to_act": g["to_act"],
                "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                if g["to_act"] else 0,
                "last_play": {
                    "username": g["last_play"]["username"],
                    "nickname": self.display_name(g["last_play"]["username"]),
                    "count": len(g["last_play"]["cards"]),
                } if g["last_play"] else None,
                "last_duel": g["last_duel"],
                "last_action": g.get("last_action"),
                "result": g.get("result"),
            })
            if username in g["hands"]:
                view["your_hand"] = list(g["hands"][username])
                if g["to_act"] == username and g["stage"] == "play":
                    view["your_options"] = {
                        "can_challenge": g["last_play"] is not None,
                        "challenge": g["last_play"]["username"] if g["last_play"] else None,
                        "challenge_name": self.display_name(g["last_play"]["username"])
                        if g["last_play"] else "",
                        "can_play": bool(g["hands"][username]),
                        "min_play": 1,
                        "max_play": self.max_play_now(username),
                        "table_card": g["table_card"],
                    }
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())

    def on_resumed(self):
        g = self.game
        if g and g["stage"] == "reveal":
            g["deadline"] = time.time() + (self.pause_remaining or REVEAL_TIMEOUT)
            self.schedule("reveal", max(0.05, g["deadline"] - time.time()),
                          self.after_reveal)
        elif g and g.get("to_act"):
            g["deadline"] = time.time() + (self.pause_remaining or TURN_TIMEOUT)
            self.schedule_turn_timer()
        self.pause_remaining = 0.0

    # ---- 计时 ----
    def schedule_turn_timer(self):
        g = self.game
        if not g or not g.get("to_act") or g["stage"] != "play":
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    async def auto_action(self):
        """超时托管：能质疑且有真牌就打一张真牌，否则质疑；无上家只能出牌。"""
        g = self.game
        if self.paused or not g or g["stage"] != "play" or not g.get("to_act"):
            return
        username = g["to_act"]
        hand = g["hands"][username]
        truthful = [i for i, card in enumerate(hand)
                    if is_truthful([card], g["table_card"], self.rules["jokers"])]
        if g["last_play"] and not truthful:
            await self.perform_action(username, "challenge", auto=True)
            return
        pick = truthful[0] if truthful else 0
        await self.perform_action(username, "play", {"cards": [pick]}, auto=True)

    # ---- 开局 ----
    async def start(self):
        if len(self.members_with_chips()) < 2:
            raise ValueError("至少需要两名有筹码的玩家才能开局")
        self.status = "playing"
        await self.start_match()

    async def start_match(self):
        self.cancel_timers()
        eligible = self.members_with_chips()
        if len(eligible) < 2:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        self.begin_rating_hand(eligible)
        self.game = {
            "match_no": self.hand_seq + 1,
            "round_no": 0,
            "stage": "play",
            "order": list(eligible),
            "hands": {},
            "table_card": None,
            "piles": {},
            "last_play": None,
            "to_act": None,
            "deadline": 0,
            "guns": {name: {"survived": 0, "dead": False} for name in eligible},
            "eliminated": [],
            "last_duel": None,
            "next_starter": eligible[0],
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["match_no"]
        self.deal_round()
        await self.broadcast_views()

    def deal_round(self):
        """换轮发牌：重建牌堆、随机桌面牌，先手开打。"""
        g = self.game
        alive = g["order"]
        deck = build_deck(len(alive), self.rules["cards"], self.rules["jokers"])
        g["hands"] = {name: sort_hand([deck.pop() for _ in range(self.rules["cards"])])
                      for name in alive}
        g["table_card"] = TABLE_CARDS[randomness.roll_die(len(TABLE_CARDS)) - 1]
        g["piles"] = {name: 0 for name in alive}
        g["last_play"] = None
        g["last_duel"] = None
        g["round_no"] += 1
        starter = g["next_starter"] if g["next_starter"] in alive else alive[0]
        g["to_act"] = starter
        g["stage"] = "play"
        g["deadline"] = time.time() + TURN_TIMEOUT
        g["last_action"] = {
            "username": starter,
            "nickname": self.display_name(starter),
            "text": f"第 {g['round_no']} 轮 · 桌面牌 {CARD_NAMES[g['table_card']]}，先出牌",
        }
        self.schedule_turn_timer()

    # ---- 行动 ----
    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or g["stage"] != "play":
            return
        if username not in g["order"] or g["to_act"] != username:
            return
        if action == "play":
            await self.act_play(username, data, auto)
        elif action == "challenge":
            await self.act_challenge(username, auto)

    async def act_play(self, username, data, auto=False):
        g = self.game
        hand = g["hands"][username]
        indices = data.get("cards") if isinstance(data, dict) else None
        if not isinstance(indices, (list, tuple)):
            return
        try:
            indices = sorted({int(i) for i in indices})
        except (TypeError, ValueError):
            return
        if not indices or len(indices) > self.max_play_now(username):
            return
        if indices[0] < 0 or indices[-1] >= len(hand):
            return
        cards = [hand[i] for i in indices]
        for i in reversed(indices):
            hand.pop(i)
        g["last_play"] = {"username": username, "cards": cards}
        g["piles"][username] += len(cards)
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": f"{'超时自动' if auto else ''}打出 {len(cards)} 张，声称是 "
                    f"{CARD_NAMES[g['table_card']]}",
        }
        logger.info("liarsbar %s@%s r%d: %s plays %d claimed %s",
                    username, self.id, g["round_no"], username, len(cards),
                    g["table_card"])
        await self.advance_turn()

    async def act_challenge(self, username, auto=False):
        g = self.game
        if not g["last_play"]:
            return
        accused = g["last_play"]["username"]
        cards = g["last_play"]["cards"]
        logger.info("liarsbar %s@%s r%d: %s challenges %s",
                    username, self.id, g["round_no"], username, accused)
        await self.resolve_duel(username, auto)

    async def advance_turn(self):
        g = self.game
        self.cancel_timer("turn")
        order = g["order"]
        index = order.index(g["to_act"]) if g["to_act"] in order else -1
        g["to_act"] = order[(index + 1) % len(order)]
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 决斗 ----
    async def resolve_duel(self, challenger, auto=False):
        g = self.game
        self.cancel_timer("turn")
        accused = g["last_play"]["username"]
        cards = g["last_play"]["cards"]
        truthful = is_truthful(cards, g["table_card"], self.rules["jokers"])
        shooter = challenger if truthful else accused
        gun = g["guns"][shooter]
        denom = shot_denominator(self.rules["chambers"], gun["survived"],
                                 self.rules["respin"])
        hit = randomness.roll_die(denom) == 1
        if hit:
            gun["dead"] = True
            g["eliminated"].append(shooter)
            g["order"].remove(shooter)
            g["next_starter"] = self.player_after(shooter)
        else:
            gun["survived"] += 1
            g["next_starter"] = shooter
        g["stage"] = "reveal"
        g["to_act"] = None
        g["deadline"] = time.time() + REVEAL_TIMEOUT
        verdict = "真话" if truthful else "说谎"
        shot_text = f"砰！{self.display_name(shooter)} 阵亡" if hit \
            else f"咔哒…空弹，{self.display_name(shooter)} 活了下来"
        g["last_duel"] = {
            "round_no": g["round_no"],
            "challenger": challenger,
            "challenger_name": self.display_name(challenger),
            "accused": accused,
            "accused_name": self.display_name(accused),
            "cards": list(cards),
            "table_card": g["table_card"],
            "truthful": truthful,
            "verdict": verdict,
            "shooter": shooter,
            "shooter_name": self.display_name(shooter),
            "odds": f"1/{denom}",
            "hit": hit,
            "text": f"{self.display_name(accused)} 的牌是 {''.join(cards)} → {verdict}，"
                    f"{self.display_name(shooter)} 对自己开枪（1/{denom}）：{shot_text}",
        }
        g["last_action"] = {
            "username": challenger,
            "nickname": self.display_name(challenger),
            "text": f"{'超时自动' if auto else ''}质疑 {self.display_name(accused)}：{shot_text}",
        }
        logger.info("liarsbar room %s r%d duel: %s vs %s truthful=%s shooter=%s hit=%s",
                    self.id, g["round_no"], challenger, accused, truthful, shooter, hit)
        self.schedule("reveal", REVEAL_TIMEOUT, self.after_reveal)
        await self.broadcast_views()

    def player_after(self, username):
        """阵亡者的下一位存活玩家（按开局座次循环）。"""
        g = self.game
        order = g["order"]
        if not order:
            return None
        seating = [name for name in self.seating if name in g["hands"]]
        start = seating.index(username) if username in seating else -1
        for step in range(1, len(seating) + 1):
            candidate = seating[(start + step) % len(seating)]
            if candidate in order:
                return candidate
        return order[0]

    async def after_reveal(self):
        """决斗展示结束：换轮发牌，或只剩一人时进入整局结算。"""
        g = self.game
        if self.paused or not g or g["stage"] != "reveal":
            return
        if len(g["order"]) <= 1:
            await self.end_match(g["order"][0] if g["order"] else None)
            return
        self.deal_round()
        await self.broadcast_views()

    # ---- 成员变动 ----
    def note_leave(self, username):
        """离桌：存活玩家视作认输出局（记入出局顺序），已阵亡者无需处理。"""
        g = self.game
        mid_match = bool(g and g["stage"] in ("play", "reveal")
                         and username in g["order"])
        if mid_match:
            g["eliminated"].append(username)
            g["order"].remove(username)
            if g["last_play"] and g["last_play"]["username"] == username:
                g["last_play"] = None   # 离桌者的暗牌不能再被质疑
        return mid_match

    def pending_refunds(self):
        """房间关闭时应退给每人的筹码（没有进行中的投入）。"""
        return {name: member["stack"] for name, member in self.members.items()}

    async def progress_game(self):
        """宿主在成员离开后调用：独存即完赛，否则补上行动者并刷新。"""
        g = self.game
        if not g or g["stage"] == "showdown":
            return
        if len(g["order"]) <= 1:
            await self.end_match(g["order"][0] if g["order"] else None)
            return
        if g["stage"] == "play" and g["to_act"] not in g["order"]:
            g["to_act"] = self.player_after(g["to_act"]) or g["order"][0]
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.cancel_timer("turn")
            self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 完赛与结算投票（与飞行棋一致的宿主协议）----
    async def end_match(self, winner):
        g = self.game
        if g["stage"] == "showdown":
            return
        self.cancel_timers()
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        g["winner"] = winner
        ranking = ranking_of(g["order"], g["eliminated"])
        payouts = {}
        total = 0.0
        endings = {name: member["stack"] for name, member in self.members.items()}
        if winner and winner in self.members:
            for rank, name in enumerate(ranking):
                if name == winner or name not in self.members:
                    continue
                unit = self.blind if self.rules["payout"] == "champion" \
                    else self.blind * rank
                pay = round(min(self.members[name]["stack"], unit), 2)
                if pay > 0:
                    payouts[name] = pay
                    total = round(total + pay, 2)
                    endings[name] = round(endings[name] - pay, 2)
            endings[winner] = round(endings[winner] + total, 2)
        ratings = self.settle_ratings(endings)
        for name, amount in endings.items():
            self.members[name]["stack"] = amount
        self.stacks_changed()
        g["result"] = {
            "match_no": g["match_no"],
            "rounds": g["round_no"],
            "winner": winner,
            "winner_name": self.display_name(winner) if winner else "",
            "payout": self.rules["payout"],
            "payouts": payouts,
            "gains": {winner: total} if winner and total else {},
            "ranking": [
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "survived": g["guns"].get(name, {}).get("survived", 0),
                    "rounds": g["round_no"],
                    "net": round(endings.get(name, 0)
                                 - self.rating_starts.get(name, 0), 2),
                }
                for name in ranking
            ],
            "ratings": ratings,
        }
        logger.info("liarsbar match #%d done in room %s, winner %s, rounds %d, "
                    "collected %.2f", g["match_no"], self.id, winner,
                    g["round_no"], total)
        # 先清票箱并挂结算倒计时再广播，避免客户端广播后立刻投的票被清掉。
        self.enter_settlement()
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]})
        await self.broadcast_views()
        await self.on_rooms_changed()

    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        """全员还有筹码且人数足够时才能再来一局。"""
        return len(self.seating) >= 2 and all(
            member["stack"] > 0 for member in self.members.values())

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
            await self.start_match()
            await self.on_rooms_changed()
        else:
            if self.on_dissolve_requested:
                await self.on_dissolve_requested("结算解散")

    async def restart(self):
        """重新开始：对局没有进行中的投入，直接重开一场。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_match()
