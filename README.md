# RelaxWeb · 直播间 + 游戏厅

一个自托管的直播间：WebRTC 看直播、实时聊天与弹幕、金币系统（转账 / 竞猜 / 游戏厅），
以及内置的两个多人小游戏——德州扑克与 UNO。后端 Python 3 标准库 + `websockets`，
前端原生 ES Module，**没有构建步骤**，改完文件刷新页面即生效。

## 功能

- **直播**：MediaMTX 推流，页面用 WebRTC（WHEP）拉流；播放器支持音量、静音、全屏、弹幕开关
- **账号**：邀请码注册、会话 token 自动续期、资料编辑（昵称/头像）、注销账号
- **聊天**：聊天室 + 弹幕（含流主/管理员标识）、在线列表、历史消息
- **金币**：注册奖励、转账（带拼音排序的收款人选择）、金币流水明细
- **签到抽奖**：每日签到领 5 次免费机会，可一直累积；每抽必得 20–500 金币，
  超过 100 金币的概率为 12%，直播间与游戏厅的账号菜单均可进入
- **竞猜**：主播/管理员出题，观众下注，开奖后按注额分配
- **游戏厅**：德州扑克（盲注轮转、边池、全下跑马、一手结束后结算投票）与 UNO（剩牌赔付），
  房间制、金币买入、离桌自动结算，手机横屏有专门布局
- **段位**：所有账号从 1000 分起步，所有游戏共用段位；按每局收益率计分，
  加分/扣分系数为 2:1，牌桌展示段位，结算展示明细，大厅可查最近 20 局
- **小胖庄园**：单人像素经营，24 种作物、24 种鱼与 4 种稀有收藏物；
  离线种植、土地升级、张力钓鱼、分层采矿与炸弹风险，接入现有金币和仓库。
  支持键鼠、左 Shift 疾跑与手机摇杆；[经济规则与验证](docs/estate-economy.md)。

## 每日签到与抽奖

点击账号菜单中的「每日奖励 / 抽奖 → 签到抽奖」。按**北京时间（UTC+8）每天 00:00**划分签到日，
每个账号每天手动签到一次，领取 **5 次**抽奖机会。不补签过去日期，未使用机会不会清零或过期，
可以跨天叠加；每抽消耗 1 次，必得整数金币：

| 金币区间（包含端点） | 概率 |
| --- | --- |
| 20–100 | 88% |
| 101–200 | 10% |
| 201–500 | 2% |

先抽取区间，再在区间内等概率选取整数，超过 100 金币合计 **12%**。
中奖金币立即加入账户余额，财务明细记为「签到抽奖」；抽奖面板展示最近 10 次中奖记录。
抽奖不消耗金币，不影响段位。

服务端使用 `secrets` 生成结果，忽略客户端提交的日期、奖励金额与次数。
启动时自动添加持久化表与机会余额，已有账号初始机会为 0，可在上线当天签到领取；
再次启动保留累计机会和签到记录，无需定时任务。
签到以「用户 ID + 北京日期」去重；抽奖以「用户 ID + 请求 ID」去重，
扣机会、发金币和记录流水在同一写事务完成，避免多标签页重复领取或超额抽奖。
浏览器在当前标签页的会话存储中保留未确认的抽奖请求 ID，断线、刷新后用同一 ID 恢复结果。
关闭标签页后可在最近中奖记录及财务明细查看已到账奖励。

## 每日德扑流水奖励

直播间和游戏厅的账号菜单「每日奖励 / 抽奖 → 德扑流水」展示当天累计下注流水和四档领取按钮。

| 当天德扑下注流水达到 | 可领取金币 |
| --- | --- |
| 100 | 20 |
| 200 | 40 |
| 500 | 100 |
| 1000 | 200 |

每档每天可手动领取一次，档位独立、可叠加，达到 1000 后共可领取 **360 金币**。
北京时间每天 00:00 重置进度与领取状态，未领奖励不结转；跨日牌局按**结算日**累计。
正常结束的德扑牌局累计玩家实际投入的盲注、跟注、加注、全下金额（含弃牌或提前离桌者在该手的投入），
不按盈利或输赢计算。买入、尚未结算、流局、重开、重启退款、UNO、竞猜、转账和奖励本身不累计。

上线后从 0 开始统计，不回填历史。下注流水随段位/筹码结算同一事务持久化，按「手牌 ID + 用户 ID」去重；
领取按「用户 ID + 北京日期 + 档位」去重，领取记录、金币余额和财务明细同事务提交。
重复请求、多个标签页并发或重连不会重复发奖；奖励直接进入账户金币，财务明细显示「每日德扑流水奖励」，不影响段位。
结算或领取后自动同步该账号各页面的进度，重新打开面板也会刷新；每日重置无需定时任务。

