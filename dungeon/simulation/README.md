# B线：动作模拟与客户端

工作分支：`dev/dungeon-beta-client`。共同起点：`2b8e757`。本提交确定接入边界与首批任务，本目录尚无动作引擎实现。

## 接口与旧UI复用

实现`dungeon.contracts.simulator.Simulator`：`create`、单tick的`step`、`snapshot`、`restore`。规则由`load_ruleset`冻结，随机数使用`SimulatorServices.random_int`的命名流，事件通过`emit`交给宿主。模拟代码不访问数据库、金币、系统时间或全局随机源。

旧UI已保存在`archive/dungeon-ui-before-beta`，先检查后选择性迁移：

```sh
git log --oneline 35ddde7..archive/dungeon-ui-before-beta
git diff --stat 35ddde7 archive/dungeon-ui-before-beta
git show archive/dungeon-ui-before-beta:dungeon.html
```

可复用页面布局、准备/装备页、素材和`/dungeon`路由。旧`dgn-protocol.js`及战斗事件播放器对应自动战斗；动作输入与权威状态须接新合同。旧catalog内容由C转成内容包，避免整支合并覆盖兼容入口。

## 首批交付

1. 游戏厅平级入口与独立页面，复用同一登录态；正式Beta协议未接通时显示明确的开发状态。
2. 测试环境中的移动、瞄准、普攻、一个敌人及胜败闭环；按服务器合同接有序输入，客户端负责输入与表现。
3. 补固定输入/种子的确定性测试，验证连续推进与快照恢复结果一致，保存机制状态及RNG位置；断线/输入epoch由A宿主负责。
4. 扩展首领与两套可辨识打法，向C提供可调参数；局内强化、升级和交易UI依赖对应合同fixture，不自行发放永久资产。

## 验收与依赖

先跑已有合同/机制测试：

```sh
uv run --locked python tests/test_dungeon_beta_content.py
uv run --locked python tests/test_dungeon_namespace.py
```

新动作实现须提供独立`unittest`入口及短局演示；新增页面须补导航、重连、版本冲突与窄屏浏览器验证。上述已有测试不等于动作手感或联机验收。A尚需提供权威宿主、正式Beta协议和发奖事务；首个本地原型只使用测试资产。

完整边界见[开发指南](../../docs/dungeon-beta-development-guide.md)和[架构方案](../../docs/dungeon-beta-architecture.md)。
