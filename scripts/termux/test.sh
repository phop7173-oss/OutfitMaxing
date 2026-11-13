#!/data/data/com.termux/files/usr/bin/bash
# Runs the test-suite against the local PostgreSQL test database (outfitmaxing_test).
# Extra arguments go to pytest, e.g.: bash scripts/termux/test.sh -k logging -x
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
bash scripts/termux/services.sh start >/dev/null
# shellcheck disable=SC1091
source .venv/bin/activate
set -a; source .env; set +a
export TEST_DATABASE_URL="${DATABASE_URL%/*}/outfitmaxing_test"
export DATABASE_URL="$TEST_DATABASE_URL"
export APP_ENV=test
cd backend
alembic upgrade head
exec pytest -q "$@"
