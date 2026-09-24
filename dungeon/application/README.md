# A线：框架、资产与Beta集成

工作分支：`dev/dungeon-beta-foundation`，同时承接B/C经审阅的合流。共同起点：`2b8e757`。本提交明确后续任务；尚未新增正式Beta协议、权威宿主或交易成交服务。

## 已有接口

- `AssetService.quote_upgrade`返回真实内容驱动的升级报价；`upgrade`在同一事务中校验版本、扣金币/材料、改装备、写成功回执。
- `FixedLevelPolicy`使用`rules.mutable_content("progression")`构造；`SharedWalletPort`共用现有金币及流水，金额以整数分传入。
- `reserve_item/release_item`由调用方在写事务中使用；旧入口已接统一预留门禁。
- 失败回滚且不占用请求号；成功重试返回提交时结果，前端另查最新余额和状态。

## 首批交付

1. 鉴权升级协议：从会话获取账号ID，不信任客户端归属；补报价、确认、错误映射、成功后状态刷新和多连接通知。先冻结fixture，再接UI。
2. 权威运行宿主：有序输入、控制epoch、有界调度、固定规则集、检查点和停服恢复；与B的Simulator通过窄接口连接。
3. 房间奖励：只接受当前宿主的内部结果，检查点、唯一奖励凭据与永久资产同事务提交，再向客户端确认。
4. 定向交易：报价、预留、接受、取消、过期；金币/手续费/物品所有权原子交割，补满包、改名/删除、并发和故障回滚验证。

升级协议与R4/R5可分小PR推进。A统一分配迁移编号；迁移1已进入公共底座，后续新增迁移，不能修改其内容使现有checksum失效。

## 验收与合流

```sh
uv run --locked python tests/test_dungeon_beta_assets.py
uv run --locked python tests/test_admin.py
uv run --locked python tests/test_dungeon_protocol.py
uv run --locked python scripts/run_python_tests.py
```

新增事务须覆盖竞争、成功重放、报价过期/变化及扣费后异常。正式公开前还需两账号端到端与共享经济回归。公共底座曾有一个既有前端静态检查失败，具体证据见[实现状态](../../docs/dungeon-beta-implementation-status.md)，不能将其当成所有未来前端失败的豁免。

B/C合并前检查公共合同和规则版本；使用普通merge保留协作者提交，必要的公共更新先在A提交，再由B/C同步。旧本地core分支已经退役，不重新创建并行集成入口。
