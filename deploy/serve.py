"""静态文件服务：白名单只提供公开前端文件，所有响应带 no-cache。

允许的路径：
  /                    -> index.html（注入客户端配置）
  /game                -> game.html（注入客户端配置）
  /assets/<路径>       -> assets 目录下的静态资源（可含子目录，如 assets/js/games/uno.js）
其余任何路径一律 404，防止源码、数据库、备份文件被下载。

页面里的 <!--LIVE_CONFIG--> 会被替换成 window.LIVE_CONFIG（只含展示文案与端口，
不含推流口令等敏感项），前端据此决定站点标题、拉流地址与 WebSocket 端口。
"""
import http.server
import io
import json
import os
import posixpath
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import client_config, get_int  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
DEAD_END = os.path.join(ROOT, "__forbidden__")
PORT = get_int("servers.web_port", env="LIVE_WEB_PORT", default=8000)

# 只放行这几类静态资源：源码（.py）、数据库、备份文件都在白名单之外。
# 音频后缀用于 assets/dungeon/beta/bgm/ 下的背景音乐曲库（见 docs/dungeon-beta-audio.md）；
# scripts/preview_game.py 与 scripts/preview_ui.py 放行的是同一组。
ALLOWED_EXT = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp",
               ".woff", ".woff2", ".json",
               ".mp3", ".ogg", ".oga", ".wav", ".m4a", ".aac", ".flac", ".opus", ".webm"}

INJECT_MARK = "<!--LIVE_CONFIG-->"


def asset_path(rel):
    """把 assets 下的相对路径解析成真实文件；越界或后缀不在白名单都返回 None。"""
    target = os.path.normpath(os.path.join(ASSETS, rel))
    if target != ASSETS and not target.startswith(ASSETS + os.sep):
        return None
    if os.path.isdir(target):
        return None                     # 不给目录列表
    if os.path.splitext(target)[1].lower() not in ALLOWED_EXT:
        return None
    return target


def render_page(filename):
    """读取页面并注入客户端配置。"""
    with open(filename, encoding="utf-8") as handle:
        html = handle.read()
    payload = json.dumps(client_config(), ensure_ascii=False)
    script = f"<script>window.LIVE_CONFIG = {payload};</script>"
    if INJECT_MARK in html:
        html = html.replace(INJECT_MARK, script, 1)
    else:
        html = html.replace("</head>", f"{script}\n</head>", 1)
    return html.encode("utf-8")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def translate_path(self, path):
        clean = posixpath.normpath(
            unquote(path.split("?", 1)[0].split("#", 1)[0])
        )
        if clean in ("", "/", ".", os.sep, "/index.html"):
            return os.path.join(ROOT, "index.html")
        if clean in ("/game", "/game.html"):
            return os.path.join(ROOT, "game.html")
        if clean in ("/dungeon-beta", "/dungeon-beta.html"):
            return os.path.join(ROOT, "dungeon-beta.html")
        if clean.startswith("/assets/"):
            # normpath 已消掉 ../，仍然再校验一次解析结果是否落在 assets 内
            return asset_path(clean[len("/assets/"):]) or DEAD_END
        return DEAD_END

    def send_head(self):
        """页面走注入分支，其余静态文件沿用父类实现。"""
        path = self.translate_path(self.path)
        if os.path.basename(path) in ("index.html", "game.html", "dungeon-beta.html") and os.path.isfile(path):
            body = render_page(path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
