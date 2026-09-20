# 休闲庄园后端重构实施计划

日期：2026-09-20
依据：`docs/superpowers/specs/2026-09-20-estate-backend-refactor-design.md`
分支：`feat/xiaopang-estate-phase2`
范围：`estate/` 包后端代码与必要的测试 import 调整
不含：前端、`chat_server.py` 协议层、任何数值与玩法改动

## 执行原则

1. 严格按任务顺序实施，每个任务完成测试后单独提交。
2. **断言一律不改**：测试只允许改 import 路径与 patch 目标。
3. 18 个导出函数的名字与签名不变，`chat_server.py` 一行都不改。
4. `EstateError` 的 `code` 与 `message` 逐字不变。
5. 不改数据库表结构与迁移，不引入第三方依赖。
6. 每个任务结束跑庄园全量测试；经济模拟数值必须与基线逐位一致。

## 基线指纹

重构前记录，重构后逐位比对（来源 `tests/test_estate_economy.py`）：

```text
Mining Lv1: mean_net 64.04245  bomb_rate 0.3795  loss_rate 0.0793
Mining Lv2: mean_net 94.6786  bomb_rate 0.7835  loss_rate 0.16845
Mining Lv3: mean_net 110.84635 bomb_rate 0.96995 loss_rate 0.228
Fishing Lv1 worm:      70% 33.563289999999995  full 56.3047
Fishing Lv2 worm:      70% 36.64467571428571   full 61.471985714285715
Fishing Lv2 glow_grub: 70% 45.827825714285716  full 78.8764857142857
Fishing Lv3 worm:      70% 41.111172272727266  full 68.89907727272728
Fishing Lv3 glow_grub: 70% 53.67906727272727   full 91.13892727272727  legendary 0.00175
```

重构前庄园 unittest 基线：schema 5 + catalog_content 4 + service 9 +
activities 11 + frontend 7 + economy 3 = **39 个用例全绿**。

## 目标文件布局

```text
estate/
  __init__.py    18 个导出不变；__all__ 由导入派生
  catalog.py     目录数据 + 命名常量 + item_id(kind, key)
  schema.py      不变
  store.py       内核 + 读模型（新）
  farming.py     农场业务（新）
  activities.py  工具 / 钓鱼 / 矿场（改）
```

依赖方向单向：`catalog` ← `store` ← {`farming`, `activities`}。
`store` 不得导入 `farming` 或 `activities`（否则循环依赖）。

## 任务 1：新建 `estate/store.py`

### 文件

- 新建 `estate/store.py`
- 保留 `estate/service.py` 暂不删除（本任务结束时它仍被 `__init__.py` 引用）

### 内容

从 `service.py` 迁入并去掉跨模块用的下划线前缀：

- `EstateError`（类型不变）
- `positive_int(value, code, message, maximum)` ← `_positive_int`
- `plot_index(value)` ← `_plot_index`
- `ensure_estate(conn, username, now)`
- `profile(conn, username)` ← `_profile`，列名以模块常量声明，映射集中一处
- `inventory_rows(conn, username)` / `inventory_used(conn, username)`
- `capacity(profile)` / `require_capacity(conn, username, profile, extra)`
- `change_inventory(conn, username, item_id, delta)` ← `_change_inventory`
- `debit(adjust_coins, conn, username, amount, detail, request_id)` ← `_debit`
- `award_xp(conn, username, amount) -> level`：**唯一的升级循环**，
  由 `activities._award_xp` 与 `harvest` 共用
- `run_action(conn, username, request_id, action_type, payload, now, mutate)` ← `_action`
- 错误常量：`LEVEL_LOCKED`、`TOOL_MISSING`、`INVALID_PLOT`、`PLOT_LOCKED`、
  `PLOT_BUSY`、`MAX_LEVEL`、`UNKNOWN_ITEM`、`INSUFFICIENT_COINS`
- `estate_state(conn, username, now)`：读模型，拆出
  `_sweep_expired_fishing`、`_plot_views`、`_inventory_views`、`_tool_views`、
  `_fishing_view`、`_mining_view` 六个小函数

### 验证

```powershell
python tests/test_estate_schema.py
python tests/test_estate_service.py
python tests/test_estate_activities.py
```

### 提交

```powershell
git commit -m "refactor: extract estate domain kernel into store module"
```

## 任务 2：新建 `estate/farming.py`，`buy()` 改分发表

### 文件

- 新建 `estate/farming.py`
- 删除 `estate/service.py`（内容已全部迁出）
- 修改 `estate/__init__.py` 的导入来源

### 设计

