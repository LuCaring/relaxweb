#!/usr/bin/env python3
"""离线模拟单场地下城战斗；只读本地快照，不连接数据库。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dungeon.legacy.catalog import CATALOG  # noqa: E402
from dungeon.legacy.combat import BattleSnapshot, make_battle_snapshot, simulate  # noqa: E402
from dungeon.legacy.effects import resolve_stats  # noqa: E402


def starter_snapshot(seed_hex):
    equipment = [
        {"item_id": item["template_id"], "template_id": item["template_id"],
         "slot": item["slot"], "quality": item["quality"],
         "stats": item["stats"], "tags": item["tags"], "effects": item["effects"]}
        for item in CATALOG["items"] if item["template_id"].startswith("starter_")
    ]
    panel = resolve_stats(CATALOG["base_stats"], equipment)
    challenge = CATALOG["challenges"][0]
    return make_battle_snapshot(challenge["challenge_id"], challenge["difficulty_id"],
                                panel["values"], equipment, panel["sources"],
                                bytes.fromhex(seed_hex), battle_id="local-preview")


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线复现地下城自动战斗")
    parser.add_argument("--snapshot", type=Path, help="战斗快照 JSON 文件")
    parser.add_argument("--seed", default="00" * 32, help="默认样例使用的 32 字节十六进制种子")
    parser.add_argument("--events", action="store_true", help="同时输出完整事件流")
    args = parser.parse_args(argv)
    if args.snapshot:
        raw = args.snapshot.read_text(encoding="utf-8")
        data = json.loads(raw)
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        snapshot = BattleSnapshot(canonical,
                                  hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    else:
        snapshot = starter_snapshot(args.seed)
    step = simulate(snapshot)
    report = {"battle_id": snapshot.as_dict()["battle_id"],
              "config_version": snapshot.as_dict()["config_version"],
              "snapshot_hash": snapshot.snapshot_hash,
              "seed_hex": snapshot.as_dict()["seed_hex"],
              "result": step.result}
    if args.events:
        report["events"] = step.events
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
