#!/usr/bin/env bash
# MediaServer process control.
#
#   ./start.sh start|stop|restart|status [lan|public|tunnel|all]
#
# Components:
#   lan     : 0.0.0.0:8080, NAS-DEPLOY contract, no auth (home network only)
#   public  : 127.0.0.1:8081, token auth, reached through the VPS tunnel
#   tunnel  : SSH reverse tunnel to the VPS (requires ~/.config/mediaserver/tunnel.env)
#
# State lives outside the repository:
#   ~/.config/mediaserver/{lan,public,tunnel}.env
#   ~/.config/mediaserver/run/*.pid
#   ~/.config/mediaserver/logs/*.log
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="${MEDIA_CONF_DIR:-$HOME/.config/mediaserver}"
RUN_DIR="${MEDIA_RUN_DIR:-$CONF_DIR/run}"
LOG_DIR="${MEDIA_LOG_DIR:-$CONF_DIR/logs}"
LAN_ENV="${LAN_ENV:-$CONF_DIR/lan.env}"
PUBLIC_ENV="${PUBLIC_ENV:-$CONF_DIR/public.env}"
TUNNEL_ENV="${TUNNEL_ENV:-$CONF_DIR/tunnel.env}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

port_of() {
    case "$1" in
        lan) echo 8080 ;;
        public) echo 8081 ;;
        *) return 1 ;;
    esac
}

env_of() {
    case "$1" in
        lan) echo "$LAN_ENV" ;;
        public) echo "$PUBLIC_ENV" ;;
        *) return 1 ;;
    esac
}

healthy() {
    curl -fsS --max-time 2 "http://127.0.0.1:$1/api/v1/status" >/dev/null 2>&1
}

pid_from_port() {
    ss -ltnp 2>/dev/null | grep -E ":$1[[:space:]]" | grep -o 'pid=[0-9]*' \
        | head -1 | cut -d= -f2
}