## 段位计分

以**每一局牌**为单位：开局扣盲注之前的筹码为本金 `B`，结算后筹码为 `F`，
收益率 `r = (F - B) / B`。再来一局用新的开局筹码，不重复使用最初进房买入金额。

```text
r >= 0：Δ = round_half_up(min(40, 40 × r))
r <  0：Δ = round_half_up(max(-20, 20 × r))
新分数 = max(0, 旧分数 + Δ)
```

使用十进制四舍五入（负数恰逢半分也向远离零的方向取整）。系数是 2:1，
让相同幅度的盈利更容易积累分数；整数取整后，极小收益的实际加扣分未必严格为 2:1。
保本为 0 分；不足半分也记为 0，不设“赢一点至少加 1”的额外奖励。
单局上限 +40、下限 −20，防止一次大底池跨越多个段位；总分最低 0，明细显示实际扣分。

| 开局筹码 | 结算筹码 | 收益率 | 积分变化 |
| --- | --- | --- | --- |
| 100 | 120 | +20% | +8 |
| 100 | 80 | −20% | −4 |
| 100 | 200 | +100% | +40 |
| 100 | 0 | −100% | −20 |
| 1000 | 1200 | +20% | +8 |

| 段位 | 分数 |
| --- | --- |
| 青铜 | 0–799 |
| 白银 | 800–1199（初始 1000） |
| 黄金 | 1200–1599 |
| 铂金 | 1600–1999 |
| 钻石 | 2000–2399 |
| 大师 | 2400+ |

游戏厅「我的段位 → 查看段位排行」可查看全站积分榜：按段位分从高到低分页展示，每页 100 位，
可以翻页查看所有玩家。同分并列（例如第 1、1、3 名），同分玩家按用户名稳定排列，跨页保持全站名次；
自己的名次单独展示。所有账号均参与，包括初始 1000 分、尚未结算牌局的玩家。
排行榜打开时会随段位结算批量刷新，也可手动刷新；刷新和同账号重连保留当前页码。
榜单向所有登录用户公开用户名、昵称、段位分、已结算局数及下述德扑累计指标，
不公开金币余额、底牌或其他玩家的逐局明细。
六个段位分别使用铜盾、银章、金星、铂金翼章、蓝色钻石、紫色王冠标志；排行页可查看全部标志与分数门槛，
大厅、房间座位、结算和两个页面的账号菜单使用相同标志，并保留段位文字与分数。

