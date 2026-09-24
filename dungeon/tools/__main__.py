from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path
import sys
import time
from uuid import uuid4

from dungeon.content import ContentError, load_ruleset


def _init_test_db(path, coins, password):
    """Create the two B/C integration accounts; safe to rerun."""
    from server.accounts import hash_password
    from server.database import database
    from server.schema import init_db
    from dungeon.legacy.service import ensure_dungeon

    factory = partial(database, str(Path(path)))
    init_db(factory)
    created, skipped = [], []
    with factory() as conn, conn:
        now = int(time.time())
        for username, tradable in (("beta_seller", True), ("beta_buyer", False)):
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                skipped.append(username)
                continue
            digest, salt = hash_password(password)
            conn.execute("""INSERT INTO users(username,password_hash,salt,created_at,coins)
                VALUES (?,?,?,?,?)""", (username, digest, salt, now, float(coins)))
            user_id = conn.execute("SELECT id FROM users WHERE username=?",
                                   (username,)).fetchone()[0]
            ensure_dungeon(conn, username, now)
            if tradable:
                conn.execute("""INSERT INTO dungeon_items
                    (item_id,owner,template_id,template_version,display_name,visual_id,slot,
                     quality,stats_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                             (uuid4().hex, username, "beta.sword.basic", "0.1.0", "遗迹长剑",
                              "beta.visual.ruins_blade", "weapon", "normal",
                              '{"atk":30}', now))
                conn.execute("""INSERT INTO dungeon_beta_progress(user_id,progress_id)
                    VALUES (?,'beta.clear.first_boss')""", (user_id,))
            created.append(username)
    print(json.dumps({"path": str(path), "created": created, "skipped_existing": skipped,
                      "coins_each": coins, "password": password,
                      "note": "beta_seller owns one tradable sword with boss progress;"
                              " starter gear is bound and not tradable"},
                     ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dungeon.tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-content", help="validate and hash a release")
    validate.add_argument("release", help="path to release.json")
    init = subparsers.add_parser("init-test-db", help="create B/C integration accounts")
    init.add_argument("path", help="database file to create or extend")
    init.add_argument("--coins", type=int, default=1000, help="coins per account")
    init.add_argument("--password", default="dungeon-beta", help="login password")
    args = parser.parse_args(argv)
    if args.command == "init-test-db":
        _init_test_db(args.path, args.coins, args.password)
        return 0
    try:
        ruleset = load_ruleset(args.release)
    except ContentError as exc:
        print(f"invalid content: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(dict(ruleset.reference()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
