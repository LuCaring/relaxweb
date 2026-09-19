"""国标麻将 · 四人对战房间。

牌与番种计算是纯函数（build_wall / win_forms / score_form / best_score），
不依赖任何 IO，可直接单元测试；MahjongRoom 负责一手牌的状态机、计时与
视图。网络广播与托管持久化由宿主注入（见 games/base.py）。

规则要点（国标麻将《中国麻将竞赛规则》的线上简化版）：
  - 144 张：万/条/筒各 9 种 ×4、风箭 7 种 ×4、花牌 8 张（可关）；
  - 吃（可选，仅上家）、碰、明杠/暗杠/补杠、抢杠胡、杠上开花、海底；
  - 和牌：4 副顺刻 + 将，或七对/连七对/十三幺/九莲宝灯（均须门清）；
  - 八番起和（可选 8/4/0）。番种实现 76 个常用项，重复计分按
    「就高不就低 + 显式不计」近似处理；不靠系（组合龙/全不靠/七星不靠）
    暂未实现；
  - 计分：底注 × 总番。自摸三家各付一份；点炮默认「包三家」付三份
    （官方规则），可选只付一份。花牌每张 1 分计入总分；
  - 座风圈风：庄家胡牌或荒庄连庄，否则下家坐庄；四人各坐一次庄为一圈，
    圈风按 东南西北 顺次轮转。
"""
import logging
import os
import random
import time
from collections import Counter
from itertools import combinations

from games.base import BaseRoom, register_room_type

logger = logging.getLogger("live-chat.mahjong")

TURN_TIMEOUT = float(os.environ.get("MAHJONG_TURN_TIMEOUT", "25"))
CLAIM_TIMEOUT = float(os.environ.get("MAHJONG_CLAIM_TIMEOUT", "10"))
SETTLE_TIMEOUT = float(os.environ.get("MAHJONG_SETTLE_TIMEOUT", "90"))
BLIND_PRESETS = (1, 2, 5, 10)
MIN_FAN_CHOICES = (8, 4, 0)

# ---- 牌编码：0-8 万 / 9-17 条 / 18-26 筒（n = 点数-1），27-33 东南西北中发白，34-41 花 ----
CHOW_MAX = 26          # 序数牌最大编码（9筒）
HONOR_MIN = 27
TILE_KINDS = HONOR_MIN + 7        # 34 种非花牌
FLOWER_MIN = 34
FLOWER_COUNT = 8
WINDS = (27, 28, 29, 30)          # 东南西北
DRAGONS = (31, 32, 33)            # 中发白

TILE_NAMES = {}
for _suit, _name in enumerate("万条筒"):
    for _n in range(9):
        TILE_NAMES[_suit * 9 + _n] = f"{_n + 1}{_name}"
for _i, _ch in enumerate("东南西北中发白"):
    TILE_NAMES[HONOR_MIN + _i] = _ch
for _i, _ch in enumerate("春夏秋冬梅兰竹菊"):
    TILE_NAMES[FLOWER_MIN + _i] = _ch
WIND_NAMES = ("东", "南", "西", "北")

GREEN_TILES = {9 + n for n in (1, 2, 3, 5, 7)} | {32}            # 绿一色：23468条+发
UPSIDE_TILES = ({18 + n for n in (0, 1, 2, 3, 4, 7, 8)}           # 推不倒：1234589筒
                | {10, 13} | {33})                                # 24条、白
TERMINAL_NS = {0, 8}
EVEN_NS = {1, 3, 5, 7}


def tile_name(code):
    return TILE_NAMES.get(code, "?")


def build_wall(include_flowers=True):
    """136 张标准牌（+8 花），洗匀后即牌墙：正面摸牌取头部，补花/杠从尾部。"""
    wall = [code for code in range(TILE_KINDS) for _ in range(4)]
    if include_flowers:
        wall.extend(range(FLOWER_MIN, FLOWER_MIN + FLOWER_COUNT))
    random.shuffle(wall)
    return wall


# =========================================================
# 和牌形式：手牌（含和牌张）拆解成「4 副顺刻 + 将」或特殊牌型
# =========================================================

def decompose(counter, need_sets):
    """把 counter 拆成 need_sets 个顺子/刻子，返回所有拆法。

    每个拆法是 [("tri", tile) / ("chi", start)] 列表。
    """
    if need_sets == 0:
        return [[]] if not counter else []
    if not counter:
        return []
    results = []
    tile = min(counter)
    if counter[tile] >= 3:
        rest = Counter(counter)
        rest[tile] -= 3
        for tail in decompose(+rest, need_sets - 1):
            results.append([("tri", tile)] + tail)
    if tile < CHOW_MAX and tile % 9 <= 6:
        a, b, c = tile, tile + 1, tile + 2
        if counter.get(b) and counter.get(c):
            rest = Counter(counter)
            rest[a] -= 1
            rest[b] -= 1
            rest[c] -= 1
            for tail in decompose(+rest, need_sets - 1):
                results.append([("chi", a)] + tail)
    return results


def meld_to_set(meld):
    """把副露（room 存储格式）转换成计分用的集合描述。"""
    tiles = sorted(meld["tiles"])
    kind = meld["type"]
    if kind == "chi":
        return {"kind": "chi", "start": tiles[0], "concealed": False, "kong": False}
    return {"kind": "tri", "start": tiles[0],
            "concealed": kind == "angang", "kong": kind in ("gang", "angang")}


def _set_tiles(s):
    """集合里的实际牌（明暗杠 4 张，其余 3 张）。"""
    if s["kind"] == "chi":
        return [s["start"], s["start"] + 1, s["start"] + 2]
    return [s["start"]] * (4 if s["kong"] else 3)


_SHISANYAO = frozenset({0, 8, 9, 17, 18, 26} | set(WINDS) | set(DRAGONS))
_JIULIAN_BASE = {0: 3, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 3}