- `_PURCHASES` 分发表：`seed` / `bait` / `plot` / `land` / `warehouse`
- seed 与 bait 合并为一个参数化实现：传入目录表、item 前缀生成器、
  流水文案模板
- plot / land / warehouse 各为小函数，共用"当前档 → 下一档"校验助手
- `harvest` 调用 `store.award_xp`，删除重复的升级循环
- 五个操作的**返回 dict 键与值保持完全一致**

### 验证

```powershell
python tests/test_estate_service.py
python tests/test_estate_activities.py
python tests/test_estate_economy.py
```

### 提交

```powershell
git commit -m "refactor: split estate farming operations and table-drive purchases"
```

## 任务 3：改写 `estate/activities.py`

### 文件

- 修改 `estate/activities.py`
- 修改 `tests/test_estate_activities.py`、`tests/test_estate_economy.py` 的 patch 目标

### 设计

- 导入来源改为 `estate.store` 的公开名称
- `_make_board` → `make_board`、`_pick_fishing_catch` → `pick_fishing_catch`
  提升为公开名（它们已被测试按路径 patch）
- 删除本模块的 `_award_xp`，改用 `store.award_xp`
- 钓鱼与矿场的魔法数字改为引用 `catalog.py` 命名常量

### 验证

```powershell
python tests/test_estate_activities.py
python tests/test_estate_economy.py
python tests/test_estate_activities_protocol.py
```

### 提交

```powershell
git commit -m "refactor: use public estate kernel from activities"
```

## 任务 4：整理 `estate/catalog.py`

### 文件

- 修改 `estate/catalog.py`

### 设计

- 新增命名常量：`FISHING_STEPS`、`FISHING_DURATION_LIMIT`、
  `FISHING_TIMEOUT_SECONDS`、`FISHING_TENSION`、`MINE_CELLS`、
  `MINE_BOARD_SIZE`、`MINE_EXTRA_CELLS`、`MINE_LEGACY_RESERVED_SLOTS`
- 6 个 `*_item()` 收敛为 `item_id(kind, key)`，保留原函数名作为一行别名
- `item_info()` 的分组映射改为由统一注册表派生
- `store.estate_state` 取矿层名改用 `MINING_LEVELS[level]["name"]`

### 验证

```powershell
python tests/test_estate_catalog_content.py
python tests/test_estate_schema.py
python tests/test_estate_service.py
python tests/test_estate_activities.py
```

### 提交

```powershell
git commit -m "refactor: name estate magic numbers and unify item ids"
```

## 任务 5：收口 `__init__.py` 与测试 import

### 文件

- 修改 `estate/__init__.py`：`__all__` 由导入派生
- 修改 `tests/test_estate_service.py`、`tests/test_estate_activities.py`、
  `tests/test_estate_economy.py` 的 import 路径

### 验证

```powershell
python tests/test_estate_service.py
python tests/test_estate_activities.py
python tests/test_estate_economy.py
python tests/test_frontend.py
```

### 提交

```powershell
git commit -m "refactor: derive estate exports and follow new module paths"
```

## 任务 6：全量回归与端到端冒烟

### 自动化回归

```powershell
python tests/test_estate_schema.py
python tests/test_estate_catalog_content.py
python tests/test_estate_service.py
python tests/test_estate_activities.py
python tests/test_estate_frontend.py
python tests/test_estate_economy.py
python tests/test_estate_protocol.py
python tests/test_estate_activities_protocol.py
python tests/test_games.py
python tests/test_frontend.py
python tests/test_guandan.py
python tests/test_mahjong.py
python tests/test_ratings.py
python tests/test_rewards.py
python tests/test_admin.py
```

### 判定标准

- 庄园 39 个 unittest 用例全绿，断言内容未变。
- 经济模拟数值与上方基线**逐位一致**。
- `test_estate_protocol.py`、`test_estate_activities_protocol.py` 通过。
- 端到端冒烟（注册 → 登录 → 庄园买卖种收 → 签到抽奖 → 建房发牌）通过。
- 仓库其余测试无**新增**失败（`test_admin` 的 `getloadavg` 属既有问题）。

### 提交

```powershell
git commit -m "test: verify estate refactor keeps behaviour unchanged"
```

## 完成判定

- `estate/` 无 `service.py`，三层职责清晰，`store` 不依赖上层。
- `grep -n "^def _" estate/*.py` 中不再出现被跨模块调用的私有名。
- `"level_locked"` 等重复字面量只剩错误常量表一处。
- 钓鱼与矿场的魔法数字全部为命名常量。
- 全量回归与端到端冒烟通过，经济指纹逐位一致。
- 每个任务单独提交，历史可逐步回退。
