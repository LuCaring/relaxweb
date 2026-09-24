#!/bin/bash
# 短效证书到期看门狗:剩余 <2 天时记日志(可选接 ntfy 推送)。
# 安装:sudo install -m 755 check-cert-expiry.sh /usr/local/bin/
# cron:sudo crontab -e 加一行 `0 9 * * * /usr/local/bin/check-cert-expiry.sh`
# 探测本机 443;nginx 443 保持通配监听即可覆盖 127.0.0.1。
EXP=$(date -d "$(echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null \
      | openssl x509 -noout -enddate | cut -d= -f2)" +%s)
LEFT=$(( (EXP - $(date +%s)) / 86400 ))
if [ "$LEFT" -lt 2 ]; then
    echo "$(date) 站点证书仅剩 ${LEFT} 天" >> /var/log/cert-alert.log
    # 可选推送:curl -s "https://ntfy.sh/你的私有topic" -d "站点证书剩${LEFT}天"
fi
