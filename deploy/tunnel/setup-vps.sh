#!/usr/bin/env bash
# Prepare the public VPS side of the reverse tunnel (Debian/Ubuntu).
#
#   sudo ./setup-vps.sh media.example.com admin@example.com
#
# The script is intentionally conservative: it installs nginx/certbot/fail2ban,
# creates an unprivileged "tunnel" user restricted to remote port forwarding,
# writes the nginx vhost and obtains a certificate. It does NOT modify the
# firewall; allow 80/tcp and 443/tcp yourself.
set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-}"
if [ -z "$DOMAIN" ] || [ -z "$ACME_EMAIL" ]; then
    echo "用法: sudo $0 <域名> <ACME 邮箱>" >&2
    exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 或 sudo 运行" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends nginx certbot python3-certbot-nginx fail2ban openssh-server

# ── tunnel user: remote forwarding only ─────────────────────────────
if ! id tunnel >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash tunnel
fi
install -d -m 0700 -o tunnel -g tunnel /home/tunnel/.ssh
touch /home/tunnel/.ssh/authorized_keys
chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
chmod 0600 /home/tunnel/.ssh/authorized_keys

cat >/etc/ssh/sshd_config.d/60-mediaserver-tunnel.conf <<'EOF'
# MediaServer reverse tunnel: forwarding only, no shell, no passwords.
Match User tunnel
    AllowTcpForwarding remote
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    PasswordAuthentication no
    AuthenticationMethods publickey
EOF
systemctl reload ssh 2>/dev/null || systemctl reload sshd

# ── nginx proxy snippet ─────────────────────────────────────────────
cat >/etc/nginx/mediaserver-proxy.conf <<'EOF'
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host $host;
proxy_buffering off;
proxy_request_buffering off;
proxy_read_timeout 300s;
proxy_send_timeout 300s;
proxy_connect_timeout 10s;
EOF

sed "s/media\.example\.com/${DOMAIN}/g" \
    "$HERE/nginx-mediaserver.conf.example" >/etc/nginx/sites-available/mediaserver
ln -sf /etc/nginx/sites-available/mediaserver /etc/nginx/sites-enabled/mediaserver
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ── certificate ─────────────────────────────────────────────────────
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ACME_EMAIL" --redirect

# ── fail2ban for nginx auth failures ────────────────────────────────
if [ -f "$HERE/fail2ban-mediaserver.conf" ]; then
    cp "$HERE/fail2ban-mediaserver.conf" /etc/fail2ban/jail.d/mediaserver.conf
    systemctl enable --now fail2ban
    systemctl restart fail2ban
fi

cat <<EOF

完成。还需要两步：

1. 把 NAS 上 tunnel 用户的公钥追加到 /home/tunnel/.ssh/authorized_keys：
   sudo -u tunnel tee -a /home/tunnel/.ssh/authorized_keys < tunnel_key.pub

2. 在 NAS 的 /etc/mediaserver/tunnel.env 里填 VPS_HOST=${DOMAIN}，
   然后 systemctl enable --now mediaserver-tunnel。

验证：curl -fsS https://${DOMAIN}/api/v1/status
EOF
