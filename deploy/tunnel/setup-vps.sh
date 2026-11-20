#!/usr/bin/env bash
# Prepare the public VPS side of the reverse tunnel (Debian/Ubuntu).
#
#   sudo HTTPS_PORT=8443 ./setup-vps.sh media.example.com admin@example.com
#   sudo HTTPS_PORT=8443 ./setup-vps.sh media.example.com -   # no ACME e-mail
#
# Port 443 is often occupied by an existing proxy (e.g. danted), so HTTPS_PORT is
# configurable. Port 80 is used for the ACME HTTP-01 challenge and a redirect.
# The script does NOT modify the firewall; allow 80/tcp and $HTTPS_PORT yourself.
set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-}"
HTTPS_PORT="${HTTPS_PORT:-443}"
if [ -z "$DOMAIN" ]; then
    echo "用法: sudo HTTPS_PORT=8443 $0 <域名> <ACME 邮箱|->" >&2
    exit 2
fi
ACME_ARGS=(--register-unsafely-without-email)
if [ -n "$ACME_EMAIL" ] && [ "$ACME_EMAIL" != "-" ]; then
    ACME_ARGS=(-m "$ACME_EMAIL")
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 或 sudo 运行" >&2
    exit 1
fi
HERE="$(cd "$(dirname "$0")" && pwd)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends nginx certbot python3-certbot-nginx \
    fail2ban openssh-server

# ── tunnel user: remote forwarding only, no shell ───────────────────
if ! id tunnel >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin tunnel
fi
install -d -m 0700 -o tunnel -g tunnel /home/tunnel/.ssh
touch /home/tunnel/.ssh/authorized_keys
chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
chmod 0600 /home/tunnel/.ssh/authorized_keys

cat >/etc/ssh/sshd_config.d/60-mediaserver-tunnel.conf <<'EOF'
# MediaServer reverse tunnel: forwarding only, no shell, no passwords.
Match User tunnel
    AllowTcpForwarding remote
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
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

install -d -m 0755 /var/www/certbot
sed -e "s/__DOMAIN__/${DOMAIN}/g" -e "s/__HTTPS_PORT__/${HTTPS_PORT}/g" \
    "$HERE/nginx-mediaserver.conf.example" >/etc/nginx/sites-available/mediaserver
ln -sf /etc/nginx/sites-available/mediaserver /etc/nginx/sites-enabled/mediaserver
rm -f /etc/nginx/sites-enabled/default

# First run: no certificate yet, so serve HTTP only until certbot finishes.
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    cat >/etc/nginx/sites-available/mediaserver <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 503; }
}
EOF
fi

nginx -t
systemctl enable --now nginx
systemctl reload nginx

# ── certificate (webroot, works on any HTTPS port) ──────────────────
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
        --non-interactive --agree-tos "${ACME_ARGS[@]}" --keep-until-expiring
fi

# ── final vhost with TLS ────────────────────────────────────────────
sed -e "s/__DOMAIN__/${DOMAIN}/g" -e "s/__HTTPS_PORT__/${HTTPS_PORT}/g" \
    "$HERE/nginx-mediaserver.conf.example" >/etc/nginx/sites-available/mediaserver
nginx -t
systemctl reload nginx

# ── fail2ban for nginx auth failures ────────────────────────────────
if [ -f "$HERE/fail2ban-mediaserver.conf" ]; then
    cp "$HERE/fail2ban-mediaserver.conf" /etc/fail2ban/jail.d/mediaserver.conf
    systemctl enable --now fail2ban
    systemctl restart fail2ban
fi

cat <<EOF

完成。还需要两步：

1. 把 NAS 上 tunnel 用户的公钥追加到 /home/tunnel/.ssh/authorized_keys，
   建议在公钥前加限制（OpenSSH 8.2+）：
   restrict,port-forwarding,permitlisten="127.0.0.1:18080" ssh-ed25519 AAAA...

2. 在 NAS 的 /etc/mediaserver/tunnel.env 里填 VPS_HOST=${DOMAIN}，
   然后 systemctl enable --now mediaserver-tunnel。

验证：curl -fsS https://${DOMAIN}:${HTTPS_PORT}/api/v1/status
EOF
