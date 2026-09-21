# 部署说明

三个进程，都用 systemd 常驻：

| 服务 | 脚本 | 作用 | 监听 |
| --- | --- | --- | --- |
| live-chat | `chat_server.py` | 聊天、账号、金币、竞猜、游戏厅（WebSocket） | `servers.chat_port`（默认 8765） |
| live-web | `deploy/serve.py` | 静态页面，白名单只放行 index.html / game.html / assets（含子目录） | `servers.web_port`（默认 8000） |
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
| `LIVE_WEB_PORT` | `servers.web_port` |
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

## 3. 反向代理与域名（可选）

`live-web` 直接对外提供 80 端口也可以；若要挂域名与 HTTPS，用 nginx 反代 8000，
并把 WebSocket 的 8765 与拉流的 8889 一并透传：

```nginx
location /            { proxy_pass http://127.0.0.1:8000; }
location /ws/         { proxy_pass http://127.0.0.1:8765; proxy_http_version 1.1;
                        proxy_set_header Upgrade $http_upgrade;
                        proxy_set_header Connection "upgrade"; }
```

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

## 5. 数据库初始化

首次启动 `live-chat` 时会自动建表（`chat_server.init_db()`）。注册需要邀请码，
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

## 6. 更新部署

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