pid_alive() {
    [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

start_one() {
    local name="$1"
    local port env pidfile log pid i
    port="$(port_of "$name")" || return 2
    env="$(env_of "$name")" || return 2
    pidfile="$RUN_DIR/$name.pid"
    log="$LOG_DIR/$name.log"

    if healthy "$port"; then
        echo "= $name 已在运行 (:${port})"
        return 0
    fi
    if [ ! -f "$env" ]; then
        echo "! 缺少环境文件 $env，跳过 $name" >&2
        return 1
    fi
    if [ ! -x "$ROOT/deploy/local/start-server.sh" ]; then
        echo "! 找不到 $ROOT/deploy/local/start-server.sh" >&2
        return 1
    fi

    nohup env MEDIA_ENV_FILE="$env" "$ROOT/deploy/local/start-server.sh" >>"$log" 2>&1 &
    pid=$!
    echo "$pid" >"$pidfile"

    for i in $(seq 1 30); do
        if healthy "$port"; then
            echo "✓ $name 已启动 (pid ${pid}, :${port})"
            return 0
        fi
        if ! pid_alive "$pid"; then
            break
        fi
        sleep 0.5
    done
    echo "✗ $name 启动失败，最近日志 ($log):" >&2
    tail -n 8 "$log" >&2 2>/dev/null || true
    rm -f "$pidfile"
    return 1
}

stop_one() {
    local name="$1"
    local port pidfile pid waited
    port="$(port_of "$name")" || return 2
    pidfile="$RUN_DIR/$name.pid"
    pid=""
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile" 2>/dev/null || true)"
    fi
    if ! pid_alive "$pid"; then
        pid="$(pid_from_port "$port")"
    fi
    if ! pid_alive "$pid"; then
        rm -f "$pidfile"
        echo "= $name 未在运行"
        return 0
    fi
    kill "$pid" 2>/dev/null || true
    for waited in $(seq 1 20); do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$pidfile"
            echo "✓ $name 已停止 (pid ${pid})"
            return 0
        fi
        sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pidfile"
    echo "✓ $name 已强制停止 (pid ${pid})"
}

start_tunnel() {
    local pidfile="$RUN_DIR/tunnel.pid"
    local log="$LOG_DIR/tunnel.log"
    if [ ! -f "$TUNNEL_ENV" ]; then
        echo "= 跳过 tunnel（没有 $TUNNEL_ENV）"
        return 0
    fi
    # shellcheck disable=SC1090
    set -a
    . "$TUNNEL_ENV"
    set +a
    if [ -z "${VPS_HOST:-}" ]; then
        echo "= 跳过 tunnel（VPS_HOST 为空）"
        return 0
    fi
    if pgrep -f "[r]un-tunnel.sh" >/dev/null 2>&1; then
        echo "= tunnel 已在运行"
        return 0
    fi
    setsid env VPS_HOST="$VPS_HOST" VPS_USER="${VPS_USER:-tunnel}" \
        VPS_PORT="${VPS_PORT:-22}" REMOTE_PORT="${REMOTE_PORT:-18080}" \
        LOCAL_PORT="${LOCAL_PORT:-8081}" \
        "$ROOT/deploy/tunnel/run-tunnel.sh" >>"$log" 2>&1 &
    echo $! >"$pidfile"
    sleep 2
    if pgrep -f "[r]un-tunnel.sh" >/dev/null 2>&1; then
        echo "✓ tunnel 已启动 (${VPS_USER:-tunnel}@${VPS_HOST})"
        return 0
    fi
    echo "✗ tunnel 启动失败，看 $log" >&2
    tail -n 5 "$log" >&2 2>/dev/null || true
    return 1
}

stop_tunnel() {
    local pidfile="$RUN_DIR/tunnel.pid"
    local pid=""
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile" 2>/dev/null || true)"
    fi
    if [ -n "$pid" ]; then
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    fi
    pkill -f "[r]un-tunnel.sh" 2>/dev/null || true
    pkill -f "[s]sh -N.*127.0.0.1:18080" 2>/dev/null || true
    rm -f "$pidfile"
    echo "✓ tunnel 已停止"
}

status_one() {
    local name="$1" port pidfile pid state
    port="$(port_of "$name")"
    pidfile="$RUN_DIR/$name.pid"
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -z "$pid" ]; then
        pid="$(pid_from_port "$port")"
    fi
    if healthy "$port"; then
        state="running"
    else
        state="stopped"
    fi
    printf '%-8s :%-5s %-8s pid=%s\n' "$name" "$port" "$state" "${pid:-–}"
}

status_tunnel() {
    local pidfile="$RUN_DIR/tunnel.pid" pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if pgrep -f "[r]un-tunnel.sh" >/dev/null 2>&1; then
        printf '%-8s %-12s pid=%s\n' "tunnel" "running" "${pid:-–}"
    else
        printf '%-8s %-12s pid=%s\n' "tunnel" "stopped" "–"
    fi
}

usage() {
    cat <<EOF
用法: $0 {start|stop|restart|status} [lan|public|tunnel|all]

  start    启动尚未运行的组件
  stop     停止组件
  restart  先 stop 再 start
  status   查看端口与进程状态

环境文件: $CONF_DIR/{lan,public,tunnel}.env
日志:     $LOG_DIR
EOF
}

command="${1:-}"
target="${2:-all}"
[ -n "$command" ] || { usage; exit 2; }

case "$command" in
    start)
        rc=0
        case "$target" in
            all)
                start_one lan || rc=1
                start_one public || rc=1
                start_tunnel || rc=1
                ;;
            lan|public) start_one "$target" || rc=1 ;;
            tunnel) start_tunnel || rc=1 ;;
            *) usage; exit 2 ;;
        esac
        exit "$rc"
        ;;
    stop)
        case "$target" in
            all) stop_tunnel; stop_one public; stop_one lan ;;
            lan|public) stop_one "$target" ;;
            tunnel) stop_tunnel ;;
            *) usage; exit 2 ;;
        esac
        ;;
    restart)
        case "$target" in
            all)
                stop_tunnel; stop_one public; stop_one lan
                sleep 1
                rc=0
                start_one lan || rc=1
                start_one public || rc=1
                start_tunnel || rc=1
                exit "$rc"
                ;;
            lan|public)
                stop_one "$target"; sleep 1; start_one "$target"
                ;;
            tunnel)
                stop_tunnel; sleep 1; start_tunnel
                ;;
            *) usage; exit 2 ;;
        esac
        ;;
    status)
        echo "MediaServer"
        status_one lan
        status_one public
        status_tunnel
        ;;
    *)
        usage
        exit 2
        ;;
esac
