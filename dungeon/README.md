# 地下城 Beta 代码入口

本目录与 `estate/` 平级。旧自动战斗、装备操作和旧局仍由 `dungeon/legacy/` 执行；`server/dungeon/legacy_protocol.py` 保留原 WebSocket 消息名与响应。`estate.dungeon.*` 和 `server.estate.dungeon` 是临时兼容导入，正式调用方应使用新路径。`server/app.py` 装配旧协议，`server/schema.py` 先初始化旧表，再调用 `dungeon.storage.beta_schema.init_beta` 执行有编号的 Beta 迁移。离线旧战斗可用 `python scripts/simulate_dungeon.py` 复现。

| 开发线 | 入口 | 边界 |
| --- | --- | --- |
| A：框架、资产和交易 | `dungeon/contracts/`、`domain/`、`application/`、`storage/`、`content/`、`plugins/`；传输适配在 `server/dungeon/` | `dungeon.contracts.simulator.Simulator` 是动作模拟合同；`dungeon.content.load_ruleset` 加载发布规则；`dungeon.plugins.registry.default_registry` 提供受信任机制注册。`AssetService` 已支持升级报价、事务扣费和成功回执重放，旧入口已接装备预留门禁；尚无对外 Beta 协议或成交服务。 |
| B：战斗和客户端 | 服务端动作实现在 `dungeon/simulation/`，遵循 `Simulator` 合同；客户端在 `assets/js/dungeon/`，页面在 `dungeon.html` | 模拟器只消费冻结规则和有序输入，不能写金币、永久装备或数据库；客户端只提交输入，正式奖励由服务端确认。 |
| C：内容和经济 | `content/dungeon/packs/`、`content/dungeon/release.json`；schema 在 `contracts/dungeon/schemas/` | 内容包使用已注册机制并经 `load_ruleset` 校验；新机制需先与 A/B 约定，不在内容里放可执行脚本。 |

B 线可从现有本地 `origin/dev/dungeon-ui` 参考 `dungeon.html`、`assets/css/dungeon.css`、`assets/js/dungeon/dgn-*.js`、`views/` 和 `deploy/serve.py` 的 `/dungeon` 路由，逐项迁入导航、准备页、装备页和素材。旧 UI 的 `dgn-protocol.js` 对接旧消息，接 Beta 时以新合同替换传输映射；旧事件播放器不能当作动作模拟器。无需整支合入，避免覆盖当前框架与资源改动。

升级策略使用 `FixedLevelPolicy(rules.ruleset_id, rules.mutable_content("progression"))`。`AssetService` 持有事务，钱包和存储操作共用连接；预留/释放也必须在调用方写事务内执行。当前只有成功操作持久化回执，失败操作全部回滚、不保存回执；状态变化后可用原请求号重试。正式协议接入前须沿用这一明确语义，或通过新回执版本扩展。

已实现范围与测试结果见 [交接记录](../docs/dungeon-beta-implementation-status.md)。完整目标见 `docs/dungeon-beta-architecture.md` 和 `docs/dungeon-beta-development-guide.md`。
