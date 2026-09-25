# 地下城 Beta 后端运行时架构设计（本地稿）

日期：2026-09-25。基于 `dev/dungeon-beta-foundation` @ `0625fa2` 本地代码盘点后编写，未提交。
**2026-09-25 更新：本文第 2～4 节的组件（`Simulator.view` 合同、`RunHost.frame`、`RunScheduler`、
`DungeonBetaActionProtocol`、`app.py` 装配与生命周期挂钩）已在本地实现并通过
`tests/test_dungeon_beta_scheduler.py` 与 `tests/test_dungeon_beta_action.py`；尚未经陆弘毅评审合入。**
目标方案以 [dungeon-beta-architecture.md](dungeon-beta-architecture.md) 为准；本文只做一件事：
把其中 R4「运行时接入收尾」落到可实现的组件、线程与消息设计上。

## 1. 现状盘点（以代码为准）

已闭环的「营地链路」（请求-响应式，无需改动）：

```text
客户端 → DungeonBetaProtocol (server/dungeon/beta_protocol.py)
       → AssetService / TradeService / StateService (dungeon/application/)
       → SQLite (dungeon/storage/，迁移1~4，回执+预留+资产版本)
```

- 9 个 `dungeon_beta_*` 消息已注册，走 JSON Schema 校验 + `asyncio.to_thread`（4 并发信号量）。
- 回执 `(user_id, request_id)` 幂等、`dungeon_beta_invalidate` 推送、金币缓存刷新已实现。

已实现但**未接线**的「战斗链路」内部件：

| 组件 | 位置 | 能力 | 缺口 |
| --- | --- | --- | --- |
| `RunService` | `dungeon/application/runs.py` | 起局（预留装备+READY 记录+回执）、检查点、`commit_room` 同事务发奖、`recover_unfinished` | 无客户端入口 |
| `RunHost` | `dungeon/runtime/host.py` | 每 tick pump、输入预算/序号/控制代次、1 秒检查点、清房事件→发奖、崩溃自动 PAUSED | 手动调用；无调度、无推送、无生命周期 |
| `Simulator` 合同 | `dungeon/contracts/simulator.py` | create/step/snapshot/restore 四方法 Protocol | 只有测试桩，无真实模拟器（B 线） |
| `Application` | `server/app.py` | 装配了 `beta_protocol` | 未装配 host/scheduler；`aclose` 不覆盖地下城运行 |

结论：**本分支后端剩余架构工作 = 把 RunHost 接进传输与生命周期，即 R4。**
以下设计只新增三块：调度器、动作协议适配、生命周期挂钩。

## 2. 组件设计

### 2.1 新增 `dungeon/runtime/scheduler.py`：RunScheduler

唯一负责"让权威局按节拍推进"的 asyncio 组件：

```python
class RunScheduler:
    def __init__(self, host, *, tick_rate=30, executor=None):
        # tick_rate=30：与文档 30Hz 档位一致；executor 默认单线程池
        self._active: dict[str, asyncio.Task] = {}   # run_id -> 每局推进任务

    async def attach(self, run_id): ...      # resume/接管成功后调用
    async def detach(self, run_id): ...      # pause/finish/abandon 后调用
    async def aclose(self): ...              # 停服：等待在途 pump 结束
```

每个活跃局一个协程任务，循环体：

```text
deadline += 1/30（整数 tick 累计，禁止每帧四舍五入累计漂移）
catch-up = min(欠账 ticks, host.max_step_ticks)   # 超欠部分放弃 → 时间膨胀，不螺旋死亡
result = await loop.run_in_executor(executor, host.pump, run_id, catch_up)
if result 是清房结果: detach(run_id) + 推送 room_cleared 结果
elif host 里已无该局（pause/异常）: detach(run_id)
每 10 个 tick（≈3 帧）触发一次 FramePusher（见 2.3）
```

关键决策：

