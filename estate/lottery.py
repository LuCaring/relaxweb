"""抽奖马戏团：服务端决定全部落点并在同一事务内结算。"""
import json
import secrets

from estate.catalog import (
    FERTILIZER_ITEM, FISHING_TREASURES, LAND_UPGRADE_TICKET,
    LOTTERY_DAILY_FREE_DRAWS, LOTTERY_GRAND_PRIZES, LOTTERY_PRICE, LOTTERY_PRIZES, SKIN_FRAGMENT_ITEM,
    collectible_item, seed_item,
)
from estate.store import (
    change_inventory, credit, debit, estate_error, load_profile,
    require_capacity, run_action, estate_day_key,
)


def lottery_daily_state(conn, username, now):
    day = estate_day_key(now)
    conn.execute(
        "INSERT INTO estate_lottery_daily(username,draw_day) VALUES (?,?) "
        "ON CONFLICT(username) DO UPDATE SET draw_day=excluded.draw_day,free_draws_used=0 "
        "WHERE estate_lottery_daily.draw_day<>excluded.draw_day", (username, day))
    used = conn.execute(
        "SELECT free_draws_used FROM estate_lottery_daily WHERE username=?", (username,)
    ).fetchone()[0]
    return {"limit": LOTTERY_DAILY_FREE_DRAWS,
            "remaining": max(0, LOTTERY_DAILY_FREE_DRAWS - used)}


def _grand_prize():
    ticket = secrets.randbelow(sum(weight for _, weight in LOTTERY_GRAND_PRIZES))
    for name, weight in LOTTERY_GRAND_PRIZES:
        if ticket < weight:
            return name
        ticket -= weight
    raise AssertionError("神秘大奖概率表无效")


def draw_lottery(conn, username, request_id, now, adjust_coins):
    def mutate():
        profile = load_profile(conn, username)
        # 所有奖项统一要求足够存下最大的实物奖，避免满仓玩家筛掉实物奖。
        require_capacity(conn, username, profile, 1)
        daily = lottery_daily_state(conn, username, now)
        cost = 0 if daily["remaining"] else LOTTERY_PRICE
        if cost:
            debit(adjust_coins, conn, username, cost,
                  "休闲庄园抽奖马戏团", request_id)
        else:
            conn.execute("UPDATE estate_lottery_daily SET free_draws_used=free_draws_used+1 "
                         "WHERE username=?", (username,))
        spins = []
        for _ in range(64):
            prize = LOTTERY_PRIZES[secrets.randbelow(len(LOTTERY_PRIZES))]
            spin = {"prize": prize}
            spins.append(spin)
            if prize != "grand":
                break
            spin["grand_prize"] = _grand_prize()
            if spin["grand_prize"] != "reroll":
                break
        else:
            raise estate_error(("lottery_retry", "抽奖暂未完成，请重试"))

        final = spins[-1]
        award = final.get("grand_prize", final["prize"])
        result = {"action": "lottery_draw", "cost": cost,
                  "free_draws_remaining": daily["remaining"] - (cost == 0),
                  "spins": spins, "award": award, "quantity": 0}
        if award.startswith("coins_"):
            amount = int(award.split("_", 1)[1])
            credit(adjust_coins, conn, username, amount,
                   "休闲庄园抽奖奖励", request_id)
            result.update({"coins_awarded": amount, "quantity": amount})
        elif award == "mystery_seed":
            crop_id = "legendary_flower" if secrets.randbelow(2) == 0 else "gpu_fruit"
            change_inventory(conn, username, seed_item(crop_id), 1)
            result.update({"item_id": seed_item(crop_id), "crop_id": crop_id,
                           "quantity": 1})
        elif award == "fertilizer_1":
            change_inventory(conn, username, FERTILIZER_ITEM, 1)
            result.update({"item_id": FERTILIZER_ITEM, "quantity": 1})
        elif award == "land_ticket":
            change_inventory(conn, username, LAND_UPGRADE_TICKET, 1)
            result.update({"item_id": LAND_UPGRADE_TICKET, "quantity": 1})
        elif award == "missing_collectible":
            owned = {row[0] for row in conn.execute(
                "SELECT item_id FROM estate_collections WHERE username=?", (username,))}
            owned.update(row[0] for row in conn.execute(
                "SELECT item_id FROM estate_inventory WHERE username=? "
                "AND item_id LIKE 'collectible:%'", (username,)))
            missing = [key for key in FISHING_TREASURES
                       if collectible_item(key) not in owned]
            if missing:
                chosen = missing[secrets.randbelow(len(missing))]
                item = collectible_item(chosen)
                change_inventory(conn, username, item, 1)
                result.update({"item_id": item, "collectible_id": chosen,
                               "quantity": 1})
            else:
                result["all_collectibles_owned"] = True
                change_inventory(conn, username, SKIN_FRAGMENT_ITEM, 1)
                result.update({"item_id": SKIN_FRAGMENT_ITEM, "quantity": 1})
        result["coins"] = conn.execute(
            "SELECT coins FROM users WHERE username=?", (username,)).fetchone()[0]
        return result

    return run_action(conn, username, request_id, "lottery_draw", {}, now, mutate)


def lottery_history(conn, username):
    rows = conn.execute("SELECT created_at,result_json FROM estate_actions "
                        "WHERE username=? AND action_type='lottery_draw' "
                        "ORDER BY created_at DESC,rowid DESC LIMIT 100", (username,)).fetchall()
    return [{"created_at": created_at, **json.loads(result_json)}
            for created_at, result_json in rows]
