# 休闲庄园后端重构设计

日期：2026-09-20
状态：设计已确认，等待实施计划
适用仓库：`LuCaring/relaxweb`
分支：`feat/nature-estate-phase2`

## 1. 背景与目标

休闲庄园已随 PR #6 合并进上游 `main`。后端 `estate/` 包共 1302 行，
功能完整、测试充分（8 个测试文件 921 行），但代码存在明显的冗长与职责
错位，后续每次改动都需要在多个近似分支之间同步修改。

本次重构**只改可维护性，不改功能与协议**。目标：

1. 消除 `service.py` 的双职责（领域内核 + 农场业务）。
2. 让跨模块使用的原语成为**公开接口**，而不是靠下划线私有名越界调用。
3. 收敛 `buy()` 的 5 个近似重复分支，升级循环的两份实现，以及散落的
   魔法数字与重复错误文案。
4. 拆分后文件更小、职责单一，便于继续在庄园这条线上迭代。

范围：`estate/` 包的后端 Python 代码（含必要的 import 路径调整）。
**不含**前端 `assets/js/estate/*`，不含 `chat_server.py` 的协议适配层，
不含任何数值或玩法调整。

## 2. 现有问题

以下均已逐条核实，附行号。

### 2.1 `buy()` 五个近似重复分支（`service.py:296-395`，约 100 行）

- `seed`（302-315）与 `bait`（317-330）近乎逐行相同：查目录表 → `_positive_int`
  → 校验 `unlock_level` → `_require_capacity` → `_debit` → `_change_inventory`
  → 返回同形状 dict。差异只有目录表、item 前缀和文案措辞。
- `plot`（332-346）、`land`（348-374）、`warehouse`（376-391）共享同一模式：
  查当前档 → 查下一档 → 校验等级与上限 → 扣款 → `UPDATE` → 返回。

### 2.2 升级循环写了两遍

`harvest()`（`service.py:459-469`）与 `_award_xp()`（`activities.py:109-117`）
是同一段 while 循环的两份实现。

### 2.3 接缝位置不对

`activities.py:10-13` 从 `service.py` 导入 5 个下划线私有名：
`_action`、`_change_inventory`、`_debit`、`_profile`、`_require_capacity`。

同时 `tests/test_estate_economy.py:9` 与 `tests/test_estate_activities.py:11,136,170,208`
直接导入并 patch `estate.activities._make_board` 与 `_pick_fishing_catch`。
被测试按路径 patch，说明它们是事实接口，只是命名挂错了。

### 2.4 `estate_state()` 108 行做四件事（`service.py:98-205`）

惰性建档、清扫过期钓鱼局、读取五类实体、组装快照，全部内联在一个函数里。

### 2.5 错误码与文案重复

| 重复项 | 出现次数 |
| --- | --- |
| `"level_locked"` | 10 |
| `"庄园等级不足"` | 9 |
| `"invalid_plot"` | 5 |
| `"tool_missing"` | 4 |

### 2.6 魔法数字散落

- 钓鱼步骤数 `36`：`activities.py:171`、`activities.py:185`、`service.py:171`。
- 钓鱼超时 `90` 秒：`activities.py:178` 硬编码。
- 钓鱼张力模拟的 9 个系数内联在 `simulate_fishing`（`activities.py:196-208`）。
- 矿场格子数 `25`：`activities.py:264/265/268/348` 四处。
- 棋盘边长 `5`：`activities.py:313`、`service.py:185`。
- 旧存档预留 `12`：`schema.py:104`。

### 2.7 目录每次读取都整体重建

`public_catalog()` 在每次 `estate_state` 调用时重建整个目录字典；
`service.py:182` 只是为了取一个矿层名字就重建了全量目录。

### 2.8 `catalog.py` 的重复小函数

`seed_item`/`crop_item`/`bait_item`/`fish_item`/`collectible_item`/`mineral_item`
是 6 个同构单行函数（`catalog.py:173-194`）；`item_info()` 内还硬编码了一张
分组映射表（`catalog.py:218-219`）。

## 3. 目标结构

```text
estate/
  __init__.py    18 个导出，保持不变（__all__ 由导入派生）
  catalog.py     目录数据 + 命名常量 + 统一 item_id(kind, key)
  schema.py      建表与迁移，保持不变
  store.py       领域内核（新）：档案、库存、容量、金币、经验、幂等、错误
  farming.py     农场业务（新）：buy / plant / harvest / sell / sell_all
  activities.py  工具 / 钓鱼 / 矿场（改）：使用 store 的公开接口
```

`service.py` 在本次重构中被 `store.py` + `farming.py` 取代，不保留兼容垫片。

## 4. 行为不变的契约

重构必须逐字守住以下契约。这些是"功能保持不变"的准确边界：

- `estate/__init__.py` 导出的 **18 个函数名与签名不变**，因此
  `chat_server.py` 一行都不用改。
- **14 个客户端消息类型**（`get_estate` 与 13 个 `estate_*`）与
  `estate_state` 快照的字段名不变；服务端仍只发 `estate_state` 与 `estate_error`。
- **所有 `EstateError` 的 `code` 与 `message` 文案逐字不变**：前端把
  `message` 直接弹给玩家，测试也逐字断言。
- `public_catalog()` 的输出结构不变，前端按字段渲染。
- 随机数行为不变：`secrets` 取种子、`random.Random(seed)` 驱动确定性模拟。
- 数据库表结构与迁移不变。

## 5. 模块设计

### 5.1 `estate/store.py`

单次事务内的领域原语，全部为**公开**名称（包内可见即公开，不再用下划线
表示跨模块接口）：

