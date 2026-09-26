"""Connection settings shared by the manual local integration scripts."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import get_int  # noqa: E402

URI = os.environ.get("LIVE_TEST_WS_URL") or (
    f"ws://127.0.0.1:{get_int('servers.chat_port', env='LIVE_CHAT_PORT', default=8765)}"
)
