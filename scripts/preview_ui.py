#!/usr/bin/env python3
"""Local, database-free UI snapshots: python3 scripts/preview_ui.py."""
import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import random
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
UI = Path(__file__).resolve().parent / "ui_preview"
sys.path.insert(0, str(ROOT))

from games.base import create_room  # noqa: E402

GAMES = ("guandan", "mahjong", "holdem", "uno")
SCENES = ("normal", "dense", "waiting", "paused")


def avatar(index):
    colors = ("#287b9c", "#875fb3", "#c47740", "#479373")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
           f'<rect width="96" height="96" rx="24" fill="{colors[index]}"/>'
           '<circle cx="48" cy="34" r="16" fill="#fff5df"/>'
           '<path d="M18 88v-8a30 30 0 0 1 60 0v8" fill="#fff5df"/></svg>')
    return "data:image/svg+xml," + quote(svg)


async def make_fixtures():
    """Use engine views so snapshots follow the production response shape."""
    random.seed(19)
    fixtures = {}
    names = ("清风", "竹间听雨", "月下客", "一杯春茶")

    async def noop(*args, **kwargs):
        pass

    for game in GAMES:
        room = create_room(game, room_id="local-" + game, name="本地 UI 测试桌",
                           owner="p0", buy_in=200, blind=1)
        room.display_name = lambda username: names[int(username[1:])]
        room.player_avatar = lambda username: avatar(int(username[1:]))
        room.broadcast_views = room.broadcast_payload = room.on_rooms_changed = noop
        for i in range(4):
            room.add_member(f"p{i}", 200)
        waiting = room.view_for("p0")
        try:
            await room.start()
            g = room.game
            g["to_act"] = "p0"
            if game == "guandan":
                g["hands"]["p0"][:2] = [{"r": 16, "s": 4}, {"r": 17, "s": 4}]
                # A real pair to exercise the standing-card area and hint controls.
                g["hands"]["p3"][:2] = [{"r": 5, "s": 0}, {"r": 5, "s": 1}]
                g["to_act"] = "p3"
                await room.perform_action("p3", "play", {"cards": [0, 1]})
            elif game == "mahjong":
                g["phase"] = "discard"
                g["hands"]["p0"] = [0, 1, 2, 3, 4, 5, 9, 10, 11, 18, 19, 20, 26, 27]
                g["last_draw"] = 27
                g["discards"] = {f"p{i}": [(j * 7 + i) % 34 for j in range(6)] for i in range(4)}
                g["last_discard"] = {"by": "p3", "tile": g["discards"]["p3"][-1]}
            elif game == "holdem":
                g["stage"] = "flop"
                g["board"] = [(10, 0), (11, 1), (12, 2)]
            elif game == "uno":
                g["hands"]["p0"] = [{"c": "r", "v": "5"}, {"c": "b", "v": "5"},
                    {"c": "g", "v": "rev"}, {"c": "y", "v": "skip"},
                    {"c": "w", "v": "wild"}, {"c": "w", "v": "wd4"}, {"c": "r", "v": "d2"}]
                g["color"], g["value"] = "r", "5"
                g["discard"][-1] = {"c": "r", "v": "5"}
            normal = room.view_for("p0")
        finally:
            room.cancel_timers()
        # Keep the UI actionable during a long design session; no engine timers run.
        normal["turn_left"] = 86400
        dense = copy.deepcopy(normal)
        dense["last_action"] = {"nickname": "一个很长的玩家昵称", "text": "打出了手中的牌，现在等待下一位玩家行动"}
        for p in dense["players"]:
            p["nickname"] += "的超长昵称"
        if game == "mahjong":
            dense["discards"] = {f"p{i}": [(j * 7 + i) % 34 for j in range(18)] for i in range(4)}
            dense["last_discard"] = {"by": "p3", "tile": dense["discards"]["p3"][-1]}
        elif game == "uno":
            dense["your_hand"] = (dense["your_hand"] * 3)[:20]
            dense["players"][0]["cards"] = 20
        elif game == "holdem":
            dense["board"] += [{"r": 13, "s": 3}, {"r": 14, "s": 0}]
            dense["stage"] = "river"
        fixtures[game] = {"normal": normal, "dense": dense, "waiting": waiting,
                          "paused": {**copy.deepcopy(normal), "paused": True}}
    return fixtures


def revision():
    files = [ROOT / "game.html", *UI.rglob("*"), *(ROOT / "assets").rglob("*")]
    values = [f"{p}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in sorted(files) if p.is_file()]
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, content_type="application/json; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Preview pages cannot contact production services, even through UI controls.
        self.send_header("Content-Security-Policy", "connect-src 'self'; img-src 'self' data: blob:; frame-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/__preview/fixtures":
            return self.reply(json.dumps(self.server.fixtures, ensure_ascii=False))
        if path == "/__preview/revision":
            return self.reply(json.dumps({"revision": revision()}))
        if path in ("/game.html", "/game"):
            html = (ROOT / "game.html").read_text()
            html = html.replace("<head>", '<head><script src="/__preview/bootstrap.js"></script>', 1)
            html = html.replace("</body>", '<script type="module" src="/__preview/table.js"></script></body>')
            return self.reply(html, "text/html; charset=utf-8")
        preview_files = {"/": "index.html", "/__preview/shell.js": "shell.js",
                         "/__preview/bootstrap.js": "bootstrap.js", "/__preview/table.js": "table.js"}
        if path in preview_files:
            file = UI / preview_files[path]
        elif path.startswith("/assets/"):
            file = (ROOT / path.lstrip("/")).resolve()
            if not file.is_relative_to((ROOT / "assets").resolve()):
                return self.reply("Not found", "text/plain", 404)
            if file.suffix.lower() not in {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico", ".woff", ".woff2", ".json", ".mp3", ".wav", ".ogg"}:
                return self.reply("Not found", "text/plain", 404)
        else:
            return self.reply("Not found", "text/plain", 404)
        if not file.is_file():
            return self.reply("Not found", "text/plain", 404)
        return self.reply(file.read_bytes(), mimetypes.guess_type(str(file))[0] or "application/octet-stream")


def main():
    parser = argparse.ArgumentParser(description="一行命令启动本地游戏 UI 预览，无需登录、数据库或额外依赖。")
    parser.add_argument("--game", choices=GAMES, default="guandan")
    parser.add_argument("--scene", choices=SCENES, default="normal")
    parser.add_argument("--port", type=int, default=8010, help="本地端口，0 表示自动选择空闲端口")
    parser.add_argument("--no-open", action="store_true", help="不自动打开系统浏览器")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    except OSError as error:
        parser.exit(1, f"无法启动端口 {args.port}：{error}。可用 --port 0 自动选择空闲端口。\n")
    server.fixtures = asyncio.run(make_fixtures())
    url = f"http://127.0.0.1:{server.server_port}/?game={args.game}&scene={args.scene}"
    print(f"本地 UI 预览：{url}\n修改前端文件后自动刷新；Ctrl+C 停止。", flush=True)
    if not args.no_open:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n本地预览已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
