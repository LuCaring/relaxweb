# 休闲庄园 · 角色皮肤与素材规范

## 玩家使用

进入休闲庄园，点击顶部「衣橱」（手机在顶栏右下方），选择角色；可以看正面、背面、左右方向，以及「站一会儿 / 走两步」。试穿不改变地图角色，点击「穿上这套」并收到服务端确认后才正式换装。

小女孩默认免费；其余 9 套普通皮肤每套 5000 金币，购买后永久解锁。珍藏奖励不可购买，默认名称为「珍藏旅人」，默认解锁条件是解锁其他全部皮肤并集齐全部收藏品；部署者可以调整这些设置。旧版免费穿戴不视为购买，未解锁的装扮恢复为小女孩。

当前角色：

| ID | 名称 | 角色素材 |
| --- | --- | --- |
| `berry` | 小女孩（默认） | [图集](../assets/estate/characters/berry/character.png) · [配置](../assets/estate/characters/berry/character.json) |
| `steve` | 史蒂夫 | [图集](../assets/estate/characters/steve/character.png) · [配置](../assets/estate/characters/steve/character.json) |
| `dva` | DVA | [图集](../assets/estate/characters/dva/character.png) · [配置](../assets/estate/characters/dva/character.json) |
| `little_gwen` | 小小格温 | [图集](../assets/estate/characters/little_gwen/character.png) · [配置](../assets/estate/characters/little_gwen/character.json) |
| `jamie` | 杰米 | [图集](../assets/estate/characters/jamie/character.png) · [配置](../assets/estate/characters/jamie/character.json) |
| `xiaofei` | 小菲 | [图集](../assets/estate/characters/xiaofei/character.png) · [配置](../assets/estate/characters/xiaofei/character.json) |
| `weichong` | 威虫 | [图集](../assets/estate/characters/weichong/character.png) · [配置](../assets/estate/characters/weichong/character.json) |
| `ryu` | 隆 | [图集](../assets/estate/characters/ryu/character.png) · [配置](../assets/estate/characters/ryu/character.json) |
| `malphite` | 墨菲特 | [图集](../assets/estate/characters/malphite/character.png) · [配置](../assets/estate/characters/malphite/character.json) |
| `nailong` | 奶龙 | [图集](../assets/estate/characters/nailong/character.png) · [配置](../assets/estate/characters/nailong/character.json) |
| `collection_reward` | 珍藏旅人 | [图集](../assets/estate/characters/collection_reward/character.png) · [配置](../assets/estate/characters/collection_reward/character.json) |

- 穿戴状态保存到当前账号的 SQLite 存档，刷新、重连、重新登录会恢复；同账号多个连接收到一致快照。
- 已解锁皮肤切换不扣金币，不改变经验、仓库、农作物、工具、钓鱼/挖矿进度，也不因皮肤改变速度或碰撞体积。
- Escape / 点击空白 / 关闭按钮可退出；支持 Tab 焦点循环并恢复入口焦点；打开衣橱会清空移动输入。
- 请求期间禁止重复换装；素材失败可重试；保存失败显示错误，15 秒超时释放请求状态。超时后服务端可能仍已完成换装，应重试或重新进入庄园确认，不能以本地预览作为已保存的证据。
- 「减少动态效果」系统设置会冻结衣橱预览帧；仍可切换方向和动作。

## 自定义珍藏奖励

在本地 `config.json` 中设置下列配置，重启聊天服务并刷新页面生效。
缺省字段使用 `config.py` 的默认值，完整示例见 `config.example.json`。

```json
{
  "estate": {
    "collection_reward": {
      "enabled": true,
      "name": "星空守望者",
      "description": "属于这个站点的珍藏奖励。",
      "asset_id": "steve",
      "required_skins": ["steve", "dva"],
      "required_collectibles": ["antique_watch", "message_bottle"]
    }
  }
}
```

