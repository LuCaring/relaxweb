# 德扑重制版（桌面）

这是一套独立的德州扑克前端视图。房间仍由服务端 `holdem` 规则引擎管理：房间、盲注、买入、动作、边池、手牌结算、整局结算和钱包都与经典版共用。大厅中的“德扑重制版”只选择客户端画面；选回“德州扑克 · 无限注”即可使用经典视图。同一 `holdem` 房间可以从两个入口加入。

## 构建

在仓库根目录运行：

```sh
npm install
npm run build:holdem-remastered
```

`types.ts` 描述服务端视图与动作协议；`layout.ts` 统一座位、牌、荷官空位及筹码动线坐标；`scene.ts` 通过 Phaser 4 编排公共牌发牌与原位翻转、下注入池和多人领奖动效；`table-svg.ts` 绘制内联 SVG 牌桌及五档筹码堆；`deck.ts` 保存 Adrian Kennard 的 CC0 传统牌；`card-svg.ts` 为每张 Phaser DOMElement 卡牌建立独立 SVG 引用；`table.ts` 负责底部横向操作台（左侧手牌、右侧动作与状态）、倒计时及 Phaser 生命周期。玩家按服务端进房顺序固定落座，从荷官空位左侧沿左边、下方、右边排到荷官右侧，整体左右对称；所有玩家和观众看到相同座位，当前玩家（或观战目标）的两张私牌在操作区左侧独立显示并标明归属。五个公共牌槽位在同一手内保持原位，翻牌、转牌、河牌依次收窄至中点换面后展开；重连和缩放直接显示当前牌面。牌桌直接作为页面主体，手数、阶段和盲注显示在底部操作台；场景上方显示本手历史入池额，桌内筹码堆按底池占总筹码比例显示五档；飞入金额使用服务端 `hand_bet` 增量，领奖使用 `result.payouts`。Canvas 保持透明，所有可见牌桌、卡牌与筹码都由 SVG 或 DOM 绘制。构建先执行 TypeScript 检查，再由 esbuild 打包到 `assets/build/holdem-remastered.js`。网页进入重制版牌桌时才加载这个 bundle，经典版无需下载它。部署静态资源时需包含 `assets/build/`。牌面来源及离线许可文本见 `DECK_LICENSE.md` 和 `CC0-1.0.txt`。

## 不接真实账号的预览

```sh
python scripts/preview_ui.py --game holdem --no-open
```

打开输出的本地地址，在“德扑画面”选择“重制版”；“演示一手”会播放本地模拟的发牌、下注、翻牌、转牌、河牌和结果，不发送到真实服务器或钱包。也可以直接打开 `/game.html?game=holdem&holdemView=remastered`（须由此预览脚本提供）。重制版针对桌面浏览器设计。
