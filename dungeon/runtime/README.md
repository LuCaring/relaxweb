# Beta 运行宿主接入

当前为内部同步宿主，注入受信任的 `Simulator` 后手动推进。没有正式动作模拟器、自动调度、动作WebSocket注册或Application生命周期装配。可运行示例在 [运行回归](../../tests/test_dungeon_beta_runtime.py)，其中模拟器只用于测试，不能当作游戏实现发布。

## A/B 接入次序

1. 启动时初始化迁移，并在允许新局/恢复前调用 `RunService.recover_unfinished()`，将遗留READY/RUNNING转为PAUSED。只在单服务启动时调用，不能在每次连接时重置活动局。
2. 为每个固定规则组合创建 `RunService(database, rules, simulator)`。P0加载 `content/dungeon/release-p0.json`；正式默认release没有路线，不会自动启用P0。
3. 认证层取得user_id，调用 `start(user_id, request_id, route_id, expected_asset_revision=...)`。起局冻结穿戴、预留实例并提交READY；重复请求返回原结果。
4. `RunHost(service).resume(run_id)` 初始化当前房间并进入RUNNING。按规则tick_rate进行有界调度，逐次 `pump(run_id)`；每次step固定1 tick。初始预算允许每次pump最多4 tick，不根据客户端时间快进。
5. 普通清房提交奖励后变READY，下一房需要显式resume。最后房变FINISHED并释放装备；abandon变ABANDONED并释放，保留已经确认的房间奖励。
6. 正常停服调用host.close保存并暂停其已挂载运行局；异常退出下次启动按最后durable检查点恢复。Application接线由A与B合流时完成。

所有宿主调用必须在同一串行执行上下文中进行。当前没有线程安全锁或跨进程运行租约，不能由多个线程同时调用同一个host。接传输时，在入口核对run所属user_id和控制连接，配有界队列/专用执行器；不要把无鉴权的内部方法直接映射为公共路由。内部服务返回的initial/checkpoint包含私有随机状态，不能原样发给客户端。

## 输入与模拟器

`Simulator` 签名以 [Protocol](../contracts/simulator.py) 为准。`create/restore` 不发事件；`step` 经 `services.emit` 发JSON对象事件，随机数仅使用 `services.random_int(stream, lower, upper)`。有序输入列表含input_seq，默认单tick最多消费8条。事件默认64条/8192字节，快照默认262144字节，超限暂停并回退未提交进度。

```python
host.input(run_id, control_epoch, input_seq, {
    "move_x": 0, "move_y": 0, "aim_x": 1024, "aim_y": 0,
    "buttons": ["attack"],
})
```

方向为−1024～1024的整数，buttons只能为不重复的attack/dash/potion列表；移动规范化和动作判定由B实现。input_seq连续递增；重复包忽略，缺号拒绝，默认队列上限64。接管会递增epoch并暂停，必须显式resume；旧epoch不能继续输入或提交检查点。

每房 `create` 的initial含冻结equipment、route_id、room_index、encounter_id、encounter（含spawn_groups）、run_resources及previous_room_snapshot。B从上一房快照继承生命、构筑等持续状态，重建本房敌人；不能每房默认为满血。`snapshot` 必须是可JSON化对象，包含room_index以及全部影响结果的模拟状态。宿主独立保存RNG位置，不由客户端提供种子。

局内资源放在snapshot.run_resources中；P0使用 `potions: [{count, heal_max_hp_bp}]`。B负责拾取/消耗/治疗并更新快照，宿主在清房时追加配置奖励。周期检查点以快照里的最新run_resources为准，防止已消耗药瓶恢复后再生。

## 清房与持久化

可信step可在同一tick发战斗事件及唯一清房事件：

```python
services.emit({"type": "room_cleared", "room_index": room_index,
               "encounter_id": encounter_id})
```

宿主核对当前房间，把结果送内部 `commit_room`。永久金币/掉落/进度完全从固定规则计算；模拟事件不能指定发奖金额。奖励、钱包流水、装备实例、房间唯一回执与下一房检查点共用一个写事务，失败全部回滚。`(run_id, room_index, reward_kind)` 防重复发奖。药瓶不进入永久背包或钱包。

普通检查点默认间隔30 tick；server_tick是内存进度，durable_tick是已落盘进度。异常恢复可能损失未落盘输入，但不重复已确认奖励。规则摘要、模拟/存档版本必须匹配，缺失旧版本拒绝恢复。正式动作状态帧、事件重放、连接失联暂停和低频控制命令幂等仍须在传输层合同中补齐。
