# C线：内容、成长与经济

工作分支：`dev/dungeon-beta-content`。共同起点：`2b8e757`。本提交明确内容制作入口；新增打法、关卡和经济数值仍待实现。

## 已有入口

- `release.json`固定发布组合；`packs/beta-core/manifest.json`声明文件与机制依赖。
- 当前包有长剑、敌人、遭遇、掉落、升级和交易策略的最小样例。
- `release-p0.json` 是火苗原野联调组合，不改变正式 `release.json`。P0 通过 `routes` 显式给出线性房间顺序；遭遇的 `spawn_groups` 与旧 `enemies` 展开列表一致。
- P0 房间 `rewards.permanent` 明确金币最小单位、掉落池抽取次数和清房进度，`rewards.run` 仅给局内药瓶。每次有效清房只发一次永久奖励，重试去重由宿主结算事务负责。
- 未声明 `routes` 的旧 beta-core 仍可加载供既有功能使用，但不提供可启动的 P0 路线；宿主应拒绝从该发布组合开启副本，不能替旧房间猜测奖励。
- 唯一已注册战斗机制是`sword.shield_on_spend`；唯一成长策略是`fixed_level_table`。
- 当前长剑升级示例消耗200个金币最小单位（2金币），攻击12→14，要求`beta.clear.first_boss`。这是验证数据，不是平衡结论。

## 首批交付

1. 先盘点现有schema能够表达的武器、敌人和房间组合，扩展一个增量内容包。每个ID使用稳定命名；全局只允许一个economy策略。
2. 给出两种长剑打法的构筑说明与示例配置。需要新机制时先提交参数、事件、状态、边界fixture，由A/B实现能力后再把引用写入可发布包。
3. 提交新号、已有大量金币账号、连续失败三种经济样例，列清关卡解锁、每局收益、升级成本和装备出售/交易假设。正式金币产出不能由客户端计算。
4. 掉落和升级参数改动更新规则版本；向A提交release组合变更，不直接调整钱包代码或数据库迁移。

## 验收

```sh
uv run --locked python -m dungeon.tools validate-content content/dungeon/release.json
uv run --locked python -m dungeon.tools validate-content content/dungeon/release-p0.json
uv run --locked python tests/test_dungeon_beta_content.py
uv run --locked python tests/test_dungeon_beta_assets.py
```

PR附规则摘要、配置差异、经济样例与仍待机制支持的内容。词条、房间事件、局内货币和完整专精树当前不具备完整schema/执行支持，不能通过向JSON加入未知字段绕过合同。

完整产品边界见[开发指南](../../docs/dungeon-beta-development-guide.md)，可执行结构见[合同说明](../../contracts/dungeon/README.md)。
