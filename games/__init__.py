"""游戏模块包：每种在线小游戏一个子模块。

子模块在导入时通过 register_room_type 把房间实现注册进注册表，
宿主 chat_server 只依赖 games.base.create_room / ROOM_TYPES。
要下线某个游戏，删除对应 import 即可。
"""
from games.base import BaseRoom, ROOM_TYPES, create_room, register_room_type
from games import holdem  # noqa: F401  导入即注册 holdem
from games import uno  # noqa: F401  导入即注册 uno
from games import guandan  # noqa: F401  导入即注册 guandan
from games import mahjong  # noqa: F401  导入即注册 mahjong
from games import ludo  # noqa: F401  导入即注册 ludo
from games import liarsbar  # noqa: F401  导入即注册 liarsbar

__all__ = ["BaseRoom", "ROOM_TYPES", "create_room", "register_room_type"]
