# 地下城 Beta 公共底座交接与实现状态

日期：2026-09-25。本轮范围：按架构方案实施 R1～R3 的公共底座，供三条开发线并行开工。编码使用 GPT-6-sol / high；主代理负责代码审阅、回归验收和分支交接。

## 当前交付范围

| 能力 | 本轮交付 | 下一步 |
| --- | --- | --- |
| 模块拆分 | 旧实现迁入 `dungeon/legacy/`，协议迁入 `server/dungeon/`；旧导入保留同模块别名 | 新动作代码在新命名空间开发，逐步淘汰兼容入口 |
| 内容加载 | JSON Schema、严格整数、引用与路径校验、固定摘要、冻结规则、公开裁剪、增量内容包 | C线扩展装备、敌人、房间、经济与专精配置 |
| 插件 | 可信白名单注册、参数/状态/操作验证、一个可执行的消耗剑势获盾机制 | B线扩展有测试的机制与动作模拟器 |
| 模拟接口 | 可导入的 Simulator / SimulatorServices 合同 | 实时宿主、碰撞、输入流、检查点、正式房间发奖尚未实现 |
| 金币与成长 | 共用现有钱包的最小单位适配、纯升级策略、事务化升级应用服务与回执 | A线接正式鉴权协议和前端；当前不向客户端开放Beta升级API |
| 资产保护 | 统一预留门禁，旧出售/穿戴/锁定/领取/开局路径接入保护 | 原子成交、订单/过期、买卖双方通知仍属于R5 |
| 数据与管理 | 有编号迁移及回滚保护，账号改名/删除相关的地下城关联处理 | 完整Beta run与交易表在后续迁移增加 |
| 客户端 | 保留现有UI分支，不合并其旧自动战斗内容 | B线选择性复用页面，在独立大厅入口上接动作版 |

这不是对玩家开放的完整 Beta。当前正式 WebSocket 仍运行旧地下城协议；新增资产服务由应用接口与临时库测试验证。不得将内部测试物品或客户端模拟结果直接接入正式金币。

## 三条开发线的起点

以 `dev/dungeon-beta-foundation` 同时承担A线与Beta集成。原本地 `dev/dungeon-core` 在提交完整保留后删除，三个Beta分支均有各自开工提交，尚未推送远端：

| 分支 | 负责人工作范围 | 第一项后续任务 |
| --- | --- | --- |
| `dev/dungeon-beta-foundation` | A：框架、资产、协议、宿主与交易 | 实现权威运行宿主和升级协议，再完成定向原子成交 |
| `dev/dungeon-beta-client` | B：动作模拟、输入、渲染与页面 | 按Simulator合同完成短局，选择性复用现有UI页面 |
| `dev/dungeon-beta-content` | C：内容、成长和经济数据 | 增加两种打法与最小关卡包，验证金币产消 |

三线共享 `8309c0e` 公共底座及后续文档整理提交。旧UI独有提交由本地标签 `archive/dungeon-ui-before-beta` 保留；远端旧core/UI引用没有改写或删除。旧UI的 `catalog.py` 扩充应转成新内容包，不能直接覆盖兼容别名文件。

分配成员时分别检出对应分支；同机并行请用独立 worktree，避免多人修改同一工作目录。A负责合并公共合同变更到集成分支，B/C同步后再改调用方。B/C的首个PR保持在各自目录内；新增机制先给出参数、事件、状态和输出的fixture，再实现代码。

## 开工命令

```sh
uv sync --locked
uv run --locked python -m dungeon.tools validate-content content/dungeon/release.json
uv run --locked python tests/test_dungeon_namespace.py
uv run --locked python tests/test_dungeon_beta_content.py
uv run --locked python tests/test_dungeon_beta_assets.py
uv run --locked python scripts/run_python_tests.py
```

内容schema在 `contracts/dungeon/schemas/`，可执行fixture在 `contracts/dungeon/fixtures/`。当前只实现这里实际存在并通过测试的子集；[合同草案](dungeon-beta-contracts.md)中尚未实现的消息不是可调用服务。公共运行入口和Python API见 [dungeon/README](../dungeon/README.md)。

## 公共底座验证结果与边界（8309c0e）

- `uv run --locked python scripts/run_python_tests.py`：48/49个测试文件通过，包括旧地下城、庄园、共享钱包、管理员和各游戏服务回归。
- 新增专用测试：命名空间3项、内容/插件21项、资产10项，共34项通过；内容测试还在Python 3.9下通过。
- 内容CLI校验通过，规则摘要为 `sha256-7b80484d5348e997d05f8afd96696e45c56089ff294e99f1ea0ef68cc1610b58`；`uv lock --check`与`git diff --check`通过。
- 唯一未通过文件是`tests/test_frontend.py`：`games/liarsbar.js`、`games/ludo.js`未从页面入口可达。已将重构前`97ca8f8`的相关源码导出到临时目录，复现同一失败（5/6检查通过）；本轮没有修改前端文件。该旧问题留在集成分支跟踪，不作为本轮地下城回归通过的证明。
- 旧战斗固定种子模拟结果保持一致。新动作引擎尚不存在，本轮没有做动作手感、浏览器联机或正式经济平衡验收。

代码审阅中发现的迁移半写入、插件依赖漏声明、策略成本校验、预留门禁等问题已修复并补回归。当前可作为三线共同开发起点；对玩家开放仍须完成R4～R6及对应验收。

当前升级回执只保存成功结果：失败操作回滚且不占用请求号，成功重放返回提交时的结果和余额。正式协议需另行获取最新状态，不能把重放余额当成当前余额。当前示例只有一个升级等级和一种护盾机制，数值用于合同与集成验证，不是已定稿的经济方案。
