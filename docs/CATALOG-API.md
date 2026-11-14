# Catalog API (Phase 3, task 1)

Categories, products, variants and images. **No prices and no stock yet** (offers and inventory are the next tasks), so the API rejects unknown fields such as `price_mmk`. Nothing here exposes internal database ids: products are addressed by `public_code` (e.g. `OM-P-000123`), categories by `slug`, variants by `sku`.

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
