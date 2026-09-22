"""飞行棋 · 经典竞速房间。

棋盘几何与行走判定是纯函数（loop_index / cell_of / step_journey /
landing_effects / movable_planes / captures_at），不依赖任何 IO，可直接
单元测试；LudoRoom 负责一局竞速的状态机、计时与视图。网络广播与托管
持久化由宿主注入（见 games/base.py）。

规则要点（常见飞行棋玩法的线上版）：
  - 2–4 人按加入顺序执 红/黄/蓝/绿，各 4 架飞机，沿 52 格主圈顺时针竞速；
  - 掷到 6（可选 5 或 6）才能起飞，起飞后飞机停在本方起飞格；
    掷 6 可再掷一次（可关），连续三个 6 则本回合最后移动的飞机被罚回机场；
  - 落在自己颜色的格子可跳到下一个同色格（+4，可关）；落在本方飞行格
    直飞 12 格（可关），飞行落点不再跳格；
  - 最终落点有敌机时把它们全部撞回机场；同格可叠任意己方飞机；
  - 绕主圈 51 格后进入本方 6 格终点跑道，需掷出恰好点数到达终点，
    超出的点数从终点反弹回退；
  - 率先 4 架全部到达终点者获胜，其余按进度排名；金币结算二选一：
    冠军通吃（每家付一份底注）或按名次递增（第 2/3/4 名分别付
    1/2/3 份底注），赔付不超过各自剩余筹码。
"""
import logging
import os
import time

from games import randomness
from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.ludo")

