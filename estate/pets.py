"""休闲庄园宠物购买、升级与出场切换。"""

from estate.catalog import PET_LEVELS, PENGUIN_FRAGMENT_COST, PENGUIN_LEVELS, SKIN_FRAGMENT_ITEM
from estate.store import change_inventory, debit, estate_error, load_profile, run_action


def buy_or_upgrade_pet(conn, username, request_id, now, adjust_coins):
    payload = {"pet": "doudou"}

    def mutate():
        profile = load_profile(conn, username)
        level = int(profile["pet_level"])
        if level >= max(PET_LEVELS):
            raise estate_error(("pet_max_level", "豆豆已经达到最高等级"))
        if level == 0:
            price, next_level, detail = PET_LEVELS[1]["buy_price"], 1, "购买宠物豆豆"
        else:
            price, next_level = PET_LEVELS[level]["upgrade_price"], level + 1
            detail = f"豆豆升级至 Lv.{next_level}"
        balance = debit(adjust_coins, conn, username, price,
                        f"休闲庄园商店：{detail}", request_id)
        conn.execute("UPDATE estate_profiles SET pet_level=?,active_pet=CASE WHEN ?=0 "
                     "THEN 'doudou' ELSE active_pet END WHERE username=?",
                     (next_level, level, username))
        return {"action": "pet_upgrade" if level else "pet_buy", "pet": "doudou",
                "pet_level": next_level, "cost": price, "coins": balance}

    return run_action(conn, username, request_id, "pet_buy_or_upgrade", payload, now, mutate)


def buy_or_upgrade_penguin(conn, username, request_id, now, adjust_coins):
    def mutate():
        profile = load_profile(conn, username)
        level = int(profile["penguin_level"])
        if level >= max(PENGUIN_LEVELS):
            raise estate_error(("pet_max_level", "臭企鹅已经达到最高等级"))
        if level:
            from estate.farming import auto_harvest_penguin
            auto_harvest_penguin(conn, username, now, adjust_coins)
        if level == 0:
            change_inventory(conn, username, SKIN_FRAGMENT_ITEM, -PENGUIN_FRAGMENT_COST)
            cost = 0
            balance = conn.execute(
                "SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
        else:
            cost = PENGUIN_LEVELS[level]["upgrade_price"]
            balance = debit(adjust_coins, conn, username, cost,
                            f"休闲庄园：臭企鹅升级至 Lv.{level + 1}", request_id)
        conn.execute("UPDATE estate_profiles SET penguin_level=?,penguin_active_at=?,"
                     "active_pet=CASE WHEN ?=0 THEN 'stinky_penguin' ELSE active_pet END "
                     "WHERE username=?", (level + 1, int(now), level, username))
        return {"action": "penguin_upgrade" if level else "penguin_redeem",
                "pet": "stinky_penguin", "penguin_level": level + 1,
                "fragments_spent": PENGUIN_FRAGMENT_COST if level == 0 else 0,
                "cost": cost, "coins": balance}

    return run_action(conn, username, request_id, "penguin_buy_or_upgrade",
                      {"pet": "stinky_penguin"}, now, mutate)


def set_active_pet(conn, username, request_id, pet, now):
    if pet not in ("doudou", "stinky_penguin"):
        raise estate_error(("pet_invalid", "无效的宠物"))

    def mutate():
        profile = load_profile(conn, username)
        level = profile["pet_level"] if pet == "doudou" else profile["penguin_level"]
        if not level:
            raise estate_error(("pet_unowned", "尚未拥有该宠物"))
        if profile["active_pet"] != pet:
            if pet == "stinky_penguin":
                conn.execute("UPDATE estate_profiles SET active_pet=?,penguin_active_at=? "
                             "WHERE username=?", (pet, int(now), username))
            else:
                conn.execute("UPDATE estate_profiles SET active_pet=? WHERE username=?",
                             (pet, username))
        return {"action": "pet_select", "pet": pet}

    return run_action(conn, username, request_id, "pet_select", {"pet": pet}, now, mutate)