- `name`、`description` 是界面文案；衣橱和商店只读取服务端目录，不需修改前端代码。
- `asset_id` 是 `assets/estate/characters/` 下的素材目录名，可以使用已有角色，也可以新增符合下述格式的四文件目录。`character.json` 的 `id` 必须等于素材目录名。界面名称以站点配置为准，不依赖素材中的 `name`。目录名仅接受小写字母、数字、下划线和连字符，不能填外部 URL 或父目录路径。
- `required_skins`、`required_collectibles` 分别接受 `"all"` 或 ID 列表。`"all"` 表示所有普通皮肤（含默认皮肤）或所有收藏品；空列表表示没有该类要求。两类条件均满足才会永久解锁，奖励不能要求自身作为前置条件。无效 ID 和重复项会在启动时报告配置错误。
- `enabled: false` 从目录中隐藏奖励并禁止购买或换装；原有所有权记录保留，已穿戴者恢复默认皮肤。重新启用后仍可穿戴已获得的奖励。
- 奖励的存档 ID 固定为 `collection_reward`。改名、换素材或提高解锁条件不会撤销已获得的奖励，也不会改变金币、背包和角色属性。
- 默认素材目录为 `collection_reward`。部署私有素材可放在被 Git 忽略的 `assets/estate/characters/local_*/` 目录，配置相应 `asset_id`；发布自己的主题包时也可使用其他目录并跟踪这些文件。

## 素材范围

内置十套普通/默认角色和一套珍藏奖励素材，每套提供 18 帧最小可用集；每个角色目录包含 4 倍高清 RGBA 图集、JSON、64×64 透明头像和 128×192 透明正面预览。地图按高质量缩小显示，衣橱直接使用高清帧。

```text
assets/estate/characters/<character_id>/
  character.png
  character.json
  portrait.png
  preview.png
```

- PNG：8-bit RGBA、sRGB，保留原图颜色和半透明抗锯齿边缘。
- 逻辑帧为 36×48，脚底锚点 (18,46)；`assetScale: 4` 的实际帧为 144×192、锚点 (72,184)。
- 图集为 8 列，实际透明间隔为 4px，列步长 148、行步长 196；六行图集为 1180×1172，未使用格子全透明。
- 行序：`idle_down, idle_up, idle_right, walk_down, walk_up, walk_right`。
- Idle：每方向 2 帧、2.5 FPS；第二帧的高度校准为 43px，保持脚底位置，产生约 1px 呼吸起伏。
- Walk：每方向 4 帧、8 FPS；向左镜像右向。疾跑目前复用 walk 并加快播放，**没有独立 run 图帧**。
- 地图与试衣镜使用同一套 [图集加载与渲染器](../assets/js/estate/characters.js)，关闭 Canvas 平滑、CSS 使用 pixelated。
- 地图位置表示脚底；逻辑碰撞框为素材尺度 14×10，随统一显示比例转换。交互探测由脚底向朝向延伸，不采用头部/发型中心。

**边界说明：**这是对用户提供图稿的最近邻规范化导入，并非重新手绘整套角色。原图不同动画行尺寸有差异，转换按动画行校准，超宽发型横向适配，保留原有角色特征；没有声称重新绘制或逐帧校验所有服装摆幅、光照及人体比例。独立互动、收获、挖矿、甩竿、等待和收杆动作尚未制作，不能把回退到 idle / walk 当作完整 96 帧动作集。

## 重建素材

[转换脚本](../tools/build_estate_characters.py) 使用 Pillow；运行时和自动校验不依赖 Pillow。

```bash
python -m pip install 'Pillow>=9'
python tools/build_estate_characters.py
```

输入为未修改的[小女孩原图](../assets/estate/nature/player/character-sheet.png)，以及 `assets/estate/characters/sources/` 中史蒂夫、DVA、小小格温、杰米、小菲、威虫和珍藏旅人的用户原稿。脚本按已核对的区域取帧；只有三方向连续帧的原稿会复用前两帧作为待机动作，左向统一镜像右向。转换只移除明确的鲜红裁切标记，保留原稿完整色彩和半透明抗锯齿边缘，并用 Lanczos 生成 4 倍高清固定网格。后续更精细的手工绘图可以直接替换同规格输出，无需改地图代码。

## 服务端协议

在地图「商店」购买使用 `estate_buy_skin`，只提交 `skin_id` 与 `request_id`；价格由服务端目录决定，扣款写入 `estate_purchase` 流水。购买不自动换装，衣橱只负责试穿和切换。已购买皮肤重复购买不重复扣款。

