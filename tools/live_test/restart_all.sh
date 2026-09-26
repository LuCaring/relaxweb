#!/bin/bash
# 一键重启全部服务（chat / web / auth），用于本地联调与测试。
#
# 与 reset.sh 的分工：
#   reset.sh      —— 清库、重建测试账号，只重启 chat_server；
#   restart_all.sh—— 不动任何数据，把三个服务一起重启。
# 后端代码改动后跑本脚本，三个服务都会用新代码起来（MediaMTX 是外部服务，不在其中）。
#
# 用法：bash tools/live_test/restart_all.sh（在任意目录下执行都可以）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
DB="$DIR/users.db"
source "$DIR/processes.sh"
if [ ! -f "$DB" ]; then
    echo "测试库不存在；请先运行 bash tools/live_test/reset.sh。" >&2
    exit 1
fi

# 端口按项目自己的规则解析（环境变量 > config.json > 默认值），与三个服务启动时一致，
# 所以改了 config.json 里的端口，这里也跟着变。
read -r CHAT_PORT WEB_PORT AUTH_PORT < <(cd "$ROOT" && "$PYTHON" -c "
from config import get_int
print(get_int('servers.chat_port', env='LIVE_CHAT_PORT', default=8765),
      get_int('servers.web_port', env='LIVE_WEB_PORT', default=8000),
      get_int('servers.auth_port', env='LIVE_AUTH_PORT', default=8001))
")
if [ -z "$CHAT_PORT" ] || [ -z "$WEB_PORT" ] || [ -z "$AUTH_PORT" ]; then
    echo "解析不出端口，请检查 config.json。" >&2
    exit 1
fi

# 只重启本工具启动的进程。
stop_managed chat chat_server.py
stop_managed web deploy/serve.py
stop_managed auth auth_server.py

# 端口必须真的空出来，否则新进程绑不上，后面的就绪检测会被旧进程骗过去
busy=""
for spec in "chat:$CHAT_PORT" "web:$WEB_PORT" "auth:$AUTH_PORT"; do
    if port_open "${spec#*:}"; then busy="$busy ${spec%%:*}(${spec#*:})"; fi
done
if [ -n "$busy" ]; then
    echo "端口仍被占用:$busy —— 可能有残留进程没清掉，先处理再重试" >&2
    exit 1
fi

echo "重启本地测试服务（数据不动，库 ${DB}）:"
cd "$ROOT"
# chat/auth 共用测试库；web 只提供静态资源。
start_managed chat chat.log env LIVE_DB_FILE="$DB" "$PYTHON" chat_server.py
wait_port chat "$CHAT_PORT" chat.log
start_managed web web.log "$PYTHON" deploy/serve.py
wait_port web "$WEB_PORT" web.log
start_managed auth auth.log env LIVE_DB_FILE="$DB" "$PYTHON" auth_server.py
wait_port auth "$AUTH_PORT" auth.log
echo "全部就绪：游戏页 http://localhost:$WEB_PORT/game.html"
