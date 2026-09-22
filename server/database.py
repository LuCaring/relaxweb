"""SQLite 连接入口；连接在退出上下文时关闭，事务由调用方管理。"""
import sqlite3
from contextlib import closing

from config import get

DB_FILE = str(get("database.file", env="LIVE_DB_FILE", default="users.db"))


def database(path=None):
    """可显式传入临时库路径；默认路径在调用时读取。"""
    return closing(sqlite3.connect(DB_FILE if path is None else path, timeout=10))
