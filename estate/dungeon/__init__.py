"""Compatibility exports for the original estate.dungeon import path."""

from dungeon.legacy.catalog import CATALOG, validate_catalog
from dungeon.legacy.actions import run_dungeon_action
from dungeon.legacy.combat import advance, make_battle_snapshot, simulate, start
from dungeon.legacy.effects import resolve_stats
from dungeon.storage.legacy_schema import init_dungeon
from dungeon.legacy.service import compare_item, dungeon_state
from dungeon.legacy.runs import progress_run, read_run, start_run

__all__ = ["CATALOG", "validate_catalog", "make_battle_snapshot", "start", "advance", "simulate",
           "resolve_stats", "init_dungeon", "dungeon_state", "compare_item", "run_dungeon_action",
           "start_run", "progress_run", "read_run"]
