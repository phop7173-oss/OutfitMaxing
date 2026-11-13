#!/data/data/com.termux/files/usr/bin/bash
# Optional local lint. CI always runs it. If ruff is unavailable on your device, rely on CI.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate
if command -v ruff >/dev/null 2>&1; then
  ruff check backend
else
  echo "ruff not installed. Try: pkg install ruff   (if not found, CI will lint on push)"
fi
