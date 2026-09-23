#!/usr/bin/env python3
"""Run every Python test module, including the three standalone game suites."""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    failed = []
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
    for test in tests:
        print(f"\n== {test.name} ==", flush=True)
        result = subprocess.run([sys.executable, str(test)], cwd=ROOT, env=env)
        if result.returncode:
            failed.append(test.name)
    print(f"\nPython 测试：{len(tests) - len(failed)}/{len(tests)} 个文件通过", flush=True)
    if failed:
        print("失败：" + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
