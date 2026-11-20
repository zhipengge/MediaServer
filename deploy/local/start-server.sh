#!/usr/bin/env bash
# User-level launcher that loads an environment file, then runs MediaServer.
#
#   ./deploy/local/start-server.sh
#
# Environment:
#   MEDIA_ENV_FILE  default: ~/.config/mediaserver/env
#
# Use this when the account has no passwordless sudo (no systemd unit). Pair it
# with a cron @reboot entry or a small supervisor.
set -euo pipefail

ENV_FILE="${MEDIA_ENV_FILE:-$HOME/.config/mediaserver/env}"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ ! -x .venv/bin/python ]; then
    echo "缺少 .venv，先在 $ROOT 执行：PIPENV_VENV_IN_PROJECT=1 pipenv install" >&2
    exit 1
fi

exec .venv/bin/python -m mediaserver
