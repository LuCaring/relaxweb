# 裸 IP HTTPS 上线实施手册（Let's Encrypt 短效 IP 证书）

制定日期：2026-09-24。目标：不注册域名、不做备案，让 `https://<公网IP>` 立即提供
全站 HTTPS（含麦克风权限），为 LiveKit 语音（见 `docs/werewolf-voice-design.md` 的 V0/V0.5）
铺路。本手册面向在生产服务器上执行的人；所有命令默认在服务器上以 admin 用户运行。

## 1. 方案决策摘要

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 证书 | Let's Encrypt，`shortlived` profile，160h（约 6 天） | 2026-01-15 起 IP 证书正式 GA，只发短效 profile |
| 签发/续期工具 | acme.sh（git 最新版） | 已支持 `--certificate-profile`；单脚本 + 自带 cron；certbot 需 snap 4.x 且 IP 标识符支持更新 |
| 验证方式 | HTTP-01，standalone 独占 80 端口 | IP 标识符只能用 HTTP-01/TLS-ALPN-01；nginx 不占 80，续期永不冲突 |
| TLS 入口 | nginx 统一终结：443（页面+WS+WHEP+LiveKit） | 单一续期重载点；后端服务退回 loopback，公网面收敛 |
| 端口策略 | nginx 对外 **443 / 8765(ssl) / 8889(ssl)**，后端全部绑 127.0.0.1 | 前端零改动（见 §2）；公网明文端口清零 |
| 回滚 | 关 443 即回到现状 | 8000 明文入口保留到收尾阶段 |

风险前提：证书绑定裸 IP，**公网 IP 一旦变化（释放/换实例）证书即失效**；阿里云 ECS 普通
公网 IP 不随重启变化，风险可控。长期仍建议域名 + 备案（与本方案无冲突，届时换证书路径即可）。
80 端口的 ACME 验证请求 Host 是裸 IP、无域名可匹配，不触发备案拦截（现状 8000/8889 同理）。

## 2. 为什么前端零改动

`assets/js/core.js:155` 与 `assets/app.js:379` 的 WebSocket、`app.js:192` 的 WHEP 播放
都是 `location.protocol 决定 ws/wss + location.hostname + 固定端口`。只要 nginx 以 **TLS
形式监听同端口号**（8765、8889），浏览器连 `wss://IP:8765`、`https://IP:8889` 直接成立，
页面协议从 `http:` 变 `https:` 后自动切 `wss`/`https`。**唯一代价**：8765/8889 的明文后端
必须退到 loopback，把端口的公网面让给 nginx（§6）。

## 3. 步骤一：开通端口与安装 nginx

1. 阿里云控制台 → 安全组：放行 **TCP 80**（ACME 验证）、**TCP 443**（HTTPS 入口）。
   8765/8889 保持放行（流量性质不变，只是换成 TLS）。
2. 服务器本机防火墙确认：`sudo ufw status`（若 active 需同样放行 80/443；多数机器未启用）。
3. 安装 nginx：`sudo apt update && sudo apt install -y nginx`，`systemctl status nginx` 确认。
   本方案 nginx 只监听 443/8765/8889，不占 80。

验证点：`curl -I http://<公网IP>:8000` 仍通（现状不破坏）；安全组规则已生效。

## 4. 步骤二：安装 acme.sh（必须最新版）

IP 标识符 + shortlived profile 的兼容修复在 2025-12/2026-01 才合入，务必用 git 最新版：

```bash
git clone https://github.com/acmesh-official/acme.sh.git /tmp/acme.sh
# GitHub 慢时用镜像：git clone https://gitee.com/neilpang/acme.sh.git /tmp/acme.sh
cd /tmp/acme.sh && ./acme.sh --install -m 你的邮箱 --server letsencrypt
exec -l $SHELL   # 重载 shell 使 acme.sh 生效
acme.sh --version   # 确认 >= 3.1.1（建议当日 master）
acme.sh --set-default-ca --server letsencrypt   # acme.sh 默认是 ZeroSSL，必须切
```

## 5. 步骤三：签发证书（先 staging，后正式）

