"""狼人杀 · 夜晚行动与白天放逐的社交推理房间。

角色与阵营是数据驱动的注册表（ROLES/FACTIONS）：每个角色声明所属
阵营 faction、夜晚苏醒次序 wake 与能力 ability。胜负判定按 faction
标签泛化：新角色/第三方阵营只需扩展注册表并在夜晚结算里登记能力分支，
板子支持按人数预设（BOARD_PRESETS）或自定义 rules["board"] 任意组合。

规则要点（简单通用规则版，全部可由 rules 调整）：
  - 4–12 人开局，按板子随机发角色；狼人互识，夜晚按 守卫→狼人→女巫→
    预言家 的次序行动，缺员自动跳过；
  - 夜晚：狼人共同刀一人（允许显式空刀，超时也空刀），守卫守护一人
    （默认不能连守同一人），女巫一瓶解药一瓶毒药各一次（自救规则可选
    首夜/始终/不可），预言家验一人得知是否狼人；
  - 白天：公布死讯（可配是否翻牌身份）→ 自由发言（走房间聊天）→
    投票放逐；平票可配重投一轮或无人出局；遗言规则可选；
  - 猎人被刀或被放逐可开枪带走一人（默认被毒不能开枪）；
  - 胜负：狼人全灭好人胜；默认屠边局（神职或平民全灭即狼胜，可配屠城），
    狼人存活数不少于好人作为兜底立即判狼胜；
  - 金币结算：输方每人付一份 blind 底注，按人头均分给胜方全体（含阵亡
    队友，已离桌者不参与分摊与瓜分）。

房间聊天按阶段定向：白天公开；夜晚存活狼人走 wolf 频道（仅狼可见），
其他存活者禁言；已出局者走 dead 频道（仅死者互见）；遗言公开发言。
语音频道视图 voice_view 供后续 LiveKit 适配层使用（见
docs/werewolf-voice-design.md）。网络广播与托管持久化由宿主注入
（见 games/base.py），引擎可脱离网络独立测试。
"""
import logging
import os
import time

from games import randomness
from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.werewolf")

NIGHT_TIMEOUT = float(os.environ.get("WEREWOLF_NIGHT_TIMEOUT", "25"))
DAY_TIMEOUT = float(os.environ.get("WEREWOLF_DAY_TIMEOUT", "120"))
VOTE_TIMEOUT = float(os.environ.get("WEREWOLF_VOTE_TIMEOUT", "30"))
SHOT_TIMEOUT = float(os.environ.get("WEREWOLF_SHOT_TIMEOUT", "15"))
LAST_WORDS_TIMEOUT = float(os.environ.get("WEREWOLF_LAST_WORDS_TIMEOUT", "20"))
SETTLE_TIMEOUT = float(os.environ.get("WEREWOLF_SETTLE_TIMEOUT", "90"))

FACTIONS = {"wolf": "狼人阵营", "god": "神职阵营", "civilian": "平民阵营"}
ROLES = {
    "werewolf": {"name": "狼人", "faction": "wolf", "wake": 10, "ability": "kill"},
    "villager": {"name": "平民", "faction": "civilian", "wake": None, "ability": None},
    "seer": {"name": "预言家", "faction": "god", "wake": 30, "ability": "check"},
    "witch": {"name": "女巫", "faction": "god", "wake": 20, "ability": "witch"},
    "hunter": {"name": "猎人", "faction": "god", "wake": None, "ability": "shot"},
    "guard": {"name": "守卫", "faction": "god", "wake": 5, "ability": "protect"},
}
BOARD_PRESETS = {
    6: ["werewolf", "werewolf", "seer", "witch", "villager", "villager"],
    8: ["werewolf", "werewolf", "werewolf", "seer", "witch", "hunter",
        "villager", "villager"],
    9: ["werewolf", "werewolf", "werewolf", "seer", "witch", "hunter",
        "villager", "villager", "villager"],
    10: ["werewolf", "werewolf", "werewolf", "seer", "witch", "hunter",
         "guard", "villager", "villager", "villager"],
    12: ["werewolf", "werewolf", "werewolf", "werewolf", "seer", "witch",
         "hunter", "guard", "villager", "villager", "villager", "villager"],
}
WIN_MODES = ("bian", "cheng")
WITCH_SELF_SAVE = ("first", "always", "never")
LAST_WORDS_MODES = ("first", "all", "none")
TIE_POLICIES = ("revote", "no_exile")
NIGHT_ROLE_NAMES = {"kill": "狼人", "witch": "女巫", "check": "预言家",
                    "protect": "守卫"}


def is_wolf_role(role_key):
    return ROLES[role_key]["faction"] == "wolf"


def role_name(role_key):
    return ROLES[role_key]["name"]


