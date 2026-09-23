#!/usr/bin/env python3
"""资产与运维后端管理入口（命令行）。

数据库与 chat_server 一致：读 config.json 的 database.file，可用环境变量 LIVE_DB_FILE 覆盖：
  python3 admin.py <命令> [参数]
  LIVE_DB_FILE=/path/users.db python3 admin.py <命令> [参数]

金币命令：
  list [用户名]         查看全部用户金币概览 / 指定用户的金币与最近记录
  set <用户名> <数量>    直接设置金币数量（记一条「管理员调整」明细）
  add <用户名> <数量>    增加金币
  sub <用户名> <数量>    扣除金币
  restore <用户名>       一键还原该用户：金币回新玩家默认值，删除其全部金币记录
  restore-all [-y]      一键还原所有用户：全员金币回默认值，清空全部金币记录
  clear-log [-y]        仅清空全部金币记录（不改变现有余额）

用户命令：
  delete <用户名> [-y]   物理删除用户及其全部数据（会话/金币/竞猜/庄园/段位/签到/抽奖）；
                        该用户参与中的开放竞猜未结账时会拒绝删除，先结账或流局
  edit <用户名> <字段> <值> [-y]
                        修改用户字段，支持：
                          nickname <昵称>            改昵称
                          role <user|admin|streamer> 改角色
                          password <新密码>          重置密码（至少 6 位）
                          username <新用户名>        改名（同步迁移全部关联记录）

运维命令：
  status                服务/系统/数据库状态（本地开发显示 Python 服务进程）
  restart [-y]          服务器上用 systemd 重启 live-chat / live-web / live-auth

说明：还原/清空是物理删除记录、不写对账明细；set/add/sub 会记一条
「管理员调整」明细。修改新玩家默认金币请改 config.json 的
economy.new_user_coins（或环境变量 NEW_USER_COINS），改后需重启 live-chat 服务。
"""
import os
from pathlib import Path
import psutil
import shutil
import subprocess
import sys
import time

from server.accounts import NEW_USER_COINS, hash_password, valid_username
from server.database import database
from server.schema import init_db
from server.wallet import adjust_coins