切换已解锁皮肤时客户端发出：

```json
{"type":"estate_set_skin","request_id":"unique-request-id","skin_id":"steve"}
```

服务端经统一授权和事务流程调用 [set_skin](../estate/skins.py)，只接受 [目录](../estate/catalog.py) 中的稳定 ID。皮肤不使用用户提交的价格、属性、资源路径或账号名。`profile.skin_id` 由 [Schema 迁移](../estate/schema.py) 追加，旧账号默认 `berry`；重复初始化不覆盖已有有效选择。

返回 `estate_state` 带最新 `profile.skin_id`、`catalog.skins` 、`skins.owned`、`skins.missing_skins`、`skins.missing_collectibles` 和动作结果。同请求 ID 的同一操作幂等重放，不再改版本或写回旧皮肤；不同参数复用 ID 返回 `request_conflict`。不合法 ID / 类型返回 `invalid_skin`，未登录返回 `auth_required`。未解锁穿戴返回 `skin_locked`，购买特殊皮肤返回 `skin_not_for_sale`，余额不足返回 `insufficient_coins`。

**界面必须以完整快照的 `profile.skin_id` 为准，而不是历史 `result.skin_id`。** [衣橱](../assets/js/estate/wardrobe.js) 的预览选择只在组件内，未写 localStorage；[地图](../assets/js/estate/map.js) 每帧读取账号快照。未知/加载失败的素材由现有程序角色暂时兜底；未在目录中的角色不能通过衣橱装备。

旧存档中未购买或无效的穿戴 ID 在读取快照时恢复为 `berry`。收集记录永久保存，旧库存中的收集品自动补记；解锁不会消耗收集品。

## 扩展为完整 96 帧

按用户约定继续追加三方向的 run4、interact4、harvest4、mine4、fish_cast4、fish_wait2、fish_reel4，各动画独占一行、仍保持 8 列和透明空格。完整集合共 27 行、295×1322。更新 JSON 的 `animations`，从 `fallbacks` 移除已有真实动画的回退即可；非对称装备需显式绘制 left 并声明相应动画。

新增普通角色须注册服务端目录并提供完整四文件目录；客户端从服务端目录注册本地素材，无需维护第二份角色名单。自定义珍藏奖励只需提供素材并修改站点配置。

## 验证

[购买规则测试](../tests/test_estate_skin_purchase.py) 覆盖价格边界、重复扣款、每个缺失条件、两种集齐顺序、旧存档和事务回滚；[购买协议测试](../tests/test_estate_skin_purchase_protocol.py) 覆盖真实并发购买、价格防篡改、流水、特殊解锁与重连。

[后端单元测试](../tests/test_estate_skins.py) 覆盖默认值、旧库迁移、持久化、账户隔离、非法请求、幂等和非空玩法数据的零影响；[WebSocket 回归](../tests/test_estate_protocol.py) 覆盖同账号广播、不同账号隔离、重放与重连。

[素材测试](../tests/test_estate_character_assets.py) 用标准库直接解码 PNG，检查 RGBA/sRGB、颜色数量、Alpha、尺寸、脚底、留白、透明间隔和空格；[浏览器测试](../tests/test_estate_wardrobe.cjs) 检查动画取帧、方向镜像、试穿不保存、确认后换装、旧结果不回退、焦点、地图输入阻断、刷新恢复、五种尺寸布局、素材重试、减少动态效果和卸载重进。

```bash
PYTHONPATH=tests python -B -m unittest test_estate_skins test_estate_character_assets test_estate_frontend -v
# 需已安装 Playwright 与 Chrome；无须启动替代 Web 服务器。
NODE_PATH=/path/to/node_modules CHROME_PATH=/path/to/chrome node tests/test_estate_wardrobe.cjs
```

截图输出可用 `ESTATE_SCREENSHOT_DIR` 指定。浏览器测试从本地文件路由提供测试页、模拟网络消息；真实协议/存档由独立 WebSocket 测试验证，而不是把模拟消息当成实际持久化测试。
