# 地下城前端美术与数值协作交接

> 动作刷宝 Beta 的协作请使用[开发指南 V0.2](dungeon-beta-development-guide.md)及其架构、合同配套文档。本文保留旧自动战斗实现的交接资料；其中庄园入口、旧战斗协议和内容范围不代表新版Beta方向。

适用分支：`dev/dungeon-core`。当前实现到 M3：单人单遭遇的服务端战斗、装备存档与奖励闭环已经可用；M4 的玩家界面、美术接入和 M5 的完整关卡、装备池与经济定稿尚未完成。规则与验收细节见 [地下城功能实施规划](estate-dungeon-design.md)，本文给协作者提供代码入口、可修改面和交付边界。跑团业务在 `main`，不属于本分支的地下城交付。

## 1. 先了解这条数据链

```text
浏览器消息与画面
  → server/estate/dungeon.py：鉴权、请求/响应、同账号推送
  → estate/dungeon/service.py、actions.py、runs.py：装备、起局、补算、结算
  → estate/dungeon/catalog.py、effects.py、combat.py：配置、属性、纯战斗规则
  → estate/dungeon/schema.py：SQLite 存档；server/wallet.py：金币流水
```

服务端是生命值、随机数、胜负、掉落、金币和解锁进度的唯一来源。浏览器只负责发命令、播放事件和展示服务器确认的状态。`dungeon_sync` 被请求时才按服务器时间补算；关闭页面不会放弃战斗，后台没有每帧战斗任务。

| 文件 | 已实现的职责 | 协作者通常修改什么 |
| --- | --- | --- |
| `estate/dungeon/catalog.py` | 版本化关卡、敌人、装备、战斗常量和奖励表；启动校验；公开目录裁剪 | 数值与内容在这里增补模板、遭遇、前置关系和奖励配置 |
| `estate/dungeon/effects.py` | 六项属性汇总，按来源记录常驻增益 | 新常驻数值的测试；新触发机制需另立规则版本 |
| `estate/dungeon/combat.py` | 确定性时间轴、伤害、暴击、超时、首领阶段和结构化事件 | 数值协作者读取结果；除规则升级外不直接调公式 |
| `estate/dungeon/runs.py` | 冻结快照、暂停/恢复/倍速、补算、唯一奖励、满包暂存和版本冲突 | 新奖励形态与关卡解锁规则须在这里和测试中一起落地 |
| `estate/dungeon/actions.py` | 穿戴、锁定、出售、领取及请求幂等 | 新装备操作须沿用所有权、活动锁和事务约束 |
| `estate/dungeon/service.py` | `get_dungeon` 读模型、一次性新手装备、装备属性对比 | 公开给界面的字段与扩展属性来源 |
| `server/estate/dungeon.py` | WebSocket 路由、错误、本人多连接状态同步 | 新协议入口与响应适配 |
| `estate/dungeon/schema.py` | 装备、穿戴、挑战、事件、奖励、进度和回执表 | 只有确实需要新存档字段时做可重复迁移 |
| `scripts/simulate_dungeon.py` | 固定种子的单场离线模拟，支持完整事件输出 | 数值实验与对照报告的起点 |

当前目录只有 `ruins_slime_01` 普通遭遇、六件新手装备、一个掉落模板和一张固定奖励表；这些是联调内容，不是上线经济。战斗内核支持首领阶段，但目录尚未配置首领。随机词缀、主动技能、状态、扫荡和局内肉鸽尚无可执行规则；现有 `affixes_json`、装备标签、效果来源类别和快照版本字段只是扩展位。

## 2. 前端、美术协作者的工作入口

目前仓库没有 `assets/js/dungeon/`、`assets/css/dungeon.css` 或 `assets/dungeon/`，也没有可玩的地下城入口。建议把这些作为 M4 的独立模块创建，避免将战斗画面与规则塞入庄园 `ui.js`：

| 建议新增文件 | 责任边界 |
| --- | --- |
| `assets/js/dungeon/protocol.js` | 使用 `core.js` 的 `send()`；在 `registry.js` 注册地下城消息；维护请求号、重连与错误处理 |
| `assets/js/dungeon/state.js` | 保存最新 `dungeon_state`、当前 battle、事件游标、已播放序号和进行中的请求 |
| `assets/js/dungeon/view.js` | 准备、装备、战斗、结算视图与离开时清理；不计算伤害和掉落 |
| `assets/js/dungeon/assets.js` | `visual_id` 到图集、帧元数据、缺图占位的映射 |
| `assets/css/dungeon.css` | 独立类名前缀、桌面与 390px 手机布局、无障碍文本 |
| `assets/dungeon/` | 怪物、装备、场景资源和来源说明；最终命名随资源清单固定 |

