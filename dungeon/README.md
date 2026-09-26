# 地下城 Beta 代码入口

本目录与 `estate/` 平级。旧自动战斗、装备操作和旧局仍由 `dungeon/legacy/` 执行；`server/dungeon/legacy_protocol.py` 保留原 WebSocket 消息名与响应。`estate.dungeon.*` 和 `server.estate.dungeon` 是临时兼容导入，正式调用方应使用新路径。`server/app.py` 装配旧协议与Beta永久操作协议，`server/schema.py` 先初始化旧表，再调用 `dungeon.storage.beta_schema.init_beta` 执行有编号的 Beta 迁移。离线旧战斗可用 `python scripts/simulate_dungeon.py` 复现。

| 开发线 | 入口 | 边界 |
| --- | --- | --- |
| A：框架、资产和交易 | `dungeon/contracts/`、`domain/`、`application/`、`storage/`、`content/`、`plugins/`；传输适配在 `server/dungeon/` | `dungeon.contracts.simulator.Simulator` 是动作模拟合同；`dungeon.content.load_ruleset` 加载发布规则；`dungeon.plugins.registry.default_registry` 提供受信任机制注册。`AssetService` 已支持升级报价、事务扣费和成功回执重放；`TradeService`（`dungeon/application/trading.py`）已支持定向报价、预留、撤销/过期与原子成交，参数来自`economy.json`的`trade_policy`。两者已由 `server/dungeon/beta_protocol.py` 注册为鉴权WebSocket的`dungeon_beta_*`消息（schema/fixture见`contracts/dungeon/`）；`python -m dungeon.tools init-test-db`可建联调账号。`StateService`提供同快照营地查询；装备工厂冻结规则/效果/来源。`RunService`与`runtime/RunHost`已支持内部起局、手动推进、恢复和奖励，接入见[运行说明](runtime/README.md)；动作传输与生命周期仍待完成。 |
| B：战斗和客户端 | 服务端动作实现在 `dungeon/simulation/`，遵循 `Simulator` 合同；客户端在 `assets/js/dungeon/`，页面在 `dungeon.html` | 模拟器只消费冻结规则和有序输入，不能写金币、永久装备或数据库；客户端只提交输入，正式奖励由服务端确认。 |
| C：内容和经济 | `content/dungeon/packs/`、`content/dungeon/release.json`；schema 在 `contracts/dungeon/schemas/` | 内容包使用已注册机制并经 `load_ruleset` 校验；新机制需先与 A/B 约定，不在内容里放可执行脚本。 |

B 线可从本地归档标签 `archive/dungeon-ui-before-beta` 参考 `dungeon.html`、`assets/css/dungeon.css`、`assets/js/dungeon/dgn-*.js`、`views/` 和 `deploy/serve.py` 的 `/dungeon` 路由，逐项迁入导航、准备页、装备页和素材。旧 UI 的 `dgn-protocol.js` 对接旧消息，接 Beta 时以新合同替换传输映射；旧事件播放器不能当作动作模拟器。无需整支合入，避免覆盖当前框架与资源改动。

升级策略使用 `FixedLevelPolicy(rules.ruleset_id, rules.mutable_content("progression"))`。`AssetService` 持有事务，钱包和存储操作共用连接；预留/释放也必须在调用方写事务内执行。当前只有成功操作持久化回执，失败操作全部回滚、不保存回执；状态变化后可用原请求号重试。当前Beta协议沿用这一语义，改变时须显式扩展回执版本。

已实现范围与测试结果见 [交接记录](../docs/dungeon-beta-implementation-status.md)。完整目标见 `docs/dungeon-beta-architecture.md` 和 `docs/dungeon-beta-development-guide.md`。

装备词条 T 级、通货改造及两套入口的概率口径见 [装备与通货规则](../docs/dungeon-equipment-currency.md)。

原型音效全部由 WebAudio 实时合成，复用游戏厅的音量/静音设置；背景音乐另有内置合成曲与可扩展的曲库，见 [地下城 Beta 音效与背景音乐](../docs/dungeon-beta-audio.md)。曲库清单与安装说明在 `assets/dungeon/beta/bgm/`。

动作原型的独立页面是 `/dungeon-beta.html`，游戏厅入口由 `config.json` 的 `dungeon.beta_enabled` 控制、默认关闭（它尚未接入正式账号与金币）。入口过滤逻辑在 `assets/js/game-config.js` 的 `hallGameTypes()`。

## 旧功能维护

旧协议的实际消息注册见 `server/dungeon/legacy_protocol.py`，装备动作见 `dungeon/legacy/actions.py`，旧局/奖励事务见 `dungeon/legacy/runs.py`。旧回执与Beta成功回执使用不同表和格式，不互相转换。旧局按原规则完成，新动作流程通过独立Beta协议接入。

修改兼容路径至少运行 `tests/test_dungeon_namespace.py`、`tests/test_dungeon_equipment.py`、`tests/test_dungeon_runs.py` 与 `tests/test_dungeon_protocol.py`；涉及资产时同时运行 `tests/test_dungeon_beta_assets.py`，涉及交易时运行 `tests/test_dungeon_beta_trading.py`。旧部署与当前Beta目标不能混用。
