# 休闲庄园前端重构设计

日期：2026-09-20
状态：设计已确认，等待实施计划
适用仓库：`LuCaring/relaxweb`
分支：`feat/xiaopang-estate-phase2`

## 1. 背景与目标

庄园后端已在同一分支完成分层重构。前端 `assets/js/estate/`（10 个模块，
1098 行）尚未整理，存在三类问题：把服务端的规则重写了一遍、同一种 UI
结构重复构造、以及缺少行为测试。

本次重构目标：

1. **消灭客户端重复实现的服务端规则**：钓鱼张力物理、鱼竿系数、维修费、
   矿场预留格改由服务端下发的规则驱动。
2. 抽出纯规则层 `rules.js`，让这部分第一次可以被自动化测试覆盖。
3. 收敛 `ui.js`、`protocol.js`、`input.js`、`mining.js` 的重复结构。
4. 给庄园前端补上它一直缺失的行为测试。

范围：`assets/js/estate/*`、`estate/catalog.py` 的 `public_catalog()`（只增
字段）、`tests/test_estate_frontend.py`、新增 `tests/test_estate_rules.py`。
**不含**：CSS、地图像素坐标与绘制、生命周期方案、任何玩法数值。

## 2. 现有问题

以下均已逐行核实。

### 2.1 客户端重写了服务端的钓鱼物理（`fishing.js`）

`fishing.js:31,58-59` 与服务端 `estate/activities.py::simulate_fishing` 是同一套
公式、同一批系数：

```js
let tension = .18; let progress = .08;
const factor = { 1: 1, 2: .82, 3: .68 }[session.rod_level] || 1;
tension += .026 * (.68 + force) * factor;  progress += .013 * (1.12 - force * .3);
tension = Math.max(0, tension - .045);     progress = Math.max(0, progress - .0035 * (.5 + force));
```

风险：在 `estate/catalog.py` 调整钓鱼难度后，玩家看到的进度条会与服务端
判定脱节，表现为"进度满了却判逃脱"。

其中 `{ 1: 1, 2: .82, 3: .68 }` **就是** `catalog.tools.rod[level].tension_factor`，
而 `public_catalog()` 早已把它下发给客户端。

同类问题：`ui.js:119` 重算了服务端 `repair_tool` 的维修费公式；
`ui.js:181` 重算了 `strikes + 2` 的预留格；`mining.js:20` 硬编码 `25` 格
（服务端按 `run.size` 下发）。

### 2.2 `ui.js`（263 行，重复最集中）

- `render()`（234-243）与 `interact()`（246-253）是**同一条 5 路分派链的两份实现**。
- 目录取值 `[String(x)] || [x]` 的数字/字符串双查出现 **5 次**（95、108、123、194、221）。
- **4 段手搓的同一种卡片结构**（46-51、109-115、148-152、177-181），其中
  80、152、181、208 用 `innerHTML` 插值服务端数据，其余用 `createElement`。
- `button()` 先按 pending 设 `disabled`，调用方再 `||=` 自己的条件，重复 **10 次**。

### 2.3 `protocol.js`（89 行）

- 音效映射表 12 行内联在消息处理函数内（46-58）。
- `estateStore.pending`（`state.js`）与模块内 `waiters` 是**两张同键表**，
  在 23/25/39/40 与 68/69 共 6 处需要同步。

### 2.4 `mining.js`（62 行）

- 战利品汇总表达式重复 **3 次**（19、29、50），空态文案各不相同。
- `25` 硬编码（服务端已按 `run.size` 下发）。
- 没有销毁逻辑（本模块不注册 window 监听，故不泄漏，但与其它模块不一致）。

### 2.5 `input.js`（81 行）

- `clearForAudioSettings = () => clear()` 是无意义别名（52）。
- 绑定 9 个监听器，`destroy()` 再手工重列同一批 9 个，容易漂移。
- 摇杆半径 `.3` 未命名（36）。

### 2.6 `map.js`（229 行）

绝大部分是像素坐标与绘制调用，**那是内容本身，不应改动**。需要就地命名的
只有：疾跑/步行速度 `190`/`125`（202）、步频 `18`/`12`（208）、交互半径 `78`
（130）、角色碰撞盒 `12`（136）、`cropSprites` 的 22 个编号（101）。

