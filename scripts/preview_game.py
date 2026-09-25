#!/usr/bin/env python3
"""本地地下城 Beta 原型预览：uv run python scripts/preview_game.py。"""
import argparse
import hashlib
import json
import mimetypes
import os
import psutil
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dungeon-beta.html"

ALLOWED_SUFFIXES = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico",
                    ".woff", ".woff2", ".json", ".mp3", ".wav", ".ogg"}

WATCH_JS = """(async function () {
  const first = await (await fetch("/__preview/revision")).json();
  let revision = first.revision;
  setInterval(async () => {
    try {
      const data = await (await fetch("/__preview/revision")).json();
      if (data.revision !== revision) location.reload();
    } catch (error) { /* 网络闪断时下一轮再试 */ }
  }, 1000);
})();
"""


def is_previous_preview(process):
    """Match the actual Python script and repository, never just a port or name."""
    if process.pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            if process.username() != psutil.Process().username():
                return False
        elif process.uids().real != os.getuid():
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


def revision():
    files = [PAGE, ROOT / "assets/css/dungeon-beta.css",
             *(ROOT / "assets/js/dungeon").rglob("*"),
             *(ROOT / "assets/dungeon").rglob("*")]
    values = [f"{p}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in sorted(files) if p.is_file()]
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


class PreviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        if path == "/__preview/revision":
            return self.reply(json.dumps({"revision": revision()}))
        if path == "/__preview/watch.js":
            return self.reply(WATCH_JS, "text/javascript; charset=utf-8")
        if path in ("/", "/dungeon-beta.html"):
            html = PAGE.read_text(encoding="utf-8")
            html = html.replace("</body>", '<script src="/__preview/watch.js"></script></body>')
            return self.reply(html, "text/html; charset=utf-8")
        if path.startswith("/assets/"):
            file = (ROOT / path.lstrip("/")).resolve()
            if not file.is_relative_to((ROOT / "assets").resolve()):
                return self.reply("Not found", "text/plain", 404)
            if file.suffix.lower() not in ALLOWED_SUFFIXES:
                return self.reply("Not found", "text/plain", 404)
            if not file.is_file():
                return self.reply("Not found", "text/plain", 404)
            return self.reply(file.read_bytes(), mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        return self.reply("Not found", "text/plain", 404)


class PreviewHTTPServer(ThreadingHTTPServer):
    # Reloading fetches many assets at once; the default 5-slot backlog
    # can reset module requests before the browser reaches the game script.
    request_queue_size = 128
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="一行命令启动本地地下城 Beta 网页原型，无需登录或数据库。")
    parser.add_argument("--port", type=int, default=8020, help="本地端口，0 表示自动选择空闲端口")
    parser.add_argument("--no-open", action="store_true", help="不自动打开系统浏览器")
    parser.add_argument("--no-replace", action="store_true", help="保留旧预览进程（用于独立自动化测试）")
    parser.add_argument("--lan", action="store_true", help="允许同一局域网的手机访问，并打印手机地址")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('端口必须介于 0 和 65535 之间')
    if not PAGE.is_file():
        parser.exit(1, f"缺少页面文件：{PAGE}\n")
    if not args.no_replace:
        try:
            stop_previous_previews()
        except (OSError, RuntimeError) as error:
            parser.exit(1, f'无法替换旧预览：{error}\n')
    try:
        server = PreviewHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), PreviewHandler)
    except OSError as error:
        parser.exit(1, f"无法启动端口 {args.port}：{error}。可用 --port 0 自动选择空闲端口。\n")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"本地地下城预览：{url}\n修改前端文件后自动刷新；Ctrl+C 停止。", flush=True)
    if args.lan:
        for ip in lan_addresses():
            print(f'手机访问（同一局域网）：http://{ip}:{server.server_port}/', flush=True)
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