这是偏成长的收益积分：相同收益率不因注额大小改变得分，没有额外胜场奖励或对手分差修正，
也不是零和的实力估计。参数设计参考 [Elo 的 K 系数控制单场影响](https://www.chess.com/terms/elo-rating-chess)
这一思路，收益率公式与 2:1 比例是本项目自己的规则，并非 Elo/Glicko 算法。

- 正常结算立即计分；离桌、解散、重复请求不会再次结算同一手同一玩家。
- 中途离桌（含断线宽限期到期）按实际退回的筹码立即结算。德州已投入筹码仍留在底池；
  UNO 沿用原额退出规则，未发生筹码损益时记 0 分，不额外处罚。
- 尚未开局、进行中的流局或重开、服务重启退款不产生积分；此前已完成/已离桌的积分保留。
- 金币转账、竞猜及管理员调币不影响积分。服务端读取真实筹码，客户端不能提交积分。
- 启动自动给已有账号添加初始分，不回填历史牌局；新账号使用同样的初始分。
  `rating_history` 持久化完整审计流水，以随机手牌 ID + 用户 ID 去重，避免房间编号重用碰撞；
  正常结算的积分与筹码托管在同一数据库事务写入。大厅仅展示本人的最近 20 条记录。

## 德扑公开统计

排行榜默认展示德扑手数、盈利胜率、弃牌率、得分期望与 BB/100，
「更多德扑指标与口径」可展开净收益、VPIP/PFR、看翻牌率、摊牌指标与 AF，每项同时展示分子/分母。
**统计从功能启用时开始**：启用时间持久化并显示在排行页，不回填缺少完整行动数据的旧牌局；
已有段位分、共享计分公式和历史流水不变。其他游戏影响共享段位，但不计入德扑指标的样本。

设 `H` 为有效已结算德扑手数（含提前离桌结算），`Nᵢ` 为第 i 手结算筹码减去扣盲注前筹码，
`Δᵢ` 为实际应用的段位分变化（含总分最低 0 的裁剪），`BBᵢ` 为该手的大盲金额：

| 指标 | 计算口径 |
| --- | --- |
| 德扑手数 | `H`，不是所有游戏共用的已结算局数 |
| 盈利胜率 | `Nᵢ > 0` 的手数 / `H`；平分保本不算赢，拿到部分底池但净亏也不算赢 |
| 弃牌率 | 弃牌手数 / `H`；分开记录主动、超时、离桌弃牌，已弃牌再离桌不重复归因 |
| 得分期望 | `ΣΔᵢ / H`，单位分/手，是实际历史均值，**不是理论 EV 或 All-in EV** |
| 净收益 / 手均净收益 | `ΣNᵢ` / `ΣNᵢ ÷ H`，单位筹码 / 筹码每手 |
| BB/100 | `100 × Σ(Nᵢ / BBᵢ) / H`，各手单独用对应大盲归一化，不混算不同盲注级别 |
| VPIP（主动入池率） | 翻牌前主动投入筹码的手数 / `H`；强制盲注不算，小盲补齐和主动全下跟注算 |
| PFR（翻前加注率） | 翻牌前实际加注手数 / `H`；每手最多计一次，短码全下跟注不算加注 |
| 看翻牌率 | 发出翻牌时仍未弃牌的手数 / `H`，包括已经全下的玩家 |
| WTSD（摊牌率） | 看到翻牌且实际参加摊牌的手数 / 看到翻牌手数；对手全弃获胜不是摊牌 |
| 摊牌盈利率 | 实际摊牌且 `Nᵢ > 0` 的手数 / 实际摊牌手数 |
| AF（翻牌后激进因子） | 翻牌后下注、加注**次数** / 翻牌后跟注次数；不计翻牌前行为、过牌和弃牌 |

分母为 0 时 API 返回 `null`，页面显示 `—`；有翻牌后进攻、没有跟注时额外返回 `af_no_calls=true`，
页面显示 `∞（无跟注）`，不会传输非标准 JSON 的 Infinity。0 手显示暂无数据，1–99 手提示小样本。
无效操作、未完成的流局/重开、服务重启退款均不计手数；此前已经离桌结算的摘要保留。
实时胜率（equity）、All-in EV、3-bet、c-bet 和周/月榜不在此版本范围。

### 存储与接口

- [统计存储模块](holdem_stats.py) 自动创建 `holdem_hand_stats`（逐手摘要）、`holdem_player_stats`（累计汇总）
  和 `holdem_stats_metadata`（一次性启用时间）；金额以整数分保存，不保存底牌或完整牌堆。
- 逐手摘要以 `(hand_id, user_id)` 唯一去重；段位、历史流水、统计摘要、累计值、每日下注流水和结算筹码托管在同一事务提交。
  每日下注流水仅归属本手结算记录对应的用户 ID，提前离桌后注销再同名注册不会继承旧手流水。
  新摘要才累加汇总，失败全部回滚。结算写入失败时冻结已完成的动作，每 2 秒重试原结算；
  重发动作不改变结果，离桌/重开/解散前必须完成待结算手，避免重复退款。暂停会暂停重试，恢复后继续。
- 账号注销和管理员删除均清除摘要与汇总。网页注销需先离开或解散房间，且会使其他已登录连接退出，
  防止同名新账号继承旧牌局；管理员在使用 CLI 删除/改名之前也应先处理活跃房间，不能直接改运行中的参局身份。
- 运维需要重建累计值时，可在备份数据库并持有 SQLite 写事务后调用
  `holdem_stats.rebuild_holdem_stats(conn)`，只从新摘要重建，不改段位或旧流水。
- 登录后发送 `{"type":"get_rating_leaderboard","offset":100,"request_id":"rating-2"}`：
  固定每页 100 位；省略 offset 为第一页；非整数重置为 0，负数截为 0，非整页向下对齐，越界截到末页。
  返回 `offset`、`limit`、`total`、`stats_since`、回显 `request_id`、`entries`、`self` 和 `tiers`。
  `entries` / `self` 的 `holdem_stats` 包含上述累计分子、分母和派生值；概率均为 0–1。
  本人由登录态确定，不接受客户端上传指标或指定另一人的私人数据。
- 榜单先计算全站并列名次，再分页，在同一读事务中批量获取本页和本人累计统计；不逐人扫描历史。
  前端用请求关联 ID 拒绝旧响应，批量段位更新使用 150ms 去抖，翻页/离页/退出不被旧消息覆盖。

升级时先备份 SQLite，再部署代码并重启 WebSocket 服务（首次初始化建表，无需手工回填），
最后刷新页面加载新前端；不要仅更新前端而继续运行不回显 `request_id` 的旧服务端。

## 目录结构

```
chat_server.py        聊天/账号/金币/竞猜/游戏厅的 WebSocket 服务
auth_server.py        拉流鉴权（仅监听本机，供 MediaMTX authExternalUrl 调用）
config.py             配置加载：config.json + 环境变量覆盖
admin.py              金币管理命令行（list/set/add/sub/restore）
manage_invite.py      邀请码管理命令行（gen/list）
games/                游戏引擎（纯逻辑，可脱离网络单测）
  base.py             房间基类：成员、计时器、注册表
  holdem.py           德州扑克：牌力、边池、状态机
  uno.py              UNO：牌堆、出牌判定、一局流程
deploy/
  serve.py            静态服务：白名单 + 注入客户端配置
  systemd/            systemd 单元模板
  README.md           部署说明（配置、单元安装、反代、推流）
assets/
  css/                样式，按功能/游戏拆分（dialog.css、games/poker.css、games/uno.css …）
  js/                 游戏厅前端模块（core/registry/hall/room/dialog/games/*）
  js/dialog-global.js 把弹层挂到 window.LiveDialog，供直播间的经典脚本调用
  app.js reader.js    直播间前端（含 WebRTC 播放器与弹幕）
  transfer-select.js  转账收款人选择组件
index.html            直播间页面      game.html  游戏厅页面
tests/                引擎单元测试与前端模块静态检查
live-test/            联调与协议测试（git submodule，独立仓库）
```

## 快速开始（本地）

```bash
python3 -m pip install --user websockets

# 1) 配置
cp config.example.json config.json
$EDITOR config.json            # 至少改 stream.publish_password

# 2) 建库并生成邀请码（首次运行会自动建表）
python3 manage_invite.py gen 3

# 3) 起服务（两个终端，或直接用 systemd，见 deploy/README.md）
python3 chat_server.py         # ws://localhost:8765
python3 deploy/serve.py        # http://localhost:8000

# 4) 打开 http://localhost:8000 ，用邀请码注册账号
```

直播间需要 MediaMTX 提供推流与拉流；只玩聊天与游戏厅不需要它。

## 配置

所有部署相关参数集中在 `config.json`（**不进版本库**，模板见 `config.example.json`）：

| 配置项 | 说明 |
| --- | --- |
| `site.title` / `site.brand` / `site.game_title` | 浏览器标题与页面品牌文案 |
| `servers.chat_host` / `servers.chat_port` | 聊天与游戏服务监听地址、端口 |
| `servers.web_port` | 静态页面端口 |
| `servers.auth_host` / `servers.auth_port` | 拉流鉴权监听地址、端口 |
| `stream.whep_port` / `stream.path` | 页面拉流地址（`http://<host>:<whep_port>/<path>/whep`） |
| `stream.publish_user` / `stream.publish_password` | 推流账号与口令（鉴权服务用它校验） |
| `database.file` | SQLite 数据库路径 |
| `economy.new_user_coins` | 新用户注册赠送金币 |

环境变量优先级高于配置文件，便于临时覆盖与 CI：
`LIVE_CONFIG_FILE`、`LIVE_CHAT_HOST`、`LIVE_CHAT_PORT`、`LIVE_WEB_PORT`、
`LIVE_AUTH_HOST`、`LIVE_AUTH_PORT`、`LIVE_DB_FILE`、`NEW_USER_COINS`、
`STREAM_PUBLISH_USER`、`STREAM_PUBLISH_PASSWORD`。

页面里的 `window.LIVE_CONFIG` 由 `deploy/serve.py` 注入，只包含展示文案与端口，
**不包含推流口令**；`config.json` 本身也不对外提供（静态服务只放行 `assets/` 下的静态资源）。

## UNO 漏喊质疑

玩家出牌后剩 1 张时，有 2 秒保护期点击「UNO!」。保护期结束仍未喊，其他在局玩家可点击该座位的「质疑漏喊 +2」，成功后对方罚摸两张，不改变当前出牌顺序。不会自动罚牌；补喊与质疑按服务器先收到的有效操作裁决，重复质疑不会重复罚牌。暂停会冻结保护期。此规则针对漏喊 UNO，不是对 +4 出牌合法性的质疑。

桌面 UNO 使用围桌座位及常驻聊天室；方向箭头、出摸牌、反转、禁止与 +2/+4 动效由服务器公开事件驱动。系统开启减少动画时保留静态提示。

## 测试

```bash
python3 tests/test_games.py            # 引擎纯逻辑（不需要起服务）
python3 tests/test_frontend.py         # 前端模块静态检查（import/导出、state 前缀、禁用原生弹窗、页面资源）
python3 tests/test_mahjong.py          # 麻将牌型、吃碰杠胡与结算
python3 tests/test_guandan.py          # 掼蛋牌型、组队升级与结算
python3 tests/test_table_regressions.py # 选牌比较、抢杠、绝张、声明状态与非法下标
python3 tests/test_table_leave_protocol.py # 临时库与真实 WebSocket：非房主退出、暂停、声明窗、多页面通知和退款
python3 tests/test_ratings.py          # 共享段位公式、结算、迁移、幂等与真实协议（需 websockets）
python3 tests/test_holdem_rewards.py   # 每日德扑流水、四档领取、并发/回滚、跨日、引擎与真实协议
python3 tests/test_holdem_stats.py     # 德扑指标、行动分类、离桌/全下、原子性、分页与真实协议
node --experimental-vm-modules tests/test_rating_leaderboard_state.cjs # 无第三方依赖的分页/请求状态测试
python3 tests/test_rewards.py          # 签到日期/概率、累计机会、并发去重、扣次入账原子性
python3 tests/test_rewards_protocol.py # 临时数据库 + 真实 WebSocket 签到/抽奖联调

git submodule update --init live-test  # 协议级联调脚本
bash live-test/reset.sh                # 重置测试库、重建测试账号、重启服务
python3 live-test/proto_test.py        # 聊天/账号/房间生命周期等 16 项协议测试
python3 live-test/uno_proto_test.py    # UNO 协议测试
python3 tests/test_uno_challenge.py    # 保护期/并发/暂停/功能牌事件
python3 tests/test_uno_challenge_protocol.py # 本地真实 WebSocket 质疑联调
```

写 UI 自动化测试时注意：页面里所有提示/确认都是自绘弹层，不是原生弹窗，
点 `#liveDialog` 里的 `.live-dialog-confirm` / `.live-dialog-cancel` 即可（也可按 Enter / Esc），
不会出现阻塞主线程、让测试卡住的原生对话框。

浏览器回归：启动 `python3 deploy/serve.py` 后，用安装了 Playwright 的 Node 环境运行
`node tests/test_desktop.cjs`、`node tests/test_ratings.cjs`、`node tests/test_rating_leaderboard.cjs`
和 `node tests/test_mobile_settlement.cjs`。
每日奖励面板可运行 `node tests/test_rewards.cjs` 与 `node tests/test_holdem_rewards.cjs`；后者覆盖两个页面的
进度、领取/重连/跨日状态与手机原生触摸滚动、领取按钮。
手机结算回归使用触摸滑动和坐标点击，验证长结算页底部的继续/解散按钮可达。
可通过 `NODE_PATH` 指定 Playwright 包路径、`CHROME_PATH` 指定 Chrome 可执行文件。
[排行榜浏览器回归](tests/test_rating_leaderboard.cjs) 还支持 `RATING_TEST_URL` 指定静态服务地址（默认 `http://localhost:8000`），
以及 `RANKING_SCREENSHOT=/tmp/holdem-stats` 导出桌面和移动端截图；测试使用模拟 WebSocket，不修改真实账号数据。
[德扑服务端专项测试](tests/test_holdem_stats.py) 使用临时数据库与随机本地端口，不需要启动线上服务或重置用户数据库。

`node tests/test_table_ui.cjs` 覆盖麻将和掼蛋的实际选牌、出牌、提示、双击、聊天弹层、
离桌结算按钮，以及 320/390/844/1024/1440 像素布局；同时对比 Python 与 JavaScript
牌型判定。使用服务端生成的视图和模拟账号，不依赖真实用户；可用 `PYTHON` 指定 Python，
`TEST_BASE_URL` 指定静态服务地址，`TABLE_SCREENSHOT_DIR` 保存界面截图。

## 部署

见 [deploy/README.md](deploy/README.md)：systemd 单元安装、nginx 反代、推流命令。

## 数据库与用户数据

- 数据库为单文件 SQLite（默认 `users.db`），**已在 `.gitignore` 中**，不会进版本库
- 口令只存 PBKDF2 哈希与盐，会话 token 只存 SHA-256 哈希
- 备份时直接复制该文件即可；`admin.py restore` 可把金币一键还原

## 许可

[MIT](LICENSE) © 2026 LuHongYi
