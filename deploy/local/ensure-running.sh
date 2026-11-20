#!/usr/bin/env bash
# Idempotent watchdog for user-level installs: start whichever instance is down.
# Intended for a cron entry such as:
#   */5 * * * * /path/to/deploy/local/ensure-running.sh >/dev/null 2>&1
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONF_DIR="${MEDIA_CONF_DIR:-$HOME/.config/mediaserver}"

start_instance() {
    local env_file="$1"
    local port="$2"
    [ -f "$env_file" ] || return 0
    if curl -fsS --max-time 3 "http://127.0.0.1:${port}/api/v1/status" >/dev/null 2>&1; then
        return 0
    fi
    echo "$(date -Is) starting instance on :${port}"
    ( cd "$ROOT" && MEDIA_ENV_FILE="$env_file" nohup ./deploy/local/start-server.sh \
        >>"/tmp/mediaserver-${port}.log" 2>&1 & )
}

start_instance "$CONF_DIR/lan.env" 8080
start_instance "$CONF_DIR/public.env" 8081

if [ -n "${VPS_HOST:-}" ] && ! pgrep -f "run-tunnel.sh" >/dev/null 2>&1; then
    echo "$(date -Is) starting reverse tunnel"
    ( cd "$ROOT" && VPS_HOST="$VPS_HOST" VPS_USER="${VPS_USER:-tunnel}" \
        LOCAL_PORT=8081 nohup ./deploy/tunnel/run-tunnel.sh \
        >>/tmp/mediaserver-tunnel.log 2>&1 & )
fi
