# 地下城 Beta：接口与插件合同 V1 草案

更新：2026-09-25。本文同时记录已实施合同与后续目标。**永久操作的9个消息已注册；动作传输协议仍是草案。** 可执行字段以[Schema与fixture](../contracts/dungeon/README.md)为准，实际进度见[实现状态](dungeon-beta-implementation-status.md)。内部运行宿主的接入约定见[运行目录](../dungeon/runtime/README.md)。

## 1. 通用约定

| 项目 | V1约定 |
| --- | --- |
| 传输 | 沿用现有鉴权WebSocket，新增`dungeon_beta_*`消息，不复用旧消息却改变语义 |
| 用户身份 | 从认证会话解析稳定user_id；请求中的目标买家ID不是调用者身份 |
| ID | 实例ID服务端生成；内容ID带命名空间，例如`beta.sword.basic` |
| 金额 | `coin_minor`，整数，1金币=100；禁止浮点、负价、NaN、布尔 |
| 比例 | bp整数，10000=100%；基值比例与增加百分点分别定义 |
| 计时 | 权威tick；UI可用ms；规则集固定tick_rate，时间换算向上取整至tick |
| 空间 | 原型1格=1024子单位；输入方向为有界整数，服务端规范化斜向速度 |
| 版本 | protocol、ruleset、simulation、save独立；聚合另有revision/version |
| 永久写幂等 | user_id＋request_id唯一；同ID同规范请求重放，同ID不同请求报错 |
| 输入序列 | run_id＋control_epoch＋input_seq；不为每个移动包创建SQL回执 |
| 错误 | code稳定，message可本地化；retryable仅指能否原请求重试 |
| 随机 | 服务端提供有版本的随机流；战斗/掉落/商店分流，不暴露未揭示奖励种子 |

接口接受字段以schema白名单为准；未知字段拒绝或由协议明确忽略，不能静默影响成本。只有公开快照可以发客户端，不把完整规则包中的隐藏奖励表、种子或私有账号数据原样透传。

第4～5节的插件描述与配方片段用于说明设计，提交内容必须遵循实际content schema；不能把说明性片段直接视为可执行fixture。

## 2. 最小领域对象

### 2.1 成本和装备

当前装备快照使用 `display_name`、`stats.atk`、`effects`、`legacy_effects`、`template_trade_allowed`、`can_trade`、`reservation` 等字段，完整样例见 [state_lifecycle.json](../contracts/dungeon/fixtures/state_lifecycle.json)。`effects` 保存取得时的Beta触发效果，`legacy_effects` 独立保存旧效果；不把二者交给同一执行器。

`template_trade_allowed` 为模板声明，旧装备可能为null；`can_trade` 综合绑定、来源、锁定、位置、穿戴和预留计算，仍须在写事务中重新检查。`reservation` 非空时含 `purpose/ref`；改变可用性会增加拥有者资产revision。

`version`在属性、归属、location或锁定标志变化时增加；单独建立预留不改变属性版本，但报价保存物品version与预留引用，两者都核对。`asset_revision`代表营地物品/材料/进度等变化，不能替代实时共享金币余额校验。

成本统一为向量，例如：

```json
{
  "costs": [
    {"resource_id": "wallet:coins", "amount": 20000},
    {"resource_id": "material:beta.scrap", "amount": 5}
  ]
}
```

这里金币为200金币，仅供fixture。永久命令禁止引用`run:*`资源；局内命令禁止直接写`wallet:coins`。物品和材料的可交易策略分别声明，不以名字猜测。初始Beta只开放物品实例交易，材料接口先保留类型边界。

### 2.2 规则集引用

```json
{
  "ruleset_id": "beta-20260924-a",
  "ruleset_hash": "sha256-example-placeholder",
  "protocol_version": 1,
  "plugin_api_version": 1,
  "simulation_version": 1,
  "save_version": 1,
  "tick_rate": 30
}
```

示例hash为占位文本，正式schema要求正确格式。内容包摘要由服务端对规范化数据及固定插件版本清单计算；对进行中run固定，不从客户端传来的hash选择任意服务器文件。

## 3. 传输接口最小集合

### 3.1 永久操作与查询（已注册）

