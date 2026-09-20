"""小胖庄园：持久化单人经营系统。

对外接口按职责分组：内核与读模型在 ``estate.store``，农场在 ``estate.farming``，
工具与钓鱼、矿场在 ``estate.activities``。宿主（``chat_server``）只依赖这里
导出的名字，因此内部模块拆分不影响协议层。
"""

from estate.schema import init_estate
from estate.store import EstateError, ensure_estate, estate_state
from estate.farming import buy, harvest, plant, sell, sell_all
from estate.activities import (
    buy_tool, finish_fishing, finish_mining, mine_cell, repair_tool,
    simulate_fishing, start_fishing, start_mining, upgrade_tool,
)

__all__ = [
    # 建档与快照
    "EstateError", "init_estate", "ensure_estate", "estate_state",
    # 农场
    "buy", "plant", "harvest", "sell", "sell_all",
    # 工具
    "buy_tool", "upgrade_tool", "repair_tool",
    # 钓鱼
    "start_fishing", "finish_fishing", "simulate_fishing",
    # 矿场
    "start_mining", "mine_cell", "finish_mining",
]