现有前端的接法：`assets/js/main.js` 导入视图模块，模块用 `registry.js` 的 `registerView()` / `onMessage()` 注册，`core.js` 的 `renderGameView()` 切换页面；`game.html` 引入新 CSS 并更新资源版本。规划要求从庄园进入准备页；庄园现有交互点在 `assets/js/estate/map.js`，面板分派表在 `assets/js/estate/ui.js`。新增入口时应只在庄园层放导航，再由独立地下城视图接管页面；离开战斗视图只停止本地动画和定时器。若产品也需要大厅卡片，可按 `assets/js/game-config.js` 的单人视图元数据另加入口，这并非现有功能。

`get_dungeon.catalog.challenges[].enemy` 已提供 `visual_id` 与 `phases`，`catalog.item_templates` 提供可见装备的名称、部位、品质和 `visual_id`；不要靠中文名推导文件路径。战斗响应的 `battle` 只含状态、HP、阶段索引和结果，敌人名称、最大 HP、可能奖励仍从对应关卡目录项读取。已经获得的装备在 `items[]` 中保留取得时的名称与资源编号，目录后续改动不会改写旧实例的展示字段。

资源建议按 `visual_id` 和动作组织：待机、攻击、受击、死亡至少各有可用表现，另列帧矩形、帧时长、脚底锚点、像素缩放倍率和横向翻转规则。具体像素尺寸与动画帧数尚未由代码锁定，首批交付应先给一份资源清单和帧元数据，再定加载器。缺图时保留明确占位，不能阻止事件或结算展示。`deploy/serve.py` 当前公开 `.png/.webp/.svg/.json` 等静态类型；若交付独立音频文件，需要先扩展静态白名单。素材来源和许可记录可仿照 `assets/estate/ATTRIBUTION.md`。

## 3. 前端可直接使用的协议

所有命令沿用当前登录的 WebSocket；`request_id` 用 1～96 位字母、数字、下划线或连字符。下面列的是当前已实现协议，而非将来设想：

| 请求 | 关键入参 | 当前响应或作用 |
| --- | --- | --- |
| `get_dungeon` | `request_id` | `dungeon_state`：`profile_version`、六项属性、`items`、`loadout`、进度、公开目录、`pending_count`、`active_job`、金币 |
| `dungeon_compare_item` | `request_id, item_id` | `dungeon_comparison`：当前值、替换后值及逐项差值，不写存档 |
| `dungeon_equip` | `request_id, slot, item_id`（卸下用 `null`）, `expected_version` | `dungeon_result`，禁止活动挑战期间换装 |
| `dungeon_lock_item` | `request_id, item_id, locked, expected_version` | 设置明确布尔值，不是切换开关 |
| `dungeon_sell_item` | `request_id, item_id, expected_version` | 出售背包中未锁定、未穿戴且未被活动快照引用的装备 |
| `dungeon_claim_items` | `request_id, item_ids, expected_version` | 将指定待领取实例移入背包，可分批领 |
| `dungeon_start` | `request_id, challenge_id, difficulty_id, expected_version` | `dungeon_result.result.battle.battle_id`；拿到 ID 后才能进入战斗页 |
| `dungeon_sync` | `request_id, battle_id, after_sequence` | `dungeon_events`：权威 `battle`、本页事件、`next_cursor`、`has_more` |
| `dungeon_control` | `request_id, battle_id, command, expected_revision`；`set_rate` 另带 `rate` | `command` 为 `pause/resume/set_rate/abandon`；倍率仅 1 或 2 |
| `dungeon_get_result` | `request_id, battle_id` | `dungeon_result.battle` 和 `result`；不替客户端补算 |

装备和起局请求使用最新 `dungeon_state.profile_version`；控制请求使用最新 `battle.revision`。写入返回 `dungeon_result`，其中 `result_kind="action"`、`result` 为操作回执、`state` 为最新状态；服务端另向本人全部连接推送 `dungeon_state`，该推送的 `request_id` 为 `null`。查询结算保留现有顶层 `battle/result` 结构，并带 `result_kind="lookup"`；前端按此字段和 `request_id` 区分两种响应。重发起局请求时，回执仍标记 `replayed`，其中 `battle` 会刷新为当前状态。

`dungeon_events.battle.hp` 的键为 `player:0` 和 `enemy:0`；`status` 为 `running/paused/settled/abandoned/error`。事件依 `sequence_id` 递增，可能有 `BattleStarted`、`AttackStarted`、`DamageApplied`、`BossPhaseChanged`、`ActorDied`、`BattleEnded`。伤害事件包含 `hp_after`、`hp_loss`、`damage`、`is_critical`；同一页事件最多 200 条、128 KiB。展示层只播放未消费序号；发现缺号就按 `next_cursor` 补页。断线后先以权威检查点恢复 HP，再补文本日志，不把历史伤害动画重新施加到当前 HP。