| 请求名 | 关键输入 | 返回/作用 |
| --- | --- | --- |
| `dungeon_beta_get_state` | request_id | 同一读快照中的钱包、装备、材料、进度、预留、资产版本和活动run摘要 |
| `dungeon_beta_get_catalog` | request_id | 经裁剪的公开内容、规则版本、可显示效果说明 |
| `dungeon_beta_quote_upgrade` | request_id、item_id、target_level | 当前物品/规则版本、成本、升级效果预览与不能执行原因 |
| `dungeon_beta_upgrade` | request_id、item_id、target_level、expected_item_version、expected_ruleset_id、expected_costs | 权威重算、检查预留/进度与足额，原子扣费和改属性 |
| `dungeon_beta_create_offer` | request_id、item_id、expected_item_version、buyer_id、price_minor、expected_trade_policy_version、expected_fee_minor | 服务端算费并检查预期值，创建定向订单并预留物品 |
| `dungeon_beta_list_offers` | request_id | 当前用户作为买家/卖家的报价与已成交结果，不泄露其它私密订单 |
| `dungeon_beta_accept_offer` | request_id、offer_id、expected_offer_version | 原子成交、买家成功回执、双方状态失效提示 |
| `dungeon_beta_cancel_offer` | request_id、offer_id、expected_offer_version | 仅卖家取消OPEN报价、解除预留 |
| `dungeon_beta_get_receipt` | request_id、lookup_request_id | 只查询本人原操作的已提交结果，未找到不等于永久失败 |

所有请求还必须包含 `type` 和整数 `protocol_version: 1`；未列字段拒绝，当前没有cursor分页。独立Beta穿戴、锁定、领取，以及分解、制作等消息尚未注册；没有启用的功能不发空按钮。交易手续费/TTL/上限的公开策略通过get_catalog返回；用户确认页面据此展示，服务端在create_offer时重算，策略变化返回`quote_changed`，不能用已变价费率自动创建报价。

升级请求示例：

```json
{
  "type": "dungeon_beta_upgrade",
  "protocol_version": 1,
  "request_id": "up_example_0001",
  "item_id": "item_example_01",
  "target_level": 3,
  "expected_item_version": 7,
  "expected_ruleset_id": "beta-20260924-a",
  "expected_costs": [
    {"resource_id": "wallet:coins", "amount": 20000},
    {"resource_id": "material:beta.scrap", "amount": 5}
  ]
}
```

`expected_costs`是价格变更保护，不是服务器扣款依据；服务端从物品、目标和规则重算成本再比较。原报价过时返回新报价，前端再次确认后用新请求号提交。

### 3.2 统一结果与错误

```json
{
  "type": "dungeon_beta_result",
  "protocol_version": 1,
  "request_id": "up_example_0001",
  "result_kind": "upgrade",
  "replayed": false,
  "asset_revision": 12,
  "result": {
    "item_id": "item_example_01",
    "item_version": 8,
    "upgrade_level": 3,
    "spent": [{"resource_id": "wallet:coins", "amount": 20000}, {"resource_id": "material:beta.scrap", "amount": 5}],
    "wallet_at_commit": {"coin_minor": 80000}
  }
}
```

回执中的`wallet_at_commit`是该操作提交时的历史余额，重放不代表当前余额。前端得到写结果后再查询最新state/钱包；不能把一个延迟回执中的旧余额直接覆盖全站当前余额。成功回执里的经济效果不可因查询时新状态而改写。

```json
{
  "type": "dungeon_beta_error",
  "protocol_version": 1,
  "request_id": "up_example_0001",
  "code": "asset_reserved",
  "message": "该装备正在交易中，请先撤销报价。",
  "retryable": false,
  "details": {"item_id": "item_example_01", "purpose": "trade_offer"}
}
```

最低错误码：`auth_required`、`invalid_request`、`unsupported_protocol`、`request_conflict`、`not_found`、`forbidden`、`version_conflict`、`quote_changed`、`insufficient_funds`、`insufficient_materials`、`asset_reserved`、`asset_not_tradable`、`inventory_full`、`offer_closed`、`offer_expired`、`control_lost`、`ruleset_unavailable`、`storage_busy`、`storage_failed`、`mode_migrated`。

网络超时、storage_busy可同ID同内容重试；version_conflict/quote_changed需要刷新、重新决策并用新ID。旧协议当前retryable语义较宽，Beta客户端不得照搬其“所有写错误自动重发”逻辑。

### 3.3 run和输入（待实施的传输草案）

本节名称不代表已开放API。当前内部 `RunService.start` 接受 `route_id`，宿主没有客户端鉴权或推送功能，不能直接暴露给网络。接传输前应冻结起局、控制、输入、快照的schema及fixture，明确身份检查与重试语义。

