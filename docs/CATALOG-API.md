# Catalog API (Phase 3, task 1)

Categories, products, variants and images. Prices are managed through **offers** (below). **No stock yet** (next task). A product cannot carry a price field; unknown fields are rejected. Nothing here exposes internal database ids: products are addressed by `public_code` (e.g. `OM-P-000123`), categories by `slug`, variants by `sku`.

## Public (read-only, no key)
- `GET /catalog/categories`
- `GET /catalog/products?category=<slug>&q=<text>&limit=20&offset=0` (ACTIVE products only; `limit` 1-100)
- `GET /catalog/products/{public_code}` (404 unless ACTIVE; inactive variants and their images are hidden)

## Admin (header `X-Admin-Key: <ADMIN_API_KEY>`)
- `POST /admin/catalog/categories`
- `POST /admin/catalog/products` (created as DRAFT)
- `GET|PATCH /admin/catalog/products/{public_code}` (PATCH can change fields and `status`)
- `POST /admin/catalog/products/{public_code}/variants`
- `PATCH /admin/catalog/variants/{sku}` (`is_active`, `extra`)
- `POST /admin/catalog/products/{public_code}/images` (`url` must be https, or `telegram_file_id`)

`ADMIN_API_KEY` (min 16 characters) is an **interim** guard until login and roles in Phase 6. If it is not set, every admin endpoint returns 503 (fails closed). Termux `setup.sh` generates one in `.env`.

## Rules enforced
- Categories whose name or slug contains watches, electronics, batteries, cosmetics or makeup are refused (business spec). Products are only checked through their category.
- Status: DRAFT to ACTIVE or ARCHIVED, ACTIVE to DRAFT or ARCHIVED, ARCHIVED to DRAFT. Publishing needs at least one active variant; the last active variant of an ACTIVE product cannot be deactivated.
- A product cannot have two variants with the same size and color (a missing size or color counts as empty); SKUs are unique. A variant image must belong to the same product (enforced by the database).

## Try it on Termux
```bash
bash scripts/termux/run-dev.sh            # session 1
bash scripts/termux/demo-catalog.sh       # session 2: loads sample data, prints the public list
```
Manual example:
```bash
KEY=$(grep ^ADMIN_API_KEY= .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/admin/catalog/categories \
  -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"Wallets","slug":"wallets"}'
curl -s http://127.0.0.1:8000/catalog/categories
```
Errors use `{"error": {"code", "message", "request_id", ...}}`; validation errors list field names only and never echo submitted values.

## Prices (offers), Phase 3 task 2a
A price is an *offer* on one variant: whole kyat (`price_mmk`, strict integer 1-10,000,000,000), optional `compare_at_price_mmk` (must be higher), and a validity window `[starts_at, ends_at)` (`starts_at` defaults to now, `ends_at` to open-ended; datetimes need a timezone, e.g. `2026-12-01T00:00:00+06:30`).
- `POST /admin/catalog/variants/{sku}/offers`: create (409 if it overlaps another active offer of that variant; adjacent windows are fine)
- `GET /admin/catalog/variants/{sku}/offers`: full history, newest first
- `POST /admin/catalog/offers/{offer_id}/cancel`: cancel (history is kept, never deleted)

Rules: the server decides the current price from the offers; nothing else can set it. Publishing a product needs at least one active variant with a current price. The public API shows only active variants that have a current price (others are not purchasable) and hides products with none; the admin view shows everything with `price_mmk: null` where no price is in effect. The last active, priced variant of an ACTIVE product cannot be deactivated. If a price expires or is cancelled, the variant silently stops being purchasable.

Example:
```bash
KEY=$(grep ^ADMIN_API_KEY= .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/admin/catalog/variants/OM-P-000001-42-BLK/offers \
  -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"price_mmk":85000,"compare_at_price_mmk":100000}'
```

## Stock labels on variants (task 2b)
Each variant has `availability`: `in_stock`, `low_stock`, `out_of_stock` or `preorder` (see docs/INVENTORY.md). Stock quantities are admin-only (`/admin/inventory/...`).

## Preorder labels (task 2c)
PREORDER variants show `availability` `preorder` (plus `preorder_lead_days_min/max`) while the owner's preorder capacity remains, otherwise `preorder_closed`. See docs/PREORDERS.md.
