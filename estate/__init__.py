"""休闲庄园：持久化单人经营系统。

对外接口按职责分组：内核与读模型在 ``estate.store``，农场在 ``estate.farming``，
工具与钓鱼、矿场在 ``estate.activities``，角色皮肤在 ``estate.skins``。
宿主（``chat_server``）只依赖这里导出的名字，因此内部模块拆分不影响协议层。
"""

from estate.schema import init_estate
from estate.store import EstateError, ensure_estate, estate_state
from estate.skins import buy_skin, set_skin
from estate.farming import buy, harvest, plant, sell, sell_all, use_land_upgrade_ticket
from estate.lottery import draw_lottery, lottery_history
from estate.market import cancel_order, market_snapshot, place_order, trade_market
from estate.activities import (
    buy_tool, finish_fishing, finish_mining, mine_cell, repair_tool,
    simulate_fishing, start_fishing, start_mining, upgrade_tool,
)
from estate.pets import buy_or_upgrade_pet, buy_or_upgrade_penguin, buy_or_upgrade_maodie, set_active_pet
from estate.visits import (
    fertilize, list_estates, mark_notifications_read, notifications, public_estate_state,
    steal_crop,
)

__all__ = [
    # 建档与快照
    "EstateError", "init_estate", "ensure_estate", "estate_state",
    # 角色皮肤
    "set_skin", "buy_skin",
    # 农场
    "buy", "plant", "harvest", "sell", "sell_all", "use_land_upgrade_ticket",
    "draw_lottery", "lottery_history", "market_snapshot", "trade_market",
    "place_order", "cancel_order",
    # 工具
    "buy_tool", "upgrade_tool", "repair_tool",
    "buy_or_upgrade_pet", "buy_or_upgrade_penguin", "buy_or_upgrade_maodie", "set_active_pet",
    # 钓鱼
    "start_fishing", "finish_fishing", "simulate_fishing",
    # 矿场
    "start_mining", "mine_cell", "finish_mining",
    # 多人拜访
    "list_estates", "public_estate_state", "steal_crop", "fertilize",
    "notifications", "mark_notifications_read",
]
