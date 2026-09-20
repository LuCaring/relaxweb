# 休闲庄园第一阶段实施计划

日期：2026-09-19  
依据：`docs/superpowers/specs/2026-09-19-pixel-estate-phase1-design.md`  
范围：庄园基础、可移动地图、农场、商店、仓库、等级、固定价出售、桌面与手机适配  
不含：可玩的钓鱼、可玩的挖矿、真实货币、动态市场、加工、好友访问

## 执行原则

1. 严格按任务顺序实施，每个任务完成测试后单独提交。
2. 先写失败测试，再写最小实现，再跑相关测试和回归测试。
3. 不修改现有四种多人游戏的房间协议、托管或段位语义。
4. 所有经济写操作在服务端 SQLite 事务内完成；客户端不得计算最终金币或奖励。
5. 所有数值集中在 `estate/catalog.py`。首轮可使用明确标注的试运行数值，但在 PR 前必须完成人工数值审阅，不能散落在 HTML、CSS 或 JavaScript 中。
6. `.superpowers/`、素材包宣传图和临时下载文件不得进入提交。
7. 每次提交前运行 `git diff --check`，确认只包含本任务文件。

## 任务 1：建立庄园包、目录和数据库迁移

### 文件

- 新建 `estate/__init__.py`
- 新建 `estate/catalog.py`
- 新建 `estate/schema.py`
- 新建 `tests/test_estate_schema.py`
- 修改 `chat_server.py`

### 测试先行

在 `tests/test_estate_schema.py` 使用内存 SQLite 或临时数据库，覆盖：

- `init_estate(conn)` 创建 `estate_profiles`、`estate_plots`、`estate_inventory`、`estate_actions`。
- 重复调用迁移不会报错或破坏数据。
- 关键主键和唯一约束存在。
- 物品数量、仓库容量、等级、经验、地块编号和土地等级不能写入负数或零以下非法值。
- 同一用户不能出现重复地块或重复库存物品。

先运行：

```powershell
python tests/test_estate_schema.py
```

确认失败后实现：

- `estate/schema.py` 暴露 `init_estate(conn)`。
- `chat_server.init_db()` 在现有奖励初始化之后调用 `init_estate(conn)`。
- 外键继续沿用项目当前 SQLite 策略，不擅自开启会破坏旧数据的全局行为。
- `estate/catalog.py` 定义稳定 ID 和只读目录结构，至少包含测试用的基础作物、土地等级、地块解锁与仓库档位。
- 初始可用地块数量设为独立常量，首版建议四块；所有测试从目录读取，不在断言中复制业务价格。

验证：

```powershell
python tests/test_estate_schema.py
python tests/test_rewards.py
python tests/test_games.py
```

提交：

```powershell
git add estate tests/test_estate_schema.py chat_server.py
git commit -m "feat: add estate schema and catalog"
```

## 任务 2：实现首次建档与权威快照

### 文件

- 新建 `estate/service.py`
- 新建 `tests/test_estate_service.py`
- 修改 `estate/__init__.py`

### 服务接口

保持领域模块不依赖 WebSocket：

```python
ensure_estate(conn, username, now)
estate_state(conn, username, now)
```

`estate_state` 返回可 JSON 序列化对象，至少包含：

- `version`
- `server_time`
- `profile`: 等级、经验、下一级阈值、仓库容量与用量
- `plots`: 地块编号、锁定状态、土地等级、作物、成熟时间、剩余秒数、是否成熟
- `inventory`: 物品 ID 与数量
- `catalog`: 客户端展示需要的公开价格、时间、等级条件和名称

### 测试先行

覆盖：

- 首次读取惰性创建一份资料和正确数量的初始土地。
- 再次读取不会重复创建或重置数据。
- 不同用户互相隔离。
- 成熟状态只由注入的 `now` 和数据库 `ready_at` 推导。
- 服务器时间倒退时剩余时间不会出现负数之外的异常值。
- 仓库用量等于全部正库存数量之和。
- 快照版本随写操作增加，纯读取不增加。

运行并实现最小代码：

```powershell
python tests/test_estate_service.py
python tests/test_estate_schema.py
```

