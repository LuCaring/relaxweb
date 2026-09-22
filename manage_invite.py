#!/usr/bin/env python3
"""直播间邀请码管理：python3 manage_invite.py gen [数量] | list"""
import secrets
import sys
import time

from server.database import database
from server.schema import init_db


def gen(count):
    init_db()
    codes = []
    with database() as conn, conn:
        for _ in range(count):
            code = secrets.token_urlsafe(8)
            conn.execute(
                "INSERT INTO invite_codes (code, created_at) VALUES (?, ?)",
                (code, int(time.time())),
            )
            codes.append(code)
    print("\n".join(codes))


def list_codes():
    init_db()
    with database() as conn:
        rows = conn.execute(
            "SELECT code, created_at, used_by, used_at FROM invite_codes "
            "ORDER BY created_at DESC"
        ).fetchall()
    for code, created, used_by, used_at in rows:
        created_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(created))
        status = "未使用"
        if used_by:
            status = f"已使用 by {used_by} at {time.strftime('%m-%d %H:%M', time.localtime(used_at))}"
        print(f"{code}\t{created_text}\t{status}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    if command == "gen":
        gen(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    elif command == "list":
        list_codes()
    else:
        print(__doc__)
        sys.exit(1)
