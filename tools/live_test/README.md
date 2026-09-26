# 本地联调与真实协议脚本

这里的工具随主仓库维护，用独立的 `tools/live_test/users.db` 和本机服务做手动联调。普通单元测试及浏览器测试仍使用仓库根目录的 `tests/`，不需要启动这些工具。

先在仓库根目录运行 `uv sync --locked`。以下命令也从仓库根目录执行：

```bash
bash tools/live_test/reset.sh        # 重建测试库及玩家1～4，仅启动聊天服务
bash tools/live_test/restart_all.sh  # 保留测试库，重启聊天、静态页和认证服务
uv run --locked python tools/live_test/proto_test.py
uv run --locked python tools/live_test/uno_proto_test.py
```

账号为 `玩家1`～`玩家4`，密码都是 `test123456`。`reset.sh` 会删除**本目录**的测试库；`restart_all.sh` 保留它。脚本只停止它们自己记录 PID 的服务；若端口被其他进程占用，会报错退出。服务日志与 PID 文件也放在本目录，已被忽略。

其他手动工具：

| 文件 | 用途 |
| --- | --- |
| `guandan_test.py`、`mahjong_test.py` | 四人真实 WebSocket 对局与结算回归 |
| `bot.py`、`poker_bot.py`、`uno_bot.py` | 陪打机器人 |
| `guandan_joiner.py`、`mahjong_joiner.py` | 浏览器手动联调时自动补齐玩家 |
| `merge_verify.py`、`settle_verify.py` | 金币流水与结算投票排查 |
| `peek.py` | 打印当前房间视图 |

Python 脚本默认连接 `config.json` / `LIVE_CHAT_PORT` 指定的聊天端口，地址可用 `LIVE_TEST_WS_URL` 覆盖。服务脚本默认使用 `.venv/bin/python`，可用 `PYTHON` 覆盖。