```bash
# 5.1 staging 试签：验证端口/账号/profile 整条链路，不消耗正式限额
acme.sh --issue --standalone -d <公网IP> \
        --certificate-profile shortlived --staging --server letsencrypt

# 5.2 正式签发
acme.sh --issue --standalone -d <公网IP> \
        --certificate-profile shortlived --server letsencrypt

# 5.3 安装到固定路径并挂 nginx 重载（acme.sh 自动登记续期动作）
sudo mkdir -p /etc/nginx/ssl
acme.sh --install-cert -d <公网IP> \
        --fullchain-file /etc/nginx/ssl/ip.crt \
        --key-file       /etc/nginx/ssl/ip.key \
        --reloadcmd      "sudo nginx -t && sudo systemctl reload nginx"
sudo chown root:root /etc/nginx/ssl/ip.* && sudo chmod 600 /etc/nginx/ssl/ip.key
```

验证点：
- `acme.sh --info -d <公网IP>`：`Le_NextRenewTime` 应落在签发后 **3~4 天**内
  （6 天期证书的 2/3 生命周期规则；若异常，加 `--days 3` 重装证书）。
- `sudo openssl x509 -in /etc/nginx/ssl/ip.crt -noout -dates -text | grep -E "Not|IP"`：
  确认 SAN 是 `IP Address:<公网IP>`、有效期 ≈ 7 天。
- standalone 模式签发期间会临时占 80；与 nginx（443/8765/8889）无冲突，续期同理。

## 6. 步骤四：nginx 配置（对外 TLS + 对内回源）

新增 `/etc/nginx/sites-available/relaxweb` 并软链到 sites-enabled，**删掉 default 站点**
（避免占 443/80）。完整配置：

```nginx
# ---- 主入口：静态页 + 直播页（443）----
server {
    listen 443 ssl;
    server_name <公网IP>;

    ssl_certificate     /etc/nginx/ssl/ip.crt;
    ssl_certificate_key /etc/nginx/ssl/ip.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    # LiveKit 信令（语音层已实现：server/voice_livekit.py，装好 LiveKit 后取消注释）
    #location /lk/ {
    #    proxy_pass http://127.0.0.1:7880/;
    #    proxy_http_version 1.1;
    #    proxy_set_header Upgrade $http_upgrade;
    #    proxy_set_header Connection "upgrade";
    #}
}

# ---- 游戏厅/庄园 WebSocket：对内仍回源 8765 明文 ----
server {
    listen 8765 ssl;
    server_name <公网IP>;
    ssl_certificate     /etc/nginx/ssl/ip.crt;
    ssl_certificate_key /etc/nginx/ssl/ip.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;      # WebSocket 升级
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;                    # 长连接不被掐
        proxy_send_timeout 3600s;
    }
}

# ---- MediaMTX WHEP/WHIP：回源 8889 明文 ----
server {
    listen 8889 ssl;
    server_name <公网IP>;
    ssl_certificate     /etc/nginx/ssl/ip.crt;
    ssl_certificate_key /etc/nginx/ssl/ip.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8889;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

# ---- LiveKit 信令：占位已并入上方 443 server 块的 /lk/ ----
```

LiveKit 启用时的配置对齐（语音层 `server/voice_livekit.py` 已实现并合入）：nginx 挂上
`/lk/` 后，服务器 `config.json` 的 `voice.url` 必须改为**浏览器可达**的
`wss://<公网IP>/lk`——语音层会把 `voice_update.url` 原样下发给浏览器直连，
不能保留默认的 `ws://127.0.0.1:7880`；LiveKit 服务端自身绑 `127.0.0.1:7880`，
媒体 UDP 端口段照常对公网放行，安全组同时放行 TCP 7880 则不需要（走 nginx 即可）。

注意：上面 443 server 块里的第二个 `location /` 是占位说明，写入配置时删掉——443 上
`/` 即静态页；LiveKit 启用后用 `/lk/` 前缀方式挂在同一 server 里。

## 7. 步骤五：后端服务退回 loopback

1. **chat_server**（8765）：服务器上编辑 `config.json`，
   `"servers": { "chat_host": "127.0.0.1", ... }`，然后 `sudo systemctl restart live-chat`。
   验证：`sudo ss -tlnp | grep 8765` 显示 `127.0.0.1:8765`。
