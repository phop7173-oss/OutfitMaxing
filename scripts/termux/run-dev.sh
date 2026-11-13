#!/data/data/com.termux/files/usr/bin/bash
# Starts services, applies migrations and runs the API on http://127.0.0.1:8000 (Ctrl+C to stop).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
bash scripts/termux/services.sh start
# shellcheck disable=SC1091
source .venv/bin/activate
cd backend
alembic upgrade head
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