建议前台战斗页每约 500ms 请求一次同步，后台降低频率；页面返回庄园不发送 `abandon`。暂停、倍速和放弃要等服务端确认后更新按钮状态。重连后先 `get_dungeon`；若 `active_job.kind="battle"`，用其 `id` 调 `dungeon_sync`。若起局回复丢失，以同一请求号重发 `dungeon_start` 找回 `battle_id`。请求超时或连接中断时，原操作重发沿用同一个 `request_id` 和参数；确实发起新操作才生成新 ID。遇到 `version_conflict/state_conflict` 刷新状态；`storage_failed` 保留原请求号再试。`pending_items` 表示先腾背包并领取；失败、超时、放弃没有挑战奖励。

## 4. 数值与内容协作者的可修改面

`CATALOG` 是当前唯一的运行配置，建议以一次配置改动配一组固定种子模拟和校验测试。六项最终属性为 `max_hp / atk / defense / crit_bp / crit_damage_bp / speed`；`crit_bp=500` 表示 5%，`crit_damage_bp=15000` 表示 150%。时间以整数微秒保存，`base_interval_us=2000000` 时速度 100 的完整攻击间隔是 2 秒。不要在前端另写伤害、攻速或胜率算法。

| 改动目标 | 入口与当前约束 | 必要验证 |
| --- | --- | --- |
| 敌人与难度 | `enemies` 的 `stats/type/visual_id/phases`，`challenges` 的前置与难度；同一关卡可有不同难度，字符串前置表示同难度，对象 `{challenge_id,difficulty_id}` 可跨难度 | `validate_catalog()`、首领阈值/超时与新手胜率模拟 |
| 装备模板 | `items` 的部位、品质、固定 `stats`、标签、常驻效果、`sell_coins` | 六槽不变；掉落实例固化属性；比较与出售测试 |
| 掉落与金币 | `reward_tables` 的 `coins/rolls/entries.weight`，关卡的 `reward_table_id` | 单场最多 20 件；背包满转 pending；金币与物品同事务 |
| 战斗节奏 | `combat` 的攻击间隔、首击比例、波动、超时及首领阶段倍率 | 固定种子事件顺序与结算结果回归；P95 性能 |
| 基础角色 | `base_stats` 与六件 `starter_` 模板 | 新账号首次开档只发一套；现有账号装备不自动改值 |

目录修改后更新 `config_version` 并跑校验。已有账号读取状态时会幂等补齐新增关卡及其可满足的解锁条件；如进度发生变化，`profile_version` 随之更新。一次挑战创建时会冻结敌人、战斗常量、已穿装备、奖励表、解锁规则及种子；之后改目录不重算活动挑战，也不改变已入库装备的属性和展示字段。`simulation_version/rng_version/reward_rng_version` 是执行规则版本，不能只因调数值而随意改变。当前掉落使用冻结种子加独立的奖励哈希输入域；不要从浏览器提供或展示种子。

当前效果执行器只接受 `trigger="passive"`，操作为 `stat_flat` 或 `stat_add_bp`。它已经能为装备、将来局内选择和环境效果记录不同 `source_kind`；`on_hit`、状态叠层、主动技能、随机词缀和装备联动尚未实现，单纯把它们写进配置会被校验拒绝。若要启用新机制，应先补执行器、事件/快照字段、存档迁移和确定性测试，再配置内容。

数值报告至少按“装备档次 × 遭遇”各跑 1000 个固定种子，列出胜率、剩余 HP、模拟时长均值/P95、现实时间金币收益、掉落及出售后总收益，并比较 x1/x2。先以一场普通战和基础装备完成闭环，再扩充规划中的三个普通遭遇、一个精英、一个首领和各部位装备池；最终发布经济仍需单独评审，当前 `20` 金币和固定短刃只是联调值。

## 5. 建议的交付与验收顺序

1. 前端先接通“进入准备页 → 查看六槽与关卡 → 起局 → 同步播放 → 结算 → 回准备页”；美术可先用占位资源并行制作，不等待完整敌人池。
2. 装备页接属性对比、锁定、穿戴、出售和 pending 领取；双标签页同时操作应能刷新并处理版本冲突。手机 390px 宽下，血条、暂停、返回和结算信息仍可操作、可读。
3. 数值先交第一轮固定种子报告与配置差异，再加入其余关卡/奖励池；每个内容 PR 写明 `config_version`、模拟命令、结果摘要和对旧存档的影响。
4. 前端验收在真实浏览器中完成一场战斗、一次换装、一次断线恢复和满包领取；动画可跳过，但服务端终态和真实到账必须始终可见。

本地验证入口：

```bash
uv run --locked python scripts/simulate_dungeon.py --events
uv run --locked python tests/test_dungeon_foundation.py
uv run --locked python tests/test_dungeon_combat.py
uv run --locked python tests/test_dungeon_equipment.py
uv run --locked python tests/test_dungeon_runs.py
uv run --locked python tests/test_dungeon_protocol.py
npm run test:browser
```

新增前端后应补独立浏览器用例。对数值改动，检查同一种子事件和结果是否按预期改变；对只改美术的 PR，战斗与钱包结果应保持一致。当前仓库已知 `test_frontend.py` 的两个带版本号游戏模块导入会触发既有可达性断言，评估新改动时需与该基线区分。
