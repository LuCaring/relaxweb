"""Process replacement and LAN startup: python3 tests/test_preview_startup.py."""
import json
import os
from pathlib import Path
import psutil
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading

ROOT = Path(__file__).resolve().parents[1]


class PreviewStartup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processes = []
        self.script = self.copy_script('first')

    def copy_script(self, name):
        script = Path(self.temp.name) / name / 'scripts' / 'preview_ui.py'
        script.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'scripts/preview_ui.py', script)
        return script

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdout.close()
        self.temp.cleanup()

    def spawn(self, script=None, *args):
        script = script or self.script
        process = subprocess.Popen([sys.executable, str(script), '--no-open', *args],
            cwd=script.parents[1], env={**os.environ, 'PYTHONPATH': str(ROOT)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def ready(self, process):
        output = b''
        deadline = time.monotonic() + 10
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if selector.select(.1):
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output += chunk
                    match = re.search(rb'http://127\.0\.0\.1:(\d+)/\?([^\s]+)', output)
                    if match:
                        return int(match[1]), match[0].decode(), output.decode()
        self.fail(f'preview failed to start: {output.decode()}')

    def test_replaces_same_repo_and_reuses_port(self):
        old = self.spawn(None, '--port', '0')
        port, url, _ = self.ready(old)
        self.assertIn('size=auto', url)
        new = self.spawn(None, '--port', str(port), '--mobile', '--lan')
        new_port, url, output = self.ready(new)
        self.assertEqual(port, new_port)
        old.wait(timeout=3)
        self.assertIn('已停止旧预览进程', output)
        self.assertIn('size=390x844', url)
        process = psutil.Process(new.pid)
        connections = (process.net_connections(kind='tcp') if hasattr(process, 'net_connections')
                       else process.connections(kind='tcp'))
        self.assertTrue(any(connection.status == psutil.CONN_LISTEN and
                            connection.laddr.ip == '0.0.0.0' and connection.laddr.port == port
                            for connection in connections),
                        '--lan listens on all IPv4 interfaces')
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/__preview/fixtures') as response:
            self.assertEqual(set(json.load(response)),
                             {'mahjong', 'guandan', 'holdem', 'uno', 'ludo', 'liarsbar', 'werewolf'})
        # Changing port still replaces the previous preview.
        newest = self.spawn(None, '--port', '0', '--landscape')
        _, url, _ = self.ready(newest)
        self.assertIn('size=844x390', url)
        new.wait(timeout=3)

    def test_preserves_other_repo_and_opt_out(self):
        first = self.spawn(None, '--port', '0')
        self.ready(first)
        other = self.spawn(self.copy_script('second'), '--port', '0')
        self.ready(other)
        self.assertIsNone(first.poll())
        independent = self.spawn(None, '--port', '0', '--no-replace')
        self.ready(independent)
        self.assertIsNone(first.poll())
        self.assertIsNone(other.poll())

    def test_does_not_kill_unrelated_port_owner(self):
        with HTTPServer(('127.0.0.1', 0), SimpleHTTPRequestHandler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                preview = self.spawn(None, '--port', str(server.server_port))
                output, _ = preview.communicate(timeout=10)
                self.assertNotEqual(preview.returncode, 0)
                self.assertIn('无法启动端口', output.decode())
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/') as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()


if __name__ == '__main__':
    unittest.main()
