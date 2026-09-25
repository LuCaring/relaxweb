# A线：框架、资产与Beta集成

工作分支为 `dev/dungeon-beta-foundation`，同时承接B/C审阅合流。当前阶段继续落实R4；升级与交易服务及其鉴权协议已经存在，不重复开发。

## 应用边界

- `AssetService`持有升级事务，`FixedLevelPolicy`只产生报价，`SharedWalletPort`共用现有金币与流水。
- `TradeService`冻结报价价格与手续费；成交在同一事务内改变双方金币、装备归属、订单终态、预留与成功回执。
- `create_item`是内部装备工厂，由调用方提供写事务。Beta效果保存在独立字段，旧`effects_json`保持旧语义；模板禁交易、测试及新手来源的限制不能由调用方解除。
- `StateService`只读一致性快照，不调用旧开档函数；已注册`dungeon_beta_get_state`供客户端刷新当前状态。
- `RunService`与内部运行宿主固定路线、规则、装备快照和控制代次；房间奖励从规则计算，不能来自客户端申报。

正式动作协议、自动调度和完整B线模拟器仍待接入。内部手动推进测试不等于对玩家开放Beta。接口详情见[当前状态](../../docs/dungeon-beta-implementation-status.md)与[合同目录](../../contracts/dungeon/README.md)。

## 继续推进

1. B线提供真实Simulator：固定tick、可恢复快照、命名随机流、符合合同的房间完成信号。
2. A将内部宿主接入Application生命周期、受控调度与动作传输，验证输入上限、双标签接管、断线暂停及停服恢复。
3. A/B完成大厅→短局→结算→升级→两账号交易的浏览器闭环；C用相同规则版本做经济模拟与试玩。
4. 补交易分页、失效通知与可观测性。生产备份迁移及目标并发测量通过后再开放。

## 迁移与验证

既有迁移1/2不可改写；新增字段和run表使用迁移3/4。后续迁移编号由A统一分配，B/C提交需求，不各自覆盖初始化脚本。

```sh
uv run --locked python tests/test_dungeon_beta_assets.py
uv run --locked python tests/test_dungeon_beta_state.py
uv run --locked python tests/test_dungeon_beta_trading.py
uv run --locked python tests/test_dungeon_beta_protocol.py
uv run --locked python tests/test_dungeon_beta_runtime.py
uv run --locked python tests/test_admin.py
uv run --locked python scripts/run_python_tests.py
```

B/C同步公共合同后再改调用方，普通merge保留协作者提交。每个PR写明规则/协议版本、实际运行的测试与尚未交付边界；不把过往测试通过当成新代码的验收。
