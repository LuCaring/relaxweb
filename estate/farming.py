"""小胖庄园农场：购买、播种、收获与出售。

金币、库存与等级变更一律通过 ``estate.store`` 的内核原语完成，
每个操作经 ``store.run_action`` 保证幂等。
"""
from estate.catalog import (
    BAITS, CROPS, LAND_LEVELS, PLOT_UNLOCKS, WAREHOUSE_LEVELS,
    bait_item, crop_item, grow_seconds, item_info, seed_item,
)
from estate.store import (
    CROP_DATA_BROKEN, LEVEL_LOCKED, PLOT_LOCKED, PLOT_NOT_FOUND,
    award_xp, change_inventory, credit, debit, estate_error, inventory_rows,
    load_profile, plot_index, positive_int, require_capacity, run_action,
)


def _ensure_level(profile, rule):
    if profile["level"] < rule["unlock_level"]:
        raise estate_error(LEVEL_LOCKED)


def _next_tier(table, current_level, too_high):
    """返回 (当前档规则, 下一档等级)；已在顶层则报 ``max_level``。"""
    current = table.get(current_level)
    next_level = current_level + 1
    if not current or current.get("upgrade_price") is None or next_level not in table:
        raise estate_error(too_high)
    return current, next_level


def _plot_row(conn, username, index):
    row = conn.execute(
        "SELECT land_level,crop_id FROM estate_plots WHERE username=? AND plot_index=?",
        (username, index),
    ).fetchone()
    if not row:
        raise estate_error(PLOT_NOT_FOUND)
    return row


# 种子与鱼饵只在目录表、取价字段、item 前缀和文案后缀上有差别，共用一个实现。
_CONSUMABLES = {
    "seed": {"table": CROPS, "price_key": "seed_price", "item_of": seed_item,
             "suffix": "种子", "unknown": ("unknown_item", "种子不存在")},
    "bait": {"table": BAITS, "price_key": "price", "item_of": bait_item,
             "suffix": "", "unknown": ("unknown_item", "鱼饵不存在")},
}


def _buy_consumable(conn, username, profile, request_id, adjust_coins, kind, item_id, quantity):
    spec = _CONSUMABLES[kind]
    entry = spec["table"].get(str(item_id))
    count = positive_int(quantity, maximum=999)
    if not entry:
        raise estate_error(spec["unknown"])
    _ensure_level(profile, entry)
    require_capacity(conn, username, profile, count)
    total = round(entry[spec["price_key"]] * count, 2)
    balance = debit(adjust_coins, conn, username, total,
                    f"小胖庄园购买：{entry['name']}{spec['suffix']} ×{count}", request_id)
    change_inventory(conn, username, spec["item_of"](str(item_id)), count)
    return {"action": "buy", "kind": kind, "item_id": item_id,
            "quantity": count, "cost": total, "coins": balance}


def _buy_plot(conn, username, profile, request_id, adjust_coins, item_id):
    index = plot_index(item_id)
    if index != profile["plot_count"] or index not in PLOT_UNLOCKS:
        raise estate_error(("plot_unavailable", "该土地当前无法购买"))
    rule = PLOT_UNLOCKS[index]
    _ensure_level(profile, rule)
    balance = debit(adjust_coins, conn, username, rule["price"],
                    f"小胖庄园购买：第 {index + 1} 块土地", request_id)
    conn.execute("UPDATE estate_profiles SET plot_count=plot_count+1 WHERE username=?",
                 (username,))
    return {"action": "buy", "kind": "plot", "item_id": index,
            "quantity": 1, "cost": rule["price"], "coins": balance}


def _buy_land(conn, username, profile, request_id, adjust_coins, item_id):
    index = plot_index(item_id)
    if index >= profile["plot_count"]:
        raise estate_error(PLOT_LOCKED)
    row = _plot_row(conn, username, index)
    if row[1] is not None:
        raise estate_error(("plot_busy", "有作物时不能升级土地"))
    current, next_level = _next_tier(LAND_LEVELS, row[0],
                                     ("max_level", "土地已达到最高等级"))
    _ensure_level(profile, current)
    balance = debit(adjust_coins, conn, username, current["upgrade_price"],
                    f"小胖庄园升级：第 {index + 1} 块土地", request_id)
    conn.execute("UPDATE estate_plots SET land_level=? WHERE username=? AND plot_index=?",
                 (next_level, username, index))
    return {"action": "buy", "kind": "land", "item_id": index,
            "land_level": next_level, "cost": current["upgrade_price"], "coins": balance}


def _buy_warehouse(conn, username, profile, request_id, adjust_coins):
    current, next_level = _next_tier(WAREHOUSE_LEVELS, profile["warehouse_level"],
                                     ("max_level", "仓库已达到最高等级"))
    _ensure_level(profile, current)
    balance = debit(adjust_coins, conn, username, current["upgrade_price"],
                    "小胖庄园升级：仓库扩容", request_id)
    conn.execute("UPDATE estate_profiles SET warehouse_level=? WHERE username=?",
                 (next_level, username))
    return {"action": "buy", "kind": "warehouse", "item_id": next_level,
            "warehouse_level": next_level, "cost": current["upgrade_price"],
            "coins": balance}


