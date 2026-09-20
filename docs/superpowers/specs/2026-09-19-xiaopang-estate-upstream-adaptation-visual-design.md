# 休闲庄园：上游架构适配与素材化美术升级设计

## 背景

上游 `main` 在 `89e6973` 将游戏厅拆成 `game-config.js`、`create-room.js`、
`room-chat.js`、`room-settlement.js`、`room-waiting.js` 等模块。休闲庄园分支仍把
游戏元数据与建房逻辑放在旧版 `hall.js`，继续叠加会覆盖作者的新结构。

本轮先迁移接口，再把庄园地图从纯 Canvas 色块绘制升级为“授权像素素材 + 原创
Canvas 特效”的混合渲染。经济数值不在本轮调整。

## 架构适配

1. 以最新 `origin/main` 为新基线重放庄园提交，不回退上游房间模块。
2. 在 `game-config.js` 注册休闲庄园，元数据增加 `mode: "solo"` 与
   `view: "estate"`。同时导出房间游戏子集，建房和房间导航只消费房间游戏。
3. `hall.js` 只负责导航：多人游戏进入 `rooms`，单人游戏直接进入其 `view`。
4. `core.renderGameView()` 对已注册的非房间页面做通用分发，不再硬编码庄园。
5. `main.js` 继续以副作用导入 `estate/view.js`；庄园协议仍复用统一 WebSocket 和
   登录态，不侵入拆分后的房间聊天与结算模块。
6. 后端庄园领域继续保留在独立 `estate/` 包，`chat_server.py` 只做消息适配。

## 美术方案

采用 Kenney Tiny Farm 与 Tiny Town 的 16×16 像素素材作为统一底座。两套素材均
明确标注为 CC0，适合随仓库分发。Tiny Farm 提供农田、农舍、农作物与农场装饰；
Tiny Town 补足道路、地形、树木与建筑变化。

地图保持 960×600 的逻辑坐标和现有碰撞/互动区域。新增素材加载器，负责预载、
按整数倍无平滑绘制以及加载失败时回退到现有原创 Canvas 图形。庄园名称、成熟
闪光、水面波纹、钓鱼线、矿洞发光和主角细节继续由本项目绘制，以避免地图
沦为素材拼贴。

素材保存于 `assets/estate/kenney/`，只纳入实际使用的 PNG 与许可证说明；
`assets/estate/ATTRIBUTION.md` 记录来源、链接、许可证和选用文件。

## 视觉目标

- 建筑、道路、田地、水岸、树木与装饰统一到 16×16 网格。
- 主角拥有四方向行走帧；交互地点采用中性命名。
- 不降低移动端按钮可读性，地图素材在高 DPR 屏幕仍保持像素锐利。
- 素材未加载或浏览器不支持时，仍可用原 Canvas 图形完成全部玩法。

## 验证

- 新增元数据契约测试，确保单人游戏不会进入建房/房间列表。
- 保留庄园目录、经济事务、钓鱼、矿场、WebSocket 与前端模块测试。
- 运行上游新增的大厅布局、下注关闭和房间模块测试。
- 在本地 HTML 页面验证桌面与手机视口、主角移动、所有地点互动及控制台错误。

## 素材来源

- Kenney Tiny Farm：16×16，CC0，https://kenney.nl/assets/tiny-farm
- Kenney Tiny Town：16×16，CC0，https://kenney.nl/assets/tiny-town
