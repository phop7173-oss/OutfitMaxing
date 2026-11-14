#!/data/data/com.termux/files/usr/bin/bash
# Loads sample catalog data into your LOCAL dev database through the API and prints the result.
# The API must be running (bash scripts/termux/run-dev.sh in another session).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"
BASE="${BASE:-http://127.0.0.1:8000}"
KEY="$(grep -E '^ADMIN_API_KEY=' .env | cut -d= -f2-)"
[ -n "$KEY" ] || { echo "ADMIN_API_KEY is missing in .env (see docs/CATALOG-API.md)"; exit 1; }

call() { # method path json
  curl -sS -X "$1" "$BASE$2" -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" -d "$3"
  echo
}

echo "== category"; call POST /admin/catalog/categories '{"name":"Sneakers","slug":"sneakers"}'
echo "== product"
OUT="$(curl -sS -X POST "$BASE/admin/catalog/products" -H "X-Admin-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Air Runner","category_slug":"sneakers","fulfilment_mode":"STOCK","brand":"Demo"}')"
echo "$OUT"
CODE="$(printf '%s' "$OUT" | python -c 'import sys,json; print(json.load(sys.stdin)["public_code"])')"
echo "== variants for $CODE"
call POST "/admin/catalog/products/$CODE/variants" "{\"sku\":\"$CODE-42-BLK\",\"size\":\"42\",\"color\":\"black\"}" >/dev/null
call POST "/admin/catalog/products/$CODE/variants" "{\"sku\":\"$CODE-43-BLK\",\"size\":\"43\",\"color\":\"black\"}" >/dev/null
echo "== publish"; call PATCH "/admin/catalog/products/$CODE" '{"status":"ACTIVE"}'
echo "== public list (no key needed)"; curl -sS "$BASE/catalog/products?category=sneakers"; echo