def buy(conn, username, request_id, item_kind, item_id, quantity, now, adjust_coins):
    item_kind = str(item_kind or "")
    payload = {"kind": item_kind, "item_id": item_id, "quantity": quantity}

    def mutate():
        profile = load_profile(conn, username)
        if item_kind in _CONSUMABLES:
            return _buy_consumable(conn, username, profile, request_id,
                                   adjust_coins, item_kind, item_id, quantity)
        if item_kind == "plot":
            return _buy_plot(conn, username, profile, request_id, adjust_coins, item_id)
        if item_kind == "land":
            return _buy_land(conn, username, profile, request_id, adjust_coins, item_id)
        if item_kind == "warehouse":
            return _buy_warehouse(conn, username, profile, request_id, adjust_coins)
        raise estate_error(("unknown_purchase", "未知购买项目"))

    return run_action(conn, username, request_id, "buy", payload, now, mutate)


def plant(conn, username, request_id, plot_id, crop_id, now):
    index = plot_index(plot_id)
    crop_id = str(crop_id or "")
    payload = {"plot_id": index, "crop_id": crop_id}

    def mutate():
        profile = load_profile(conn, username)
        crop = CROPS.get(crop_id)
        if not crop:
            raise estate_error(("unknown_crop", "作物不存在"))
        _ensure_level(profile, crop)
        if index >= profile["plot_count"]:
            raise estate_error(PLOT_LOCKED)
        row = _plot_row(conn, username, index)
        if row[1] is not None:
            raise estate_error(("plot_busy", "土地上已有作物"))
        change_inventory(conn, username, seed_item(crop_id), -1)
        ready_at = int(now) + grow_seconds(crop_id, row[0])
        conn.execute(
            "UPDATE estate_plots SET crop_id=?,planted_at=?,ready_at=? "
            "WHERE username=? AND plot_index=?",
            (crop_id, int(now), ready_at, username, index),
        )
        return {"action": "plant", "plot_id": index, "crop_id": crop_id,
                "ready_at": ready_at}

    return run_action(conn, username, request_id, "plant", payload, now, mutate)


def harvest(conn, username, request_id, plot_id, now):
    index = plot_index(plot_id)
    payload = {"plot_id": index}

    def mutate():
        profile = load_profile(conn, username)
        if index >= profile["plot_count"]:
            raise estate_error(PLOT_LOCKED)
        row = conn.execute(
            "SELECT crop_id,ready_at FROM estate_plots WHERE username=? AND plot_index=?",
            (username, index),
        ).fetchone()
        if not row or not row[0]:
            raise estate_error(("plot_empty", "土地上没有作物"))
        if row[1] > int(now):
            raise estate_error(("crop_growing", "作物还没有成熟"))
        crop = CROPS.get(row[0])
        if not crop:
            raise estate_error(CROP_DATA_BROKEN)
        require_capacity(conn, username, profile, crop["yield"])
        change_inventory(conn, username, crop_item(row[0]), crop["yield"])
        conn.execute(
            "UPDATE estate_plots SET crop_id=NULL,planted_at=NULL,ready_at=NULL "
            "WHERE username=? AND plot_index=?", (username, index),
        )
        before = profile["level"]
        level = award_xp(conn, username, crop["xp"])
        return {"action": "harvest", "plot_id": index, "crop_id": row[0],
                "quantity": crop["yield"], "xp_awarded": crop["xp"],
                "levels_gained": level - before, "level": level}

    return run_action(conn, username, request_id, "harvest", payload, now, mutate)


def sell(conn, username, request_id, item_id, quantity, now, adjust_coins):
    item_id = str(item_id or "")
    count = positive_int(quantity, maximum=9999)
    payload = {"item_id": item_id, "quantity": count}

    def mutate():
        info = item_info(item_id)
        if not info or not info["sellable"]:
            raise estate_error(("not_sellable", "该物品不能出售"))
        change_inventory(conn, username, item_id, -count)
        total = round(info["sell_price"] * count, 2)
        balance = credit(adjust_coins, conn, username, total,
                         f"小胖庄园出售：{info['name']} ×{count}", request_id)
        return {"action": "sell", "item_id": item_id, "quantity": count,
                "earned": total, "coins": balance}

    return run_action(conn, username, request_id, "sell", payload, now, mutate)


def sell_all(conn, username, request_id, now, adjust_coins):
    payload = {"sell_all": True}

    def mutate():
        sold = []
        total = 0.0
        for item_id, quantity in inventory_rows(conn, username):
            info = item_info(item_id)
            if not info or not info["sellable"]:
                continue
            amount = round(info["sell_price"] * quantity, 2)
            sold.append({"item_id": item_id, "quantity": quantity, "earned": amount})
            total += amount
        if not sold:
            raise estate_error(("nothing_to_sell", "仓库里没有可出售的产品"))
        for entry in sold:
            change_inventory(conn, username, entry["item_id"], -entry["quantity"])
        total = round(total, 2)
        balance = credit(adjust_coins, conn, username, total,
                         "小胖庄园一键出售", request_id)
        return {"action": "sell_all", "sold": sold, "earned": total, "coins": balance}

    return run_action(conn, username, request_id, "sell_all", payload, now, mutate)
