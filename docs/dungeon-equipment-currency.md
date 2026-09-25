# 地下城装备词条与通货

本轮同时更新两套入口：正式地下城存档使用 `dungeon/legacy/` 和 `server/dungeon/legacy_protocol.py`；`dungeon-beta.html` 是浏览器内的动作玩法原型，局内商店和通货暂存在本局状态中。Beta 原型的结果不会写入正式账号。

## 参考与概率口径

- [poe2db 单手剑词条](https://poe2db.tw/tw/One_Hand_Swords)和[腰带词条](https://poe2db.tw/tw/Belts)列出 T 级、数值区间、物品等级门槛与前后缀。本站同时提示部分词条权重无法从游戏文件取得。
- [poe2db 可堆叠通货](https://poe2db.tw/tw/Stackable_Currency)给出蜕变、增幅、富豪、点金、崇高、混沌、神圣和无效石的主要效果。本项目采用这些操作顺序，但数值、可用装备和掉落率按本游戏平衡设定。
- **所有展示的权重和概率是本游戏配置，不是 PoE2 官方概率。** 单条候选词条概率 = 该词条权重 / 当前物品等级、部位、前后缀上限及已有词条过滤后的总权重。掉落表的 `chance_bp` 是每种通货独立判定，`10000` 表示 100%。

正式存档的词条规则版本为 `ruins-affixes-v1`，T3 / T2 / T1 的最低物品等级分别为 1 / 35 / 65，权重分别为 1000 / 450 / 120。数值区间在 `dungeon/legacy/crafting.py`。一次挑战会把词条规则、物品等级和通货掉落配置冻结进战斗快照；旧快照仍按原 v1 奖励结算。已有物品的物品等级迁移为 1，属性和词条保持原值。

## 正式存档协议

- `get_dungeon` 的状态增加 `currencies`、`crafting_rules`；每件物品增加 `item_level`。挑战奖励结果增加 `currencies`，掉落预览包含物品等级和每种通货的独立掉落概率。
- `dungeon_affix_probabilities` 输入 `request_id`、`item_id`，可选 `currency_id`。不指定时返回自由新增词条的候选池；指定蜕变、增幅、富豪或崇高石时按该通货的前后缀规则过滤。返回 `weight`、`total_weight` 和向下取整的 `probability_bp`，分数 `weight / total_weight` 是精确配置概率。点金石连续抽取、混沌石先随机移除，需逐步计算，不提供单次新增概率表。
- `dungeon_use_currency` 输入 `request_id`、`item_id`、`currency_id`、`expected_version`。动作在同一数据库事务里检查所有权、背包位置、锁定、活动挑战、资产预留和余额；成功时扣一枚通货、更新装备属性与词条、递增版本并保存幂等回执。失败不会扣费。
- 魔法装备最多一前缀一后缀，稀有装备最多三前缀三后缀；同一词条组不能重复。普通/魔法/稀有分别映射旧存档的 `normal` / `excellent` / `rare`。旧 `epic` 品质保持可读，但不参与这批基础通货。

## Beta 局内原型

商店现在按波次限制基底 T 级，并为随机装备生成独立实例、稀有度、物品等级和词条。选择右侧装备后可以使用已有通货，查看 T 级汇总概率和每条候选词条概率。商店能售卖通货，每波结算也会按配置概率独立掉落通货；这些概率写在 `assets/js/dungeon/dgn-beta-crafting.js`，仅服务本地原型。
