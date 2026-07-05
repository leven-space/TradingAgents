#!/bin/sh
# Ensure bind-mounted NAS data dirs exist and match PUID/PGID before dropping privileges.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-10}"
DATA_ROOT="${TRADINGAGENTS_DATA_ROOT:-/home/appuser/.tradingagents}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p \
        "${DATA_ROOT}/cache" \
        "${DATA_ROOT}/logs" \
        "${DATA_ROOT}/memory"
    if ! chown -R "${PUID}:${PGID}" "${DATA_ROOT}" 2>/dev/null; then
        echo "warning: chown ${DATA_ROOT} failed; applying permissive mode" >&2
        chmod -R a+rwX "${DATA_ROOT}"
    fi
    exec gosu "${PUID}:${PGID}" "$@"
fi

exec "$@"
