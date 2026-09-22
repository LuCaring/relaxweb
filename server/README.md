# 服务端模块

`chat_server.py` 是 systemd 和本地开发的薄启动入口。`server.app.create_app()`
装配独立的应用实例，不访问数据库、不启动后台任务；`app.run(host, port)` 负责
建表、恢复托管与竞猜、监听 WebSocket 和管理关闭流程。启动命令和配置项不变。

## 职责与前端对应关系

按业务职责对应 `assets/js/`，不强制把展示模块逐一复制到后端。

| 模块 | 职责 | 对应前端 |
| --- | --- | --- |
| `app.py`、`routing.py` | 显式装配、消息注册、启动和连接生命周期 | `main.js`、`registry.js` |
| `transport.py` | 连接集合、带锁收发、广播和在线人数 | `core.js` |
| `database.py`、`schema.py` | SQLite 连接、建表与兼容迁移 | 无 |
| `accounts.py`、`auth.py` | 账号持久化、会话、资料和注销协议 | `auth.js` |
| `wallet.py` | 金币流水原语、账单、转账与候选用户 | `auth.js` 的金币部分 |
| `admin.py` | 管理员改币、邀请码协议 | `auth.js` 的管理部分 |
| `ranking.py` | 段位、历史和资产排行榜 | `rating.js`、`asset-ranking.js` |
| `rewards.py` | 每日奖励与德州奖励协议 | `auth.js` 的奖励部分 |
| `chat.py` | 公共聊天、系统消息与历史 | `assets/app.js` 的聊天部分 |
| `rooms/protocol.py` | 开房、入座、观战、游戏操作与房间聊天 | `hall.js`、`create-room.js`、`room.js`、`room-chat.js` |
| `rooms/host.py` | 房间集合、引擎回调、视图分发与断线清理 | `room-waiting.js`、`room-settlement.js` |
| `rooms/settlement.py` | 评分、统计、托管与退出退款的持久化事务 | `room-settlement.js` |
| `estate/protocol.py` | 庄园消息、事务编排、快照和错误响应 | `estate/protocol.js` |
| `estate/presence.py` | 拜访频道、移动校验、离线清理 | `estate/state.js`、`estate/protocol.js` |
| `betting.py` | 竞猜、结算、恢复与自动封盘 | `assets/app.js` 的竞猜部分 |

## 依赖与状态

- `server/` 不反向导入 `chat_server`。管理 CLI 直接依赖存储模块，不创建应用实例。
- 根目录 `games/`、`estate/`、`rewards.py`、`holdem_stats.py` 保留业务规则；
  `server/` 负责协议、在线状态和事务编排。本次拆分不改变规则和数据库格式。
- 模块只接收所需依赖，不接受整个 `Application`。`ConnectionHub` 拥有连接字典；
  `RoomHost` 拥有房间、编号和离桌计时器；聊天、注册限流、竞猜和庄园频道也属于实例。
- `handlers()` 显式列出消息类型；`merge_handlers()` 拒绝重复注册。现有 65 种消息的
  名称、权限检查、响应字段与发送顺序保持不变。
- 所有 JSON 和已编码消息都经过 `ConnectionHub` 的连接发送锁。不要直接发送 socket。
- 房间引擎通过 `RoomHost.attach_host()` 获得广播、资料、评级和托管能力，不查询应用全局变量。
- 连接断开时，先退出庄园频道，再移除连接，随后执行房间断线检查和在线人数广播。
- `run()` 退出监听后调用 `aclose()`，取消并等待封盘和宿主离桌任务、取消离桌计时器、
  关闭房间计时器。不在关闭时额外退款；残留托管仍由下次启动退款。
- 为保持行为一致，原有断线清理的两阶段等待时序未改动。

## 事务约束

`database(path=None)` 返回退出时关闭连接的上下文管理器，不自动提交。
写操作继续使用 `with database() as conn, conn:`；需要提前加锁的流程保留
`BEGIN IMMEDIATE`。`wallet` 原语只使用调用方传入的 `conn`，不另开连接或提交。

`Settlement.record_hand_ratings()` 在同一事务内完成评分、德州统计、托管与下注流水。
迁移没有拆开事务，也没有在这些同步写入步骤之间新增 `await`。
排行榜的页码、排名、本人和统计仍从同一读事务快照生成。

## 测试与扩展

推荐测试通过独立应用和显式数据库路径隔离状态：

```python
from functools import partial
from server.app import create_app
from server.database import database
from server.schema import init_db

app = create_app(partial(database, temporary_database_path))
init_db(app.database)
# 协议测试：websockets.serve(app.handler, "127.0.0.1", 0)
# 生命周期结束：await app.aclose()
```

已有兼容测试也可 patch `server.database.DB_FILE`，但不要替换已注入的连接字典对象。
故障注入应定位实际模块，例如 `server.rooms.settlement.record_holdem_hand`；
不再 patch 启动入口。`chat_server.py` 不重新导出旧的业务函数或全局状态。

新增消息时在所属模块的 `handlers()` 注册；新业务模块在 `app.py` 显式装配。
新增玩法仍走 `games.base` 注册表，无需在每个协议中增加玩法分支。

```bash
python3 tests/test_server_app.py
python3 tests/test_server_modules.py
python3 tests/test_holdem_stats.py
python3 tests/test_holdem_rewards.py
python3 tests/test_rewards_protocol.py
python3 tests/test_spectator.py
python3 tests/test_table_leave_protocol.py
python3 tests/test_uno_challenge_protocol.py
```

`test_server_app.py` 覆盖实例隔离、65 种路由、连接锁、发送失败、在线统计、
账号/转账/注销流程、断线恢复和后台任务释放。`test_server_modules.py` 覆盖
存储回滚、管理命令、独立竞猜和庄园，以及真实启动入口的退款、竞猜恢复与定时封盘。
UNO 协议测试现使用临时数据库及随机端口，不依赖预先运行的本地服务。
