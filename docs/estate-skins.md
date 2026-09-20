# 小胖庄园 · 角色皮肤与素材规范

## 玩家使用

进入小胖庄园，点击顶部「衣橱」（手机在顶栏右下方），选择角色；可以看正面、背面、左右方向，以及「站一会儿 / 走两步」。试穿不改变地图角色，点击「穿上这套」并收到服务端确认后才正式换装。

当前免费开放：

| ID | 名称 | 角色素材 |
| --- | --- | --- |
| `berry` | 经典莓果（默认） | [图集](../assets/estate/characters/berry/character.png) · [配置](../assets/estate/characters/berry/character.json) |
| `xiaopang` | 小胖庄园主 | [图集](../assets/estate/characters/xiaopang/character.png) · [配置](../assets/estate/characters/xiaopang/character.json) |
| `rose_mage` | 粉樱礼帽 | [图集](../assets/estate/characters/rose_mage/character.png) · [配置](../assets/estate/characters/rose_mage/character.json) |

- 穿戴状态保存到当前账号的 SQLite 存档，刷新、重连、重新登录会恢复；同账号多个连接收到一致快照。
- 不扣金币，不改变经验、仓库、农作物、工具、钓鱼/挖矿进度，也不因皮肤改变速度或碰撞体积。
- Escape / 点击空白 / 关闭按钮可退出；支持 Tab 焦点循环并恢复入口焦点；打开衣橱会清空移动输入。
- 请求期间禁止重复换装；素材失败可重试；保存失败显示错误，15 秒超时释放请求状态。超时后服务端可能仍已完成换装，应重试或重新进入庄园确认，不能以本地预览作为已保存的证据。
- 「减少动态效果」系统设置会冻结衣橱预览帧；仍可切换方向和动作。

## 本次交付的素材范围

三个角色各提供 18 帧最小可用集，共 54 帧；每个角色目录包含图集、JSON、64×64 透明头像和 128×192 透明正面预览。

```text
assets/estate/characters/<character_id>/
  character.png
  character.json
  portrait.png
  preview.png
```

- PNG：8-bit RGBA、sRGB；Alpha 仅 0 / 255；每角色最多 24 个不透明颜色；无纯黑描边或半透明边缘。
- 帧：36×48；脚底锚点 (18,46)；透明侧边至少 2px，顶部和底部至少 1px；内容高度 43–44px，宽度不超过 32px。
- 图集：8 列，1px 全透明间隔；列步长 37，行步长 49；无外边距。本次六行的图集为 295×293，未使用格子全透明。
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

输入为未修改的[经典莓果原图](../assets/estate/xiaopang/player/character-sheet.png)、[小胖原图](../assets/estate/characters/sources/xiaopang.png)和[粉樱原图](../assets/estate/characters/sources/rose_mage.png)。脚本按已核对的区域取帧，去除低 Alpha 杂点与鲜红生成边，最近邻归一化尺寸、使用共享调色板，输出固定网格。后续更精细的手工绘图可以直接替换同规格输出，无需改地图代码。

## 服务端协议

客户端发出：

```json
{"type":"estate_set_skin","request_id":"unique-request-id","skin_id":"xiaopang"}
```

服务端经统一授权和事务流程调用 [set_skin](../estate/skins.py)，只接受 [目录](../estate/catalog.py) 中的稳定 ID。皮肤不使用用户提交的价格、属性、资源路径或账号名。`profile.skin_id` 由 [Schema 迁移](../estate/schema.py) 追加，旧账号默认 `berry`；重复初始化不覆盖已有有效选择。

返回 `estate_state` 带最新 `profile.skin_id`、`catalog.skins` 和动作结果。同请求 ID 的同一操作幂等重放，不再改版本或写回旧皮肤；不同参数复用 ID 返回 `request_conflict`。不合法 ID / 类型返回 `invalid_skin`，未登录返回 `auth_required`。

**界面必须以完整快照的 `profile.skin_id` 为准，而不是历史 `result.skin_id`。** [衣橱](../assets/js/estate/wardrobe.js) 的预览选择只在组件内，未写 localStorage；[地图](../assets/js/estate/map.js) 每帧读取账号快照。未知/加载失败的素材由现有程序角色暂时兜底；未在目录中的角色不能通过衣橱装备。

若开发库中曾手动存过已废弃的实验 ID（`mint / sky / wisteria / farmer`），本次不会擅自重写数据；进入衣橱重新选择有效角色即可。

## 扩展为完整 96 帧

按用户约定继续追加三方向的 run4、interact4、harvest4、mine4、fish_cast4、fish_wait2、fish_reel4，各动画独占一行、仍保持 8 列和透明空格。完整集合共 27 行、295×1322。更新 JSON 的 `animations`，从 `fallbacks` 移除已有真实动画的回退即可；非对称装备需显式绘制 left 并声明相应动画。

新角色须同时注册服务端目录和客户端 `CHARACTER_IDS`，并提供完整四文件目录；不要让任意服务端字符串拼出外部素材 URL。

## 验证

[后端单元测试](../tests/test_estate_skins.py) 覆盖默认值、旧库迁移、持久化、账户隔离、非法请求、幂等和非空玩法数据的零影响；[WebSocket 回归](../tests/test_estate_protocol.py) 覆盖同账号广播、不同账号隔离、重放与重连。

[素材测试](../tests/test_estate_character_assets.py) 用标准库直接解码 PNG，检查 RGBA/sRGB、颜色数量、Alpha、尺寸、脚底、留白、透明间隔和空格；[浏览器测试](../tests/test_estate_wardrobe.cjs) 检查动画取帧、方向镜像、试穿不保存、确认后换装、旧结果不回退、焦点、地图输入阻断、刷新恢复、五种尺寸布局、素材重试、减少动态效果和卸载重进。

```bash
PYTHONPATH=tests python -B -m unittest test_estate_skins test_estate_character_assets test_estate_frontend -v
# 需已安装 Playwright 与 Chrome；无须启动替代 Web 服务器。
NODE_PATH=/path/to/node_modules CHROME_PATH=/path/to/chrome node tests/test_estate_wardrobe.cjs
```

截图输出可用 `ESTATE_SCREENSHOT_DIR` 指定。浏览器测试从本地文件路由提供测试页、模拟网络消息；真实协议/存档由独立 WebSocket 测试验证，而不是把模拟消息当成实际持久化测试。