def win_forms(tiles, melds):
    """手牌（含和牌张）+ 副露的全部和牌形式。

    返回 form 列表：{"type", "sets", "pair", "counter", "block"}；sets 为
    {"kind","start","concealed","kong"}。副露非空时只有标准形式。
    """
    counter = Counter(t for t in tiles if t < FLOWER_MIN)
    meld_sets = [meld_to_set(m) for m in melds]
    k = len(meld_sets)
    forms = []

    for pair in [c for c in counter if counter[c] >= 2]:
        rest = Counter(counter)
        rest[pair] -= 2
        for parts in decompose(+rest, 4 - k):
            sets = meld_sets + [
                {"kind": kind, "start": start, "concealed": True, "kong": False}
                for kind, start in parts
            ]
            forms.append({"type": "std", "sets": sets, "pair": pair,
                          "counter": counter, "block": frozenset()})
    if k == 0:
        if len(counter) == 7 and all(v % 2 == 0 for v in counter.values()):
            forms.append({"type": "qidui", "sets": [], "pair": None,
                          "counter": counter, "block": frozenset()})
            if len({c // 9 for c in counter}) == 1 \
                    and sorted(c % 9 for c in counter) == list(range(7)):
                forms.append({
                    "type": "lianqidui", "sets": [], "pair": None,
                    "counter": counter,
                    "block": frozenset({"七对", "清一色", "门前清", "不求人"}),
                })
        if len(counter) == 13 and set(counter) == _SHISANYAO \
                and sorted(counter.values()) == [1] * 12 + [2]:
            forms.append({
                "type": "shisanyao", "sets": [], "pair": None,
                "counter": counter,
                "block": frozenset({"混幺九", "门前清", "不求人"}),
            })
        if len(counter) == 9 and len({c // 9 for c in counter}) == 1:
            suit = next(iter(counter)) // 9
            counts = {c % 9: v for c, v in counter.items()}
            if any(counts == {n: (_JIULIAN_BASE[n] + (1 if n == win else 0))
                              for n in range(9)}
                   for win in range(9)):
                forms.append({
                    "type": "jiulian", "sets": [], "pair": None,
                    "counter": counter,
                    "block": frozenset({"清一色", "门前清", "不求人"}),
                })
    return forms


def win_shape_of(form, win_tile):
    """和牌张在该形式中的位置：边张 / 坎张 / 单钓将（其余 None）。"""
    if form["type"] == "shisanyao":
        return "diao"
    if form["type"] != "std" or win_tile is None:
        return None
    if form["pair"] == win_tile:
        return "diao"
    for s in form["sets"]:
        if s["kind"] == "chi" and s["start"] // 9 == win_tile // 9:
            start_n = s["start"] % 9
            win_n = win_tile % 9
            if start_n <= win_n <= start_n + 2:
                if win_n == start_n + 2 and start_n == 0:
                    return "bian"       # 12 等 3
                if win_n == start_n and start_n == 6:
                    return "bian"       # 89 等 7
                if win_n == start_n + 1:
                    return "kan"        # 嵌张
                return None             # 两面/多面听不计
    return None


# =========================================================
# 番种计算
# =========================================================

# 就高不就低：接受左侧番种后，右侧番种不再计分。
EXCLUDES = {
    "九莲宝灯": {"清一色", "门前清", "不求人"},
    "连七对": {"七对", "清一色", "门前清", "不求人"},
    "十三幺": {"混幺九"},
    "清幺九": {"碰碰和", "混幺九", "清一色"},
    "混幺九": {"碰碰和"},
    "字一色": {"碰碰和"},
    "绿一色": {"混一色"},
    "四暗刻": {"三暗刻", "双暗刻", "碰碰和"},
    "三暗刻": {"双暗刻"},
    "大四喜": {"三风刻"},
    "小四喜": {"三风刻"},
    "大三元": {"箭刻"},
    "小三元": {"箭刻"},
    "三同刻": {"双同刻", "喜相逢"},
    "一色四同顺": {"一色三同顺"},
    "一色四节高": {"一色三节高"},
    "一色四步高": {"一色三步高"},
    "四杠": {"三杠", "双明杠", "双暗杠", "明杠", "暗杠"},
    "三杠": {"双明杠", "双暗杠", "明杠", "暗杠"},
    "双明杠": {"明杠"},
    "双暗杠": {"暗杠", "双暗刻"},
    "全大": {"大于五"},
    "全小": {"小于五"},
    "全中": {"全带五", "断幺"},
    "全双刻": {"碰碰和", "断幺"},
    "全带五": {"断幺"},
    "断幺": {"无字", "幺九刻"},
    "清一色": {"缺一门"},
}

# 情景番种：只由和牌方式决定，总是累加，不参与牌型冲突消解。
SITUATIONAL = {"自摸", "门前清", "不求人", "全求人", "妙手回春", "海底捞月",
               "杠上开花", "抢杠胡", "和绝张", "边张", "坎张", "单钓将", "花牌"}


def _situational_fans(form, ctx):
    fans = []
    blocked = form["block"]
    if ctx["zimo"]:
        fans.append(("自摸", 1))
    if ctx["menqing"]:
        fans.append(("门前清", 2))
        if ctx["zimo"]:
            fans.append(("不求人", 6))
    elif ctx["all_exposed"] and not ctx["zimo"]:
        fans.append(("全求人", 4))
    if ctx["haidi"]:
        fans.append(("妙手回春" if ctx["zimo"] else "海底捞月", 8))
    if ctx["zimo"] and ctx["gang_draw"]:
        fans.append(("杠上开花", 8))
    if ctx["qianggang"]:
        fans.append(("抢杠胡", 8))
    if ctx["juezhang"]:
        fans.append(("和绝张", 6))
    shape = win_shape_of(form, ctx["win_tile"])
    if shape == "bian":
        fans.append(("边张", 1))
    elif shape == "kan":
        fans.append(("坎张", 1))
    elif shape == "diao":
        fans.append(("单钓将", 1))
    fans.extend([("花牌", 1)] * ctx["flowers"])
    return [f for f in fans if f[0] not in blocked]


def _has_consecutive(starts, k):
    uniq = sorted(set(starts))
    return any(uniq[i + k - 1] - uniq[i] == k - 1 for i in range(len(uniq) - k + 1))


def _std_fans(form, ctx):
    """标准形式（4 副顺刻 + 将）的结构番种。"""
    fans = []
    sets = form["sets"]
    pair = form["pair"]
    tris = [s for s in sets if s["kind"] == "tri"]
    chis = [s for s in sets if s["kind"] == "chi"]
    all_tiles = [t for s in sets for t in _set_tiles(s)] + [pair] * 2
    counts = Counter(all_tiles)
    number_tiles = [t for t in all_tiles if t < HONOR_MIN]
    suits_used = {t // 9 for t in number_tiles}
    has_honor = any(t >= HONOR_MIN for t in all_tiles)
    has_terminal = any(t < HONOR_MIN and t % 9 in TERMINAL_NS for t in all_tiles)
    all_tri = len(tris) == 4
    all_chi = len(chis) == 4
    chi_starts = [s["start"] for s in chis]
    tri_starts = [s["start"] for s in tris if s["start"] < HONOR_MIN]

    # ---- 一色系 / 特殊全体 ----
    if not has_honor and len(suits_used) == 1:
        fans.append(("清一色", 24))
    elif has_honor and len(suits_used) == 1:
        fans.append(("混一色", 8))
    if has_honor and not number_tiles:
        fans.append(("字一色", 64))
    if all(t in GREEN_TILES for t in all_tiles):
        fans.append(("绿一色", 88))
    if all(t in UPSIDE_TILES for t in all_tiles):
        fans.append(("推不倒", 8))
    if all_tri and not has_honor and all(t % 9 in TERMINAL_NS for t in all_tiles):
        fans.append(("清幺九", 88))
    if all_tri and not any(t < HONOR_MIN and t % 9 not in TERMINAL_NS
                           for t in all_tiles):
        fans.append(("混幺九", 32))

    # ---- 字牌刻子与风箭牌型 ----
    wind_tris = [s["start"] for s in tris if s["start"] in WINDS]
    dragon_tris = [s["start"] for s in tris if s["start"] in DRAGONS]
    for _ in dragon_tris:
        fans.append(("箭刻", 2))
    for w in wind_tris:
        if w == ctx["round_wind"]:
            fans.append(("圈风刻", 2))
        if w == ctx["seat_wind"]:
            fans.append(("门风刻", 2))
    if len(set(wind_tris)) == 4:
        fans.append(("大四喜", 88))
    elif len(set(wind_tris)) == 3 and pair in WINDS:
        fans.append(("小四喜", 64))
    if len(wind_tris) >= 3:
        fans.append(("三风刻", 12))
    if set(DRAGONS) <= set(dragon_tris):
        fans.append(("大三元", 88))
    elif len(set(dragon_tris)) == 2 and pair in DRAGONS:
        fans.append(("小三元", 64))

    # ---- 刻/杠系列 ----
    concealed_tris = [s for s in tris if s["concealed"]]
    if len(concealed_tris) >= 2:
        fans.append(("双暗刻", 4))
    if len(concealed_tris) >= 3:
        fans.append(("三暗刻", 16))
    if len(concealed_tris) >= 4:
        fans.append(("四暗刻", 64))
    kongs = [s for s in tris if s["kong"]]
    ming_k = sum(1 for s in kongs if not s["concealed"])
    an_k = len(kongs) - ming_k
    if len(kongs) >= 3:
        fans.append(("四杠" if len(kongs) >= 4 else "三杠",
                     88 if len(kongs) >= 4 else 32))
    if ming_k >= 2:
        fans.append(("双明杠", 4))
    if an_k >= 2:
        fans.append(("双暗杠", 6))
    for _ in range(ming_k):
        fans.append(("明杠", 1))
    for _ in range(an_k):
        fans.append(("暗杠", 2))
    for s in tris:
        if s["start"] < HONOR_MIN and s["start"] % 9 in TERMINAL_NS:
            fans.append(("幺九刻", 1))

    # ---- 顺子小组合 ----
    if any(chi_starts.count(start) >= 2 for start in set(chi_starts)):
        fans.append(("一般高", 1))
    for a, b in combinations(chi_starts, 2):
        if a // 9 == b // 9 and abs(a - b) == 3:
            fans.append(("连六", 1))
            break
    for suit in range(3):
        starts = {a % 9 for a in chi_starts if a // 9 == suit}
        if {0, 6} <= starts:
            fans.append(("老少副", 1))
            break
    # 喜相逢 / 双同刻 / 三同刻
    by_num = {}
    for s in tris:
        if s["start"] < HONOR_MIN:
            by_num.setdefault(s["start"] % 9, []).append(s["start"])
    for num, tiles_ in by_num.items():
        suit_set = {t // 9 for t in tiles_}
        if len(suit_set) >= 3:
            fans.append(("三同刻", 16))
        elif len(suit_set) == 2:
            if num in TERMINAL_NS:
                fans.append(("喜相逢", 1))
            else:
                fans.append(("双同刻", 2))
    # 清龙 / 花龙
    if any({0, 3, 6} <= {a % 9 for a in chi_starts if a // 9 == suit}
           for suit in range(3)):
        fans.append(("清龙", 16))
    by_start_n = {}
    for a in chi_starts:
        by_start_n.setdefault(a % 9, set()).add(a // 9)
    zero, mid, top = by_start_n.get(0, set()), by_start_n.get(3, set()), \
        by_start_n.get(6, set())
    if len(zero) == len(mid) == len(top) == 1 and len(zero | mid | top) == 3:
        fans.append(("花龙", 8))
    # 三色三同顺
    for n in set(by_start_n):
        if len(by_start_n[n]) >= 3:
            fans.append(("三色三同顺", 8))
            break
    # 一色三同顺 / 一色四同顺
    for suit in range(3):
        starts = [a % 9 for a in chi_starts if a // 9 == suit]
        if any(starts.count(n) >= 4 for n in set(starts)):
            fans.append(("一色四同顺", 48))
            break
        if any(starts.count(n) >= 3 for n in set(starts)):
            fans.append(("一色三同顺", 24))
            break
    # 一色三节高 / 一色四节高 / 三色三节高
    for suit in range(3):
        starts = [a % 9 for a in tri_starts if a // 9 == suit]
        if _has_consecutive(starts, 4):
            fans.append(("一色四节高", 48))
            break
        if _has_consecutive(starts, 3):
            fans.append(("一色三节高", 24))
            break
    for t1, t2, t3 in combinations(tri_starts, 3):
        if len({t1 // 9, t2 // 9, t3 // 9}) == 3:
            ns = sorted(t % 9 for t in (t1, t2, t3))
            if ns[2] - ns[0] == 2:
                fans.append(("三色三节高", 8))
                break
    # 一色三步高 / 一色四步高 / 三色三步高（步进 1 或 2）
    for suit in range(3):
        uniq = sorted({a % 9 for a in chi_starts if a // 9 == suit})
        for k, value in ((4, 32), (3, 16)):
            if any(all(uniq[i + j] - uniq[i] == j * step for j in range(k))
                   for i in range(len(uniq) - k + 1) for step in (1, 2)):
                fans.append(("一色四步高" if k == 4 else "一色三步高", value))
                break
        else:
            continue
        break
    for c1, c2, c3 in combinations(chi_starts, 3):
        if len({c1 // 9, c2 // 9, c3 // 9}) == 3:
            ns = sorted(c % 9 for c in (c1, c2, c3))
            span = ns[2] - ns[0]
            if span in (2, 4) and ns[1] - ns[0] == span // 2:
                fans.append(("三色三步高", 8))
                break
    # 双龙会
    for suit in range(3):
        starts = sorted(a % 9 for a in chi_starts if a // 9 == suit)
        if starts == [0, 0, 6, 6] and pair % 9 == 4 and pair // 9 == suit:
            fans.append(("一色双龙会", 64))
    if len(by_start_n) == 2 and pair % 9 == 4 and \
            all(v == {0, 6} for v in by_start_n.values()):
        fans.append(("三色双龙会", 16))

    # ---- 全体系 / 组合作法 ----
    if number_tiles and not has_honor:
        ns = {t % 9 for t in number_tiles}
        if ns <= {6, 7, 8}:
            fans.append(("全大", 24))
        if ns == {4}:
            fans.append(("全中", 24))
        if ns <= {0, 1, 2}:
            fans.append(("全小", 24))
        if ns <= {5, 6, 7, 8}:
            fans.append(("大于五", 12))
        if ns <= {0, 1, 2, 3}:
            fans.append(("小于五", 12))
    if all_tri and not has_honor and all(t % 9 in EVEN_NS for t in all_tiles):
        fans.append(("全双刻", 24))
    if _every_set_has(sets, pair, lambda t: t >= HONOR_MIN or t % 9 in TERMINAL_NS):
        fans.append(("全带么", 6))
    if _every_set_has(sets, pair, lambda t: t < HONOR_MIN and t % 9 == 4):
        fans.append(("全带五", 16))
    if not has_terminal and not has_honor:
        fans.append(("断幺", 8))
    if not has_honor:
        fans.append(("无字", 1))
    if number_tiles and len(suits_used) <= 2:
        fans.append(("缺一门", 1))
    if all_tri:
        fans.append(("碰碰和", 8))
    if all_chi and not has_honor and pair < HONOR_MIN:
        fans.append(("平和", 1))
    kong_starts = {s["start"] for s in tris if s["kong"]}
    for code, n in counts.items():
        if n == 4 and code not in kong_starts:
            fans.append(("四归一", 2))
            break
    return fans


def _qidui_fans(form, ctx):
    """七对结构番种（可与清一色等累加）。"""
    counter = form["counter"]
    tiles = [c for c, n in counter.items() for _ in range(n)]
    number_tiles = [t for t in tiles if t < HONOR_MIN]
    suits_used = {t // 9 for t in number_tiles}
    has_honor = any(t >= HONOR_MIN for t in tiles)
    has_terminal = any(t < HONOR_MIN and t % 9 in TERMINAL_NS for t in tiles)
    fans = [("七对", 24)]
    if not has_honor and len(suits_used) == 1:
        fans.append(("清一色", 24))
    elif has_honor and len(suits_used) == 1:
        fans.append(("混一色", 8))
    if has_honor and not number_tiles:
        fans.append(("字一色", 64))
    if all(t in GREEN_TILES for t in tiles):
        fans.append(("绿一色", 88))
    if all(t in UPSIDE_TILES for t in tiles):
        fans.append(("推不倒", 8))
    if not has_terminal and not has_honor:
        fans.append(("断幺", 8))
    if not has_honor:
        fans.append(("无字", 1))
    if number_tiles and len(suits_used) <= 2:
        fans.append(("缺一门", 1))
    return fans


def _every_set_has(sets, pair, predicate):
    groups = [[t for t in _set_tiles(s)] for s in sets] + [[pair, pair]]
    return all(any(predicate(t) for t in group) for group in groups)


def _resolve_fans(fans):
    """就高不就低：按分值从高到低接受，被已接受番种「不计」的跳过。"""
    accepted = []
    blocked = set()
    for name, value in sorted(fans, key=lambda f: -f[1]):
        if name in blocked:
            continue
        accepted.append((name, value))
        blocked |= EXCLUDES.get(name, set())
    return accepted


def score_form(form, ctx):
    """单一形式的番种列表与总分（不含起和门槛判断）。"""
    if form["type"] == "std":
        structural = _std_fans(form, ctx)
    elif form["type"] == "qidui":
        structural = _qidui_fans(form, ctx)
    elif form["type"] == "lianqidui":
        structural = [("连七对", 88)]
    elif form["type"] == "shisanyao":
        structural = [("十三幺", 88)]
    else:
        structural = [("九莲宝灯", 88)]
    accepted = _resolve_fans(structural)
    if form["type"] == "std" and not accepted:
        accepted.append(("无番和", 8))
    fans = accepted + _situational_fans(form, ctx)
    return fans, sum(v for _, v in fans)


def best_score(tiles, melds, ctx):
    """全部形式里总分最高的拆法；不可和返回 None。返回 (fans, total, form)。"""
    best = None
    for form in win_forms(tiles, melds):
        fans, total = score_form(form, ctx)
        if best is None or total > best[1]:
            best = (fans, total, form)
    return best


def waits_for(tiles, melds):
    """听牌集合：13 张（不含和牌张）时再进哪张能和。"""
    if len(tiles) != 13 - 3 * len(melds):
        return []
    counter = Counter(t for t in tiles if t < FLOWER_MIN)
    return [code for code in range(TILE_KINDS)
            if counter[code] < 4 and win_forms(tiles + [code], melds)]


@register_room_type("mahjong")
class MahjongRoom(BaseRoom):
    """一手牌的状态机：发牌 -> 摸打/吃碰杠胡 -> 结算 -> 投票再来。"""

    max_seats = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rules = self.sanitize_rules(self.rules)
        self.game = None
        self.hand_seq = 0
        self.dealer_idx = 0       # 庄家在 ring 中的下标（跨手保持）
        self.round_wind = 0       # 圈风：0东 1南 2西 3北
        self.pause_remaining = 0.0
        self.votes = {}

    @staticmethod
    def sanitize_rules(rules):
        rules = rules if isinstance(rules, dict) else {}
        try:
            min_fan = int(rules.get("min_fan", 8))
        except (TypeError, ValueError):
            min_fan = 8
        if min_fan not in MIN_FAN_CHOICES:
            min_fan = 8
        return {
            "min_fan": min_fan,
            "flowers": bool(rules.get("flowers", True)),
            "chow": bool(rules.get("chow", True)),
            "dianpao_full": bool(rules.get("dianpao_full", True)),
        }

    # ---- 暂停/恢复 ----
    def on_paused(self):
        g = self.game
        if g and g.get("deadline"):
            self.pause_remaining = max(1.0, g["deadline"] - time.time())

    def on_resumed(self):
        g = self.game
        if g and (g.get("to_act") or g.get("phase") == "claim"):
            g["deadline"] = time.time() + (self.pause_remaining or TURN_TIMEOUT)
            self.schedule_turn_timer()
        self.pause_remaining = 0.0

    # ---- 基础查询 ----
    def in_hand(self):
        g = self.game
        return bool(g) and g.get("stage") != "showdown"

    def seat_index(self, username):
        ring = self.game["ring"] if self.game else self.seating
        try:
            return ring.index(username)
        except ValueError:
            return 0

    def meld_sets_of(self, username):
        """该玩家的副露（物理格式，win_forms 内部统一转换）。"""
        return list(self.game["melds"].get(username, []))

    def visible_counts(self):
        """桌面可见牌（牌河 + 副露，含暗杠的明示 4 张）。"""
        g = self.game
        counts = Counter()
        for pile in g["discards"].values():
            counts.update(pile)
        for melds in g["melds"].values():
            for meld in melds:
                counts.update(meld["tiles"])
        return counts

    def make_ctx(self, username, win_tile, zimo, gang_draw=False, qianggang=False):
        """和牌计分的情景：门清、绝张、圈风门风、花牌数等。"""
        g = self.game
        counts = self.visible_counts()
        if not zimo and not qianggang and win_tile is not None:
            counts[win_tile] -= 1
        idx = self.seat_index(username)
        melds = g["melds"].get(username, [])
        exposed = [m for m in melds if m["type"] in ("chi", "peng", "gang")]
        return {
            "win_tile": win_tile,
            "zimo": zimo,
            "haidi": not g["wall"],
            "gang_draw": gang_draw,
            "qianggang": qianggang,
            "juezhang": win_tile is not None and counts[win_tile] >= 3,
            "menqing": not exposed,
            "all_exposed": len(melds) == 4 and len(exposed) == 4,
            "round_wind": WINDS[self.round_wind],
            "seat_wind": WINDS[(idx - self.dealer_idx) % 4],
            "flowers": len(g["flowers"].get(username, [])),
        }

    def zimo_result(self, username, gang_draw=False):
        """自查胡牌：正常摸牌以刚摸的张为和牌张；碰杠后无新张时逐张试。"""
        g = self.game
        hand = g["hands"][username]
        melds = self.meld_sets_of(username)
        candidates = [t for t in dict.fromkeys(hand)]
        draw = g.get("last_draw")
        if draw in hand:
            candidates = [draw]
        best = None
        for tile in candidates:
            ctx = self.make_ctx(username, tile, zimo=True, gang_draw=gang_draw)
            result = best_score(hand, melds, ctx)
            if result and (best is None or result[1] > best[1]):
                best = (result[0], result[1], tile)
        return best

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
            "discards": {},
        }
        for name in self.seating:
            member = self.members[name]
            in_hand = bool(g and name in g.get("hands", {}))
            melds = [
                {"type": m["type"], "tiles": list(m["tiles"]),
                 "from": m.get("from")}
                for m in (g["melds"].get(name, []) if g else [])
            ]
            view["players"].append({
                "username": name,
                "nickname": self.display_name(name),
                "stack": member["stack"],
                "rating": self.player_rating(name),
                "in_hand": in_hand,
                "is_dealer": bool(g and self.seat_index(name) == self.dealer_idx),
                "seat_wind": WIND_NAMES[(self.seat_index(name) - self.dealer_idx) % 4],
                "flowers": len(g["flowers"].get(name, [])) if g else 0,
                "melds": melds,
                "concealed": len(g["hands"].get(name, [])) if in_hand else 0,
            })
            view["discards"][name] = list(g["discards"].get(name, [])) if g else []
        if g:
            view.update({
                "hand_no": g["hand_no"],
                "round_wind": WIND_NAMES[self.round_wind],
                "dealer": g["ring"][self.dealer_idx] if g["ring"] else None,
                "wall_count": len(g["wall"]),
                "phase": g["phase"],
                "to_act": g["to_act"],
                "turn_left": round(max(0, g["deadline"] - time.time()), 1)
                if g["to_act"] or g["phase"] == "claim" else 0,
                "last_discard": g.get("last_discard"),
                "last_action": g.get("last_action"),
                "result": g.get("result"),
                "claim": None,
            })
            claim = g.get("claim")
            if claim:
                view["claim"] = {
                    "tile": claim["tile"],
                    "by": claim["by"],
                    "mode": claim.get("mode", "discard"),
                    "waiting": [name for name in claim["options"]
                                if name not in claim["passed"]
                                and name not in claim["claims"]],
                }
            if username in g.get("hands", {}):
                view["your_hand"] = list(g["hands"][username])
                view["your_draw_index"] = (
                    len(g["hands"][username]) - 1
                    if g["to_act"] == username and g["phase"] == "discard"
                    and g.get("last_draw") is not None else None
                )
                view["your_flowers"] = list(g["flowers"].get(username, []))
                view["your_options"] = self.options_for(username)
                view["tenpai"] = self.tenpai_for(username)
        if self.status == "playing" and not self.in_hand():
            view["settlement"] = {
                "votes": dict(self.votes),
                "total": len(self.seating),
                "can_next": self.can_continue(self.blind),
                "blind": self.blind,
            }
        return view

    # ---- 选项与听牌 ----
    def options_for(self, username):
        g = self.game
        if not g or username not in g.get("hands", {}):
            return {}
        opts = {"discard": False, "angang": [], "bugang": [], "zimo": False,
                "claim": None, "passed": False, "submitted": False}
        if self.paused or not self.in_hand():
            return opts
        hand = g["hands"][username]
        if g["phase"] == "discard" and g["to_act"] == username and not self.paused:
            opts["discard"] = True
            counter = Counter(hand)
            if g["wall"]:
                opts["angang"] = sorted(t for t, n in counter.items() if n == 4)
                peng_tiles = {m["tiles"][0] for m in g["melds"][username]
                              if m["type"] == "peng"}
                opts["bugang"] = sorted(t for t in peng_tiles if counter.get(t))
            best = self.zimo_result(username, gang_draw=g["gang_draw"])
            opts["zimo"] = bool(best) and best[1] >= self.rules["min_fan"]
        elif g["phase"] == "claim" and g.get("claim") \
                and username in g["claim"]["options"]:
            if username in g["claim"]["passed"]:
                opts["passed"] = True
            elif username in g["claim"]["claims"]:
                opts["submitted"] = True
            else:
                mine = g["claim"]["options"][username]
                opts["claim"] = {
                    "hu": mine.get("hu", False),
                    "peng": mine.get("peng", False),
                    "gang": mine.get("gang", False),
                    "chi": [list(pair) for pair in mine.get("chi", [])],
                }
        return opts

    def tenpai_for(self, username):
        """轮不到自己打牌时提示听牌（含可见余张）。"""
        g = self.game
        if not g or username not in g.get("hands", {}):
            return None
        if g["phase"] == "discard" and g["to_act"] == username:
            return None
        hand = g["hands"][username]
        if len(hand) != 13 - 3 * len(g["melds"][username]):
            return None
        waits = waits_for(hand, g["melds"][username])
        if not waits:
            return None
        counts = self.visible_counts()
        counts.update(hand)
        return {
            "waits": waits,
            "remaining": {code: max(0, 4 - counts[code]) for code in waits},
        }

    # ---- 计时 ----
    def schedule_turn_timer(self):
        g = self.game
        if not g or (not g.get("to_act") and g.get("phase") != "claim"):
            return
        self.schedule("turn", max(0.05, g["deadline"] - time.time()), self.auto_action)

    async def auto_action(self):
        g = self.game
        if self.paused or not g or not self.in_hand():
            return
        if g["phase"] == "claim" and g.get("claim"):
            claim = g["claim"]
            undecided = [name for name in claim["options"]
                         if name not in claim["passed"]
                         and name not in claim["claims"]]
            for name in undecided:
                claim["passed"].add(name)
                g["last_action"] = {
                    "username": name,
                    "nickname": self.display_name(name),
                    "text": "超时自动放弃",
                }
            await self._resolve_claims()
            return
        username = g.get("to_act")
        if username and username in g.get("hands", {}):
            hand = g["hands"][username]
            tile = g.get("last_draw")
            if tile is None or tile not in hand:
                tile = hand[-1]
            await self.perform_action(
                username, "discard", {"index": hand.index(tile)}, auto=True)

    # ---- 一手牌状态机 ----
    def note_leave(self, username):
        g = self.game
        mid_hand = self.in_hand() and username in g.get("hands", {})
        if mid_hand:
            g["broken"] = True
        return mid_hand

    def pending_refunds(self):
        return {name: member["stack"] for name, member in self.members.items()}

    async def start(self):
        eligible = self.members_with_chips()
        if len(eligible) != 4:
            raise ValueError("麻将需要正好 4 名有筹码的玩家才能开局")
        self.status = "playing"
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
        self.begin_rating_hand(eligible)
        ring = list(eligible)
        self.dealer_idx %= len(ring)
        self.game = {
            "hand_no": self.hand_seq + 1,
            "stage": "play",
            "ring": ring,
            "wall": build_wall(self.rules["flowers"]),
            "hands": {name: [] for name in ring},
            "flowers": {name: [] for name in ring},
            "melds": {name: [] for name in ring},
            "discards": {name: [] for name in ring},
            "to_act": None,
            "phase": "discard",
            "last_draw": None,
            "gang_draw": False,
            "claim": None,
            "deadline": 0,
            "last_discard": None,
            "last_action": None,
            "result": None,
            "broken": False,
        }
        self.hand_seq = self.game["hand_no"]
        g = self.game
        for name in ring:
            g["hands"][name] = [g["wall"].pop(0) for _ in range(13)]
        for name in ring:
            if not self._strip_flowers(name):
                await self._end_hand_draw()
                return
        g["last_action"] = {
            "username": g["ring"][self.dealer_idx],
            "nickname": self.display_name(g["ring"][self.dealer_idx]),
            "text": f"坐庄 · {WIND_NAMES[self.round_wind]}风圈",
        }
        if not await self._deal_to(ring[self.dealer_idx]):
            return
        await self.broadcast_views()

    def _strip_flowers(self, username):
        """开局把手里的花牌换成杠尾牌。返回 False 表示牌墙耗尽（荒庄）。"""
        g = self.game
        hand = g["hands"][username]
        while True:
            flower = next((t for t in hand if t >= FLOWER_MIN), None)
            if flower is None:
                return True
            hand.remove(flower)
            g["flowers"][username].append(flower)
            tile = self._take_replacement(username)
            if tile is None:
                return False
            hand.append(tile)

    def _take_replacement(self, username):
        """杠/补花从杠尾摸一张，摸到花自动再补。牌墙空返回 None。"""
        g = self.game
        while g["wall"]:
            tile = g["wall"].pop()
            if tile < FLOWER_MIN:
                return tile
            g["flowers"][username].append(tile)
        return None

    async def _deal_to(self, username):
        """当前行动者从牌墙正面摸牌进入打牌阶段；摸到花补杠尾。"""
        g = self.game
        if not g["wall"]:
            await self._end_hand_draw()
            return False
        tile = g["wall"].pop(0)
        while tile >= FLOWER_MIN:
            g["flowers"][username].append(tile)
            replacement = self._take_replacement(username)
            if replacement is None:
                await self._end_hand_draw()
                return False
            tile = replacement
        g["hands"][username].append(tile)
        g["to_act"] = username
        g["phase"] = "discard"
        g["claim"] = None
        g["last_draw"] = tile
        g["gang_draw"] = False
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        return True

    async def perform_action(self, username, action, data=None, auto=False):
        g = self.game
        if not g or self.paused or not self.in_hand():
            return
        data = data or {}
        if action == "discard":
            await self.act_discard(username, data, auto)
        elif action == "angang":
            await self.act_angang(username, data)
        elif action == "bugang":
            await self.act_bugang(username, data)
        elif action == "hu":
            await self.act_zimo(username)
        elif action == "claim":
            await self.act_claim(username, data)
        elif action == "pass":
            if g["phase"] == "claim":
                await self.pass_claim(username)

    def take_index(self, username, data):
        """校验手牌下标，返回牌编码；非法返回 None。"""
        g = self.game
        hand = g["hands"].get(username, [])
        index = data.get("index")
        if type(index) is not int:
            return None
        if not 0 <= index < len(hand):
            return None
        return hand[index]

    async def act_discard(self, username, data, auto=False):
        g = self.game
        if g["phase"] != "discard" or g["to_act"] != username:
            return
        tile = self.take_index(username, data)
        if tile is None:
            return
        g["hands"][username].remove(tile)
        g["discards"][username].append(tile)
        g["last_draw"] = None
        g["gang_draw"] = False
        g["last_discard"] = {"by": username, "tile": tile}
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": ("超时自动" if auto else "") + f"打出 {tile_name(tile)}",
        }
        logger.info("mahjong %s@%s: discard %s", username, self.id, tile_name(tile))
        await self._open_claims(username, tile)

    def claim_options(self, discarder, tile):
        """打出的牌能被谁吃/碰/杠/胡（胡需达到起和分）。"""
        g = self.game
        options = {}
        ring = g["ring"]
        disc_idx = ring.index(discarder)
        for name in ring:
            if name == discarder or name not in g["hands"]:
                continue
            hand = g["hands"][name]
            counter = Counter(hand)
            entry = {}
            if counter.get(tile, 0) >= 2:
                entry["peng"] = True
            if counter.get(tile, 0) >= 3 and g["wall"]:
                entry["gang"] = True
            if self.rules["chow"] and tile < HONOR_MIN \
                    and ring[(disc_idx + 1) % 4] == name:
                chis = []
                base = tile - tile % 9
                for start in (tile - 2, tile - 1, tile):
                    if start < base or start + 2 > base + 8:
                        continue
                    others = [t for t in (start, start + 1, start + 2) if t != tile]
                    if all(counter.get(t, 0) >= 1 for t in others):
                        chis.append(others)
                if chis:
                    entry["chi"] = chis
            ctx = self.make_ctx(name, tile, zimo=False)
            best = best_score(hand + [tile], self.meld_sets_of(name), ctx)
            if best and best[1] >= self.rules["min_fan"]:
                entry["hu"] = True
            if entry:
                options[name] = entry
        return options

    async def _open_claims(self, discarder, tile):
        g = self.game
        options = self.claim_options(discarder, tile)
        if not options:
            await self._advance(discarder)
            return
        g["phase"] = "claim"
        g["claim"] = {
            "mode": "discard",
            "tile": tile,
            "by": discarder,
            "options": options,
            "passed": set(),
            "claims": {},
        }
        g["deadline"] = time.time() + CLAIM_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def _advance(self, after):
        g = self.game
        g["claim"] = None
        ring = g["ring"]
        index = ring.index(after) if after in ring else -1
        await self._deal_to(ring[(index + 1) % 4])
        await self.broadcast_views()

    async def pass_claim(self, username):
        g = self.game
        claim = g.get("claim")
        if not claim or username not in claim["options"] or self.paused:
            return
        if username in claim["passed"] or username in claim["claims"]:
            return
        claim["passed"].add(username)
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": "放弃",
        }
        await self._resolve_claims()

    async def act_claim(self, username, data):
        g = self.game
        claim = g.get("claim")
        if not claim or username not in claim["options"] or self.paused:
            return
        if username in claim["passed"] or username in claim["claims"]:
            return
        kind = str(data.get("kind") or "")
        mine = claim["options"][username]
        if kind == "hu":
            if not mine.get("hu"):
                return
            claim["claims"][username] = "hu"
        elif kind in ("peng", "gang") and mine.get(kind):
            claim["claims"][username] = kind
        elif kind == "chi" and mine.get("chi"):
            pair = sorted(data.get("tiles") or [])
            if pair not in [sorted(p) for p in mine["chi"]]:
                return
            claim["claims"][username] = ("chi", pair)
        else:
            return
        await self._resolve_claims()

    async def _resolve_claims(self):
        """吃碰杠在全员表态后按 胡>杠>碰>吃 结算；有人胡则立即胡。"""
        g = self.game
        claim = g.get("claim")
        if not claim:
            return
        undecided = [name for name in claim["options"]
                     if name not in claim["passed"] and name not in claim["claims"]]
        if undecided:
            await self.broadcast_views()
            return
        hus = [name for name, kind in claim["claims"].items() if kind == "hu"]
        if hus:
            ring = g["ring"]
            by = claim["by"]
            hus.sort(key=lambda name: (ring.index(name) - ring.index(by)) % 4)
            await self._win_on_discard(hus[0], claim["tile"], claim["by"],
                                       qianggang=claim["mode"] == "bugang")
            return
        if claim["mode"] == "bugang":
            await self._apply_bugang(claim["by"], claim["tile"])
            return
        # 吃的声明是 ("chi", 两张手牌) 元组，其余是字符串
        priority = {"gang": 3, "peng": 2, "chi": 1}
        takers = []
        for name, declared in claim["claims"].items():
            kind = declared[0] if isinstance(declared, tuple) else declared
            if kind in priority:
                takers.append((name, kind))
        if not takers:
            await self._advance(claim["by"])
            return
        takers.sort(key=lambda item: (-priority[item[1]], g["ring"].index(item[0])))
        name, kind = takers[0]
        if kind == "chi":
            await self._apply_chi(name, claim["by"], claim["tile"],
                                  claim["claims"][name][1])
        elif kind == "peng":
            await self._apply_peng(name, claim["by"], claim["tile"])
        else:
            await self._apply_gang(name, claim["by"], claim["tile"])

    # ---- 吃碰杠的具体执行 ----
    def _pop_discard(self, discarder, tile):
        pile = self.game["discards"][discarder]
        if pile and pile[-1] == tile:
            pile.pop()
        self.game["last_discard"] = None

    def _enter_discard_phase(self, username, text):
        g = self.game
        g["claim"] = None
        g["phase"] = "discard"
        g["to_act"] = username
        g["last_draw"] = None
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": text,
        }
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()

    async def _apply_peng(self, username, discarder, tile):
        g = self.game
        hand = g["hands"][username]
        for _ in range(2):
            hand.remove(tile)
        self._pop_discard(discarder, tile)
        g["melds"][username].append(
            {"type": "peng", "tiles": [tile] * 3, "from": discarder})
        self._enter_discard_phase(username, f"碰 {tile_name(tile)}")
        await self.broadcast_views()

    async def _apply_chi(self, username, discarder, tile, pair):
        g = self.game
        hand = g["hands"][username]
        for t in pair:
            hand.remove(t)
        self._pop_discard(discarder, tile)
        tiles = sorted(pair + [tile])
        g["melds"][username].append({"type": "chi", "tiles": tiles, "from": discarder})
        self._enter_discard_phase(
            username, f"吃 {''.join(tile_name(t) for t in tiles)}")
        await self.broadcast_views()

    async def _apply_gang(self, username, discarder, tile):
        g = self.game
        hand = g["hands"][username]
        for _ in range(3):
            hand.remove(tile)
        self._pop_discard(discarder, tile)
        g["melds"][username].append(
            {"type": "gang", "tiles": [tile] * 4, "from": discarder})
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": f"明杠 {tile_name(tile)}",
        }
        await self._after_kong(username)

    async def act_angang(self, username, data):
        g = self.game
        if g["phase"] != "discard" or g["to_act"] != username or not g["wall"]:
            return
        tile = self.take_index(username, data)
        if tile is None or Counter(g["hands"][username])[tile] < 4:
            return
        hand = g["hands"][username]
        for _ in range(4):
            hand.remove(tile)
        g["melds"][username].append(
            {"type": "angang", "tiles": [tile] * 4, "from": None})
        g["last_action"] = {
            "username": username,
            "nickname": self.display_name(username),
            "text": f"暗杠 {tile_name(tile)}",
        }
        await self._after_kong(username)

    async def act_bugang(self, username, data):
        g = self.game
        if g["phase"] != "discard" or g["to_act"] != username or not g["wall"]:
            return
        tile = self.take_index(username, data)
        peng = next((m for m in g["melds"][username]
                     if m["type"] == "peng" and m["tiles"][0] == tile), None)
        if tile is None or peng is None:
            return
        options = {}
        for name in g["ring"]:
            if name == username or name not in g["hands"]:
                continue
            ctx = self.make_ctx(name, tile, zimo=False, qianggang=True)
            best = best_score(g["hands"][name] + [tile],
                              self.meld_sets_of(name), ctx)
            if best and best[1] >= self.rules["min_fan"]:
                options[name] = {"hu": True}
        if not options:
            await self._apply_bugang(username, tile)
            return
        g["phase"] = "claim"
        g["claim"] = {
            "mode": "bugang",
            "tile": tile,
            "by": username,
            "options": options,
            "passed": set(),
            "claims": {},
        }
        g["deadline"] = time.time() + CLAIM_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def _apply_bugang(self, username, tile):
        g = self.game
        g["hands"][username].remove(tile)
        peng = next(m for m in g["melds"][username]
                    if m["type"] == "peng" and m["tiles"][0] == tile)
        peng["type"] = "gang"
        peng["tiles"] = [tile] * 4
        g["claim"] = None
        await self._after_kong(username)

    async def _after_kong(self, username):
        """杠后从杠尾补牌；无牌可补则荒庄。"""
        g = self.game
        tile = self._take_replacement(username)
        if tile is None:
            await self._end_hand_draw()
            return
        g["hands"][username].append(tile)
        g["claim"] = None
        g["phase"] = "discard"
        g["to_act"] = username
        g["last_draw"] = tile
        g["gang_draw"] = True
        g["deadline"] = time.time() + TURN_TIMEOUT
        self.schedule_turn_timer()
        await self.broadcast_views()

    async def act_zimo(self, username):
        g = self.game
        if g["phase"] != "discard" or g["to_act"] != username:
            return
        best = self.zimo_result(username, gang_draw=g["gang_draw"])
        if not best or best[1] < self.rules["min_fan"]:
            return
        await self._end_hand_win(username, None, best[2], True, best[0], best[1])

    async def _win_on_discard(self, username, tile, discarder, qianggang=False):
        g = self.game
        ctx = self.make_ctx(username, tile, zimo=False, qianggang=qianggang)
        best = best_score(g["hands"][username] + [tile],
                          self.meld_sets_of(username), ctx)
        if not best or best[1] < self.rules["min_fan"]:
            return
        if not qianggang:
            self._pop_discard(discarder, tile)
        else:
            g["hands"][discarder].remove(tile)
        g["hands"][username].append(tile)
        await self._end_hand_win(username, discarder, tile, False, best[0], best[1])

    # ---- 结束与结算 ----
    def enter_settlement(self):
        self.votes = {}
        self.schedule("settle", SETTLE_TIMEOUT, self.settle_timeout)

    def can_continue(self, blind):
        return len(self.seating) >= 4 and all(
            member["stack"] > 0 for member in self.members.values())

    async def settle_timeout(self):
        if self.paused or self.in_hand() or self.status != "playing":
            return
        await self.execute_decision("next", self.blind)

    async def cast_vote(self, username, choice, blind):
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

    def _rotate_dealer(self, dealer_won):
        """庄家胡牌连庄；否则下家坐庄，庄位轮回原点时圈风进一步。"""
        if dealer_won:
            return
        old = self.dealer_idx
        self.dealer_idx = (self.dealer_idx + 1) % 4
        if self.dealer_idx == 0 or self.dealer_idx <= old:
            self.round_wind = (self.round_wind + 1) % 4

    def _build_result(self):
        g = self.game
        return {
            "hand_no": g["hand_no"],
            "hands": {name: list(g["hands"].get(name, [])) for name in g["ring"]},
            "melds": {name: [dict(m) for m in g["melds"].get(name, [])]
                      for name in g["ring"]},
            "flowers": {name: list(g["flowers"].get(name, [])) for name in g["ring"]},
        }

    async def _finish_hand(self, result):
        g = self.game
        self.paused = False
        self.pause_remaining = 0.0
        g["stage"] = "showdown"
        g["to_act"] = None
        g["deadline"] = 0
        g["claim"] = None
        g["result"] = result
        self.enter_settlement()
        await self.broadcast_payload(
            {"type": "hand_result", "room_id": self.id, **result})
        await self.broadcast_views()
        await self.on_rooms_changed()

    async def _end_hand_draw(self):
        """荒庄流局：不计分，庄家连庄。"""
        g = self.game
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        result = self._build_result()
        result.update({"draw_game": True, "aborted": False, "winner": None,
                       "fans": [], "fan_total": 0, "payouts": {}, "gains": {},
                       "dealer_repeat": True})
        logger.info("mahjong hand #%d draw in room %s", g["hand_no"], self.id)
        await self._finish_hand(result)

    async def _end_hand_aborted(self):
        g = self.game
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        result = self._build_result()
        result.update({"draw_game": False, "aborted": True, "winner": None,
                       "fans": [], "fan_total": 0, "payouts": {}, "gains": {},
                       "dealer_repeat": False})
        logger.info("mahjong hand #%d aborted in room %s", g["hand_no"], self.id)
        await self._finish_hand(result)

    async def _end_hand_win(self, winner, loser, win_tile, zimo, fans, total):
        g = self.game
        if not self.in_hand():
            return
        self.cancel_timer("turn")
        unit = round(self.blind * total, 2)
        payouts = {}
        if zimo:
            for name in g["ring"]:
                if name == winner or name not in self.members:
                    continue
                pay = round(min(self.members[name]["stack"], unit), 2)
                if pay > 0:
                    payouts[name] = pay
        else:
            pay = round(min(self.members[loser]["stack"],
                            unit * (3 if self.rules["dianpao_full"] else 1)), 2)
            if pay > 0:
                payouts[loser] = pay
        gain = round(sum(payouts.values()), 2)
        endings = {
            name: round(member["stack"] - payouts.get(name, 0)
                        + (gain if name == winner else 0), 2)
            for name, member in self.members.items()
        }
        ratings = self.settle_ratings(endings)
        for name, amount in endings.items():
            self.members[name]["stack"] = amount
        self.stacks_changed()
        dealer_won = self.seat_index(winner) == self.dealer_idx
        self._rotate_dealer(dealer_won)
        result = self._build_result()
        result.update({
            "draw_game": False,
            "aborted": False,
            "winner": winner,
            "winner_name": self.display_name(winner),
            "loser": loser,
            "win_tile": win_tile,
            "zimo": zimo,
            "fans": [{"name": name, "value": value} for name, value in fans],
            "fan_total": total,
            "unit": unit,
            "payouts": payouts,
            "gains": {winner: gain} if gain else {},
            "ratings": ratings,
            "dealer_repeat": dealer_won,
            "round_wind": WIND_NAMES[self.round_wind],
        })
        logger.info("mahjong hand #%d won by %s in room %s, fan=%s, payouts=%s",
                    g["hand_no"], winner, self.id, total, payouts)
        await self._finish_hand(result)

    async def progress_game(self):
        """宿主在成员离开后调用：麻将有人离场本手作废。"""
        g = self.game
        if not g or not self.in_hand():
            return
        if g.get("broken") or any(name not in self.members for name in g["ring"]):
            await self._end_hand_aborted()
            return
        if not g.get("to_act") or g["to_act"] not in self.members:
            nxt = next((name for name in g["ring"] if name in self.members), None)
            if nxt is None:
                await self._end_hand_aborted()
                return
            g["to_act"] = nxt
            g["deadline"] = time.time() + TURN_TIMEOUT
            self.schedule_turn_timer()
        await self.broadcast_views()

    async def restart(self):
        """重新开始：收回本局手牌重新发牌（庄位圈风保留）。"""
        self.game = None
        self.paused = False
        self.votes = {}
        await self.broadcast_payload({"type": "game_restart"})
        await self.start_hand()