提交：

```powershell
git add estate tests/test_estate_service.py
git commit -m "feat: initialize estate profiles and snapshots"
```

## 任务 3：实现幂等动作基础设施

### 文件

- 修改 `estate/service.py`
- 修改 `tests/test_estate_service.py`

### 设计要求

- 请求 ID 使用非空、长度受限的字符串。
- 对动作类型和经过规范化的业务参数生成请求摘要。
- 相同用户、相同请求 ID、相同摘要：返回已保存动作结果，`replayed=true`。
- 相同用户、相同请求 ID、不同摘要：抛出稳定错误码 `request_conflict`。
- 动作记录和业务写入必须在同一事务中提交。
- 重放时返回保存的动作结果，再现场生成当前最新庄园快照。

### 测试先行

覆盖正常执行、同连接重放、断线后重放、双连接并发使用相同请求 ID、内容冲突和业务异常回滚。断言动作失败时不留下“成功”记录。

验证：

```powershell
python tests/test_estate_service.py
```

提交：

```powershell
git add estate/service.py tests/test_estate_service.py
git commit -m "feat: make estate actions idempotent"
```

## 任务 4：实现购买、播种、收获和出售事务

### 文件

- 修改 `estate/service.py`
- 修改 `estate/catalog.py`
- 修改 `tests/test_estate_service.py`

### 服务接口

采用与 `rewards.draw_lottery` 相同的依赖注入模式，避免 `estate` 反向导入 `chat_server`：

```python
buy(conn, username, request_id, item_kind, item_id, quantity, now, adjust_coins)
plant(conn, username, request_id, plot_id, crop_id, now)
harvest(conn, username, request_id, plot_id, now)
sell(conn, username, request_id, item_id, quantity, now, adjust_coins)
sell_all(conn, username, request_id, now, adjust_coins)
```

### 购买

- 种子：检查解锁、余额、数量和仓库容量；扣金币后增加库存。
- 地块：检查前置地块、等级、余额和未购买状态；扣金币后解锁。
- 土地升级：只允许空地升级；检查等级、余额和下一档存在。
- 仓库扩容：检查当前档位、等级、余额和下一档存在。
- 金币流水使用稳定 `kind`，例如 `estate_purchase`；`detail` 包含中文项目名；`ref` 含请求 ID。

### 播种

- 地块必须已解锁、为空且种子数量足够。
- 在事务内扣一粒种子。
- `ready_at = now + ceil(base_seconds * land_multiplier)`。
- 不允许客户端提交价格、经验、成熟时间或产量。

### 收获

- 使用服务器 `now` 判断成熟。
- 仓库剩余容量必须容纳完整固定产量；否则不改变状态。
- 清空地块、增加产品库存、增加经验和处理连续升级在同一事务完成。
- 第一阶段不允许提前铲除作物，避免增加尚未设计的退款语义。

### 出售

- 只允许出售目录中 `sellable=true` 的产品。
- 单项出售严格检查数量。
- 一键出售只出售可出售产品，不出售种子。
- 减库存、加金币和写 `estate_sale` 流水在同一事务完成。

### 测试先行

每类动作覆盖成功路径和所有稳定错误码：余额不足、等级不足、仓库满、地块锁定、土地非空、种子不足、作物未成熟、库存不足、无下一档升级。额外覆盖：

- 土地加速倍率只影响播种时计算的新作物。
- 连续升级正确处理剩余经验。
- 一键出售不出售种子。
- 事务中注入异常后金币、流水、库存、地块和经验全部回滚。
- 多标签页同时收获只有一次成功。

验证：

```powershell
python tests/test_estate_service.py
python tests/test_rewards.py
python tests/test_ratings.py
```

提交：

```powershell
git add estate tests/test_estate_service.py
git commit -m "feat: add estate farming transactions"
```

## 任务 5：接入 WebSocket 协议

### 文件

- 修改 `chat_server.py`
- 新建 `tests/test_estate_protocol.py`

### 消息

新增客户端消息：

- `get_estate`
- `estate_buy`
- `estate_plant`
- `estate_harvest`
- `estate_sell`
- `estate_sell_all`

