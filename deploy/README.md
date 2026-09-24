# 部署说明

三个进程，都用 systemd 常驻：

| 服务 | 脚本 | 作用 | 监听 |
| --- | --- | --- | --- |
| live-chat | `chat_server.py` | 聊天、账号、金币、竞猜、游戏厅（WebSocket） | `servers.chat_port`（默认 8765） |
| live-web | `deploy/serve.py` | 静态页面，白名单只放行 index.html / game.html / assets（含子目录） | `servers.web_host:web_port`（默认 0.0.0.0:8000；nginx 反代时配 127.0.0.1） |
| live-auth | `auth_server.py` | 拉流鉴权，供 MediaMTX 的 `authExternalUrl` 调用 | `servers.auth_host:auth_port`（默认 127.0.0.1:8001） |

## 1. 配置

```bash
cp config.example.json config.json
$EDITOR config.json        # 站点标题、端口、推流路径与口令、数据库路径
```

`config.json` 不进版本库；环境变量优先级高于它，便于临时覆盖：

| 环境变量 | 覆盖的配置项 |
| --- | --- |
| `LIVE_CONFIG_FILE` | 配置文件路径本身 |
| `LIVE_CHAT_HOST` / `LIVE_CHAT_PORT` | `servers.chat_host` / `servers.chat_port` |
| `LIVE_WEB_HOST` / `LIVE_WEB_PORT` | `servers.web_host` / `servers.web_port` |
| `LIVE_AUTH_HOST` / `LIVE_AUTH_PORT` | `servers.auth_host` / `servers.auth_port` |
| `LIVE_DB_FILE` | `database.file` |
| `NEW_USER_COINS` | `economy.new_user_coins` |
| `STREAM_PATH` | `stream.path` |
| `STREAM_PUBLISH_USER` / `STREAM_PUBLISH_PASSWORD` | `stream.publish_user` / `stream.publish_password` |

## 2. 安装 systemd 单元

`deploy/systemd/*.service.example` 是模板，把 `__USER__`、`__APP_DIR__` 换成实际值后安装：

```bash
sed 's|__USER__|你的用户名|g; s|__APP_DIR__|/path/to/relaxweb|g' \
    deploy/systemd/live-chat.service.example | sudo tee /etc/systemd/system/live-chat.service
# live-web / live-auth 同理
sudo systemctl daemon-reload
sudo systemctl enable --now live-chat live-web live-auth
```

## 3. HTTPS 与反向代理（可选）

TLS 入口统一由 nginx 提供,以与后端相同的端口号对外(443 页面 / 8765 聊天与游戏 /
8889 直播信令),后端全部退绑 `127.0.0.1`,前端零改动。完整配置模板与两条证书路线
(裸 IP 短效证书 / 域名常规证书)见主 README 的「HTTPS(裸 IP 或域名)」一节与
[`deploy/nginx/relaxweb.conf.example`](nginx/relaxweb.conf.example);
裸 IP 方案的完整实施手册见 [`docs/https-ip-rollout.md`](../docs/https-ip-rollout.md)。

## 4. 推流侧

推流与播放都走 MediaMTX，本项目只提供两处接入：

- 鉴权：MediaMTX 的 `authExternalUrl` 指向 `http://127.0.0.1:8001`，
  它校验「推流口令」或「登录会话 token」，实现见 `auth_server.py`；
- 播放：页面用 WebRTC（WHEP）拉流，地址按 `stream.whep_port` + `stream.path` 拼出来
  （默认 `http://<host>:8889/<path>/whep`），协议名与路径都在 `config.json` 里改。

推流命令示例（用户名、口令、流路径分别来自 `stream.publish_user`、`stream.publish_password`、`stream.path`，三者可独立配置）：

```bash
ffmpeg -re -i 你的视频源 -c:v libx264 -c:a aac \
  -f rtsp 'rtsp://<推流用户名>:<推流口令>@127.0.0.1:8554/<流路径>'
```

## 5. 语音（LiveKit，可选）

