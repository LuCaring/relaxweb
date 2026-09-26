#!/usr/bin/env bash
# DIR and ROOT are set by the caller.
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    echo "找不到 Python 环境：$PYTHON；请先运行 uv sync --locked。" >&2
    exit 1
fi

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

stop_managed() {  # <service> <process name>
    local service="$1" expected="$2" pid_file="$DIR/$1.pid" pid command i
    [ -f "$pid_file" ] || return 0
    read -r pid < "$pid_file"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
        if [[ "$command" != *"$expected"* ]]; then
            echo "拒绝停止 PID $pid：不是 $expected。请检查 $pid_file。" >&2
            return 1
        fi
        kill "$pid"
        for i in $(seq 1 40); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "$expected (PID $pid) 未停止；请手动检查。" >&2
            return 1
        fi
    fi
    rm -f "$pid_file"
}

start_managed() {  # <service> <log> <command...>
    local service="$1" log="$2"
    shift 2
    nohup "$@" > "$DIR/$log" 2>&1 < /dev/null &
    echo "$!" > "$DIR/$service.pid"
    disown
}

wait_port() {  # <service> <port> <log>
    local service="$1" port="$2" log="$3" pid i
    read -r pid < "$DIR/$service.pid"
    for i in $(seq 1 40); do
        if port_open "$port"; then
            printf '  ✔ %-5s :%-5s pid=%-7s %s\n' "$service" "$port" "$pid" "$DIR/$log"
            return 0
        fi
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    printf '  ✘ %-5s :%-5s 未监听，见 %s\n' "$service" "$port" "$DIR/$log" >&2
    return 1
}
