#!/bin/bash
# 重置本地测试环境：清库、重建测试账号、重启 chat_server
# 用法：bash tools/live_test/reset.sh（在任意目录下执行都可以）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
DB="$DIR/users.db"
source "$DIR/processes.sh"
CHAT_PORT="$(cd "$ROOT" && "$PYTHON" -c "from config import get_int; print(get_int('servers.chat_port', env='LIVE_CHAT_PORT', default=8765))")"

# 只停止本工具启动的服务，避免误伤同机其他进程。
stop_managed chat chat_server.py
if port_open "$CHAT_PORT"; then
    echo "聊天端口 $CHAT_PORT 已被其他进程占用；请先停止它。" >&2
    exit 1
fi
rm -f "$DB"
cd "$ROOT"
LIVE_DB_FILE="$DB" "$PYTHON" - <<'EOF'
import os, sqlite3, time
from server.accounts import Accounts
from server.database import database
from server.schema import init_db

db = os.environ["LIVE_DB_FILE"]
init_db()
accounts = Accounts(database)
conn = sqlite3.connect(db)
for i in range(1, 6):
    conn.execute(
        "INSERT OR IGNORE INTO invite_codes (code, created_at, created_by) VALUES (?, ?, 'seed')",
        (f"TESTCODE{i}", int(time.time())),
    )
conn.commit()
conn.close()
for i in (1, 2, 3, 4):
    accounts.register_user(f"玩家{i}", "test123456", f"TESTCODE{i}")
print("seeded")
EOF
start_managed chat chat.log env LIVE_DB_FILE="$DB" "$PYTHON" chat_server.py
wait_port chat "$CHAT_PORT" chat.log
