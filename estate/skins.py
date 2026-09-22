"""休闲庄园角色皮肤：购买解锁与免费切换，使用统一的账号存档与幂等动作。"""
from estate.catalog import SKINS
from estate.store import debit, estate_error, run_action, skin_state


def set_skin(conn, username, request_id, skin_id, now):
    # 在目录查询与请求摘要前检查类型，拒绝列表、对象等非法负载而不抛 TypeError。
    if not isinstance(skin_id, str) or skin_id not in SKINS:
        raise estate_error(("invalid_skin", "皮肤不存在"))

    def mutate():
        if skin_id not in skin_state(conn, username)["owned"]:
            raise estate_error(("skin_locked", "皮肤尚未解锁"))
        conn.execute(
            "UPDATE estate_profiles SET skin_id=? WHERE username=?", (skin_id, username),
        )
        return {"action": "set_skin", "skin_id": skin_id}

    return run_action(conn, username, request_id, "set_skin", {"skin_id": skin_id}, now, mutate)


def buy_skin(conn, username, request_id, skin_id, now, adjust_coins):
    if not isinstance(skin_id, str) or skin_id not in SKINS:
        raise estate_error(("invalid_skin", "皮肤不存在"))
    if SKINS[skin_id]["unlock"] != "purchase":
        raise estate_error(("skin_not_for_sale", "该皮肤不可购买"))

    def mutate():
        owned = skin_state(conn, username)["owned"]
        charged = 0
        if skin_id not in owned:
            charged = SKINS[skin_id]["price"]
            debit(adjust_coins, conn, username, charged, f"购买皮肤：{SKINS[skin_id]['name']}", request_id)
            conn.execute("INSERT INTO estate_owned_skins VALUES (?,?)", (username, skin_id))
        skin_state(conn, username)
        return {"action": "buy_skin", "skin_id": skin_id, "charged": charged}

    return run_action(conn, username, request_id, "buy_skin", {"skin_id": skin_id}, now, mutate)