### 2.7 缺少行为测试

- 庄园前端**没有任何行为测试**：`tests/` 下不存在 `test_estate_ui.cjs` /
  `test_estate_mobile.cjs`（phase1 计划里列过，最终未进仓库）。
- Playwright 与浏览器在当前环境均不可用，无法运行 `.cjs` 回归。
- 唯一的 `test_estate_frontend.py` 是纯文本断言，且**把实现细节钉死**：
  `assertIn("Math.floor(trace.length / 10)", fishing)`、
  `assertIn("190", world)`、`assertIn('bomb: "💣"', mining)`。
  这类断言阻碍重构，却不保护行为。

## 3. 已验证的技术前提

Node `v22.19.0` 在无 `package.json` 时按语法自动识别 ESM，实测：

| 模块 | `import()` 结果 |
| --- | --- |
| `state.js`（纯逻辑） | 成功 |
| `protocol.js` / `ui.js` / `map.js` | 失败：`document is not defined` / `Image is not defined` |

因此**纯规则模块可以被 Node 直接测试，DOM 模块不能**。这正是把规则抽成
独立纯模块的依据。

## 4. 协议扩展（只增不改）

`estate/catalog.py::public_catalog()` 新增两项，**不修改任何既有键**，
旧客户端不受影响：

```python
"fishing_rules": {
    "steps": FISHING_STEPS, "steps_per_frame": FISHING_STEPS_PER_FRAME,
    "tension_start": TENSION_START, "progress_start": PROGRESS_START,
    "hold_tension_gain": HOLD_TENSION_GAIN,
    "hold_tension_force_base": HOLD_TENSION_FORCE_BASE,
    "hold_progress_gain": HOLD_PROGRESS_GAIN,
    "hold_progress_base": HOLD_PROGRESS_BASE,
    "hold_progress_force_scale": HOLD_PROGRESS_FORCE_SCALE,
    "release_tension_drop": RELEASE_TENSION_DROP,
    "release_progress_drop": RELEASE_PROGRESS_DROP,
    "release_progress_force_base": RELEASE_PROGRESS_FORCE_BASE,
    "snapped_at": TENSION_SNAPPED_AT, "caught_at": PROGRESS_CAUGHT_AT,
},
"mining_rules": {
    "cells": MINE_CELLS, "board_size": MINE_BOARD_SIZE,
    "extra_cells": MINE_EXTRA_CELLS,
},
```

**鱼竿系数不重复下发**：客户端从既有的 `catalog.tools.rod[level].tension_factor`
读取。`test_estate_catalog_content.py` 只断言指定键存在、不禁止新键，实测不受影响。

## 5. 目标结构

```text
assets/js/estate/
  rules.js       纯规则层（新）：无 DOM，可被 Node 直接测试
  state.js       不变
  protocol.js    音效表提为常量；两张请求表合并为一条记录
  input.js       去掉别名，改为表驱动绑定
  map.js         就地命名魔术数字，坐标不动
  art.js         不变
  sprites.js     不变
  ui.js          单一分派表、单一卡片构造、目录取值走 rules，规则走 catalog
  fishing.js     物理与系数改为消费 catalog.fishing_rules
  mining.js      格数与战利品汇总走 rules 与 run.size
  view.js        模板提为模块常量
```

### 5.1 `rules.js`（新，纯函数）

- `catalogEntry(table, key)`：统一 `[String(key)] || [key]` 取值。
- `tensionStep(rules, state, { held, force, factor })`：**客户端唯一的张力
  推进实现**，返回 `{ tension, progress, outcome }`，`outcome` 为
  `null | "snapped" | "caught"`。
- `rodFactor(catalog, rodLevel)`：从 `catalog.tools.rod` 读 `tension_factor`。
- `repairCost(rule, durability)`：与服务端 `repair_tool` 同式同舍入。
- `reservedSlots(catalog, pickaxeRule)`：`strikes + mining_rules.extra_cells`。
- `formatDuration(seconds)`：原 `ui.js::timeLeft` 迁移。
- `lootSummary(loot, icons, emptyLabel)`：合并 `mining.js` 的三份实现。

### 5.2 `ui.js`

- 一张 `PANELS` 分派表，`render()` 与 `interact()` 共用。
- 一个 `itemCard({ icon, title, meta, action })` 构造器，全部改用
  `createElement`，不再有 `innerHTML` 插值服务端数据。
