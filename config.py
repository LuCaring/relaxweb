"""集中配置：部署相关的参数（站点标题、端口、推流、数据库）都放 config.json。

优先级：环境变量 > config.json > 这里的默认值。
config.json 不进版本库（见 .gitignore），仓库里提供 config.example.json 作模板；
没有 config.json 时用默认值也能本地跑起来。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("LIVE_CONFIG_FILE") or ROOT / "config.json")

DEFAULTS = {
    "site": {
        "title": "我的直播间",
        "brand": "Live",
        "game_title": "游戏厅",
    },
    "servers": {
        "chat_host": "0.0.0.0",
        "chat_port": 8765,
        "web_port": 8000,
        "auth_host": "127.0.0.1",
        "auth_port": 8001,
    },
    "stream": {
        "whep_port": 8889,
        "path": "live",
        "publish_user": "publisher",
        "publish_password": "",
    },
    "database": {
        "file": "users.db",
    },
    "voice": {
        "enabled": False,
        "url": "ws://127.0.0.1:7880",
        "api_url": "http://127.0.0.1:7880",
        "api_key": "devkey",
        "api_secret": "",
        "token_ttl": 600,
    },
    "economy": {
        "new_user_coins": 1000,
    },
    "estate": {
        "collection_reward": {
            "enabled": True,
            "name": "珍藏旅人",
            "description": "献给热爱探索与收集的庄园主人。",
            "asset_id": "collection_reward",
            "required_skins": "all",
            "required_collectibles": "all",
        },
    },
}


def _merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=None):
    """读取配置文件并与默认值合并；文件不存在时只用默认值。"""
    target = Path(path) if path else CONFIG_FILE
    if target.is_file():
        with target.open(encoding="utf-8") as handle:
            return _merge(DEFAULTS, json.load(handle))
    return dict(DEFAULTS)


CONFIG = load()


def _dig(data, dotted):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def get(dotted, env=None, default=None):
    """取配置项：环境变量优先，其次配置文件，最后 default。"""
    if env:
        raw = os.environ.get(env)
        if raw not in (None, ""):
            return raw
    value = _dig(CONFIG, dotted)
    return default if value in (None, "") else value


def get_int(dotted, env=None, default=0):
    try:
        return int(float(get(dotted, env=env, default=default)))
    except (TypeError, ValueError):
        return default


def client_config():
    """下发给浏览器的子集：只有展示文案与端口，不含任何口令。"""
    return {
        "site": CONFIG.get("site", {}),
        "stream": {
            "whep_port": get_int("stream.whep_port", default=8889),
            "path": get("stream.path", env="STREAM_PATH", default="live"),
        },
        "chat_port": get_int("servers.chat_port", env="LIVE_CHAT_PORT", default=8765),
        "voice": {
            "enabled": bool(get("voice.enabled", env="VOICE_ENABLED", default=False)),
            "url": str(get("voice.url", env="VOICE_URL", default="")),
        },
    }
