#!/bin/sh
# Drop privileges to PUID:PGID after preparing the writable data directory.
# The media library is never touched: it is mounted read-only on purpose.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${MEDIA_DATA_DIR:-${DATA_DIR:-/data}}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true
    chmod -R u+rwX "$DATA_DIR" 2>/dev/null || true
    if command -v gosu >/dev/null 2>&1; then
        exec gosu "$PUID:$PGID" "$@"
    elif command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
    fi
    echo "entrypoint: no gosu/setpriv, refusing to run as root" >&2
    exit 1
fi

exec "$@"