- **executor 默认 `ThreadPoolExecutor(max_workers=1)`**：所有模拟串行在一个线程，
  与 `RunHost._runs` 的非线程安全内存态天然互斥（不需要再加锁），SQLite 连接
  在该线程内随用随建随关，符合"连接在哪个线程创建就在哪关"约束。并发局增多、
  P95 step 超标时再提高 workers 并引入每 run 串行锁，接口不变。
- **调度器只调 `host.pump`，不碰模拟器、不碰 SQL**。`RunHost` 已有的崩溃处理
  （丢弃内存进度→自动 PAUSED）就是调度器看到的异常路径，不需要重复设计。
- 不做进程内多实例分片；单进程单 scheduler，与文档 §7.1 一致。

### 2.2 新增 `server/dungeon/beta_action_protocol.py`：动作消息适配

独立于现有 `beta_protocol.py`（营地消息不动），同样从鉴权会话解析 user_id，
但**不复用 `_blocking` 信号量**——动作消息高频，走独立路径：

| 消息 | 方向 | 处理 |
| --- | --- | --- |
| `dungeon_beta_start_run` | 请求 | `RunService.start`（已有回执幂等）→ 成功后自动 `take_control` |
| `dungeon_beta_take_control` | 请求 | `host.take_control` → 返回新 `control_epoch`；记录 run→websocket 映射 |
| `dungeon_beta_resume` / `dungeon_beta_pause` / `dungeon_beta_abandon` | 请求 | 对应 host 方法；resume 成功后 `scheduler.attach` |
| `dungeon_beta_input` | 高频推送 | **不等回执**：限频后 `run_in_executor(host.input)`，失败只回 `dungeon_beta_error`，无 session 回执（属文档定义的非持久命令） |
| `dungeon_beta_sync` | 请求 | 返回 `host.status` + 最近一次 frame 快照，客户端丢帧后兜底 |
| `dungeon_beta_frame`（推送）/ `dungeon_beta_room_cleared`（推送） | 服务端→客户端 | 见 2.3；`dungeon_beta_invalidate` 机制保持不变 |

Schema：`REQUEST_DEFINITIONS` 与 `contracts/dungeon/schemas/beta_messages.schema.json`
按现有格式追加动作消息定义；`input` 的字段校验以 `RunHost.input` 的白名单为准
（move/aim ∈ [-1024,1024] 整数，buttons ⊆ {attack,dash,potion}），schema 与 host 保持同源。

控制权规则（复用 host 现状，协议层只补映射）：

- 每 run 至多一个控制器 websocket；`take_control` 必然落到 durable PAUSED 检查点并
  `control_epoch += 1`，旧连接的 input 因 epoch 不匹配被拒（host 已保证）。
- **断线**：连接关闭时若该 websocket 持有 run X，启动宽限计时（默认 30s，与
  `disconnect_grace` 对齐），到期仍未重连接管则 `host.pause(X)`；宽限期内模拟
  继续推进——这是文档 §7.3 已声明并接受的边界，UI 文案要如实说明。
- **双标签**：第二个标签 `take_control` 成功后，第一个标签靠 epoch 失配自然降级为只读。

### 2.3 帧推送：先加一个轻量 `view` 合同

`Simulator.snapshot()` 是**存档**（≤256KB 上限，含 RNG/冷却/插件状态），10Hz 全量推
不可接受。建议给 `Simulator` Protocol 追加第五个方法（对 B 线的唯一合同变更，需评审）：

```python
def view(self, state: Any) -> JsonObject:
    """面向渲染的轻量读视图：玩家/敌人/投射物的位置与关键动画态。"""
```

- `FramePusher` 每 3 tick 调 `simulator.view`，经 canonical 序列化后按 run 广播给
  控制端与只读观察者；预算上限单独设（建议 16KB/帧）。
- 客户端预测只用于移动表现；`dungeon_beta_frame` 携带权威 `server_tick`，
  与 `durable_tick`（最近检查点）区分展示，重连时提示"已恢复到存档点"。