新增服务端消息：

- `estate_state`: `request_id` 可为空，包含 `replayed`、动作结果和完整快照。
- `estate_error`: 包含稳定 `code`、中文 `message`、原 `request_id`，状态冲突时附最新快照。

### 实现要求

- 每个 handler 首先验证登录。
- 读取可以使用短限流；写动作不得通过静默丢弃实现限流，必须返回明确错误。
- handler 打开数据库事务，将现有 `adjust_coins` 作为依赖传给服务层。
- 成功后同步当前连接的 `state["user"]["coins"]`。
- 同账号其他已连接标签页收到最新 `estate_state`，避免长期显示旧库存或余额。
- 把新 handler 加入 `handlers` 字典。

### 协议测试

`tests/test_estate_protocol.py` 仿照 `tests/test_rewards_protocol.py`，使用临时数据库和随机本地端口，覆盖：

1. 未登录被拒绝。
2. 两个连接恢复同一账号。
3. 首次读取创建庄园。
4. 完成购买、播种、推进注入时钟、收获和出售。
5. 两连接并发收获只成功一次。
6. 相同请求 ID 重试得到 `replayed=true`。
7. 查询财务流水包含购买和出售。
8. 服务重新初始化数据库结构后存档仍存在。

验证：

```powershell
python tests/test_estate_protocol.py
python tests/test_rewards_protocol.py
python tests/test_games.py
```

提交：

```powershell
git add chat_server.py tests/test_estate_protocol.py
git commit -m "feat: expose estate websocket protocol"
```

## 任务 6：增加大厅入口与前端状态模块

### 文件

- 修改 `assets/js/core.js`
- 修改 `assets/js/hall.js`
- 修改 `assets/js/main.js`
- 新建 `assets/js/estate/state.js`
- 新建 `assets/js/estate/protocol.js`
- 修改 `tests/test_frontend.py`
- 修改或新增前端模块测试文件

### 先写测试

静态测试应验证：

- `main.js` 能到达全部 `estate/` 模块。
- 游戏厅包含名称“休闲庄园”，并标记为单人直接入口。
- 点击庄园卡片不会进入房间列表或发送 `create_room`。
- 未登录不会请求庄园。
- 退出登录会清空庄园状态和待处理请求。
- 服务端快照更新 `state.currentUser.coins` 和顶部金币显示。

### 实现

- 在游戏元数据中增加 `mode: "solo"`，不要用 `id === "estate"` 到处硬编码分支。
- 点击单人卡片后设定 `hallPage = "estate"`，发送 `get_estate` 并渲染 `estate` 视图。
- `core.renderGameView()` 在房间判断之后、普通房间页面之前处理庄园视图。
- `estate/state.js` 保存快照、服务端时差、当前选中地块、待处理请求和客户端视图状态。
- `estate/protocol.js` 注册 `estate_state` 与 `estate_error`；错误使用现有自绘弹层。
- WebSocket 重连并恢复登录后，如果仍停留庄园页面，重新发送 `get_estate`。

验证：

```powershell
python tests/test_frontend.py
```

提交：

```powershell
git add assets/js tests/test_frontend.py
git commit -m "feat: add 休闲庄园 hall entry"
```

## 任务 7：选定并登记正式像素素材

### 文件

- 新建 `assets/estate/ATTRIBUTION.md`
- 新建 `assets/estate/tiles/` 下经过审核的图集
- 新建 `assets/estate/sprites/` 下经过审核的角色与交互物图集
- 新建 `assets/estate/ui/` 下经过审核的物品和 HUD 图集

### 许可检查

对每个素材包逐项确认：

- 原始来源 URL 与作者。
- 明确许可证文本。
- 是否允许修改。
- 是否允许随公开 Git 仓库再分发原始或裁剪后的文件。
- 是否要求署名、同许可或游戏内声明。

宣传图、商店截图和禁止再分发的原始包不得提交。优先选择 CC0。若素材许可不允许公开仓库再分发，则只保留为视觉参考，改用可再分发素材或自行重绘。

### 美术验收

