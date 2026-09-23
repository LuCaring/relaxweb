"""战斗快照合同。时间轴与结算器在下一阶段基于该只读快照实现。"""

from dataclasses import dataclass
import hashlib
import json

from estate.dungeon.catalog import CATALOG, validate_catalog
from estate.dungeon.effects import STAT_FIELDS, validate_base_stats


@dataclass(frozen=True)
class BattleSnapshot:
    snapshot_json: str
    snapshot_hash: str

    def as_dict(self):
        return json.loads(self.snapshot_json)


def make_battle_snapshot(challenge_id, difficulty_id, player_stats, equipment,
                         effect_sources, seed, catalog=CATALOG):
    """冻结一次遭遇的内容和属性；调用者单独保存并保护种子。"""
    validate_catalog(catalog)
    validate_base_stats(player_stats)
    if not isinstance(seed, bytes) or len(seed) != 32:
        raise ValueError("战斗种子必须为 32 字节")
    if not isinstance(equipment, list) or not isinstance(effect_sources, list):
        raise ValueError("装备和效果来源必须为列表")
    challenge = next((row for row in catalog["challenges"]
                      if row["challenge_id"] == challenge_id
                      and row["difficulty_id"] == difficulty_id), None)
    if challenge is None:
        raise ValueError("挑战不存在")
    enemy = next(row for row in catalog["enemies"]
                 if row["enemy_id"] == challenge["enemy_id"])
    # Canonical JSON creates a value snapshot. Mutating later item/catalog objects
    # cannot affect a live run or its hash.
    payload = {"config_version": catalog["config_version"],
               "simulation_version": catalog["simulation_version"],
               "rng_version": catalog["rng_version"], "seed_hex": seed.hex(),
               "challenge_id": challenge_id, "difficulty_id": difficulty_id,
               "player_stats": player_stats, "equipment": equipment,
               "effect_sources": effect_sources, "enemy": enemy,
               "combat": catalog["combat"], "reward_table": None}
    if set(player_stats) != set(STAT_FIELDS):
        raise ValueError("玩家属性字段不完整")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return BattleSnapshot(raw, hashlib.sha256(raw.encode("utf-8")).hexdigest())