- `button(label, onClick, { disabled, className })`：由 `button` 内部合并
  `pendingEstateAction()` 与传入条件，调用方不再写 `disabled ||=`。

### 5.3 `protocol.js`

- `SOUND_CUES` 提为模块常量。
- 请求记录合并为一条：`{ type, at, resolve?, reject? }`，只保留一张表；
  `pendingEstateAction()` 语义不变。

## 6. 行为不变的契约

- **14 个**庄园客户端消息类型（`get_estate` + 13 个 `estate_*`）与全部字段不变；
  `catalog` 既有键不变。
- 所有可见文案、DOM class 名与结构、地图坐标、玩法数值不变。
- 钓鱼张力曲线与服务端 `simulate_fishing` **逐步结果一致**（第 7 节验证）。
- 摇杆、键盘、疾跑、交互键的输入行为不变。

## 7. 验证

### 7.1 新增 `tests/test_estate_rules.py`（Python 驱动 node）

Node 端运行 `rules.js`，Python 端用服务端实现作参考，比对：

1. **跨语言等价**：对同一组 `force` 序列与鱼竿等级，`rules.tensionStep`
   逐步推进的结果必须与 Python `estate.activities.simulate_fishing` 完全一致
   （含 snapped / caught / escaped 三种结局与数值）。
2. `repairCost` 与服务端 `repair_tool` 的算式一致（含 `max(1.0, ...)`）。
3. `formatDuration`、`lootSummary` 的行为断言。
4. `rodFactor` 对 1/2/3 级与未知等级的回退。
5. 断言 `rules.js` 不含钓鱼物理字面量（.18/.026/.045/.0035/.68/1.12/.5），
   证明规则确实来自服务端。

### 7.2 扩展 `tests/test_estate_catalog_content.py`

断言 `public_catalog()["fishing_rules"]` 与 `catalog.py` 的常量逐项相等，
防止下发值与服务端实现漂移。

### 7.3 修正 `tests/test_estate_frontend.py`

- 把钉死实现文本的断言改为钉行为：不再要求
  `Math.floor(trace.length / 10)` 出现在 `fishing.js`，改为断言分帧步长来自
  `fishing_rules.steps_per_frame`。
- 保留并补强"模块可达、样式版本号、无原生弹窗、品牌命名"等结构性断言。
- 新增断言：`ui.js` 不再出现 `estate_shop` 之外的硬编码价格算式、
  `fishing.js` 不再出现 `.026` 等物理字面量。

### 7.4 回归

```powershell
python tests/test_estate_rules.py
python tests/test_estate_frontend.py
python tests/test_estate_catalog_content.py
python tests/test_estate_schema.py
python tests/test_estate_service.py
python tests/test_estate_activities.py
python tests/test_estate_economy.py
python tests/test_estate_protocol.py
python tests/test_estate_activities_protocol.py
python tests/test_frontend.py
```

外加全仓回归与端到端冒烟（沿用上一轮的 `.tmp-test/run_smoke.ps1`）。

判定：庄园全部测试通过；经济模拟数值与基线逐位一致；端到端冒烟 29/29；
仓库其余测试无新增失败。

## 8. 不做的事

- 不改地图像素坐标、绘制调用与 `art.js`、`sprites.js`。
- 不改 CSS。
- **不改生命周期方案**：`fishing.js` 的 `MutationObserver` 自清理保留。
  生命周期改动在无浏览器测试时无法验证，留到有 Playwright 环境时再做。
- 不新增玩法、不改任何数值、不改 DOM 结构与 class 名。
- 不引入构建步骤或第三方运行时依赖。

## 9. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| 无浏览器测试，DOM 改动无法自动验证 | 只做结构性等价改写；`rules.js` 承担全部可测逻辑；改完逐模块人工比对 |
| 协议新增字段被旧断言拒绝 | 已实测 `test_estate_catalog_content.py` 不禁止新键 |
| 修正旧断言被误当成"放宽测试" | 旧断言改为等强度的行为断言，并在提交信息中说明改了什么、为什么 |
| `rules.js` 引入循环依赖 | `rules.js` 只依赖 `catalog` 数据，不 import 其它 estate 模块 |
