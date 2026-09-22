"""WebSocket 服务启动入口；协议与状态由 server.app 装配。"""
import asyncio
import logging

from config import get, get_int
from server.app import create_app


async def main():
    host = str(get("servers.chat_host", env="LIVE_CHAT_HOST", default="0.0.0.0"))
    port = get_int("servers.chat_port", env="LIVE_CHAT_PORT", default=8765)
    await create_app().run(host, port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