PROG = "admin.py"
SERVICES = ("live-chat", "live-web", "live-auth")
LOCAL_SERVICES = dict(zip(SERVICES, ("chat_server.py", "deploy/serve.py", "auth_server.py")))
ROLES = ("user", "admin", "streamer")
EDIT_FIELDS = ("nickname", "role", "password", "username")
# 这些表以 username 文本关联用户，改名时要一并迁移（user_id 关联的表不用动）
USERNAME_REF_TABLES = (
    "coin_transactions", "bet_entries", "bets", "game_escrows",
    "invite_codes",  # created_by / used_by 两列
    "estate_actions", "estate_fishing_sessions", "estate_mining_runs",
    "estate_tools", "estate_inventory", "estate_plots", "estate_profiles",
)
DELETION_PLAN = (
    ("auth_sessions", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("rating_history", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("holdem_hand_stats", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("holdem_player_stats", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("holdem_turnover", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("holdem_reward_claims", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("daily_checkins", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("lottery_draws", "user_id IN (SELECT id FROM users WHERE username = ?)"),
    ("bet_entries", "username = ?"),
    ("coin_transactions", "username = ?"),
    ("game_escrows", "username = ?"),
    ("estate_actions", "username = ?"),
    ("estate_fishing_sessions", "username = ?"),
    ("estate_mining_runs", "username = ?"),
    ("estate_tools", "username = ?"),
    ("estate_inventory", "username = ?"),
    ("estate_plots", "username = ?"),
    ("estate_profiles", "username = ?"),
    ("users", "username = ?"),
)


def fail(message):
    print(f"失败：{message}")
    sys.exit(1)


def get_user(conn, username):
    return conn.execute(
        "SELECT username, nickname, coins FROM users WHERE username = ?",
        (username,),
    ).fetchone()


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def money_arg(raw):
    try:
        value = round(float(raw), 2)
    except ValueError:
        fail("数量必须是数字")
    if value < 0:
        fail("数量不能为负")
    return value


def confirm(prompt, assume_yes):
    if assume_yes:
        return True
    try:
        answer = input(f"{prompt} (y/N) ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def run_quiet(args, timeout=15):
    """跑一条系统命令，返回 (成功, 去尾输出)；命令不存在或超时不算致命。"""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, ""
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


# =========================================================
# 金币命令
# =========================================================

def cmd_list(username=None):
    with database() as conn:
        if username:
            row = get_user(conn, username)
            if not row:
                fail("用户不存在")
            print(f"用户 {row[0]}  昵称 {row[1] or '-'}  金币 {row[2]:,.2f}")
            rows = conn.execute(
                "SELECT amount, balance, kind, detail, created_at "
                "FROM coin_transactions WHERE username = ? "
                "ORDER BY id DESC LIMIT 20",
                (username,),
            ).fetchall()
            if not rows:
                print("（无金币记录）")
                return
            for amount, balance, kind, detail, _ in rows:
                print(f"  {amount:+,.2f}  余额 {balance:,.2f}  [{kind}] {detail}")
            return
        rows = conn.execute(
            "SELECT username, nickname, coins FROM users ORDER BY username"
        ).fetchall()
        total = 0.0
        for name, nickname, coins in rows:
            total += coins or 0.0
            print(f"{name}  {nickname or '-'}  {coins:,.2f}")
        print(f"-- 共 {len(rows)} 名用户，金币总量 {total:,.2f}")


def cmd_adjust(mode, username, raw):
    amount = money_arg(raw)
    init_db()
    with database() as conn, conn:
        row = get_user(conn, username)
        if not row:
            fail("用户不存在")
        if mode == "add":
            delta = amount
        elif mode == "sub":
            delta = -amount
        else:
            delta = round(amount - (row[2] or 0.0), 2)
            if delta == 0:
                print(f"完成：{username} 金币已为 {amount:,.2f}（无变化）")
                return
        try:
            balance = adjust_coins(conn, username, delta, "admin", "管理员调整")
        except ValueError as error:
            fail(str(error))
        print(f"完成：{username} 当前金币 {balance:,.2f}")


def cmd_restore(username=None, assume_yes=False):
    if username is None:
        with database() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if not confirm(
            f"将 {count} 名用户的金币全部重置为 {NEW_USER_COINS:,.0f} "
            "并删除全部金币记录？",
            assume_yes,
        ):
            print("已取消")
            return
        init_db()
        with database() as conn, conn:
            conn.execute("UPDATE users SET coins = ?", (NEW_USER_COINS,))
            conn.execute("DELETE FROM coin_transactions")
        print(
            f"完成：{count} 名用户金币已重置为 {NEW_USER_COINS:,.2f}，"
            "金币记录已全部删除"
        )
        return
    init_db()
    with database() as conn, conn:
        if not get_user(conn, username):
            fail("用户不存在")
        conn.execute(
            "UPDATE users SET coins = ? WHERE username = ?",
            (NEW_USER_COINS, username),
        )
        conn.execute("DELETE FROM coin_transactions WHERE username = ?", (username,))
    print(f"完成：{username} 金币已重置为 {NEW_USER_COINS:,.2f}，其金币记录已删除")


def cmd_clear_log(assume_yes=False):
    if not confirm("确认清空全部金币记录？（不改变现有余额）", assume_yes):
        print("已取消")
        return
    init_db()
    with database() as conn, conn:
        conn.execute("DELETE FROM coin_transactions")
    print("完成：金币记录已清空（余额不变）")


# =========================================================
# 用户命令
# =========================================================

def open_bet_involving(conn, username):
    """该用户参与中的开放竞猜（发起或投注），返回描述或 None。"""
    bet = conn.execute(
        "SELECT id, question, creator FROM bets WHERE status = 'open' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not bet:
        return None
    if bet[2] == username:
        return f"竞猜 #{bet[0]}「{bet[1]}」由该用户发起"
    joined = conn.execute(
        "SELECT 1 FROM bet_entries WHERE bet_id = ? AND username = ?",
        (bet[0], username),
    ).fetchone()
    if joined:
        return f"竞猜 #{bet[0]}「{bet[1]}」有该用户的投注"
    return None


def cmd_delete(username, assume_yes=False):
    init_db()
    with database() as conn:
        row = conn.execute(
            "SELECT username, nickname, role, coins, created_at FROM users "
            "WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            fail("用户不存在")
        blocker = open_bet_involving(conn, username)
        if blocker:
            fail(f"{blocker}，请先结账或流局再删除")
        counts = []
        for table, where in DELETION_PLAN:
            if not table_exists(conn, table):
                continue
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}", (username,)
            ).fetchone()[0]
            if n:
                counts.append((table, n))
    print(
        f"将删除用户 {row[0]}（昵称 {row[1] or '-'}，角色 {row[2]}，"
        f"金币 {row[3]:,.2f}）："
    )
    for table, n in counts:
        print(f"  {table}: {n} 条")
    if not counts:
        print("  （无关联数据）")
    if not confirm("确认物理删除？删除后不可恢复。", assume_yes):
        print("已取消")
        return
    with database() as conn, conn:
        if not get_user(conn, username):
            fail("用户不存在")
        for table, where in DELETION_PLAN:
            if table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE {where}", (username,))
    print(f"完成：用户 {username} 及其全部数据已删除")


def cmd_edit(username, field, value, assume_yes=False):
    field = field.lower()
    if field not in EDIT_FIELDS:
        fail("支持的字段：" + " / ".join(EDIT_FIELDS))
    init_db()
    with database() as conn, conn:
        if not get_user(conn, username):
            fail("用户不存在")

    if field == "nickname":
        value = value.strip()
        if not 0 < len(value) <= 20:
            fail("昵称需为 1-20 个字符")
        with database() as conn, conn:
            conn.execute(
                "UPDATE users SET nickname = ? WHERE username = ?",
                (value, username),
            )
        print(f"完成：{username} 昵称改为「{value}」")
        return

    if field == "role":
        if value not in ROLES:
            fail("角色只能是：" + " / ".join(ROLES))
        with database() as conn, conn:
            conn.execute(
                "UPDATE users SET role = ? WHERE username = ?", (value, username)
            )
        print(f"完成：{username} 角色改为 {value}")
        return

    if field == "password":
        if not 6 <= len(value) <= 128:
            fail("密码至少需要 6 位，最长 128 位")
        password_hash, salt = hash_password(value)
        with database() as conn, conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
                (password_hash, salt, username),
            )
        print(f"完成：{username} 密码已重置")
        return

    # username 改名：全部以 username 文本关联的表都要迁移，一个事务完成
    new_name = value.strip()
    if new_name == username:
        print(f"完成：{username} 名字无变化")
        return
    if not valid_username(new_name):
        fail("用户名需为2-20位中文、英文、数字、_ 或 -")
    with database() as conn:
        clash = conn.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (new_name,)
        ).fetchone()
    if clash:
        fail(f"用户名 {new_name} 已存在")
    prompt = f"确认把 {username} 改名为 {new_name}？将同步迁移其全部关联记录"
    if not confirm(prompt, assume_yes):
        print("已取消")
        return
    with database() as conn, conn:
        if not get_user(conn, username):
            fail("用户不存在")
        conn.execute(
            "UPDATE users SET username = ? WHERE username = ?", (new_name, username)
        )
        moved = 0
        for table in USERNAME_REF_TABLES:
            if not table_exists(conn, table):
                continue
            columns = {info[1] for info in conn.execute(f"PRAGMA table_info({table})")}
            for column in ("username", "creator", "created_by", "used_by"):
                if column in columns:
                    cur = conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        (new_name, username),
                    )
                    moved += cur.rowcount
    print(f"完成：{username} 已改名为 {new_name}（迁移 {moved} 条关联记录）")


# =========================================================
# 运维命令
# =========================================================

def service_lines():
    if not Path('/run/systemd/system').exists():
        root = Path(__file__).resolve().parent
        found = {}
        for process in psutil.process_iter():
            try:
                if process.uids().real != os.getuid():
                    continue
                if not Path(process.exe()).name.lower().startswith('python'):
                    continue
                args = process.cmdline()
                if len(args) < 2 or args[1].startswith('-'):
                    continue
                script = (Path(process.cwd()) / args[1]).resolve()
                for service, relative in LOCAL_SERVICES.items():
                    if script == root / relative:
                        found[service] = process.pid
            except (psutil.Error, OSError, UnicodeError):
                continue
        return [f"  {service:<10} 本地运行中（PID {found[service]}）" if service in found
                else f"  {service:<10} 本地未启动" for service in SERVICES]
    lines = []
    for service in SERVICES:
        ok, state = run_quiet(["systemctl", "is-active", service])
        ok2, since = run_quiet(
            ["systemctl", "show", service, "-p", "ActiveEnterTimestamp", "--value"]
        )
        if not ok and not state:
            lines.append(f"  {service:<10} 不可用（本机无 systemd 或服务未安装）")
            continue
        lines.append(f"  {service:<10} {state or 'unknown'}   自 {since or '?'}")
    return lines


def online_count_line():
    if not Path('/run/systemd/system').exists():
        return None
    ok, out = run_quiet(
        ["journalctl", "-u", "live-chat", "--since", "-30min", "--no-pager", "-q"]
    )
    if ok:
        marks = [line for line in out.splitlines() if "online=" in line]
        if marks:
            return marks[-1].split(";", 1)[-1].strip() or marks[-1][-60:]
    return None


def cmd_status():
    print("== 服务 ==")
    for line in service_lines():
        print(line)
    online = online_count_line()
    if online:
        print(f"  最近在线：{online}")

    print("\n== 系统 ==")
    uptime = max(0, int(time.time() - psutil.boot_time()))
    days, remainder = divmod(uptime, 86400)
    hours, minutes = divmod(remainder, 3600)
    print(f"  运行时长：{days} 天 {hours} 小时 {minutes // 60} 分钟")
    load = ", ".join(f"{v:.2f}" for v in os.getloadavg())
    print(f"  负载：{load}")
    total, used, free = shutil.disk_usage(os.getcwd())
    print(
        f"  磁盘（应用目录）：已用 {used >> 30}G / 共 {total >> 30}G"
        f"（剩 {free >> 30}G）"
    )
    memory = psutil.virtual_memory()
    print(f"  内存：{memory.used >> 20}M 已用 / {memory.total >> 20}M"
          f"（可用 {memory.available >> 20}M）")

    print("\n== 数据库 ==")
    with database() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        coins = conn.execute("SELECT ROUND(SUM(coins), 2) FROM users").fetchone()[0]
        print(f"  用户 {users} 名，金币总量 {coins or 0:,.2f}")
        bet = conn.execute(
            "SELECT id, question, creator, created_at FROM bets "
            "WHERE status = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if bet:
            age = max(0, int(time.time()) - bet[3])
            print(f"  进行中竞猜：#{bet[0]}「{bet[1]}」发起者 {bet[2]}（已 {age // 60} 分钟）")
        else:
            print("  进行中竞猜：无")
        if table_exists(conn, "invite_codes"):
            unused = conn.execute(
                "SELECT COUNT(*) FROM invite_codes WHERE used_by IS NULL"
            ).fetchone()[0]
            print(f"  可用邀请码：{unused} 个")
        if table_exists(conn, "estate_profiles"):
            estates = conn.execute("SELECT COUNT(*) FROM estate_profiles").fetchone()[0]
            print(f"  庄园档案：{estates} 份")

    print("\n== 最近 24h 错误 ==")
    if not Path('/run/systemd/system').exists():
        print("  本地服务日志请查看启动服务的终端")
        return
    found = False
    for service in SERVICES:
        ok, out = run_quiet(
            ["journalctl", "-u", service, "-p", "err", "--since", "-24h",
             "--no-pager", "-q"]
        )
        if ok and out:
            found = True
            for line in out.splitlines()[-5:]:
                print(f"  {line}")
    if not found:
        print("  无")


def cmd_restart(assume_yes=False):
    if not Path('/run/systemd/system').exists():
        fail("本地开发服务请在对应终端按 Ctrl+C 停止后重新启动；systemd 重启仅用于服务器部署")
    ok, _ = run_quiet(["sudo", "-n", "true"], timeout=5)
    if not ok:
        fail("重启需要免密 sudo（admin 用户在服务器上运行即可）。"
             "也可手动：sudo systemctl restart live-chat live-web live-auth")
    if not confirm("确认重启 live-chat / live-web / live-auth？线上连接会短暂断开", assume_yes):
        print("已取消")
        return
    ok, out = run_quiet(
        ["sudo", "-n", "systemctl", "restart", *SERVICES], timeout=60
    )
    if not ok:
        fail(f"重启命令失败：{out}")
    time.sleep(1.5)
    bad = []
    for service in SERVICES:
        ok, state = run_quiet(["systemctl", "is-active", service])
        print(f"  {service:<10} {state or 'unknown'}")
        if state != "active":
            bad.append(service)
    if bad:
        fail("以下服务未恢复 active，请 journalctl -u " + bad[0] + " 排查")
    print("完成：全部服务已重启并恢复 active")


# =========================================================
# 入口
# =========================================================

def main(argv):
    args = [a for a in argv if a != "-y"]
    assume_yes = len(args) != len(argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    command, rest = args[0], args[1:]

    if command == "list":
        cmd_list(rest[0] if rest else None)
    elif command == "set" and len(rest) == 2:
        cmd_adjust("set", rest[0], rest[1])
    elif command == "add" and len(rest) == 2:
        cmd_adjust("add", rest[0], rest[1])
    elif command == "sub" and len(rest) == 2:
        cmd_adjust("sub", rest[0], rest[1])
    elif command == "restore" and len(rest) == 1:
        cmd_restore(rest[0], assume_yes)
    elif command == "restore-all":
        cmd_restore(None, assume_yes)
    elif command == "clear-log":
        cmd_clear_log(assume_yes)
    elif command == "delete" and len(rest) == 1:
        cmd_delete(rest[0], assume_yes)
    elif command == "edit" and len(rest) == 3:
        cmd_edit(rest[0], rest[1], rest[2], assume_yes)
    elif command == "status":
        cmd_status()
    elif command == "restart":
        cmd_restart(assume_yes)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
