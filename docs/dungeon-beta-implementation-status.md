# 地下城 Beta 实现状态

更新：2026-09-25。此页区分已注册协议、内部可测试能力和待接入玩法；完整目标见[开发指南](dungeon-beta-development-guide.md)，实现约束见[架构方案](dungeon-beta-architecture.md)。

## 当前能力

| 模块 | 已实现范围 | 后续边界 |
| --- | --- | --- |
| 旧功能兼容 | `dungeon/legacy/`、同模块导入别名、旧局与装备操作 | 旧自动战斗继续使用原协议，不执行Beta触发型机制 |
| 内容加载 | 严格Schema、跨引用、依赖版本、不可变规则和稳定摘要 | 新能力先补合同和执行器，再由内容配置引用 |
| P0内容 | 已整合content提交`d9ccc61`；四房显式路线、刷怪编组、永久/局内奖励分开；联调使用`release-p0.json` | 正式`release.json`仍是beta-core；P0参数属于首测提案 |
| 装备与成长 | 共享金币、事务升级、成功回执；Beta装备工厂冻结规则、效果、绑定和来源，满包进入pending | 造物是服务端内部能力，没有客户端造物接口 |
| 玩家交易 | 定向报价、预留、接受、撤销、到期、原子成交，满包转pending | 列表分页、浏览器两账号联调待完成 |
| 营地查询 | 独立Beta状态读模型，在同一数据库快照中读取钱包、装备、材料、进度与预留 | 不调用旧开档函数，不因查询发新手装备 |
| 协议 | 升级/交易/目录/回执及状态查询走鉴权`dungeon_beta_*`消息；具体字段以可执行schema为准 | start/input/frame等动作消息未注册 |
| 运行与奖励 | 注入Simulator的内部运行服务、手动推进、输入/控制权、检查点、恢复及房间奖励事务 | 没有正式动作引擎、自动服务调度和浏览器动作联调；不能宣称可玩Beta已交付 |
| 数据管理 | 编号迁移、旧入口门禁、改名/删除检查；报价删除释放预留 | 正式部署前仍需对生产备份做迁移演练 |

内部宿主用于验证框架。只接受受信任模拟器的房间完成事件；金币与掉落金额由固定规则计算。药瓶是局内生成/拾取状态，不能写入永久材料或金币余额。测试模拟器只存在于测试文件。

## 内容与版本

- 正式组合：`content/dungeon/release.json`，`beta-20260925-f0`。原规则摘要保持 `sha256-7b80484d5348e997d05f8afd96696e45c56089ff294e99f1ea0ef68cc1610b58`。
- P0联调组合：`content/dungeon/release-p0.json`，包含beta-core与萤灯原野增量包。校验器输出当前摘要；不能仅凭同一个ruleset_id替换正在运行的规则。
- 首轮迁移1/2的SQL及校验和保持不变。迁移3扩展Beta装备与交易预留清理，迁移4新增内部run/清房回执表。
- 存档恢复要求原规则摘要及模拟/存档版本匹配。摘要不能替代旧插件代码；缺少相应版本时拒绝恢复，不静默改用新规则。

## 开发线与合流

| 分支 | 职责 | 当前下一步 |
| --- | --- | --- |
| `dev/dungeon-beta-foundation` | A：公共合同、资产、宿主及集成 | 审阅B模拟器接入，补自动生命周期与动作协议，再做端到端资产验收 |
| `dev/dungeon-beta-client` | B：动作模拟与客户端 | 按现有Simulator接口接短局，完成大厅入口及营地/交易UI |
| `dev/dungeon-beta-content` | C：内容、成长与经济 | 同步A的新schema，完善两套打法与经济模拟/试玩报告 |

三条分支及旧UI归档标签此前已推送；本轮新增提交是否同步以Git为准。原本地core分支已退役，A兼任集成。旧UI的独有实现保存在`archive/dungeon-ui-before-beta`；远端旧core/UI引用未删除。content原提交已普通merge进入A，后续合同更新由C同步A，避免重复cherry-pick同一内容。

## 运行与验证

```sh
uv sync --locked
uv run --locked python -m dungeon.tools validate-content content/dungeon/release.json
uv run --locked python -m dungeon.tools validate-content content/dungeon/release-p0.json
uv run --locked python tests/test_dungeon_beta_content.py
uv run --locked python tests/test_dungeon_beta_assets.py
uv run --locked python tests/test_dungeon_beta_trading.py
uv run --locked python tests/test_dungeon_beta_state.py
uv run --locked python tests/test_dungeon_beta_protocol.py
uv run --locked python tests/test_dungeon_beta_runtime.py
uv run --locked python scripts/run_python_tests.py
```

联调账号工具为 `python -m dungeon.tools init-test-db PATH`，只对专用测试库使用。P0联调需要显式选择P0规则组合；创建测试账号不等于已启用正式路线。

## 回执与资产边界

成功永久操作持久化回执；业务失败回滚、不占用请求号。交易过期可能提交释放预留后返回`offer_expired`，其状态转换不是成功成交。成功重放返回当次提交结果；`wallet_at_commit`不是当前余额，界面应查询新的get_state。

每个有效清房只提交一次永久奖励，首领房同样只按本房配置发一次；没有隐式重复首领奖励。已确认房间奖励不可因检查点恢复重发。局内药瓶留在运行快照中，其实际碰撞拾取和治疗由B模拟器负责。

## 本轮验证与既有基线

本轮最终全量Python回归为 **52/53个文件通过**。唯一失败仍是 `test_frontend.py` 中 `games/liarsbar.js` 与 `games/ludo.js` 的入口可达性检查，和 `fcaf1c0` 阶段50/51文件的既有失败一致；该问题更早已在重构前提交复现。

运行宿主13项、营地状态7项、协议9项均通过；子代理另以Python 3.9独立环境验证这些模块及服务器装配10项。两套release校验通过，迁移1/2原SQL逐字比较不变，文档相对文件链接与 `git diff --check` 通过。新增的管理测试前置冲突已修正：新手装备发放现在会创建资产版本，测试改为幂等设置该记录。

本轮没有浏览器动作端到端验收，不代表真实战斗、手感或经济平衡已验收。
