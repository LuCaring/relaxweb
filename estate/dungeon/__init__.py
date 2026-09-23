"""地下城领域模块。目录、属性、存档和战斗可分别演进。"""

from estate.dungeon.catalog import CATALOG, validate_catalog
from estate.dungeon.combat import make_battle_snapshot
from estate.dungeon.effects import resolve_stats
from estate.dungeon.schema import init_dungeon
from estate.dungeon.service import dungeon_state

__all__ = ["CATALOG", "validate_catalog", "make_battle_snapshot",
           "resolve_stats", "init_dungeon", "dungeon_state"]
