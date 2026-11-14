#!/data/data/com.termux/files/usr/bin/bash
# Local lint. Always runs the line-length check (ruff's E501 equivalent). Runs ruff only if it
# is installed (it usually cannot be built on Termux); CI always runs ruff.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate
python scripts/check_line_length.py
if command -v ruff >/dev/null 2>&1; then
  ruff check backend
else
  echo "ruff not installed here; CI will run it on your pull request."
fi
