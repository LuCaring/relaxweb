# RelaxWeb

RelaxWeb 是一个可自托管的直播间与游戏厅。它提供 WebRTC 直播、聊天与弹幕、邀请码账号、金币与竞猜，以及多人牌桌和单人庄园。后端使用 Python 与 SQLite，前端是原生 ES Module，部署时不需要前端构建。

## 功能与玩法概览

- **直播间**：MediaMTX 推流、WebRTC/WHEP 播放、实时聊天、弹幕、在线列表和历史消息。
- **账号与金币**：邀请码注册、个人资料、金币转账与流水、签到抽奖、德扑流水奖励、资产榜和共享段位榜。
- **竞猜**：登录用户可发起问题、金币下注；发起者负责封盘结算或流局退款。
- **德州扑克**：无限注、盲注轮转、边池和全下结算，支持 2–9 人。
- **UNO**：经典功能牌、UNO 漏喊质疑和剩牌赔付，支持 2–9 人。
- **掼蛋**：四人组队、双副牌、级牌与炸弹规则，从 2 升级到 A。
- **国标麻将**：四人吃碰杠胡、八番起和、花牌与常用番种计分。
- **飞行棋**：2–4 人经典竞速，掷点起飞、同色跳格、飞行捷径与撞机回机场，可自定义连投、跳格、飞行与金币结算方式。
- **骗子酒馆**：2–6 人轮盘对峙，暗打出牌声称桌面牌、质疑翻牌定真假，输掉决斗对自己开枪；弹巢、手牌数、小丑与金币结算方式可自定义。
- **休闲庄园**：单人种植、钓鱼、采矿、土地升级和收藏，使用同一账号金币与仓库。

具体规则、可选项和结算提示以创建房间页面及牌桌内说明为准。

## 用户注册与参与流程

1. 已有用户在账号菜单的“邀请码”窗口生成邀请码，或由管理员运行 `python3 manage_invite.py gen` 生成。
2. 新用户打开站点，切换到“注册”，填写邀请码、用户名和密码。用户名支持 2–20 位中文、英文、数字、下划线或连字符；密码至少 6 位。
3. 注册成功后自动登录并获得 `economy.new_user_coins` 设置的初始金币。邀请码立即失效，不能重复使用。
4. 首页可观看直播、发送聊天与弹幕、参与竞猜，并在账号菜单中编辑昵称/头像、查看财务、转账和领取每日奖励。
5. 点击“游戏厅”或直接访问 `/game`。单人庄园可直接进入；多人游戏可创建房间、选择底注和买入，也可加入现有房间。
6. 牌局开始后再进入房间会成为观战者，不买入、不占座且不能操作牌局；观战者可以切换玩家视角并参与房间聊天。
7. 离桌时筹码自动结算回钱包。正常完成的多人牌局会更新共享段位和相关排行榜。

登录会话默认有效 30 天并自动恢复。用户应自行保管密码；管理员只能重置密码，无法读取原密码。

## 服务器部署

### 1. 准备环境