- 合同未加 `view` 前的过渡方案：直接推 `snapshot()`（60 字节级测试桩可用，
  真模拟器接入前必须完成合同变更）。

### 2.4 生命周期挂钩（`server/app.py`）

```text
启动:  init_db → RunService.recover_unfinished()   # 活动局一律转 PAUSED
       → 装配 RunService + RunHost + RunScheduler + 动作协议（并入 merge_handlers）
运行:  scheduler 只在 Application.run 的事件循环内存活
停服:  Application.aclose() 顺序 = scheduler.aclose()（冻结输入、等在途 pump）
       → host.close()（活动局逐个 pause 落检查点）→ 现有 rooms.aclose()
```

崩溃恢复边界复用现状：进程任意时点被杀，重启后 `recover_unfinished` 把 ready/running
转 PAUSED（epoch+1），客户端重连后 `take_control` → `resume` 从最近 1 秒检查点重放；
已确认的房间奖励由 `dungeon_beta_room_rewards` 唯一键保证不重发（已实现并有测试）。

## 3. 线程与锁矩阵（设计不变量）

| 资源 | 谁写 | 并发保护 |
| --- | --- | --- |
| `RunHost._runs` 内存态 | 模拟 executor 线程 + 事件循环的 attach/pause | 默认单工作线程互斥；input 经 executor 排队入队 |
| SQLite 写事务 | 各用例 `BEGIN IMMEDIATE` | 事务内只做读表+计算+写表（现状已守住：模拟 step、事件序列化都在锁外） |
| 输入队列 | host.input（executor 内） | max_queue=64 / 每 tick 8 条，超限报 `input_budget`（已实现） |
| 控制权 | host.take_control（写事务 + epoch 条件更新） | 双标签竞争由 `run_revision`/epoch 条件更新裁决（已实现） |
| 金币 | `SharedWalletPort`（营地与局内结算共用） | 唯一权威仍是 users.coins，Beta 不建第二余额 |

需要验证的一个点：`server/database.py` 连接工厂是否启用 WAL——10 个并发局 × 1 秒
检查点写 + 营地事务 + 聊天，回退 journal 模式下写锁竞争会更早出现；若未开 WAL，
在迁移演练里一并评估，不要在运行时代码里私自 PRAGMA。

## 4. 待办与实现顺序（本分支范围）

1. **合同变更**：`beta_messages.schema.json` 注册动作消息；与 B 线确认 `Simulator.view`
   是否进合同（阻塞帧推送设计）。
2. **RunScheduler**（`dungeon/runtime/scheduler.py`）+ 测试：节拍漂移、catch-up 上限、
   清房后 detach、aclose 等待语义。
3. **beta_action_protocol** + 测试：鉴权/限频/断线宽限/双标签 epoch/输入预算路径。
4. **app.py 装配**：启动恢复 + aclose 挂钩 + `tests/test_dungeon_beta_runtime.py`
   扩展端到端（协议→调度→pump→清房发奖→重启恢复）。
5. **观测**：最小指标集——step 耗时 P95、活跃局数、检查点落后 tick、输入队列深度、
   SQLite busy 计数（文档 §10.3 的首日子集，先打日志不做面板）。

明确不做（与文档 §11 对齐）：多进程房间分片、消息队列、插件热替换、客户端造奖接口、
把 `pump` 暴露为任何客户端可调用的消息。

## 5. 与现有文档的差异点（需陆弘毅确认后落码）

- `Simulator.view` 是对已冻结合同的新增方法——架构文档 §7.1 只约定了四方法合同，
  本文建议扩到五方法；若 B 线倾向不改合同，备选是 simulator 在 snapshot 内区分
  `save`/`view` 两段，但那会把渲染关注点漏进存档格式。
- 断线宽限期内继续模拟：文档 §7.3 说"连接失联检测窗口内仍可能继续模拟"，本文把
  窗口具体化为 30s 且由协议层计时；若希望失联即暂停，删掉 2.2 的宽限计时即可，
  其余设计不受影响。
