#!/data/data/com.termux/files/usr/bin/bash
# One-time (safe to re-run) setup for developing OutfitMaxing on Android/Termux.
# First run compiles Rust extensions (pydantic-core): can take 10-30+ minutes. Keep the screen on.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

echo "==> Installing system packages"
pkg update -y
pkg install -y python postgresql libpq redis rust binutils clang make git openssh unzip nano curl

echo "==> Python virtual environment"
[ -d .venv ] || python -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

echo "==> Python dependencies"
export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-$(getprop ro.build.version.sdk 2>/dev/null || echo 24)}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"   # lower = less RAM; raise if your phone has plenty
pip install -r backend/requirements-dev.txt

echo "==> Checking the PostgreSQL driver"
if ! python -c "import psycopg; psycopg.pq.version()" 2>/dev/null; then
  echo "Pure-Python psycopg could not load libpq; building the C implementation instead"
  pip install "psycopg-c==$(python -c 'import importlib.metadata as m; print(m.version("psycopg"))')"
fi
python -c "import psycopg; print('psycopg OK, implementation:', psycopg.pq.__impl__)"

echo "==> Local .env (never committed)"
[ -f .env ] || cp .env.example .env
PW="$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- || true)"
if [ -z "$PW" ] || [ "$PW" = "change-me" ]; then
  PW="$(python -c 'import secrets; print(secrets.token_hex(16))')"
  sed -i "s/change-me/$PW/g" .env     # random local password instead of the example one
fi
chmod 600 .env

echo "==> Starting PostgreSQL and Redis"
bash "$ROOT/scripts/termux/services.sh" start

echo "==> Creating database role and databases"
psql -d postgres -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='outfitmaxing') THEN CREATE ROLE outfitmaxing LOGIN; END IF; END \$\$;"
psql -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE outfitmaxing PASSWORD '$PW'"
for db in outfitmaxing outfitmaxing_test; do
  psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
    || createdb -O outfitmaxing "$db"
done

echo "==> Verifying the connection with the app settings"
( cd backend && python -c "
from sqlalchemy import text
from app.database.session import get_engine
with get_engine().connect() as c:
    print('database reachable:', c.execute(text('SELECT 1')).scalar() == 1)
" )

command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
echo
echo "Setup complete. Next: bash scripts/termux/run-dev.sh   (then bash scripts/termux/test.sh)"