TURN_TIMEOUT = float(os.environ.get("LUDO_TURN_TIMEOUT", "25"))
SETTLE_TIMEOUT = float(os.environ.get("LUDO_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)
LAUNCH_CHOICES = (5, 6)
PAYOUT_CHOICES = ("champion", "rank")

COLOR_NAMES = ("红", "黄", "蓝", "绿")

# ---- 棋盘几何：15×15 网格上的 52 格主圈（顺时针），起飞格相距 13 格 ----
LOOP = (
    (6, 1), (6, 2), (6, 3), (6, 4), (6, 5),
    (5, 6), (4, 6), (3, 6), (2, 6), (1, 6), (0, 6),
    (0, 7),
    (0, 8), (1, 8), (2, 8), (3, 8), (4, 8), (5, 8),
    (6, 9), (6, 10), (6, 11), (6, 12), (6, 13), (6, 14),
    (7, 14),
    (8, 14), (8, 13), (8, 12), (8, 11), (8, 10), (8, 9),
    (9, 8), (10, 8), (11, 8), (12, 8), (13, 8), (14, 8),
    (14, 7),
    (14, 6), (13, 6), (12, 6), (11, 6), (10, 6), (9, 6),
    (8, 5), (8, 4), (8, 3), (8, 2), (8, 1), (8, 0),
    (7, 0),
    (6, 0),
)
ENTRY = (0, 13, 26, 39)          # 各色起飞格在主圈上的下标（颜色 = 下标 % 4）
LOOP_STEPS = 51                  # 主圈步数：journey 0..50，第 51 格拐入终点跑道
RUNWAY_STEPS = 5                 # 终点跑道 5 格（journey 51..55），终点在中央
GOAL = LOOP_STEPS + RUNWAY_STEPS         # 56：终点
JUMP_STEP = 4                    # 同色格跳跃步长
FLY_FROM = 16                    # 本方飞行格 journey（✈）
FLY_TO = 28                      # 飞行落点 journey（直飞 12 格）
PLANES_PER_PLAYER = 4


def loop_index(color, journey):
    """journey（主圈段）映射到主圈下标；不在主圈段返回 None。"""
    if not 0 <= journey <= LOOP_STEPS - 1:
        return None
    return (ENTRY[color] + journey) % len(LOOP)


def cell_of(color, journey):
    """journey -> (row, col)。-1 表示在机场（返回 None），终点返回中心格。"""
    idx = loop_index(color, journey)
    if idx is not None:
        return LOOP[idx]
    if journey == GOAL:
        return (7, 7)
    if LOOP_STEPS <= journey < GOAL:
        row, col = 7, journey - LOOP_STEPS + 1
        if color == 1:
            return (col, 7)
        if color == 2:
            return (row, 14 - col)
        if color == 3:
            return (14 - col, 7)
        return (row, col)
    return None


def step_journey(journey, dice):
    """主圈/跑道前进 dice 步；超出终点的点数从终点反弹。"""
    target = journey + dice
    if target > GOAL:
        target = 2 * GOAL - target
    return target


def landing_effects(journey, rules):
    """落点效果链：飞行格直飞 12（可关）优先；同色跳 4（可关）后再判飞行。

    跳格只在本方主圈格上生效且不越过主圈末格；飞行落点不再跳格。
    """
    j = journey
    if rules.get("fly12") and j == FLY_FROM:
        return FLY_TO
    if rules.get("jump4") and JUMP_STEP <= j <= LOOP_STEPS - 1 - JUMP_STEP \
            and j % JUMP_STEP == 0:
        j += JUMP_STEP
        if rules.get("fly12") and j == FLY_FROM:
            j = FLY_TO
    return j


def movable_planes(planes, dice, launch_value):
    """掷出 dice 后可动的飞机下标：机场飞机须掷到起飞点数，场上的任意点数可走。"""
    result = []
    for idx, journey in enumerate(planes):
        if journey == -1:
            if dice >= launch_value:
                result.append(idx)
        elif journey < GOAL:
            result.append(idx)
    return result


def captures_at(all_planes, colors, color, journey):
    """最终落点在主圈段时，返回被撞回机场的 (username, plane_idx) 列表。

    all_planes 以用户名为键，colors 是用户名 -> 颜色的映射。
    """
    my_cell = loop_index(color, journey)
    if my_cell is None:
        return []
    hits = []
    for who, planes in all_planes.items():
        other = colors.get(who)
        if other is None or other == color:
            continue
        for idx, j in enumerate(planes):
            if loop_index(other, j) == my_cell:
                hits.append((who, idx))
    return hits


@register_room_type("ludo")
class LudoRoom(BaseRoom):
    """一局竞速的状态机：开局 -> 轮流掷骰移动 -> 有人夺冠结算 -> 投票再来。"""

    max_seats = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.game = None
        self.hand_seq = 0        # 完成的竞速局数；结算落库沿用宿主约定
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}
        try:
            launch = int(rules.get("launch", 6))
        except (TypeError, ValueError):
            launch = 6
        launch = launch if launch in LAUNCH_CHOICES else 6
        payout = rules.get("payout")
        payout = payout if payout in PAYOUT_CHOICES else "champion"
        return {
            "launch": launch,
            "extra_roll": bool(rules.get("extra_roll", True)),
            "jump4": bool(rules.get("jump4", True)),
            "fly12": bool(rules.get("fly12", True)),
            "payout": payout,
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

    # ---- 基础查询 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def launch_value(self):
        return self.rules["launch"]

    def finished_count(self, username):
        return sum(1 for j in self.game["planes"][username] if j == GOAL)

    def progress_of(self, username):
        """排名进度：终点记 GOAL+1 步，机场不计步。"""
        return sum(GOAL + 1 if j == GOAL else max(0, j) for j in self.game["planes"][username])

    # ---- 视图 ----
    def summary(self):
        data = super().summary()
        data["hand_no"] = self.game["race_no"] if self.game else 0

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
            in_race = bool(g and name in g["order"])
            planes = list(g["planes"].get(name, [-1] * PLANES_PER_PLAYER)) if in_race else [-1] * PLANES_PER_PLAYER
            view["players"].append({
                "username": name,
                "nickname": self.display_name(name),
                "avatar": self.player_avatar(name),
                "stack": member["stack"],
                "rating": self.player_rating(name),
                "in_race": in_race,
                "color": g["colors"].get(name) if g and name in g["colors"] else self.seating.index(name),
                "planes": planes,
                "finished": sum(1 for j in planes if j == GOAL),
                "progress": self.progress_of(name) if in_race else 0,
            })
        if g:
            view.update({
                "race_no": g["race_no"],
                "to_act": g["to_act"],
                "dice": g["dice"],
                "awaiting_move": g["awaiting_move"],
                "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                if g["to_act"] else 0,
                "last_action": g.get("last_action"),
                "result": g.get("result"),
            })
            if username in g["order"] and not self.paused and g["stage"] == "play":
                if not g["awaiting_move"]:
                    view["your_options"] = {"roll": g["to_act"] == username, "plane": []}
                else:
                    view["your_options"] = {
                        "roll": False,
                        "plane": movable_planes(
                            g["planes"][username], g["dice"], self.launch_value())
                        if g["to_act"] == username else [],
                    }
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    # ---- 计时 ----
    def schedule_turn_timer(self):
        g = self.game
        if not g or not g.get("to_act") or g["stage"] != "play":
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    async def auto_action(self):
        g = self.game
        if self.paused or not g or g["stage"] != "play" or not g.get("to_act"):
            return
        if not g["awaiting_move"]:
            await self.perform_action(g["to_act"], "roll", auto=True)
            return
        planes = g["planes"][g["to_act"]]
        movable = movable_planes(planes, g["dice"], self.launch_value())
        if not movable:
            await self.advance()
            return
        # 自动策略：优先起飞，其次走最靠前的飞机。
        pick = next((i for i in movable if planes[i] == -1),
                    max(movable, key=lambda i: (planes[i], -i)))
        await self.perform_action(g["to_act"], "move", {"plane": pick}, auto=True)

    # ---- 开局 ----
    async def start(self):
        if len(self.members_with_chips()) < 2:
            raise ValueError("至少需要两名有筹码的玩家才能开局")
        self.status = "playing"
        await self.start_race()

    async def start_race(self):
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
            "race_no": self.hand_seq + 1,
            "stage": "play",
            "order": list(eligible),
            "colors": {name: idx for idx, name in enumerate(eligible)},
            "planes": {name: [-1] * PLANES_PER_PLAYER for name in eligible},
            "to_act": eligible[0],
            "awaiting_move": False,
            "dice": None,
            "six_chain": 0,
            "moved_this_turn": None,
            "deadline": time.time() + TURN_TIMEOUT,
            "last_action": None,
            "result": None,
        }
        self.hand_seq = self.game["race_no"]
        g = self.game
        g["last_action"] = {
            "username": g["to_act"],
            "nickname": self.display_name(g["to_act"]),
            "text": f"{COLOR_NAMES[g['colors'][g['to_act']]]}方先行",
        }
        self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 行动 ----
    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or g["stage"] != "play":
            return
        if username not in g["order"] or g["to_act"] != username:
            return
        if action == "roll":
            await self.act_roll(username, auto)
        elif action == "move":
            await self.act_move(username, data, auto)

    async def act_roll(self, username, auto=False):
        g = self.game
        if g["awaiting_move"]:
            return
        dice = randomness.roll_die()
        g["dice"] = dice
        g["six_chain"] = g["six_chain"] + 1 if dice == 6 else 0
        nickname = self.display_name(username)
        text = f"{'超时自动' if auto else ''}掷出 {dice} 点"
        if g["six_chain"] >= 3:
            # 连续三个 6：本回合最后移动的飞机被罚回机场，回合结束。
            penalized = None
            if g["moved_this_turn"]:
                pname, pidx = g["moved_this_turn"]
                if pname in g["planes"] and 0 <= g["planes"][pname][pidx] < GOAL:
                    g["planes"][pname][pidx] = -1
                    penalized = pname
            text += "，连续三个 6，" + (
                f"{self.display_name(penalized)} 的飞机被罚回机场" if penalized
                else "本回合结束")
            g["last_action"] = {"username": username, "nickname": nickname, "text": text}
            logger.info("ludo %s@%s: triple six, penalize %s", username, self.id, penalized)
            await self.advance()
            return
        movable = movable_planes(g["planes"][username], dice, self.launch_value())
        if movable:
            g["awaiting_move"] = True
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
        else:
            text += "，无子可动"
            g["last_action"] = {"username": username, "nickname": nickname, "text": text}
            await self.advance()

    async def act_move(self, username, data, auto=False):
        g = self.game
        if not g["awaiting_move"]:
            return
        planes = g["planes"][username]
        try:
            idx = int(data.get("plane"))
        except (TypeError, ValueError):
            return
        if idx not in movable_planes(planes, g["dice"], self.launch_value()):
            return
        journey = planes[idx]
        if journey == -1:
            final = 0                      # 起飞：落在起飞格，不消耗点数
            text = "起飞"
        else:
            final = landing_effects(step_journey(journey, g["dice"]), self.rules)
            text = "前进"
            if final > journey:
                text = f"前进 {final - journey} 格"
            elif final < journey:
                text = f"反弹 {journey - final} 格"
        planes[idx] = final
        g["moved_this_turn"] = (username, idx)
        hits = captures_at(g["planes"], g["colors"], g["colors"][username], final)
        if hits:
            for other, pidx in hits:
                if other in g["planes"]:
                    g["planes"][other][pidx] = -1
            text += "，撞回 " + "、".join(
                sorted({self.display_name(other) for other, _ in hits}))
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": f"{'超时自动' if auto else ''}{text}",
        }
        logger.info("ludo %s@%s: plane %d -> %d hits=%d",
                    username, self.id, idx, final, len(hits))
        if all(j == GOAL for j in planes):
            await self.end_race(username)
            return
        if g["dice"] == 6 and self.rules["extra_roll"]:
            # 掷 6 再掷一次；连掷到第三个 6 会在下次掷骰时受罚。
            g["awaiting_move"] = False
            g["dice"] = None
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
            await self.broadcast_views()
        else:
            await self.advance()

    async def advance(self):
        """结束当前玩家回合，轮到下一位；记录上一手供展示。"""
        g = self.game
        self.cancel_timer("turn")
        g["awaiting_move"] = False
        g["dice"] = None
        g["six_chain"] = 0
        g["moved_this_turn"] = None
        index = g["order"].index(g["to_act"]) if g["to_act"] in g["order"] else -1
        g["to_act"] = g["order"][(index + 1) % len(g["order"])]
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 成员变动 ----
    def note_leave(self, username):
        """离桌时把该玩家移出本局（飞机一并撤下），返回竞速是否进行中。"""
        g = self.game
        mid_race = bool(g and g["stage"] == "play" and username in g["order"])
        if mid_race:
            g["order"].remove(username)
        return mid_race

    def pending_refunds(self):
        """房间关闭时应退给每人的筹码（竞速没有进行中的投入）。"""
        return {name: member["stack"] for name, member in self.members.items()}

    async def progress_game(self):
        """宿主在成员离开后调用：剩一人即完赛，否则推进或仅刷新。"""
        g = self.game
        if not g or g["stage"] == "showdown":
            return
        if len(g["order"]) <= 1:
            await self.end_race(g["order"][0] if g["order"] else None)
            return
        if g["to_act"] not in g["order"]:
            g["to_act"] = g["order"][0]
            g["awaiting_move"] = False
            g["dice"] = None
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
        await self.broadcast_views()

    # ---- 完赛与结算投票（与 UNO 一致的宿主协议）----
    def rankings(self):
        """完赛排名：冠军第一，其余按到达架数与总进度。"""
        g = self.game
        order = list(g["order"])
        if g.get("winner") in order:
            order.remove(g["winner"])
            order.insert(0, g["winner"])
        order.sort(key=lambda name: (name != g.get("winner"),
                                     -self.finished_count(name),
                                     -self.progress_of(name)))
        return order

    async def end_race(self, winner):
        g = self.game
        if g["stage"] == "showdown":
            return
        self.cancel_timer("turn")
        g["stage"] = "showdown"
        g["to_act"] = None
        g["awaiting_move"] = False
        g["dice"] = None
        g["deadline"] = 0
        g["winner"] = winner
        ranking = self.rankings()
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
            "race_no": g["race_no"],
            "winner": winner,
            "winner_name": self.display_name(winner) if winner else "",
            "payout": self.rules["payout"],
            "payouts": payouts,
            "gains": {winner: total} if winner and total else {},
            "ranking": [
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "color": g["colors"].get(name),
                    "finished": self.finished_count(name),
                    "progress": self.progress_of(name),
                    "net": round(endings.get(name, 0)
                                 - (self.rating_starts.get(name, 0)), 2),
                }
                for name in ranking
            ],
            "ratings": ratings,
        }
        logger.info("ludo race #%d done in room %s, winner %s, collected %.2f",
                    g["race_no"], self.id, winner, total)
        # 先清空上一轮票箱并挂上结算倒计时，再广播结果：
        # 否则客户端在广播后立刻投出的票会被 enter_settlement 清掉。
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
            await self.start_race()
            await self.on_rooms_changed()
        else:
            if self.on_dissolve_requested:
                await self.on_dissolve_requested("结算解散")

    async def restart(self):
        """重新开始：竞速没有进行中的投入，直接重开一局。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_race()