狼人杀等房间的实时语音走自托管 [LiveKit](https://github.com/livekit/livekit)（Apache-2.0）。
语音关闭时整条链路为空操作，不影响其他功能。

1. 安装 LiveKit 并用 systemd 常驻。CN 网络服务器直连 GitHub 往往不通，可在本地解析
   release 资产的 302 直链（`objects.githubusercontent.com` 通常可达）后让服务器直接
   `curl -L` 下载。模板见 `deploy/livekit/livekit.yaml.example`、
   `deploy/systemd/livekit.service.example`，最小 `livekit.yaml`：

   ```yaml
   port: 7880
   rtc:
     tcp_port: 7881
     port_range_start: 50000
     port_range_end: 50100
     # NAT 云（如阿里云）静态写公网 IP；1.13.7 实测必须用标量写法，
     # ips: {binds, resolves} 结构体能通过解析但不作用于 ICE 候选
     use_external_ip: false
     node_ip: <公网IP>
   keys:
     <API_KEY>: <API_SECRET>
   ```

2. 防火墙/安全组放行：UDP 50000-50100（媒体；LiveKit 按**会话**从该段分配独立 UDP
   端口，必须整段放行，不能只开起始端口）。TCP 7880 无需对公网开放：信令经 nginx
   `/lk/` 反代（见 `deploy/nginx/relaxweb.conf.example`），7880 只绑本机。

3. chat 服务运行用户安装 `livekit-api`（`server/voice_livekit.py` 签发 JWT 用）。
   系统统 Python 部署用 `pip3 install --user livekit-api`（CN 服务器加
   `-i https://mirrors.aliyun.com/pypi/simple/`），装完重启 live-chat。

4. `config.json` 增加：

   ```json
   "voice": {
     "enabled": true,
     "url": "wss://<公网IP或域名>/lk",
     "api_url": "http://127.0.0.1:7880",
     "api_key": "<API_KEY>",
     "api_secret": "<API_SECRET>",
     "token_ttl": 600
   }
   ```

   环境变量 `VOICE_ENABLED` / `VOICE_URL` / `VOICE_API_URL` / `VOICE_API_KEY` /
   `VOICE_API_SECRET` 可覆盖。`url` 是浏览器实际连接的地址；`api_url` 是聊天服务
   访问 LiveKit 管理 API 的内网地址，用于阶段切换踢人及解散删房。生产浏览器连接
   必须走 HTTPS 站点的 `wss://`，本地测试用 `ws://127.0.0.1:7880` 即可。启动日志出现
   `voice enabled -> …` 即生效；若提示 `livekit-api 未安装` 回到第 3 步。

5. 冒烟验证（不碰业务数据）：`node scripts/probe_voice.mjs`，双假麦浏览器直连
   生产 `wss://…/lk`，断言信令、ICE UDP 直连与音频字节流动；密钥从 `LK_SECRET` 环境变量读入。

6. 权限模型：语音房间名包含房间 ID、局号与阶段序号（`ww{房间ID}-m{局号}-v{阶段号}-day/wolf`），
   旧令牌无法加入新阶段频道；入夜/天亮/死亡时服务端踢出旧连接并换发短时 JWT。
   前端 `assets/js/room-voice.js` + 自托管的
   `assets/vendor/livekit-client.umd.min.js` 完成连接、上麦与说话指示。

## 6. 数据库初始化

首次启动 `live-chat` 时会自动建表（`server.schema.init_db()`）。注册需要邀请码，
用 `manage_invite.py` 生成：

```bash
python3 manage_invite.py gen 5      # 生成 5 个邀请码
```

管理用 `admin.py`（在服务器应用目录以 admin 用户运行）：

```bash
python3 admin.py list                 # 用户与金币概览
python3 admin.py set/add/sub <用户> <数量>
python3 admin.py delete <用户> -y     # 物理删除用户及全部关联数据
python3 admin.py edit <用户> <字段> <值>   # 字段：nickname / role / password / username
python3 admin.py status               # 服务、系统、数据库、在线与最近错误
python3 admin.py restart -y           # 一键重启 live-chat / live-web / live-auth（需免密 sudo）
```

默认金币数量在 `config.json` 的 `economy.new_user_coins`。

## 7. 更新部署

两种方式，按网络情况选：

```bash
# 方式 A：本机直推服务器（服务器访问 GitHub 慢时用这个）
git remote add server <用户>@<服务器>:/path/to/relaxweb   # 只需一次
git push server main
# 服务器仓库设了 receive.denyCurrentBranch=updateInstead，
# 推送会直接更新工作区；推送后重启服务：
ssh <用户>@<服务器> 'sudo systemctl restart live-chat live-web live-auth'

# 方式 B：服务器自己从 GitHub 拉（服务器能顺畅访问 GitHub 时更省事）
ssh <用户>@<服务器> 'cd /path/to/relaxweb && git fetch origin && git reset --hard origin/main'
```

`git reset --hard` 不会删除未跟踪文件，`users.db` 与 `config.json` 都在未跟踪之列，因此不会丢；
但**改过 `config.json` 后要重启服务**才会重新读取。

> 注意：`live-test/` 是子模块，服务器上不需要它——不执行 `git submodule update` 就不会拉取。
