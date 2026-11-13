#!/data/data/com.termux/files/usr/bin/bash
# Shared helpers. Source this file; do not run it.
: "${PREFIX:?These scripts must run inside Termux}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PGDATA="${PGDATA:-$PREFIX/var/lib/postgresql}"
# Lets Python find Termux's libpq (needed by psycopg).
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