- 以“精致平衡”为主，环境细节接近“丰富华丽”。
- 土地、商店、仓库、矿洞和湖边在手机缩放后仍能一眼辨认。
- 所有图集采用一致像素密度、透视和有限调色板。
- 不包含任何商业游戏的提取素材或近似临摹资产。

测试：扩展 `tests/test_frontend.py`，验证 HTML 引用资源存在并带版本号；增加一个小脚本验证 `ATTRIBUTION.md` 列出的本地文件都存在。

提交：

```powershell
git add assets/estate tests
git commit -m "assets: add licensed estate pixel art"
```

## 任务 8：实现 Canvas 地图、角色移动和双端输入

### 文件

- 新建 `assets/js/estate/map.js`
- 新建 `assets/js/estate/input.js`
- 新建 `assets/js/estate/view.js`
- 新建 `assets/css/estate.css`
- 修改 `assets/js/main.js`
- 修改 `game.html`
- 新建 `tests/test_estate_ui.cjs`

### 地图实现

- Canvas 只绘制地图、角色、交互物高亮和短暂世界提示。
- DOM 绘制 HUD、返回按钮、快捷栏、虚拟摇杆、操作按钮和弹层。
- 使用 16×16 基础瓦片的整数倍缩放；关闭图像平滑。
- 固定时间步长更新，限制单帧最大补偿，避免标签页恢复后角色瞬移。
- 轴向分离的 AABB 碰撞；地图数据显式定义阻挡矩形与交互区域。
- 镜头跟随角色并夹在地图边界内。
- 接近目标时选择最近且朝向合理的交互区域；高亮并显示操作提示。

### 输入实现

- `input.js` 只输出标准化移动向量、交互按下、交互按住和交互松开。
- 键盘支持 WASD、方向键、E 和空格。
- 手机使用 Pointer Events 实现虚拟摇杆和操作按钮，处理 `pointercancel`、多点触控和窗口失焦。
- 弹层打开、页面隐藏、输入框聚焦或视图销毁时立即清空输入。
- 设置必要的 `touch-action: none`，但不阻止弹层内部正常滚动。

### Playwright 测试

在不依赖真实素材像素位置的前提下验证：

- 键盘移动改变角色坐标。
- 碰撞阻止穿过建筑和水面。
- 进入土地范围显示操作提示。
- 打开弹层后角色停止。
- 手机虚拟摇杆产生移动并在松开后归零。
- 操作按钮触发当前目标。
- 视图离开后取消动画帧和事件监听，不产生重复循环。

验证：

```powershell
python deploy/serve.py
node tests/test_estate_ui.cjs
python tests/test_frontend.py
```

提交：

```powershell
git add assets/js/estate assets/css/estate.css game.html tests/test_estate_ui.cjs tests/test_frontend.py
git commit -m "feat: add movable estate map"
```

## 任务 9：实现商店、土地和仓库界面

### 文件

- 新建 `assets/js/estate/ui.js`
- 修改 `assets/js/estate/view.js`
- 修改 `assets/js/estate/protocol.js`
- 修改 `assets/css/estate.css`
- 扩展 `tests/test_estate_ui.cjs`

### 界面

- HUD：金币、仓库用量、庄园等级、经验进度。
- 商店：按等级显示种子、地块、土地升级和仓库扩容；锁定项显示条件。
- 土地：空地选择种子；成长中显示服务器校准倒计时；成熟显示收获按钮；锁定地块显示购买条件。
- 仓库：分类、数量、单项出售、数量输入和明确的一键出售确认。
- 所有写操作按钮提交后进入 pending 状态，直到匹配请求 ID 的结果返回。
- 断线或超时不生成新请求 ID；用户重试沿用原 ID，直到服务端确认或用户主动取消未发送动作。

### 测试

Playwright 使用可控的 WebSocket mock 或本地真实服务器，覆盖：

- 商店购买后金币和库存使用服务端返回值更新。
- 重复点击不会生成多个请求 ID。
- 土地倒计时使用服务端时间偏移，刷新后保持一致。
- 仓库满时显示错误且成熟作物仍在地块。
- 一键出售确认文案明确说明不出售种子。
- 服务器返回新快照时当前打开弹层无崩溃并刷新内容。

提交：

