# 小胖庄园前端重构实施计划

日期：2026-09-20
依据：`docs/superpowers/specs/2026-09-20-estate-frontend-refactor-design.md`
分支：`feat/xiaopang-estate-phase2`
范围：`assets/js/estate/*`、`estate/catalog.py::public_catalog()`（只增字段）、庄园前端测试

## 执行原则

1. 严格按任务顺序实施，每个任务完成测试后单独提交。
2. 不引入构建步骤、不引入第三方运行时依赖。
3. 不改地图像素坐标、不改 CSS、不改生命周期方案、不改任何玩法数值。
4. 每个任务结束跑庄园全量测试；经济模拟数值必须与基线逐位一致。
5. `rules.js` 不 import 其它 estate 模块，只消费传入的 catalog 数据。

## 任务 1：协议只增字段，下发钓鱼与矿场规则

### 文件

- 修改 `estate/catalog.py`（`public_catalog()`）
- 修改 `tests/test_estate_catalog_content.py`

### 内容

新增 `fishing_rules`（9 个物理系数、`steps`、`steps_per_frame`、
`snapped_at`、`caught_at`）与 `mining_rules`（`cells`、`board_size`、
`extra_cells`）。既有键一个不动。鱼竿系数不重复下发，客户端读
`catalog.tools.rod[level].tension_factor`。

新增断言：`fishing_rules` / `mining_rules` 的每一项与 `catalog.py` 的常量
逐项相等，防止下发值与服务端实现漂移。

### 验证

```powershell
python tests/test_estate_catalog_content.py
python tests/test_estate_schema.py
python tests/test_estate_protocol.py
```

### 提交

```powershell
git commit -m "feat: ship estate fishing and mining rules in the catalog"
```

## 任务 2：新建纯规则层与跨语言等价测试

### 文件

- 新建 `assets/js/estate/rules.js`
- 新建 `tests/test_estate_rules.py`

### 内容

`rules.js` 导出：`catalogEntry`、`tensionStep`、`rodFactor`、`repairCost`、
`reservedSlots`、`formatDuration`、`lootSummary`。

`tests/test_estate_rules.py` 用 Python 驱动 `node`，覆盖：

1. **跨语言等价**：同一组 force 序列与鱼竿等级下，`rules.tensionStep` 逐步
   推进的结果必须与 Python `estate.activities.simulate_fishing` 完全一致，
   含 `snapped` / `caught` / `escaped` 三种结局与数值。
2. `repairCost` 与服务端 `repair_tool` 算式一致（含 `max(1.0, ...)`）。
3. `formatDuration`、`lootSummary` 行为断言。
4. `rodFactor` 对 1/2/3 级与未知等级的回退。
5. 断言 `rules.js` 不含 `.18/.026/.045/.0035/.68/1.12/.5` 等物理字面量。

### 验证

```powershell
python tests/test_estate_rules.py
python tests/test_frontend.py
```

### 提交

```powershell
git commit -m "feat: add pure estate rules module with cross-language tests"
```

## 任务 3：钓鱼改为消费服务端规则

### 文件

- 修改 `assets/js/estate/fishing.js`

### 内容

物理推进改调 `rules.tensionStep`，鱼竿系数改从 `catalog.tools.rod` 读，
危险阈值改为客户端表现常量并命名，分帧步长改读
`fishing_rules.steps_per_frame`，操作上限改读 `fishing_rules.steps`。
生命周期（`MutationObserver` 自清理）保持不变。

### 验证

```powershell
python tests/test_estate_rules.py
python tests/test_estate_frontend.py
```

### 提交

```powershell
git commit -m "refactor: drive estate fishing from server-shipped rules"
```

## 任务 4：收敛 `ui.js`

### 文件

- 修改 `assets/js/estate/ui.js`

### 内容

- `PANELS` 分派表，`render()` 与 `interact()` 共用一条链。
- `itemCard(...)` 单一卡片构造，全部改用 `createElement`，删除
  `innerHTML` 插值服务端数据的四处。
- 五处目录取值改走 `rules.catalogEntry`。
- `button(label, onClick, { disabled, className })` 内部合并
  `pendingEstateAction()`，删除十处 `disabled ||=`。
- 维修费、预留格改走 `rules.repairCost` / `rules.reservedSlots`。
- `timeLeft` 迁移为 `rules.formatDuration`。

### 验证

```powershell
python tests/test_estate_rules.py
python tests/test_estate_frontend.py
python tests/test_frontend.py
```

### 提交

```powershell
git commit -m "refactor: collapse duplicated panel and card logic in estate ui"
```

## 任务 5：收敛其余模块

### 文件

- 修改 `assets/js/estate/mining.js`、`protocol.js`、`input.js`、`map.js`、`view.js`

### 内容

- `mining.js`：格数改读 `run.size`，三份战利品汇总改为
  `rules.lootSummary`。
- `protocol.js`：`SOUND_CUES` 提为模块常量；两张同键请求表合并为一条记录。
- `input.js`：删除无意义别名；监听器改为表驱动绑定与解绑；摇杆半径命名。
- `map.js`：就地命名 `190/125/18/12/78/12/cropSprites`，坐标一律不动。
- `view.js`：模板提为模块常量。

### 验证

```powershell
python tests/test_estate_frontend.py
python tests/test_frontend.py
python tests/test_estate_rules.py
```

### 提交

```powershell
git commit -m "refactor: tidy estate mining, protocol, input, map and view"
```

## 任务 6：修正钉死实现文本的断言并全量回归

### 文件

- 修改 `tests/test_estate_frontend.py`

### 内容

把 `assertIn("Math.floor(trace.length / 10)", fishing)` 改为钉行为的断言
（分帧步长来自 `fishing_rules.steps_per_frame`），并新增：`fishing.js` 不含
物理字面量、`ui.js` 不再出现硬编码维修费算式。保留模块可达、样式版本号、
无原生弹窗、品牌命名等结构性断言。

### 全量回归

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
python tests/test_games.py
python tests/test_mahjong.py
python tests/test_guandan.py
python tests/test_ratings.py
python tests/test_rewards.py
```

外加端到端冒烟（`.tmp-test/run_smoke.ps1`）。

### 提交

```powershell
git commit -m "test: pin estate frontend behaviour instead of implementation text"
```

## 完成判定

- `rules.js` 是客户端唯一的钓鱼张力推进实现，且不含物理字面量。
- `fishing.js`、`ui.js` 不再重算服务端规则（维修费、鱼竿系数、预留格、格数）。
- `ui.js` 只剩一条分派链、一个卡片构造器，无 `disabled ||=`。
- 庄园全部测试通过；`test_estate_rules.py` 的跨语言等价用例通过。
- 经济模拟数值逐位一致；端到端冒烟 29/29；仓库其余测试无新增失败。
