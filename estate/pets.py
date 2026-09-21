"""休闲庄园宠物购买与升级。"""

from estate.catalog import PET_LEVELS
from estate.store import debit, estate_error, load_profile, run_action


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
        conn.execute("UPDATE estate_profiles SET pet_level=? WHERE username=?",
                     (next_level, username))
        return {"action": "pet_upgrade" if level else "pet_buy", "pet": "doudou",
                "pet_level": next_level, "cost": price, "coins": balance}

    return run_action(conn, username, request_id, "pet_buy_or_upgrade", payload, now, mutate)