- `EstateError`：错误类型，从 `service.py` 迁入。
- `ensure_estate(conn, username, now)`：幂等建档。
- `profile(conn, username)`：读取档案；**行到 dict 的映射集中在此处**，
  列名以模块常量声明，避免位置下标散落各处。
- `capacity(profile)`、`inventory_used(conn, username)`、
  `require_capacity(conn, username, profile, extra)`。
- `change_inventory(conn, username, item_id, delta)`。
- `debit(adjust_coins, conn, username, amount, detail, request_id)`。
- `award_xp(conn, username, amount) -> level`：**唯一的升级循环**。
- `run_action(conn, username, request_id, action_type, payload, now, mutate)`：
  幂等包装（原 `_action`），签名与语义不变。
- `positive_int(value, code, message, maximum)`、`plot_index(value)`：入参规范化。
- 错误常量：`level_locked()`、`tool_missing()`、`invalid_plot()` 等，把重复的
  `code`/`message` 收成一处。

### 5.2 `estate/farming.py`

`buy` / `plant` / `harvest` / `sell` / `sell_all`。

- `buy` 改为小型分发表 `_PURCHASES = {"seed": ..., "bait": ..., "plot": ...,
  "land": ..., "warehouse": ...}`；seed 与 bait 合并为一个参数化实现，
  plot/land/warehouse 各自小函数并共用一段"当前档 → 下一档"的升级校验。
- `harvest` 调用 `store.award_xp`，删除重复的升级循环。
- 每个操作的返回值 dict 的键与值保持完全一致。

### 5.3 `estate/activities.py`

- 导入来源改为 `estate.store` 的公开名称。
- `_make_board` → `make_board`，`_pick_fishing_catch` → `pick_fishing_catch`：
  提升为公开名，测试按新名称导入与 patch。
- 钓鱼与矿场的常量改为引用 `catalog.py` 中的命名常量。
- `_active`、`_tool` 保留为模块私有（仅本模块使用）。

### 5.4 `estate/catalog.py`

- 新增命名常量：`FISHING_STEPS`、`FISHING_DURATION_LIMIT`、
  `FISHING_TIMEOUT_SECONDS`、`FISHING_TENSION`（张力模拟系数）、
  `MINE_CELLS`、`MINE_BOARD_SIZE`、`MINE_EXTRA_CELLS`、
  `MINE_LEGACY_RESERVED_SLOTS`。
- 6 个 `*_item()` 收敛为 `item_id(kind, key)`；保留 6 个原函数名作为
  一行别名，因为 `tests/test_estate_service.py:10` 导入 `crop_item`、`seed_item`，
  `tests/test_estate_activities.py:15` 导入 `bait_item`。保留别名可以避免
  为了改名去动这三个测试文件的 import。
- `item_info()` 的分组映射表改为由统一注册表派生。
- `estate_state` 取矿层名改用 `MINING_LEVELS[level]["name"]`，不再重建全量目录。

### 5.5 `estate/__init__.py`

导出清单与顺序不变；`__all__` 由导入派生，删除重复书写的一份名单。

## 6. 测试调整

**断言一律不改。** 只允许两类机械调整：

1. import 路径：`estate.service` → `estate.store` / `estate.farming`。
2. patch 目标：`estate.activities._make_board` → `estate.activities.make_board`，
   `_pick_fishing_catch` 同理。

涉及文件：`test_estate_service.py`、`test_estate_activities.py`、
`test_estate_economy.py`。其余庄园测试不受影响。

## 7. 验证

每个任务完成后按序执行：

```powershell
python tests/test_estate_schema.py
python tests/test_estate_catalog_content.py
python tests/test_estate_service.py
python tests/test_estate_activities.py
python tests/test_estate_frontend.py
python tests/test_estate_economy.py
python tests/test_estate_protocol.py
python tests/test_estate_activities_protocol.py
```

全量回归：

```powershell
python tests/test_games.py
python tests/test_frontend.py
python tests/test_guandan.py
python tests/test_mahjong.py
python tests/test_ratings.py
python tests/test_rewards.py
python tests/test_admin.py
```

判定标准：

- 全部庄园测试通过，断言内容未变。
- 经济模拟数值逐位一致：钓鱼 33.56 / 78.88 / 91.14，矿场 64.04 / 94.68 / 110.85，
  传奇鱼 0.175%。
- 两个真实 WebSocket 协议测试通过。
- 端到端冒烟（注册 → 登录 → 庄园买卖种收 → 签到抽奖 → 建房发牌）通过。
- 仓库其余测试无新增失败。

## 8. 不做的事

- 不新增、不删除、不调整任何玩法、数值、道具或解锁条件。
- 不改数据库表结构与迁移。
- 不改前端 `assets/js/estate/*` 与 `assets/css/estate.css`。
- 不改 `chat_server.py` 的协议适配层。
- 不改动测试断言。
- 不引入第三方依赖。
- 不处理本次范围外的既有问题（`estate_actions` 无清理、庄园 handler 无限流、
  `state.js` 不丢弃过期快照）。它们留给后续任务。

## 9. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| 文件搬迁导致测试 import 断裂 | 一次只搬一个模块，每步跑庄园全量测试 |
| 分发表改写 `buy()` 时行为漂移 | 逐分支对照原实现；错误码与文案逐字核对 |
| 常量提取改变了模拟参数 | 经济模拟测试逐位比对；不动数值，只改名 |
| 重命名被 patch 的私有函数 | 同步更新测试 patch 路径，保持断言不变 |

每一步都在 `feat/nature-estate-phase2` 上单独提交；出现无法解释的
测试失败时，回退到上一个提交再定位。