| 请求/事件 | 关键字段 | 约束 |
| --- | --- | --- |
| `dungeon_beta_start` | request_id、challenge_id、loadout/preset引用、expected_asset_revision、expected_ruleset_id | 服务端生成run_id/种子、冻结装备、预留引用资产 |
| `dungeon_beta_take_control` | request_id、run_id | 显式接管；返回新的control_epoch，不自动恢复战斗 |
| `dungeon_beta_input` | run_id、control_epoch、input_seq、move_x/y、aim_x/y、buttons | 短输入流，校验速率/范围；不接收伤害或掉落 |
| `dungeon_beta_control` | request_id、run_id、command、expected_run_revision | pause/resume/abandon；Beta不提供倍速 |
| `dungeon_beta_choose` | request_id、run_id、choice_id、option_id、expected_run_revision | 只能选服务器当前展示选项，支付局内钱与选择一起保存 |
| `dungeon_beta_sync` | request_id、run_id、after_event_seq可选 | 获取全量快照或增量；缺号/截断后强制完整重同步 |
| `dungeon_beta_frame` | run_id、server_tick、durable_tick、control_epoch、acked_input_seq、state_delta、event_seq | 高频推送，不包含未揭示奖励；按run与tick去旧 |
| `dungeon_beta_run_result` | run_id、outcome、committed_rewards、result_revision | 权威已落库终态；客户端不能提交此事件 |

move/aim分量暂定−1024～1024，原点aim沿上一次有效朝向；move规范化保证斜向不加速。内部宿主的buttons目前为不重复字符串列表，可选 `attack/dash/potion`；动作传输schema尚未冻结。闪避/武技按输入序号识别新按下，不因重复包反复触发。服务端时间权威，不按客户端声称的经过时间推进世界。

控制接管成功必须先使旧epoch失效，再接受新输入。run_revision用于低频选择/暂停等状态变更，不能每tick递增导致菜单命令永远冲突；server_tick/event_seq负责高频排序。恢复检查点会递增控制代次，客户端清空旧预测和未确认输入，避免把上局动作重放进恢复后的世界。

## 4. 纯模拟器和机制插件

### 4.1 模拟器接口形状

以下接口已落到 `dungeon.contracts.simulator.Simulator`，实现方遵循同一签名：

```python
class Simulator:
    def create(self, initial, rules, services): ...
    def step(self, state, ordered_inputs, services): ...  # 推进固定1 tick
    def snapshot(self, state): ...                      # 返回可JSON化状态
    def restore(self, snapshot, rules, services): ...
```

当前 `services` 包含宿主提供的命名确定性随机流与事件收集器，不含数据库、wallet、网络连接或系统时钟。空间查询属于模拟器内部实现，扩充公共services能力须先改Protocol与测试。step返回新状态，事件通过 `services.emit` 收集；不能返回任意“金币增加X”命令。

快照必须包含全部影响结果的状态，包括插件状态、触发冷却、计数器和随机流位置。宿主把规则/版本、控制状态与快照一起存档。禁止把Python对象指针、闭包、set迭代顺序或进程随机hash当作可恢复状态。

### 4.2 机制注册描述

```json
{
  "plugin_id": "builtin.sword_rules",
  "plugin_version": "0.1.0",
  "api_version": 1,
  "requires": [],
  "provides": {
    "effect_kinds": ["sword.shield_on_spend"],
    "event_types": ["sword.momentum_spent"]
  },
  "state_version": 1
}
```

manifest只描述已注册代码，不包含可由客户端提供的import路径。A在白名单代码中把`sword.shield_on_spend`绑定到处理器，并同时注册参数schema、状态schema、允许输出操作与预算。依赖精确版本范围/优先级由F0格式统一；示例无依赖。

机制函数逻辑形状：

```text
handle(event, readonly_context, params, effect_state)
  → EffectResult(next_effect_state, ordered_operations)
```

示例读取`actual_spent>=3`且冷却就绪，返回`shield.add`与新的冷却截止tick。不直接减金币，不直接修改其它插件的私有state。状态按`actor_id＋effect_instance_id`隔离，同名两件物品不会误共用冷却，除非显式声明cooldown_group。

### 4.3 内容侧的效果实例

