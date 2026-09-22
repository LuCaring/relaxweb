# 庄园目录标识迁移

仓库使用通用目录和存档 ID。资源位于 `assets/estate/nature/`，珍藏奖励 ID 为
`collection_reward`，稀有作物为 `magic_grass`、`miracle_flower`，稀有鱼为
`mystery_fish`，收藏品为 `message_bottle`、`gold_button`、`antique_watch`、`lost_underwear`。
新安装不需要迁移。已有存档必须在新代码首次读取前完成标识迁移，避免未知物品
无法使用或旧皮肤被恢复为默认皮肤。

## 提供本地映射

历史标识由部署者保存在被 Git 忽略的 `config.migration.local.json` 中。
仓库不内置部署相关的旧名称，也不通过编码或字符串拼接隐藏旧名称。
每一组是旧 ID 到新 ID 的映射，ID 不带 `seed:`、`crop:` 等类别前缀：

```json
{
  "skins": {"previous_reward": "collection_reward"},
  "crops": {"previous_crop": "magic_grass"},
  "fish": {"previous_fish": "mystery_fish"},
  "collectibles": {"previous_collectible": "antique_watch"}
}
```

遗漏的分组默认为空；支持多个旧 ID 合并到一个新 ID，不支持链式或循环映射。
把示例左侧替换为旧数据库中的真实标识，右侧必须是新目录中的对应标识。

## 生成迁移副本

停止聊天和鉴权服务后执行，保持停服直到切换数据库和新代码完成：

```bash
python3 tools/migrate_estate_ids.py \
  --source users.db \
  --output users.migrated.db \
  --mapping config.migration.local.json
```

工具使用 SQLite backup 读取一致快照，原文件保持不变，输出文件必须不存在。
迁移在副本的单一事务中执行，失败删除不完整副本。完成后执行完整性检查。
检查副本并备份原配置，再将 `database.file` 或 `LIVE_DB_FILE` 指向副本的绝对路径；
聊天、鉴权与管理命令必须使用同一个路径。保留原数据库以便回退。

覆盖穿戴、已解锁皮肤、已种作物、库存、永久收藏记录、钓鱼目标、偷菜记录及
动作/会话结果中的结构化 ID。库存碰撞累加数量，皮肤和收藏品去重；重复迁移不增加数量。
用户名、金币、历史流水文案、请求 ID 和请求摘要保持不变。

旧动作的请求摘要包含旧标识而不能从结果安全重建，因此迁移后使用新标识重发旧请求 ID
会得到 `request_conflict`。切换后刷新所有客户端，由客户端生成新的请求 ID；
不要让旧客户端跨升级继续发送操作。

本地站点标题、推流用户名、推流路径仍由部署配置决定。本次通用默认值调整不会
重写现有 `config.json`；更改直播路径或用户名时还需同步推流端和 MediaMTX 配置。