2. **live-web**（8000）：暂保持 0.0.0.0（回退通道），收尾阶段再改 127.0.0.1。
3. **MediaMTX**（8889）：`systemctl cat mediamtx` 找到配置文件（通常 `/etc/mediamtx.yml`）：
   - `webrtcAddress: 127.0.0.1:8889`
   - `webrtcAdditionalHosts: [<公网IP>]`（绑 loopback 后 ICE 候选必须手动指公网 IP，
     否则浏览器拿到 127.0.0.1 候选直接黑屏）
   - UDP mux 端口保持安全组放行；`sudo systemctl restart mediamtx`。
4. **推流端**：主播用 WHIP 的把推流地址 `http://IP:8889/...` 改为 `https://`（证书受信，
   OBS 无需额外设置）；走 RTMP/SRT 的推流不受影响。
5. **auth_server**：本就只在 127.0.0.1，不动。

验证点：`sudo ss -tlnp | grep -E "8000|8765|8889"`——最终应只有 8000 在公网，8765/8889
绑 loopback，nginx 持有 443/8765/8889 的公网监听。

## 8. 步骤六：全链路验证清单

浏览器（Chrome + Safari 各一台，手机亦然）打开 `https://<公网IP>`：

1. 地址栏无告警（点开证书：颁发者 Let's Encrypt，SAN 为 IP）。
2. 登录/注册 → 聊天在线状态绿点（wss://IP:8765 已升级，DevTools Network/WS 确认）。
3. 游戏厅开 UNO/狼人杀房间进出、发消息、下注（走同一 WS）。
4. 直播页拉流播放（WHEP 经 443→8889 TLS 回源；ICE 候选应是公网 IP 的 UDP）。
5. DevTools Console 无混合内容告警；`openssl s_client -connect <公网IP>:443`
   与 `:8765`、`:8889` 各确认一次证书链。
6. 麦克风试金石：任意页面 Console 执行
   `navigator.mediaDevices.getUserMedia({audio:true}).then(s=>s.getTracks().forEach(t=>t.stop()))`
   ——不报 SecurityError 即为达成 V0 目标。

## 9. 步骤七：续期自动化与到期监控

- acme.sh 安装时已注册每日 cron（`crontab -l` 可见 `acme.sh --cron`）。手动演练一次：
  `acme.sh --renew -d <公网IP> --force --certificate-profile shortlived`，
  确认 `install-cert` 的 reloadcmd 生效（nginx reload 且站点无感）。
- 到期看门狗 `/usr/local/bin/check-cert-expiry.sh`（cron 每天 9 点跑一次）：

```bash
#!/bin/bash
EXP=$(date -d "$(echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null \
      | openssl x509 -noout -enddate | cut -d= -f2)" +%s)
LEFT=$(( (EXP - $(date +%s)) / 86400 ))
if [ "$LEFT" -lt 2 ]; then
    echo "$(date) IP 证书仅剩 ${LEFT} 天" >> /var/log/cert-alert.log
    # 可选推送：curl -s "https://ntfy.sh/你的私有topic" -d "IP证书剩${LEFT}天"
fi
```

- 续期失败的兜底顺序：证书 6 天有效期意味着任何一次失败都有 2~3 次重试机会；
  看门狗连续两天报警时人工执行 §5.2 重签。最坏情况（80 被堵/CA 故障）：旧证书到期前
  临时关闭 443、恢复 `IP:8000` 明文入口，服务不中断。

## 10. 收尾与回滚

- 收尾（HTTPS 稳定运行数日后）：`config.json` 把 live-web 的监听改 127.0.0.1，安全组
  关闭 TCP 8000；用户书签统一切到 `https://<公网IP>`。
- 回滚：安全组关 443/80，把 chat_host/MediaMTX 绑定改回 0.0.0.0 并重启——即回到今天的
  明文现状，代码与数据零损失。
- 未来接域名 + 备案后：只需给 nginx 增加 443 的 `server_name 域名` + 域名证书路径，
  本手册其余部分原样复用。

## 11. 已知边界

- 证书 6 天有效 → **续期自动化是生命线**，看门狗必须装。
- 裸 IP 证书无法覆盖"换服务器/换公网 IP"，迁移时需重新签发。
- 微信内置浏览器对非 443 端口与自建站点的兼容性本来就差，语音功能仍建议引导系统浏览器。
- LE 对 shortlived profile 的速率限制独立且宽松，每 3~4 天续一张远低于限额；
  staging 演练不计入正式额度。
