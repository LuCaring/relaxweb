# C线：内容、成长与经济

工作分支：`dev/dungeon-beta-content`。共同起点：`2b8e757`。本提交明确内容制作入口；新增打法、关卡和经济数值仍待实现。

## 已有入口

- `release.json`固定发布组合；`packs/beta-core/manifest.json`声明文件与机制依赖。
- 当前包有长剑、敌人、遭遇、掉落、升级和交易策略的最小样例。
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
uv run --locked python tests/test_dungeon_beta_content.py
uv run --locked python tests/test_dungeon_beta_assets.py
```

PR附规则摘要、配置差异、经济样例与仍待机制支持的内容。词条、房间事件、局内货币和完整专精树当前不具备完整schema/执行支持，不能通过向JSON加入未知字段绕过合同。

完整产品边界见[开发指南](../../docs/dungeon-beta-development-guide.md)，可执行结构见[合同说明](../../contracts/dungeon/README.md)。