```powershell
git add assets/js/estate assets/css/estate.css tests/test_estate_ui.cjs
git commit -m "feat: add estate farming interface"
```

## 任务 10：完成桌面、手机与恢复场景回归

### 文件

- 扩展 `tests/test_estate_ui.cjs`
- 新建 `tests/test_estate_mobile.cjs`
- 扩展 `tests/test_estate_protocol.py`
- 必要时修改庄园前后端实现

### 自动化场景

桌面：

- 登录后从大厅进入休闲庄园。
- WASD 移动到商店，购买种子。
- 移动到土地，播种。
- 使用测试时钟推进到成熟，收获并出售。
- 刷新页面和 WebSocket 重连后状态保持。

手机：

- 常见窄屏竖屏和横屏视口。
- 虚拟摇杆、操作按钮、多点触控取消。
- HUD 不遮挡返回按钮、土地提示和交互按钮。
- 浏览器安全区域和地址栏高度变化不会让关键按钮不可达。
- 仓库长列表可以滚动，地图本身不跟随页面滚动。

恢复与并发：

- 请求提交后立即断线，再用原请求 ID 恢复。
- 两个标签页同时进入庄园并操作同一土地。
- 服务重启后成熟时间、余额、库存和动作幂等记录保持。

验证：

```powershell
python tests/test_estate_service.py
python tests/test_estate_protocol.py
node tests/test_estate_ui.cjs
node tests/test_estate_mobile.cjs
```

提交：

```powershell
git add tests estate assets/js/estate assets/css/estate.css chat_server.py
git commit -m "test: cover estate desktop and mobile flows"
```

## 任务 11：全量回归、人工 HTML 验收和 PR 准备

### 自动化回归

至少运行：

```powershell
python tests/test_games.py
python tests/test_frontend.py
python tests/test_ratings.py
python tests/test_rewards.py
python tests/test_rewards_protocol.py
python tests/test_guandan.py
python tests/test_mahjong.py
python tests/test_uno_challenge.py
python tests/test_estate_schema.py
python tests/test_estate_service.py
python tests/test_estate_protocol.py
```

在 Playwright 环境运行现有浏览器测试以及：

```powershell
node tests/test_estate_ui.cjs
node tests/test_estate_mobile.cjs
```

### 人工 HTML 验收

启动真实后端和静态服务：

```powershell
python chat_server.py
python deploy/serve.py
```

桌面人工检查：大厅入口、角色移动、碰撞、地图精细度、商店、播种、倒计时、收获、出售、刷新和重连。

手机人工检查：竖屏、横屏、虚拟摇杆、操作按钮、弹层滚动、HUD、安全区域、长按和触摸取消。

经济人工检查：每类流水的金额、余额、中文明细和 `ref`；确认所有购买、出售与仓库变化一致。

资产人工检查：逐项核对 `assets/estate/ATTRIBUTION.md` 与提交文件，确认允许公开仓库再分发。

### 文档

- 更新 `README.md` 的功能、目录、启动和测试说明。
- 在第一阶段说明矿洞和湖边为后续开放，不宣称已经可玩。
- 记录试运行数值和调整入口。

最终检查：

```powershell
git diff --check origin/main...HEAD
git status --short
```

PR 创建前必须由用户在 HTML 页面明确确认桌面与手机体验。未确认时保持本地分支或草稿状态，不提交正式 PR。

最终提交：

```powershell
git add README.md
git commit -m "docs: document 休闲庄园 farming phase"
```

## 实施完成判定

第一阶段只有同时满足以下条件才算完成：

- 休闲庄园是登录用户可直接进入的永久单人游戏，不经过房间系统。
- 角色可在桌面和手机地图上稳定移动并与目标交互。
- 购买、播种、离线成熟、收获、出售、土地升级、仓库扩容和等级解锁均由服务端权威处理。
- 重复请求、并发标签页、断线重试和服务重启不会造成重复奖励、重复扣款或状态丢失。
- 所有外部素材允许随当前公开仓库分发并有完整记录。
- 新增测试和现有回归全部通过。
- 用户已在 PR 前完成桌面与手机 HTML 人工验收。
