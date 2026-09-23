# 地下城美术资源占位目录

前端当前全部使用占位表现（emoji + `assets/js/dungeon/assets.js`）。接入正式素材时
按下面的命名放入本目录，前端无需改代码即可自动加载；找不到文件时会继续用占位图。

## 命名规则

| 类别 | 路径 | 来源字段 |
| --- | --- | --- |
| 敌人 | `enemies/<visual_id>.png` | `catalog.challenges[].enemy.visual_id` |
| 装备 | `items/<visual_id>.png` | 物品实例与 `catalog.item_templates[].visual_id` |

- `visual_id` 只含字母、数字、下划线、连字符（后端正则 `[A-Za-z0-9_-]{1,96}`）。
- 现有敌人：`ruins_slime`（遗迹软泥）。现有装备模板：`starter_blade`、`starter_helm`、
  `starter_chest`、`starter_belt`、`starter_boots`、`starter_charm`、`ruins_blade`。
- 玩家形象暂用固定 emoji（🧙），后续如需素材再约定 `players/<id>` 命名。

## 交付清单（美术协作者第一批）

1. 每个敌人至少：待机一帧静态图（后续可扩展为攻击/受击/死亡的帧序列）。
2. 每件装备一张 32–64px 方形图标。
3. 一份 `ATTRIBUTION.md`，记录素材来源与许可（可仿照 `assets/estate/ATTRIBUTION.md`）。

## 注意

- `deploy/serve.py` 静态白名单已包含 `.png/.webp/.svg/.json`，无需改服务端。
- 缺图不得阻塞战斗事件播放与结算展示：`assets.js` 的加载失败回退逻辑保留。
