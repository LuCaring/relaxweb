"""地下城领域模块。目录、属性、存档和战斗可分别演进。"""

from estate.dungeon.catalog import CATALOG, validate_catalog
from estate.dungeon.actions import run_dungeon_action
from estate.dungeon.combat import advance, make_battle_snapshot, simulate, start
from estate.dungeon.effects import resolve_stats
from estate.dungeon.schema import init_dungeon
from estate.dungeon.service import compare_item, dungeon_state
from estate.dungeon.runs import progress_run, read_run, start_run

__all__ = ["CATALOG", "validate_catalog", "make_battle_snapshot", "start", "advance", "simulate",
           "resolve_stats", "init_dungeon", "dungeon_state", "compare_item", "run_dungeon_action",
           "start_run", "progress_run", "read_run"]
