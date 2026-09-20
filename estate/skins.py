"""小胖庄园角色皮肤：免费切换，使用统一的账号存档与幂等动作。"""
from estate.catalog import SKINS
from estate.store import estate_error, run_action


def set_skin(conn, username, request_id, skin_id, now):
    # 在目录查询与请求摘要前检查类型，拒绝列表、对象等非法负载而不抛 TypeError。
    if not isinstance(skin_id, str) or skin_id not in SKINS:
        raise estate_error(("invalid_skin", "皮肤不存在"))

    def mutate():
        conn.execute(
            "UPDATE estate_profiles SET skin_id=? WHERE username=?", (skin_id, username),
        )
        return {"action": "set_skin", "skin_id": skin_id}

    return run_action(conn, username, request_id, "set_skin", {"skin_id": skin_id}, now, mutate)