def resolve_board(rules, count):
    """板子解析：自定义 board 优先，否则按人数选预设；返回 (board, 错误)。"""
    board = rules.get("board")
    if board is not None:
        if len(board) != count:
            return None, f"自定义板子需要 {count} 个角色，当前配置了 {len(board)} 个"
    else:
        board = BOARD_PRESETS.get(count)
        if board is None:
            return None, f"没有 {count} 人预设板子，请在自定义规则里指定 board"
    wolves = sum(1 for key in board if is_wolf_role(key))
    if wolves < 1:
        return None, "板子里至少要有一名狼人"
    if wolves >= count:
        return None, "板子里必须保留好阵营角色"
    return list(board), ""


@register_room_type("werewolf")
class WerewolfRoom(BaseRoom):
    """一局狼人杀的状态机：夜晚分段行动 → 白天死讯/发言/投票 → 放逐循环。"""

    max_seats = 12
    MIN_PLAYERS = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.game = None
        self.hand_seq = 0
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}

        def pick(key, choices, default):
            value = rules.get(key, default)
            return value if value in choices else default

        def clamp_seconds(key, default):
            try:
                value = int(rules.get(key, default))
            except (TypeError, ValueError):
                return default
            return min(600, max(15, value))

        board = rules.get("board")
        if (not isinstance(board, list) or not board
                or not all(isinstance(key, str) and key in ROLES for key in board)):
            board = None
        return {
            "board": board,
            "win_mode": pick("win_mode", WIN_MODES, "bian"),
            "witch_self_save": pick("witch_self_save", WITCH_SELF_SAVE, "first"),
            "guard_continuous": bool(rules.get("guard_continuous", False)),
            "hunter_shot_on_poison": bool(rules.get("hunter_shot_on_poison", False)),
            "reveal_role": bool(rules.get("reveal_role", True)),
            "last_words": pick("last_words", LAST_WORDS_MODES, "first"),
            "tie": pick("tie", TIE_POLICIES, "revote"),
            "day_seconds": clamp_seconds("day_seconds", int(DAY_TIMEOUT)),
            "vote_seconds": clamp_seconds("vote_seconds", int(VOTE_TIMEOUT)),
            "night_seconds": clamp_seconds("night_seconds", int(NIGHT_TIMEOUT)),
        }

    # ---- 基础查询 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("phase") != "showdown"

    def alive_roles(self):
        return [self.game["roles"][name] for name in self.game["alive"]]

    def _target_name(self, value):
        if isinstance(value, dict):
            value = value.get("target")
        return str(value) if value else None

    # ---- 视图 ----
    def summary(self):
        data = super().summary()
        data["hand_no"] = self.game["match_no"] if self.game else 0
        return data

    def _public_view(self):
        """不含任何私有信息的视图：观战者与死亡翻牌之外的角色全部隐藏。"""
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
            alive = bool(g and name in g["alive"])
            role_key = g["roles"].get(name) if g else None
            shown = None
            if g and role_key and not alive and self.rules["reveal_role"]:
                shown = role_name(role_key)
            view["players"].append({
                "username": name,
                "nickname": self.display_name(name),
                "avatar": self.player_avatar(name),
                "stack": member["stack"],
                "rating": self.player_rating(name),
                "alive": alive,
                "role": shown,
            })
        if g:
            view.update({
                "match_no": g["match_no"],
                "day_no": g["day_no"],
                "phase": g["phase"],
                "turn_seq": g.get("turn_seq", 0),
                "night_role": role_name(g["night_role"])
                if g.get("night_role") else None,
                "last_words_current": g["last_words"]["current"],
                "turn_left": round(max(0.0, g["deadline"] - time.time()), 1)
                if g["deadline"] else 0,
                "vote": dict(g["vote"]) if g["phase"] == "vote" else {},
                "vote_round": g["vote_round"],
                "candidates": list(g["candidates"]) if g["candidates"] else None,
                "last_night": [
                    {"username": name, "nickname": self.display_name(name),
                     "cause": cause}
                    for name, cause in g.get("last_night", {}).items()
                ],
                "history": g["history"][-12:],
                "last_action": g.get("last_action"),
                "result": g.get("result"),
            })
        return view

    def view_for(self, username):
        g = self.game if isinstance(self.game, dict) else None
        view = self._public_view()
        if not g:
            view["voice"] = self.voice_view(username)
            return view
        me_role = g["roles"].get(username)
        for row in view["players"]:
            if row["username"] == username and me_role:
                row["role"] = role_name(me_role)
            elif (me_role and row["username"] != username
                  and is_wolf_role(me_role)
                  and is_wolf_role(g["roles"][row["username"]])):
                row["role"] = role_name(g["roles"][row["username"]])
        if me_role:
            info = ROLES[me_role]
            teammates = []
            if info["faction"] == "wolf":
                teammates = [name for name in g["alive"]
                             if name != username
                             and is_wolf_role(g["roles"][name])]
            view["your_role"] = {
                "key": me_role,
                "name": info["name"],
                "faction": info["faction"],
                "faction_name": FACTIONS[info["faction"]],
                "teammates": [
                    {"username": name, "nickname": self.display_name(name)}
                    for name in teammates
                ],
            }
            if info["ability"] == "check":
                view["check_log"] = list(g["check_log"].get(username, []))
            if info["ability"] == "protect":
                view["last_protect"] = g["last_protect"].get(username)
            if me_role == "witch":
                view["witch_stock"] = dict(g["witch_stock"].get(username, {}))
        options = self._your_options(username)
        if options:
            view["your_options"] = options
        view["voice"] = self.voice_view(username)
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    def _targets_view(self, names):
        return [{"username": name, "nickname": self.display_name(name)}
                for name in names]

    def _your_options(self, username):
        g = self.game
        if g["phase"] == "showdown" or username not in g["alive"]:
            return None
        if g["phase"] == "night" and username in g.get("pending", []):
            return self._night_options(username)
        if g["phase"] == "vote":
            targets = g["candidates"] or list(g["alive"])
            return {
                "kind": "vote",
                "targets": self._targets_view(targets),
                "voted": g["vote"].get(username),
            }
        if g["phase"] == "shot" and g["shot_pending"] == username:
            targets = [name for name in g["alive"] if name != username]
            return {"kind": "shot",
                    "targets": self._targets_view(targets)}
        return None

    def _night_options(self, username):
        g = self.game
        ability = ROLES[g["night_role"]]["ability"]
        night = g["night"]
        others = [name for name in g["alive"]]
        if ability == "kill":
            targets = [name for name in others
                       if not is_wolf_role(g["roles"][name])]
            return {
                "kind": "night",
                "role": NIGHT_ROLE_NAMES["kill"],
                "targets": self._targets_view(targets),
                "allow_skip": True,
                "current": night["kill"],
                "submitted": username in night["kill_votes"],
            }
        if ability == "check":
            targets = [name for name in others if name != username]
            return {
                "kind": "night",
                "role": NIGHT_ROLE_NAMES["check"],
                "targets": self._targets_view(targets),
                "allow_skip": True,
                "submitted": username in night["checks"],
            }
        if ability == "protect":
            forbidden = None
            if not self.rules["guard_continuous"]:
                forbidden = g["last_protect"].get(username)
            targets = [name for name in others if name != forbidden]
            return {
                "kind": "night",
                "role": NIGHT_ROLE_NAMES["protect"],
                "targets": self._targets_view(targets),
                "allow_skip": True,
                "forbidden": forbidden,
                "submitted": username in night["protect"],
            }
        if ability == "witch":
            stock = g["witch_stock"].get(username, {})
            killed = night["kill"]
            self_save = self._witch_self_save_allowed()
            can_save = bool(stock.get("save")) and bool(killed) and (
                killed != username or self_save)
            poison_used = not stock.get("poison")
            return {
                "kind": "witch",
                "role": NIGHT_ROLE_NAMES["witch"],
                "kill_target": self._targets_view([killed])[0] if killed else None,
                "can_save": can_save,
                "can_poison": not poison_used,
                "self_save": self_save,
                "poison_targets": self._targets_view(
                    [name for name in others if name != username])
                if not poison_used else [],
                "submitted": username in night["witch"],
            }
        return None

    def voice_plan(self, username):
        """把 voice 视图的频道翻译成 LiveKit 房间（server/voice_livekit 消费）。"""
        view = self.voice_view(username)
        channel = view["channel"]
        if not channel:
            return {}
        if channel == "lobby":
            return {f"ww{self.id}-lobby": True}
        g = self.game
        return {f"ww{self.id}-m{g['match_no']}-v{g['voice_epoch']}-{channel}":
                bool(view["can_speak"])}

    def spectator_view(self, username):
        """观战视角：只给公开视图，不跟随被观战者（否则会泄露其角色与夜间信息）。"""
        watched = self.spectators.get(username)
        if watched not in self.members:
            watched = self.add_spectator(username)
        view = self._public_view()
        view["spectator"] = True
        view["watching"] = watched
        return view

    def voice_view(self, username):
        """语音频道分配：LiveKit 适配层据此签发 token / 建立订阅（V3）。"""
        if self.status == "waiting" and self.has_member(username):
            return {"channel": "lobby", "can_speak": True}
        g = self.game
        if not g or self.status != "playing" or g["phase"] == "showdown":
            return {"channel": None, "can_speak": False}
        phase = g["phase"]
        if phase == "last_words" and g["last_words"]["current"]:
            if username in g["roles"] or username in self.spectators:
                return {"channel": "day",
                        "can_speak": username == g["last_words"]["current"]}
        if username in g["alive"]:
            if phase in ("day", "vote"):
                return {"channel": "day", "can_speak": True}
            if phase == "night" and is_wolf_role(g["roles"][username]):
                return {"channel": "wolf", "can_speak": True}
            return {"channel": None, "can_speak": False}
        # 死者与观战者白天可听公开频道（只听不说）
        if phase in ("day", "vote") and (username in g["roles"]
                                         or username in self.spectators):
            return {"channel": "day", "can_speak": False}
        return {"channel": None, "can_speak": False}

    # ---- 房间聊天定向（挂点见 server/rooms/protocol.py）----
    def chat_route(self, username, message):
        g = self.game
        if not g or self.status != "playing" or g["phase"] == "showdown":
            return None
        if username in self.spectators:
            return ""
        if g["last_words"]["current"] == username:
            return None
        if username in g["alive"]:
            if g["phase"] in ("day", "vote"):
                return None
            if g["phase"] == "night" and is_wolf_role(g["roles"][username]):
                return "wolf"
            return ""
        return "dead"

    def chat_audience(self, channel):
        g = self.game
        if channel == "wolf":
            return [name for name in g["alive"] if is_wolf_role(g["roles"][name])]
        if channel == "dead":
            return [name for name in g["roles"]
                    if name not in g["alive"] and name in self.members]
        return None

    def visible_chat(self, username):
        g = self.game
        if not g or self.status != "playing":
            return list(self.chat)
        alive = username in g["alive"]
        is_dead_player = username in g["roles"] and not alive
        is_wolf = alive and is_wolf_role(g["roles"].get(username) or "")
        visible = []
        for message in self.chat:
            channel = message.get("channel")
            if not channel:
                visible.append(message)
            elif channel == "wolf" and is_wolf:
                visible.append(message)
            elif channel == "dead" and is_dead_player:
                visible.append(message)
        return visible

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())

    def on_resumed(self):
        g = self.game
        if g and g.get("deadline"):
            g["deadline"] = time.time() + (self.pause_remaining or 1.0)
            self._arm_phase_timer()
        self.pause_remaining = 0.0

    def _arm_phase_timer(self):
        g = self.game
        self.schedule("phase", max(0.05, g["deadline"] - time.time()),
                      self._phase_timeout)

    async def _phase_timeout(self):
        g = self.game
        if self.paused or not g or g["phase"] == "showdown":
            return
        phase = g["phase"]
        if phase == "night":
            await self._advance_night()
        elif phase == "day":
            await self._close_speech()
        elif phase == "vote":
            await self._close_vote()
        elif phase == "shot":
            await self._close_shot()
        elif phase == "last_words":
            await self._advance_last_words()

    # ---- 开局 ----
    async def start(self):
        if len(self.members_with_chips()) < self.MIN_PLAYERS:
            raise ValueError(f"狼人杀至少需要 {self.MIN_PLAYERS} 名玩家开局")
        self.status = "playing"
        await self.start_match()

    async def start_match(self):
        self.cancel_timers()
        eligible = self.members_with_chips()
        board, error = resolve_board(self.rules, len(eligible))
        if board is None or len(eligible) < self.MIN_PLAYERS:
            self.status = "waiting"
            self.game = None
            self.votes = {}
            await self.broadcast_views()
            await self.on_rooms_changed()
            return
        self.begin_rating_hand(eligible)
        randomness.shuffle(board)
        roles = dict(zip(eligible, board))
        self.hand_seq += 1
        self.game = {
            "match_no": self.hand_seq,
            "voice_epoch": 0,
            "turn_seq": 0,
            "day_no": 0,
            "phase": "night",
            "roles": roles,
            "alive": list(eligible),
            "night": self._fresh_night(),
            "witch_stock": {name: {"save": True, "poison": True}
                            for name in eligible if roles[name] == "witch"},
            "last_protect": {},
            "shot_pending": None,
            "shot_after": None,
            "last_words": {"queue": [], "current": None, "deadline": 0},
            "vote": {},
            "vote_round": 1,
            "candidates": None,
            "check_log": {},
            "history": [],
            "deadline": 0,
            "last_night": {},
            "last_action": None,
            "result": None,
        }
        logger.info("werewolf room %s match #%d: %d players, wolves=%d",
                    self.id, self.game["match_no"], len(eligible),
                    sum(1 for key in board if is_wolf_role(key)))
        self._log_event("对局开始，角色已分发")
        await self._enter_night()

    def _fresh_night(self):
        return {"kill": None, "kill_votes": {}, "protect": {}, "checks": {},
                "witch": {}, "steps": []}

    def _log_event(self, text, kind="system"):
        g = self.game
        g["history"].append({"day": g["day_no"], "text": text, "kind": kind})
        if len(g["history"]) > 120:
            del g["history"][:-120]
        g["last_action"] = {"text": text}

    # ---- 夜晚 ----
    async def _enter_night(self):
        g = self.game
        g["voice_epoch"] += 1
        g["day_no"] += 1
        g["phase"] = "night"
        g["night"] = self._fresh_night()
        g["vote"] = {}
        g["candidates"] = None
        g["vote_round"] = 1
        g["last_night"] = {}
        abilities = {"kill", "witch", "check", "protect"}
        steps = sorted({
            (ROLES[key]["wake"], key)
            for key in set(g["roles"].values())
            if ROLES[key]["wake"] is not None and ROLES[key]["ability"] in abilities
        })
        g["night"]["steps"] = steps
        self._log_event(f"第 {g['day_no']} 夜降临，全员闭眼")
        await self._night_step(0)

    async def _night_step(self, index):
        g = self.game
        steps = g["night"]["steps"]
        while index < len(steps):
            role_key = steps[index][1]
            pending = [name for name in g["alive"]
                       if g["roles"][name] == role_key]
            if pending:
                g["wake_idx"] = index
                g["night_role"] = role_key
                g["pending"] = pending
                g["turn_seq"] += 1
                g["deadline"] = time.time() + self.rules["night_seconds"]
                self._arm_phase_timer()
                await self.broadcast_views()
                return
            index += 1
        await self._resolve_night()

    def _night_ready(self):
        g = self.game
        ability = ROLES[g["night_role"]]["ability"]
        night = g["night"]
        if ability == "kill":
            book = night["kill_votes"]
        elif ability == "witch":
            book = night["witch"]
        elif ability == "check":
            book = night["checks"]
        elif ability == "protect":
            book = night["protect"]
        else:
            return True
        return all(name in book for name in g["pending"])

    async def _advance_night(self):
        g = self.game
        self.cancel_timer("phase")
        await self._night_step(g.get("wake_idx", -1) + 1)

    async def _after_night_submit(self):
        if self._night_ready():
            await self._advance_night()
        else:
            await self.broadcast_views()

    # ---- 行动 ----
    async def perform_action(self, username, action, data=None, auto=False):
        """宿主协议入口：夜晚目标 / 女巫决策 / 投票 / 猎人开枪。"""
        g = self.game
        if not g or self.paused or g["phase"] == "showdown":
            return
        if action == "night":
            await self.act_night(username, data)
        elif action == "witch":
            await self.act_witch(username, data)
        elif action == "vote":
            await self.act_vote(username, data)
        elif action == "shoot":
            await self.act_shot(username, data)

    async def act_night(self, username, data):
        g = self.game
        if g["phase"] != "night" or username not in g.get("pending", []):
            return
        ability = ROLES[g["night_role"]]["ability"]
        night = g["night"]
        target = self._target_name(data)
        if ability == "kill":
            if target and (target not in g["alive"]
                           or is_wolf_role(g["roles"][target])):
                return
            night["kill"] = target or None       # 多狼以最后一次提交为准，显式留白=空刀
            night["kill_votes"][username] = target or None
        elif ability == "check":
            if username in night["checks"]:      # 单角色提交即锁定，防重复改验人
                return
            if target:
                if target not in g["alive"] or target == username:
                    return
                night["checks"][username] = target
                g["check_log"].setdefault(username, []).append({
                    "day": g["day_no"],
                    "target": target,
                    "target_name": self.display_name(target),
                    "is_wolf": is_wolf_role(g["roles"][target]),
                })
            else:
                night["checks"][username] = None    # 显式不验
        elif ability == "protect":
            if username in night["protect"]:     # 守卫同样一锤定音
                return
            if target:
                if target not in g["alive"]:
                    return
                if not self.rules["guard_continuous"] \
                        and g["last_protect"].get(username) == target:
                    return
                night["protect"][username] = target
            else:
                night["protect"][username] = None   # 显式不守
        else:
            return
        await self._after_night_submit()

    def _witch_self_save_allowed(self):
        mode = self.rules["witch_self_save"]
        if mode == "always":
            return True
        if mode == "never":
            return False
        return self.game["day_no"] == 1

    async def act_witch(self, username, data):
        g = self.game
        if g["phase"] != "night" or username not in g.get("pending", []):
            return
        if g["night_role"] != "witch" or username in g["night"]["witch"]:
            return
        data = data if isinstance(data, dict) else {}
        stock = g["witch_stock"][username]
        night = g["night"]
        want_save = bool(data.get("save"))
        poison = self._target_name(data.get("poison"))
        killed = night["kill"]
        if want_save and (not stock["save"] or not killed
                          or (killed == username
                              and not self._witch_self_save_allowed())):
            return
        if poison and (not stock["poison"] or poison not in g["alive"]
                       or poison == username):
            return
        night["witch"][username] = {
            "save": want_save if killed else False,
            "poison": poison or None,
        }
        await self._after_night_submit()

    async def _resolve_night(self):
        g = self.game
        night = g["night"]
        g["wake_idx"] = None
        g["night_role"] = None
        g["pending"] = []
        g["deadline"] = 0
        killed = night["kill"]
        protected = {target for target in night["protect"].values()
                     if target and target in g["alive"]}
        saved = False
        deaths = {}
        for witch, decision in night["witch"].items():
            stock = g["witch_stock"][witch]
            if decision["save"] and killed and stock["save"]:
                stock["save"] = False
                if killed != witch or self._witch_self_save_allowed():
                    saved = True
            if decision["poison"] and stock["poison"]:
                stock["poison"] = False
                if decision["poison"] in g["alive"]:
                    deaths[decision["poison"]] = "poison"
        if killed and killed in g["alive"] and killed not in protected \
                and not saved:
            deaths.setdefault(killed, "kill")
        g["last_protect"] = {guard: target
                             for guard, target in night["protect"].items() if target}
        g["last_night"] = dict(deaths)
        for name in deaths:
            if name in g["alive"]:
                g["alive"].remove(name)
        if deaths:
            detail = "、".join(
                f"{self.display_name(name)}"
                + (f"（{role_name(g['roles'][name])}）"
                   if self.rules["reveal_role"] else "")
                for name in deaths)
            self._log_event(f"天亮了，昨夜 {detail} 倒在了血泊中", "dawn")
        else:
            self._log_event("天亮了，昨夜是平安夜", "dawn")
        shot = None
        for name, cause in deaths.items():
            if g["roles"][name] == "hunter" and (
                    cause != "poison" or self.rules["hunter_shot_on_poison"]):
                shot = name
        g["shot_pending"] = shot
        g["shot_after"] = "day"              # 夜间死者(无论是否开枪)天亮后进白天
        over = self._evaluate_winner()
        if over:
            await self.end_match(*over)
            return
        queue = [name for name in deaths
                 if self._night_death_has_last_words()]
        g["last_words"] = {"queue": queue, "current": None, "deadline": 0}
        logger.info("werewolf room %s night d%d: deaths=%s shot=%s",
                    self.id, g["day_no"], deaths, shot)
        await self._continue_epilogue()

    def _night_death_has_last_words(self):
        mode = self.rules["last_words"]
        return mode == "all" or (mode == "first"
                                 and self.game["day_no"] == 1)

    # ---- 白天 ----
    async def _enter_day(self):
        g = self.game
        g["voice_epoch"] += 1
        g["phase"] = "day"
        g["turn_seq"] += 1
        g["deadline"] = time.time() + self.rules["day_seconds"]
        self._arm_phase_timer()
        self._log_event(f"第 {g['day_no']} 天，开始自由发言", "day")
        await self.broadcast_views()

    async def _close_speech(self):
        g = self.game
        if g["phase"] != "day":
            return
        self.cancel_timer("phase")
        await self._enter_vote()

    async def _enter_vote(self):
        g = self.game
        g["voice_epoch"] += 1
        g["phase"] = "vote"
        g["vote"] = {}
        g["candidates"] = None
        g["turn_seq"] += 1
        g["deadline"] = time.time() + self.rules["vote_seconds"]
        self._arm_phase_timer()
        self._log_event("开始投票放逐", "vote")
        await self.broadcast_views()

    async def act_vote(self, username, data):
        g = self.game
        if g["phase"] != "vote" or username not in g["alive"]:
            return
        if username in g["vote"]:            # 一人一票：投出即锁定，防止刷屏刷新倒计时
            return
        target = self._target_name(data)
        if not target or target not in g["alive"]:
            return
        if g["candidates"] and target not in g["candidates"]:
            return
        g["vote"][username] = target
        if all(name in g["vote"] for name in g["alive"]):
            await self._close_vote()
        else:
            await self.broadcast_views()

    async def _close_vote(self):
        g = self.game
        if g["phase"] != "vote":
            return
        self.cancel_timer("phase")
        counts = {}
        for target in g["vote"].values():
            counts[target] = counts.get(target, 0) + 1
        best = max(counts.values()) if counts else 0
        tied = [target for target, count in counts.items() if count == best]
        if best > 0 and len(tied) == 1:
            await self._exile(tied[0])
            return
        if best == 0:
            self._log_event("无人投票，无人出局")
            await self._enter_night()
            return
        if self.rules["tie"] == "revote" and g["vote_round"] == 1:
            g["voice_epoch"] += 1
            g["vote_round"] = 2
            g["candidates"] = tied
            g["vote"] = {}
            g["turn_seq"] += 1
            g["deadline"] = time.time() + self.rules["vote_seconds"]
            self._arm_phase_timer()
            names = "、".join(self.display_name(name) for name in tied)
            self._log_event(f"平票，只在 {names} 之间重投一轮", "vote")
            await self.broadcast_views()
            return
        self._log_event("平票，无人出局")
        await self._enter_night()

    async def _exile(self, target):
        g = self.game
        g["alive"].remove(target)
        g["vote_round"] = 1
        g["candidates"] = None
        g["deadline"] = 0
        reveal = f"（{role_name(g['roles'][target])}）" \
            if self.rules["reveal_role"] else ""
        self._log_event(f"{self.display_name(target)} 被投票放逐{reveal}", "exile")
        logger.info("werewolf room %s d%d: %s exiled", self.id,
                    g["day_no"], target)
        over = self._evaluate_winner()
        if over:
            await self.end_match(*over)
            return
        g["shot_pending"] = target if g["roles"][target] == "hunter" else None
        g["shot_after"] = "night" if g["shot_pending"] else None
        queue = [target] if self.rules["last_words"] != "none" else []
        g["last_words"] = {"queue": queue, "current": None, "deadline": 0}
        await self._continue_epilogue()

    # ---- 遗言与猎人开枪 ----
    async def _continue_epilogue(self):
        g = self.game
        if g["last_words"]["queue"]:
            g["phase"] = "last_words"
            await self._advance_last_words()
        elif g["shot_pending"]:
            await self._enter_shot()
        elif g["shot_after"] == "day":
            g["shot_after"] = None
            await self._enter_day()
        else:
            g["shot_after"] = None
            await self._enter_night()

    async def _advance_last_words(self):
        g = self.game
        self.cancel_timer("phase")
        queue = g["last_words"]["queue"]
        if queue:
            current = queue.pop(0)
            g["voice_epoch"] += 1
            g["last_words"]["current"] = current
            g["turn_seq"] += 1
            g["deadline"] = time.time() + LAST_WORDS_TIMEOUT
            g["last_words"]["deadline"] = g["deadline"]
            self._arm_phase_timer()
            self._log_event(f"请 {self.display_name(current)} 发表遗言",
                            "last_words")
            await self.broadcast_views()
            return
        g["last_words"]["current"] = None
        g["last_words"]["deadline"] = 0
        if g["shot_pending"]:
            await self._enter_shot()
        elif g["shot_after"] == "day":
            g["shot_after"] = None
            await self._enter_day()
        else:
            g["shot_after"] = None
            await self._enter_night()

    async def _enter_shot(self):
        g = self.game
        g["voice_epoch"] += 1
        g["phase"] = "shot"
        g["pending"] = [g["shot_pending"]]
        g["turn_seq"] += 1
        g["deadline"] = time.time() + SHOT_TIMEOUT
        self._arm_phase_timer()
        self._log_event(f"猎人 {self.display_name(g['shot_pending'])} 亮出猎枪",
                        "shot")
        await self.broadcast_views()

    async def act_shot(self, username, data):
        g = self.game
        if g["phase"] != "shot" or g["shot_pending"] != username:
            return
        target = self._target_name(data)
        if not target:
            await self._close_shot()
            return
        if target not in g["alive"] or target == username:
            return
        self.cancel_timer("phase")
        g["alive"].remove(target)
        reveal = f"（{role_name(g['roles'][target])}）" \
            if self.rules["reveal_role"] else ""
        self._log_event(
            f"{self.display_name(username)} 开枪带走了 "
            f"{self.display_name(target)}{reveal}", "shot")
        g["shot_pending"] = None
        after = g["shot_after"]
        g["shot_after"] = None
        over = self._evaluate_winner()
        if over:
            await self.end_match(*over)
            return
        if after == "day":
            await self._enter_day()
        else:
            await self._enter_night()

    async def _close_shot(self):
        g = self.game
        if g["phase"] != "shot":
            return
        self.cancel_timer("phase")
        if g["shot_pending"]:
            self._log_event(
                f"{self.display_name(g['shot_pending'])} 收枪放弃开枪", "shot")
        g["shot_pending"] = None
        after = g["shot_after"]
        g["shot_after"] = None
        if after == "day":
            await self._enter_day()
        else:
            await self._enter_night()

    # ---- 胜负与结算 ----
    def _evaluate_winner(self):
        """返回 None 继续，或 (胜方, 原因)。胜负按 faction 标签泛化判定。"""
        g = self.game
        wolves = [name for name in g["alive"]
                  if is_wolf_role(g["roles"][name])]
        good = [name for name in g["alive"]
                if not is_wolf_role(g["roles"][name])]
        if not wolves:
            return ("good", "狼人全部出局")
        if not good:
            return ("wolf", "好人全部出局")
        if self.rules["win_mode"] == "bian":
            labels = {"god": "神职", "civilian": "平民"}
            for faction, label in labels.items():
                seated = [name for name, key in g["roles"].items()
                          if ROLES[key]["faction"] == faction]
                if seated and not any(name in g["alive"] for name in seated):
                    return ("wolf", f"{label}阵营被屠边")
        if len(wolves) >= len(good):
            return ("wolf", "狼人数量已不少于好人")
        return None

    async def end_match(self, winner_side, reason):
        g = self.game
        if g["phase"] == "showdown":
            return
        self.cancel_timers()
        g["phase"] = "showdown"
        g["pending"] = []
        g["deadline"] = 0
        winners = [name for name, key in g["roles"].items()
                   if is_wolf_role(key) == (winner_side == "wolf")
                   and name in self.members]
        losers = [name for name in g["roles"]
                  if name not in winners and name in self.members]
        payouts = {}
        total = 0.0
        endings = {name: member["stack"]
                   for name, member in self.members.items()}
        for name in losers:
            pay = round(min(self.members[name]["stack"], self.blind), 2)
            if pay > 0:
                payouts[name] = pay
                total = round(total + pay, 2)
                endings[name] = round(endings[name] - pay, 2)
        gains = {}
        if winners and total > 0:
            cents = int(round(total * 100))
            base, extra = divmod(cents, len(winners))
            for index, name in enumerate(winners):
                gain = (base + (1 if index < extra else 0)) / 100.0
                gains[name] = gain
                endings[name] = round(endings.get(name, 0.0) + gain, 2)
        ratings = self.settle_ratings(endings)
        for name, amount in endings.items():
            self.members[name]["stack"] = amount
        self.stacks_changed()
        winner_names = "、".join(self.display_name(name) for name in winners) \
            or "（胜方均已离桌）"
        g["result"] = {
            "match_no": g["match_no"],
            "days": g["day_no"],
            "winner": winner_side,
            "winner_text": f"{'狼人阵营' if winner_side == 'wolf' else '好人阵营'}获胜：{reason}",
            "reason": reason,
            "payout": "ante",
            "payouts": payouts,
            "gains": gains,
            "roles": {name: role_name(key)
                      for name, key in g["roles"].items()},
            "players": [
                {
                    "username": name,
                    "nickname": self.display_name(name),
                    "role": role_name(g["roles"][name]),
                    "alive": name in g["alive"],
                    "net": round(endings.get(name, 0)
                                 - self.rating_starts.get(name, 0), 2),
                }
                for name in g["roles"]
            ],
            "ratings": ratings,
        }
        logger.info("werewolf match #%d done in room %s: %s wins (%s), "
                    "collected %.2f", g["match_no"], self.id, winner_side,
                    reason, total)
        self._log_event(g["result"]["winner_text"], "result")
        self.enter_settlement()
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **g["result"]})
        await self.broadcast_views()
        await self.on_rooms_changed()

    # ---- 成员变动 ----
    def note_leave(self, username):
        """离桌即出局：从存活集合、待行动、投票、遗言队列与刀目标里摘除。"""
        g = self.game
        if not g or g["phase"] == "showdown" or username not in g["roles"]:
            return False
        was_alive = username in g["alive"]
        if was_alive:
            g["alive"].remove(username)
        if username in g.get("pending", []):
            g["pending"].remove(username)
        g["vote"].pop(username, None)
        if g["shot_pending"] == username:
            g["shot_pending"] = None
        if g["last_words"]["current"] == username:
            g["last_words"]["current"] = None
        if g["last_words"].get("queue") and username in g["last_words"]["queue"]:
            g["last_words"]["queue"].remove(username)
        if g["night"].get("kill") == username:
            g["night"]["kill"] = None       # 刀目标离开视作空刀
        return was_alive

    def pending_refunds(self):
        return {name: member["stack"] for name, member in self.members.items()}

    async def progress_game(self):
        """宿主在成员离开后调用：先判胜负，再修复当前阶段的待行动集合。"""
        g = self.game
        if not g or g["phase"] == "showdown":
            return
        over = self._evaluate_winner()
        if over:
            await self.end_match(*over)
            return
        phase = g["phase"]
        if phase == "night":
            g["pending"] = [name for name in g.get("pending", [])
                            if name in g["alive"]]
            if self._night_ready():
                await self._advance_night()
            else:
                await self.broadcast_views()
        elif phase == "vote":
            if g["alive"] and all(name in g["vote"] for name in g["alive"]):
                await self._close_vote()
            else:
                await self.broadcast_views()
        elif phase == "shot":
            if not g["shot_pending"]:
                await self._close_shot()
            else:
                await self.broadcast_views()
        elif phase == "last_words":
            if not g["last_words"]["current"]:
                await self._advance_last_words()
            else:
                await self.broadcast_views()
        else:
            await self.broadcast_views()

    # ---- 结算投票（与骗子酒馆/飞行棋一致的宿主协议）----
    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        board, _ = resolve_board(self.rules, len(self.seating))
        return (board is not None and len(self.seating) >= self.MIN_PLAYERS
                and all(member["stack"] > 0
                        for member in self.members.values()))

    async def settle_timeout(self):
        if self.paused or self.in_hand() or self.status != "playing":
            return
        await self.execute_decision("next", self.blind)

    async def cast_vote(self, username, choice, blind):
        if self.in_hand() or self.status != "playing":
            return None
        if choice == "next" and not self.can_continue(blind):
            raise ValueError("人数或筹码不满足开局条件，只能结算并解散房间")
        self.votes[username] = {
            "choice": choice,
            "blind": blind if choice == "next" else None,
        }
        live = {name: vote for name, vote in self.votes.items()
                if name in self.members}
        total = len(self.seating)
        next_votes = sum(1 for v in live.values() if v["choice"] == "next")
        dissolve_votes = sum(1 for v in live.values()
                             if v["choice"] == "dissolve")
        executed = None
        if next_votes > total / 2:
            executed = "next"
        elif dissolve_votes > total / 2:
            executed = "dissolve"
        elif all(name in live for name in self.seating):
            executed = "next" if next_votes >= dissolve_votes else "dissolve"
        if executed == "next":
            await self.execute_decision("next", blind or self.blind)
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
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_match()