```json
{
  "id": "beta.resonance.guard_momentum",
  "kind": "sword.shield_on_spend",
  "trigger": "sword.momentum_spent",
  "filter": {"origin": "main_weapon"},
  "params": {"minimum_spent": 3, "shield_max_hp_bp": 800, "duration_ms": 2000},
  "cooldown_ms": 5000,
  "limits": {"per_action": 1, "max_proc_depth": 1},
  "stacking": "refresh_max",
  "remove_policy": "remove_owned_state"
}
```

`kind`实现语义，`trigger`必须在其可订阅事件内；新增陌生字符串不能自动成为新能力。编译器把ms按规则集tick_rate转换；不让前后端分别用不同舍入。

效果来源中保留main_weapon/resonance等origin、action_id、parent_event_id和proc_depth。默认派生效果不再触发其它装备派生；特殊例外需要新的机制版本与有界递归证明。引擎验证操作类型、目标、数量、状态上限；超预算视为配置/插件错误并停止该规则集开新局，不静默无限递归。

## 5. 内容包最小模板

```json
{
  "pack_id": "beta.core",
  "pack_version": "0.1.0",
  "schema_version": 1,
  "requires": [{"plugin_id": "builtin.sword_rules", "plugin_version": "0.1.0"}],
  "files": {
    "weapons": "weapons.json",
    "enemies": "enemies.json",
    "encounters": "encounters.json",
    "effects": "effects.json",
    "progression": "progression.json",
    "economy": "economy.json",
    "loot": "loot.json"
  }
}
```

这些相对路径必须位于内容包内，拒绝越目录路径；资源引用需检查存在性。公共展示资源以visual_id引用，美术文件变化不改变物品身份。manifest里列了文件就必须实际提交，不能用空路径占位。

成长数据示例：

```json
{
  "id": "beta.upgrade.sword",
  "strategy": "fixed_level_table",
  "rows": [{
    "from_level": 2,
    "to_level": 3,
    "requires_progress": "beta.clear.first_boss",
    "costs": [{"resource_id": "wallet:coins", "amount": 20000}],
    "changes": [{"operation": "item.stat_flat", "stat": "attack", "value": 2}]
  }]
}
```

这是独立的金币单资源配方示例，不与第2节含锻材的报价fixture混为同一发布配方。`item.stat_flat`是应用层认可的升级计划操作，不是战斗插件获准执行的写操作；核心检查白名单后在事务中应用。提交实际fixture时每一组请求、规则和结果必须匹配。

C线至少交：一套合法包、每个效果触发示例、每个掉落池可达性、金币来源/消耗清单、可交易/绑定规则、两个代表构筑。A提供基础测试资产与账号生成工具；C不能把调试无限金币奖励放入正式release清单。

## 6. 三线互相交付的fixture

| A提供的fixture | B验证什么 | C验证什么 |
| --- | --- | --- |
| 新号与已有金币账号营地state | 余额/装备/入口正确，无庄园前置 | 金币富裕与新号的成长差异 |
| 短局帧、缺号、完整恢复 | 渲染/预测/断线/控制权提示 | 战斗事件字段足够表达效果 |
| 三选一与局内钱不足 | 能选、能取消、无效选项不可提交 | 随机池兼容和消费向量 |
| 升级报价、价格变化、成功重放 | 重新确认与当前余额查询 | 成本/解锁/效果符合配置 |
| 报价创建、接受、取消、过期 | 双角色视图与终态、满包进入待领取 | 净收入/手续费/交易标志 |
| 旧模式账号或不支持规则版本 | 明确刷新/恢复路径，不显示假成功 | 内容版本有兼容/退出方案 |

当前已有护盾、升级、交易和营地状态fixture；运行恢复及奖励通过内部宿主测试验证，短局帧/选择/重连传输fixture仍待补齐。fixture由正式schema校验后提交，B使用同一JSON做假服务，C使用同一规则集做样例模拟；不能三组各写一种“差不多”的字段。F0至少包含一个从开始到结束的完整正常路径和资金不足、资产预留、响应丢失三条失败路径。

## 7. 合同变更办法

新增字段先给schema、默认/兼容策略和fixture，再合调用方；删除/改含义/改单位必须升级相应版本。新规则不能只改展示文本却复用旧事件语义。每次跨线变更记录“为什么要改、受影响字段/插件/存档、迁移或过渡办法、验收例子”。

在协议V1真正冻结前，本文可快速修订；冻结后以向后兼容添加为主。玩法百分比可以频繁调整，用户身份、钱的单位、奖励唯一性和成交原子性保持稳定。
