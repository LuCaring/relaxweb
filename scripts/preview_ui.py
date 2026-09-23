#!/usr/bin/env python3
"""Local, database-free UI snapshots: uv run python scripts/preview_ui.py."""
import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
import psutil
import random
import socket
import sys
import threading
import time
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
PLAYERS = ("p0", "p1", "p2", "p3")


def is_previous_preview(process):
    """Match the actual Python script and repository, never just a port or name."""
    if process.pid == os.getpid():
        return False
    try:
        if process.uids().real != os.getuid():
            return False
        if not Path(process.exe()).name.lower().startswith('python'):
            return False
        args = process.cmdline()
        # -c/-m are not script invocations; arguments to another script must not match.
        for arg in args[1:]:
            if arg in ('-c', '-m', '-W', '-X'):
                return False
            if arg.startswith('-'):
                continue
            script = (Path(process.cwd()) / arg).resolve()
            return script == Path(__file__).resolve()
    except (psutil.Error, OSError, UnicodeError):
        pass
    return False


def stop_previous_previews():
    """Replace older previews of this script on Linux and macOS."""
    def stopped(process, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    return True
            except psutil.NoSuchProcess:
                return True
            time.sleep(.05)
        return False

    current_start = psutil.Process().create_time()
    for process in psutil.process_iter():
        if not is_previous_preview(process):
            continue
        try:
            if process.create_time() >= current_start:
                continue  # A concurrent newer launch should replace us, not vice versa.
            if not is_previous_preview(process):
                continue
            process.terminate()
            if not stopped(process, 3):
                if not is_previous_preview(process):
                    continue
                process.kill()
                if not stopped(process, 1):
                    raise RuntimeError(f'旧预览进程 {process.pid} 未退出')
            print(f'已停止旧预览进程：{process.pid}', flush=True)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except psutil.Error as error:
            raise RuntimeError(f'无法停止旧预览进程 {process.pid}：{error}') from error


def lan_addresses():
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
        # UDP connect only selects a route; no packet or external request is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in addresses if not ip.startswith('127.') and ip != '0.0.0.0')


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
    spectators = {}
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

        def capture():
            # Each target uses the engine's actual private view, including team,
            # flowers and hole cards. Never reuse p0's hand for another seat.
            own = copy.deepcopy(room.view_for("p0"))
            views = {}
            for watched in PLAYERS:
                room.add_spectator("preview-watcher", watched)
                views[watched] = copy.deepcopy(room.spectator_view("preview-watcher"))
            for view in [own, *views.values()]:
                view["turn_left"] = 86400
            return own, views

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
                for name, meld in zip(("p1", "p2", "p3"), (
                    {"type": "chi", "tiles": [0, 1, 2], "from": "p0"},
                    {"type": "peng", "tiles": [13, 13, 13], "from": "p1"},
                    {"type": "gang", "tiles": [27, 27, 27, 27], "from": "p2"},
                )):
                    g["melds"][name] = [meld]
                    g["hands"][name] = g["hands"][name][:10]
            elif game == "holdem":
                g["stage"] = "flop"
                g["board"] = [(10, 0), (11, 1), (12, 2)]
            elif game == "uno":
                g["hands"]["p0"] = [{"c": "r", "v": "5"}, {"c": "b", "v": "5"},
                    {"c": "g", "v": "rev"}, {"c": "y", "v": "skip"},
                    {"c": "w", "v": "wild"}, {"c": "w", "v": "wd4"}, {"c": "r", "v": "d2"}]
                g["color"], g["value"] = "r", "5"
                g["discard"][-1] = {"c": "r", "v": "5"}
            normal, normal_spectators = capture()

            if game == "mahjong":
                g["discards"] = {f"p{i}": [(j * 7 + i) % 34 for j in range(18)] for i in range(4)}
                g["last_discard"] = {"by": "p3", "tile": g["discards"]["p3"][-1]}
                g["melds"]["p1"].append({"type": "angang", "tiles": [8, 8, 8, 8]})
                g["hands"]["p1"] = g["hands"]["p1"][:7]
                g["flowers"]["p0"] = [34, 35, 36, 37]
            elif game == "uno":
                g["hands"]["p0"] = (g["hands"]["p0"] * 3)[:20]
            elif game == "holdem":
                g["board"] += [(13, 3), (14, 0)]
                g["stage"] = "river"
            dense, dense_spectators = capture()
            for view in [dense, *dense_spectators.values()]:
                view["last_action"] = {"nickname": "一个很长的玩家昵称", "text": "打出了手中的牌，现在等待下一位玩家行动"}
                for player in view["players"]:
                    player["nickname"] += "的超长昵称"
            if game == "mahjong":
                # Deliberate layout sample, scoped to the player whose hand it describes.
                for view in [dense, dense_spectators["p0"]]:
                    view["tenpai"] = {"waits": [24, 27, 30], "remaining": {24: 3, 27: 2, 30: 1}}
        finally:
            room.cancel_timers()
        fixtures[game] = {"normal": normal, "dense": dense, "waiting": waiting,
                          "paused": {**copy.deepcopy(normal), "paused": True}}
        spectators[game] = {"normal": normal_spectators, "dense": dense_spectators,
                            "paused": {name: {**copy.deepcopy(view), "paused": True}
                                       for name, view in normal_spectators.items()}}
    return fixtures, spectators


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
        if path == "/__preview/spectators":
            return self.reply(json.dumps(self.server.spectators, ensure_ascii=False))
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
    parser = argparse.ArgumentParser(description="一行命令启动本地游戏 UI 预览，无需登录或数据库。")
    parser.add_argument("--game", choices=GAMES, default="guandan")
    parser.add_argument("--scene", choices=SCENES, default="normal")
    parser.add_argument("--spectator", action="store_true", help="以观战视角启动，可在牌桌内更换玩家")
    parser.add_argument("--watch", choices=PLAYERS, help="以观战视角观看指定玩家，默认 p0")
    parser.add_argument("--port", type=int, default=8010, help="本地端口，0 表示自动选择空闲端口")
    parser.add_argument("--no-open", action="store_true", help="不自动打开系统浏览器")
    parser.add_argument("--no-replace", action="store_true", help="保留旧预览进程（用于独立自动化测试）")
    parser.add_argument("--lan", action="store_true", help="允许同一局域网的手机访问，并打印手机地址")
    sizes = parser.add_mutually_exclusive_group()
    sizes.add_argument("--mobile", dest="size", action="store_const", const="390x844", help="启动手机竖屏预览")
    sizes.add_argument("--landscape", dest="size", action="store_const", const="844x390", help="启动手机横屏预览")
    sizes.add_argument("--size", choices=("auto", "1440x900", "1024x768", "390x844", "320x568", "844x390"))
    parser.set_defaults(size="auto")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('端口必须介于 0 和 65535 之间')
    if (args.spectator or args.watch) and args.scene == "waiting":
        parser.error("等待开局场景不支持观战；请使用 normal、dense 或 paused")
    fixtures, spectators = asyncio.run(make_fixtures())
    if not args.no_replace:
        try:
            stop_previous_previews()
        except (OSError, RuntimeError) as error:
            parser.exit(1, f'无法替换旧预览：{error}\n')
    try:
        server = ThreadingHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), PreviewHandler)
    except OSError as error:
        parser.exit(1, f"无法启动端口 {args.port}：{error}。可用 --port 0 自动选择空闲端口。\n")
    server.fixtures = fixtures
    server.spectators = spectators
    query = f"game={args.game}&scene={args.scene}"
    if args.spectator or args.watch:
        query += f"&perspective=spectator&watch={args.watch or 'p0'}"
    url = f"http://127.0.0.1:{server.server_port}/?{query}&size={args.size}"
    print(f"本地 UI 预览：{url}\n修改前端文件后自动刷新；Ctrl+C 停止。", flush=True)
    if args.lan:
        for ip in lan_addresses():
            print(f'手机访问（同一局域网）：http://{ip}:{server.server_port}/game.html?{query}', flush=True)
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
