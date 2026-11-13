#!/data/data/com.termux/files/usr/bin/bash
# Usage: bash scripts/termux/services.sh start|stop|status
# Starts/stops the local PostgreSQL (and Redis, if installed). Both listen on 127.0.0.1 only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
REDIS_DIR="$PREFIX/var/lib/redis"

pg_up() { pg_ctl -D "$PGDATA" status >/dev/null 2>&1; }

start() {
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "==> Initialising PostgreSQL data directory"
    mkdir -p "$PGDATA"
    # Local socket: trust (single-user phone). TCP/localhost: password (scram-sha-256).
    initdb -D "$PGDATA" -E UTF8 --no-locale --auth-local=trust --auth-host=scram-sha-256
  fi
  pg_up || pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" -w start
  if command -v redis-server >/dev/null 2>&1; then
    if ! redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then
      mkdir -p "$REDIS_DIR"
      redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --dir "$REDIS_DIR" --save ""
    fi
  fi
  status
}

stop() {
  pg_up && pg_ctl -D "$PGDATA" -m fast stop || true
  redis-cli -h 127.0.0.1 shutdown nosave >/dev/null 2>&1 || true
  status
}

status() {
  if pg_up; then echo "postgresql: running"; else echo "postgresql: stopped"; fi
  if redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; then echo "redis: running"; else echo "redis: stopped"; fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
