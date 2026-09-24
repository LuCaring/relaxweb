from __future__ import annotations

import argparse
import json
import sys

from dungeon.content import ContentError, load_ruleset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dungeon.tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-content", help="validate and hash a release")
    validate.add_argument("release", help="path to release.json")
    args = parser.parse_args(argv)
    try:
        ruleset = load_ruleset(args.release)
    except ContentError as exc:
        print(f"invalid content: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(dict(ruleset.reference()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
