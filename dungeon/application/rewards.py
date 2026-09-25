"""Server-owned room reward planning and application.

Only the runtime calls this module after its simulator clears a room. Content
specifies amounts and a pool; neither inputs nor simulator events can do so.
"""

from collections.abc import Mapping

from dungeon.application.items import create_item
from dungeon.domain.errors import DungeonError
from dungeon.storage.assets import bump_asset_revision


def _entry(rules, kind, identifier):
    return next((row for row in rules.content(kind) if row["id"] == identifier), None)


def plan_room_reward(rules, encounter, rng):
    rewards = encounter.get("rewards")
    if not isinstance(rewards, Mapping):
        raise DungeonError("invalid_config", "房间缺少奖励配置")
    permanent = rewards.get("permanent", {})
    coin_minor = permanent.get("coins_minor", 0)
    rolls = permanent.get("loot_rolls", 0)
    progress_ids = permanent.get("progress_ids", [])
    if (type(coin_minor) is not int or coin_minor < 0 or type(rolls) is not int
            or not 0 <= rolls <= 20 or not isinstance(progress_ids, (list, tuple))
            or any(not isinstance(value, str) or not value for value in progress_ids)):
        raise DungeonError("invalid_config", "房间奖励配置无效")
    pool = _entry(rules, "loot", encounter["loot_pool_id"])
    if pool is None:
        raise DungeonError("invalid_config", "掉落池不存在")
    entries = pool["entries"]
    total = sum(entry["weight"] for entry in entries)
    if total <= 0:
        raise DungeonError("invalid_config", "掉落池为空")
    templates = []
    for _ in range(rolls):
        draw = rng.random_int("loot:" + encounter["id"], 1, total)
        for entry in entries:
            draw -= entry["weight"]
            if draw <= 0:
                templates.append(entry["item_template_id"])
                break
    run = rewards.get("run", {})
    potions = [{"count": entry["count"], "heal_max_hp_bp": entry["heal_max_hp_bp"]}
               for entry in run.get("potions", ())]
    return {"coin_minor": coin_minor, "item_template_ids": templates,
            "progress_ids": list(dict.fromkeys(progress_ids)), "run": {"potions": potions}}


def apply_room_reward(conn, rules, username, user_id, run_id, room_index, plan,
                      wallet, now):
    """Apply inside caller's BEGIN IMMEDIATE, then caller saves reward receipt."""
    coin_minor = plan["coin_minor"]
    if coin_minor:
        wallet.change(conn, username, coin_minor, kind="dungeon_beta_room_reward",
                      ref=f"dungeon:beta:run:{run_id}:room:{room_index}",
                      detail="地下城房间奖励")
    items = [create_item(conn, rules, username, template_id, source="run_reward", now=now)
             for template_id in plan["item_template_ids"]]
    for progress_id in plan["progress_ids"]:
        conn.execute("""INSERT OR IGNORE INTO dungeon_beta_progress(user_id,progress_id)
            VALUES (?,?)""", (user_id, progress_id))
    bump_asset_revision(conn, user_id)
    return {"coin_minor": coin_minor, "items": items,
            "progress_ids": plan["progress_ids"], "run": plan["run"]}
