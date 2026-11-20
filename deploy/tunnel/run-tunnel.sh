#!/usr/bin/env bash
# User-level reverse tunnel with automatic reconnect (no root required).
#
#   VPS_HOST=media.example.com ./run-tunnel.sh
#
# Environment:
#   VPS_HOST     required, the VPS domain or IP
#   VPS_USER     default: tunnel
#   VPS_PORT     default: 22
#   REMOTE_PORT  default: 18080 (bound to 127.0.0.1 on the VPS)
#   LOCAL_PORT   default: 8080
#   KEY          default: ~/.ssh/mediaserver_tunnel
#
# For a system service use mediaserver-tunnel.service instead; this script is
# for accounts without passwordless sudo (cron @reboot also works).
set -euo pipefail

: "${VPS_HOST:?set VPS_HOST}"
VPS_USER="${VPS_USER:-tunnel}"
VPS_PORT="${VPS_PORT:-22}"
REMOTE_PORT="${REMOTE_PORT:-18080}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
KEY="${KEY:-$HOME/.ssh/mediaserver_tunnel}"

if [ ! -f "$KEY" ]; then
    echo "缺少私钥 $KEY，先执行：ssh-keygen -t ed25519 -N '' -f $KEY" >&2
    exit 1
fi

echo "reverse tunnel 127.0.0.1:${REMOTE_PORT} -> 127.0.0.1:${LOCAL_PORT} via ${VPS_USER}@${VPS_HOST}:${VPS_PORT}"
while true; do
    ssh -N \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new \
        -o BatchMode=yes \
        -i "$KEY" -p "$VPS_PORT" \
        -R "127.0.0.1:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" \
        "${VPS_USER}@${VPS_HOST}" || true
    echo "$(date -Is) tunnel dropped, reconnecting in 10s" >&2
    sleep 10
done
