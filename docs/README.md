# 开发文档

地下城Beta按以下顺序阅读：

1. [开发指南](dungeon-beta-development-guide.md)：产品方向、团队边界、分支协作及开放验收。
2. [当前实现状态](dungeon-beta-implementation-status.md)：已有能力、剩余任务与验证结果。
3. [架构方案](dungeon-beta-architecture.md)：模块边界、运行宿主、共享资产、交易和迁移。
4. [接口与插件合同草案](dungeon-beta-contracts.md)：待实现的完整协议；可执行子集见[合同目录](../contracts/dungeon/README.md)。

代码入口见 [dungeon/README](../dungeon/README.md)。A/B/C分别使用 `dev/dungeon-beta-foundation`、`dev/dungeon-beta-client`、`dev/dungeon-beta-content`。分支开工说明随各线提交维护，变更公共合同须同步开发指南与可执行fixture。

旧玩法提案、旧自动战斗实施规划及旧协作交接已退出当前文档集；需要追溯原方案时查看Git历史。运行中的兼容实现继续维护，不因移除旧规划文档而删除代码或存档。

庄园仍在使用的专题文档：

- [经济规则](estate-economy.md)
- [目录标识迁移](estate-id-migration.md)
- [皮肤与自定义珍藏](estate-skins.md)