推荐使用 Debian / Ubuntu 服务器、Python 3.9+、systemd 和 nginx。直播功能还需要单独安装 [MediaMTX](https://github.com/bluenviron/mediamtx)；只使用聊天和游戏厅时可以不安装 MediaMTX，也不用启动 `live-auth`。

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv python3-websockets python3-psutil nginx sqlite3 ffmpeg
sudo git clone <本仓库地址> /opt/relaxweb
sudo chown -R <运行用户>:<运行用户> /opt/relaxweb
cd /opt/relaxweb
```

如果发行版没有 `python3-websockets` 或 `python3-psutil`，可创建虚拟环境并安装依赖，同时把三个 systemd 模板中的 `/usr/bin/python3` 改为虚拟环境里的 Python：

```bash
python3 -m venv .venv
.venv/bin/pip install websockets psutil
```

### 2. 配置站点

```bash
cp config.example.json config.json
$EDITOR config.json
```

将下列所有 `<...>` 替换为实际值（配置文件中不要保留尖括号）；端口和初始金币可按需调整：

```json
{
  "site": {
    "title": "<直播间标题>",
    "brand": "<站点品牌名>",
    "game_title": "<游戏厅标题>"
  },
  "servers": {
    "chat_host": "<聊天服务监听地址>",
    "chat_port": 8765,
    "web_port": 8000,
    "auth_host": "<鉴权服务监听地址>",
    "auth_port": 8001
  },
  "stream": {
    "whep_port": 8889,
    "path": "<直播流路径>",
    "publish_user": "<推流用户名>",
    "publish_password": "<推流密码>"
  },
  "database": {
    "file": "<数据库文件绝对路径>"
  },
  "economy": {
    "new_user_coins": 1000
  }
}
```

创建数据库目录并限制权限：

```bash
install -d -m 700 /opt/relaxweb/data
chmod 600 config.json
```

配置优先级是“环境变量 > `config.json` > 内置默认值”。可用的环境变量包括：

| 环境变量 | 配置项 |
| --- | --- |
| `LIVE_CONFIG_FILE` | 配置文件路径 |
| `LIVE_CHAT_HOST` / `LIVE_CHAT_PORT` | WebSocket 监听地址 / 端口 |
| `LIVE_WEB_PORT` | 网页服务端口 |
| `LIVE_AUTH_HOST` / `LIVE_AUTH_PORT` | MediaMTX 鉴权服务地址 / 端口 |
| `LIVE_DB_FILE` | SQLite 文件路径 |
| `NEW_USER_COINS` | 新用户初始金币 |
| `STREAM_PATH` | MediaMTX 流路径，覆盖 `stream.path` |
| `STREAM_PUBLISH_USER` / `STREAM_PUBLISH_PASSWORD` | 推流用户名 / 密码 |

`config.json` 不会被网页服务公开，也不应提交到版本库。浏览器只会收到站点文案、WebSocket 端口、WHEP 端口和流路径。

### 3. 初始化数据库和首批邀请码

首次执行管理命令时会自动建表：

```bash
cd /opt/relaxweb
python3 manage_invite.py gen 5
python3 manage_invite.py list
```

保存输出的邀请码。每个邀请码只能注册一个账号。注册首个账号后，可将其设为管理员：

```bash
python3 admin.py edit <用户名> role admin -y
```

### 4. 安装 systemd 服务

项目包含三个服务模板：

| 服务 | 用途 | 默认监听 |
| --- | --- | --- |
| `live-chat` | 账号、聊天、金币、竞猜和游戏 | `0.0.0.0:8765` |
| `live-web` | 首页、游戏厅和静态资源 | `0.0.0.0:8000` |
| `live-auth` | MediaMTX 外部鉴权，仅供本机调用 | `127.0.0.1:8001` |

替换模板中的运行用户和绝对路径，再启用服务：

```bash
for name in live-chat live-web live-auth; do
  sed 's|__USER__|<运行用户>|g; s|__APP_DIR__|/opt/relaxweb|g' \
    "deploy/systemd/${name}.service.example" \
    | sudo tee "/etc/systemd/system/${name}.service" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now live-chat live-web live-auth
sudo systemctl status live-chat live-web live-auth --no-pager
```

如果使用 `.venv`，安装前先把模板内的 `ExecStart=/usr/bin/python3` 改为 `ExecStart=/opt/relaxweb/.venv/bin/python`。

如果按下文用 nginx 为 WebSocket 终止 TLS，还要在 `live-chat.service` 的 `[Service]` 中加入以下覆盖，让后端改为仅监听本机的内部端口；`config.json` 仍保留浏览器使用的外部端口 `8765`：

```ini
Environment=LIVE_CHAT_HOST=127.0.0.1
Environment=LIVE_CHAT_PORT=18765
```

常用日志命令：

```bash
journalctl -u live-chat -f
journalctl -u live-web -u live-auth --since today
```

### 5. 配置 MediaMTX 和推流

在 MediaMTX 配置中完成三件事：

1. 开启 WebRTC/WHEP。直接对外提供 HTTP 时端口与 `stream.whep_port` 一致（默认 `8889`）；使用下文的 nginx HTTPS 配置时，让 MediaMTX 改为仅监听 `127.0.0.1:18889`，浏览器外部端口仍是 `8889`。
2. 把外部鉴权地址设为 `http://127.0.0.1:8001`。新版 MediaMTX 使用 `authMethod: http` 与 `authHTTPAddress`；旧版对应 `authExternalURL`，以所安装版本的示例配置为准。
3. 公网部署时设置 WebRTC 对外可达的域名或 IP，并放行 MediaMTX 的 ICE/UDP 端口（常见默认值为 `8189/udp`）。

流路径和推流用户名是两个独立配置项：`stream.path` 决定 MediaMTX 路径及观众播放地址，`stream.publish_user` 只用于推流鉴权。两者都可以改名，不要求相同。RTSP 推流示例：

```bash
ffmpeg -re -i <视频源> -c:v libx264 -c:a aac -f rtsp \
  'rtsp://<推流用户名>:<推流密码>@<服务器地址>:8554/<流路径>'
```

其中 `<推流用户名>`、`<推流密码>` 和 `<流路径>` 分别对应 `stream.publish_user`、`stream.publish_password` 和 `stream.path`。观众拉流时使用登录会话鉴权，不需要知道推流密码。

### 6. 域名、HTTPS 和反向代理

网页在 HTTPS 下会分别连接：

- `https://<域名>/`：站点页面；
- `wss://<域名>:8765/`：聊天与游戏；
- `https://<域名>:8889/<流路径>/whep`：直播信令。

因此不能只代理网页的 443 端口；WebSocket 和 WHEP 端口也要提供有效证书。以下 nginx 示例保留项目默认端口，证书路径按实际环境修改：

```nginx
server {
    listen 443 ssl http2;
    server_name live.example.com;
    ssl_certificate     /etc/letsencrypt/live/live.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/live.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}

server {
    listen 8765 ssl;
    server_name live.example.com;
    ssl_certificate     /etc/letsencrypt/live/live.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/live.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}

server {
    listen 8889 ssl;
    server_name live.example.com;
    ssl_certificate     /etc/letsencrypt/live/live.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/live.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:18889;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
    }
}
```

检查并加载配置：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

防火墙通常需要开放 `443/tcp`、`8765/tcp`、`8889/tcp` 和 MediaMTX 的 WebRTC UDP 端口。内部端口 `8000`、`8001`、`18765`、`18889` 不应直接暴露公网；`8554/tcp` 只向可信推流端开放。若不使用直播，只需网页和 WebSocket。

### 7. 部署验收

```bash
curl -I http://127.0.0.1:8000/
python3 admin.py status
systemctl is-active live-chat live-web live-auth
```

然后访问 `https://<域名>/`，依次验证注册、登录、聊天和 `/game` 游戏厅。启用直播时再验证推流、播放及浏览器控制台中是否存在 WHEP、证书或 ICE 错误。

### 8. 更新、备份和恢复

更新前先备份数据库和配置：

```bash
cd /opt/relaxweb
sudo sqlite3 data/users.db ".backup '/var/backups/relaxweb-users-$(date +%F-%H%M).db'"
sudo cp -a config.json /var/backups/relaxweb-config.json

git pull --ff-only
sudo systemctl restart live-chat live-web live-auth
python3 admin.py status
```

数据库升级在服务启动时自动完成，不需要手工建表。恢复数据库时先停止 `live-chat`，替换数据库文件并确认文件属主，再启动服务：

```bash
sudo systemctl stop live-chat
sudo cp /var/backups/<备份文件>.db /opt/relaxweb/data/users.db
sudo chown <运行用户>:<运行用户> /opt/relaxweb/data/users.db
sudo systemctl start live-chat
```

## 管理入口

### 网页管理

登录后，直播间的账号菜单可修改资料、查看财务、转账、领取每日奖励、生成邀请码及注销账号；游戏厅账号菜单提供财务和每日奖励入口。每个账号最多保留 5 个未使用邀请码，可在直播间的“邀请码”窗口生成并复制给新用户。

竞猜由任意登录用户发起，并由发起者封盘、结账或流局。`admin` 与 `streamer` 角色用于身份标识及受保护的管理协议；用户、角色、密码和金币的日常运维统一通过服务器命令行完成。角色不能在网页自行申请。

### 服务器命令行

所有命令都应在应用目录中、以能够读写数据库的运行用户执行：

```bash
python3 admin.py status                         # 服务、系统、数据库、在线与近期错误；本地开发也可用
python3 admin.py restart -y                     # systemd 服务器上重启三个项目服务，需要免密 sudo
python3 admin.py list                           # 所有用户和金币概览
python3 admin.py list <用户名>                  # 用户余额及最近流水
python3 admin.py set <用户名> <数量>             # 设置金币
python3 admin.py add <用户名> <数量>             # 增加金币
python3 admin.py sub <用户名> <数量>             # 扣除金币
python3 admin.py edit <用户名> nickname <昵称>   # 修改昵称
python3 admin.py edit <用户名> role admin -y     # 设置角色：user/admin/streamer
python3 admin.py edit <用户名> password <新密码> # 重置密码，至少 6 位
python3 admin.py edit <用户名> username <新名字> # 修改用户名并迁移关联数据
python3 admin.py delete <用户名> -y              # 删除账号及关联数据

python3 manage_invite.py gen 5                  # 生成服务器邀请码
python3 manage_invite.py list                   # 查看邀请码及使用状态
```

`restore`、`restore-all` 和 `clear-log` 会物理删除金币流水，`delete` 会物理删除账号数据；执行前应先备份数据库。删除或改名正在牌局中的用户前，应先让其离桌或解散房间。

## 本地运行与开发

站点标题与品牌通过 `site` 配置，直播默认路径为 `live`、推流用户名为 `publisher`。
庄园珍藏奖励的名称、说明、素材和解锁条件通过 `estate.collection_reward` 配置，
详见[自定义珍藏奖励](docs/estate-skins.md#自定义珍藏奖励)。
从旧版本升级已有存档时，先按[目录标识迁移](docs/estate-id-migration.md)生成迁移副本。

不配置 MediaMTX 也可以开发聊天和游戏功能。macOS 首次使用先运行 `brew install uv node`；其他系统参见 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)，浏览器测试需要 Node.js 20 或更新版本：

```bash
uv sync --locked                      # 按 uv.lock 安装 Python 与依赖到 .venv
cp config.example.json config.json
uv run --locked python manage_invite.py gen 3
uv run --locked python chat_server.py
# 另一个终端
uv run --locked python deploy/serve.py
```

项目使用 `.python-version` 选择本地 Python 3.11，`pyproject.toml` 声明依赖，`uv.lock` 固定版本。`uv` 会管理 `.venv`，无需手动激活。新增运行依赖使用 `uv add <包名>`；重新同步使用 `uv sync --locked`。

角色素材生成工具额外需要 Pillow，可运行 `uv sync --locked --extra assets` 后执行 `uv run --locked --extra assets python tools/build_estate_characters.py`。

打开 `http://127.0.0.1:8000/`。只预览游戏 UI 时可运行：

```bash
uv run --locked python scripts/preview_ui.py
```

再次启动会自动停止同一项目的旧预览进程。需要同时运行多个预览时加 `--no-replace`。本地服务在上述终端按 Ctrl+C 停止后重新启动；`uv run --locked python admin.py status` 可查看本地服务状态，`admin.py restart` 仅用于 systemd 部署。

运行全部 Python 测试（包括独立游戏测试脚本）：

```bash
uv run --locked python scripts/run_python_tests.py
```

浏览器测试使用 npm 安装 Playwright，并使用与其版本匹配的 Chromium：

```bash
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci
npx playwright install chromium
npm run test:browser
```

`test:browser` 会临时启动 8000 端口的静态网页服务，自动使用 `.venv` 中的 Python 和 Playwright Chromium；可在命令末尾指定单个测试文件。测试其他 Chrome 版本时可设置 `CHROME_PATH`。

systemd 模板位于 [deploy/systemd/](deploy/systemd/)，测试代码与覆盖范围见 [tests/](tests/)。

## 许可

[MIT](LICENSE) © 2026 LuHongYi
