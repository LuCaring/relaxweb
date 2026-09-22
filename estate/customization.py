"""Validate deployment customization before exposing it to game clients."""
import re


COLLECTION_REWARD_ID = "collection_reward"


def collection_reward(settings, skins, collectibles):
    """Resolve one optional reward; its storage ID stays stable across rebranding."""
    if not isinstance(settings, dict):
        raise ValueError("estate.collection_reward 必须是对象")
    enabled = settings.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("estate.collection_reward.enabled 必须是布尔值")
    if not enabled:
        return None

    def text(key):
        value = settings.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"estate.collection_reward.{key} 必须是非空字符串")
        return value.strip()

    def requirements(key, available):
        value = settings.get(key)
        if value == "all":
            return list(available)
        if (not isinstance(value, list)
                or any(not isinstance(item, str) or item not in available for item in value)
                or len(set(value)) != len(value)):
            raise ValueError(f"estate.collection_reward.{key} 必须为 all 或不重复的有效 ID 列表")
        return list(value)

    asset_id = text("asset_id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", asset_id):
        raise ValueError("estate.collection_reward.asset_id 必须为本地素材目录名")
    return {
        "name": text("name"), "description": text("description"),
        "asset_id": asset_id, "price": 0, "unlock": "collection",
        "required_skins": requirements("required_skins", skins),
        "required_collectibles": requirements("required_collectibles", collectibles),
    }
