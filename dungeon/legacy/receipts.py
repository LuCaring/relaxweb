"""地下城写操作共用的请求摘要和持久化回执。"""

import hashlib
import json
import re

from dungeon.legacy.service import DungeonError


REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def request_digest(action, payload, *, expected_version=None):
    body = {"action": action, "payload": payload}
    if expected_version is not None:
        # Preserve the digest format of receipts written before this extraction.
        body["expected_version"] = expected_version
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def load_receipt(conn, username, request_id, action, digest):
    row = conn.execute("""SELECT action_type,request_hash,result_json FROM dungeon_actions
        WHERE username=? AND request_id=?""", (username, request_id)).fetchone()
    if row is None:
        return None
    if row[0] != action or row[1] != digest:
        raise DungeonError("request_conflict", "请求编号已用于其他操作")
    return {**json.loads(row[2]), "replayed": True}


def save_receipt(conn, username, request_id, action, digest, result, now_seconds):
    conn.execute("""INSERT INTO dungeon_actions
        (username,request_id,action_type,request_hash,result_json,created_at)
        VALUES (?,?,?,?,?,?)""",
        (username, request_id, action, digest, canonical_json(result), int(now_seconds)))
