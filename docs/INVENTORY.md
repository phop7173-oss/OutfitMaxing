# Inventory (Phase 3, task 2b)

Physical stock only. Preorders (supplier sourcing) are task 2c and stay separate: a PREORDER product can never be reserved from physical stock and is never shown as "in stock".

## Model
`available = on_hand - reserved`, per variant and location. One location exists: `main` ("Main"), created by the migration. Tables: `inventory_locations`, `inventory_balances`, `inventory_reservations`, `inventory_movements` (append-only audit log: every change to `on_hand` or `reserved` writes one row with a reason: RECEIVE, ADJUST, RESERVE, RELEASE, EXPIRE, COMMIT). The database itself refuses negative stock and `reserved > on_hand`.

## Reservations (service only, no HTTP endpoint)
- `reserve_stock`: one atomic conditional `UPDATE ... WHERE on_hand - reserved >= qty`, so the check and the hold cannot be separated; concurrent buyers cannot oversell. A hold lasts `ttl_minutes` (the app default `RESERVATION_TTL_MINUTES` is 15). Optional `idempotency_key` returns the same reservation on a retry.
- Expiry needs no background worker to stay correct: every reserve first releases that variant's due holds. `expire_due_reservations` is a sweeper for a periodic job (added with the order engine).
- `release_reservation` is idempotent. `commit_reservation` turns the hold into a real deduction exactly once; repeating it changes nothing; an expired or released hold cannot be committed (reserve again).
- Only ACTIVE products with active variants and fulfilment mode STOCK can be reserved.
- The checkout (later) will call these functions; the AI will get no tool that changes stock directly.

## Admin API (header `X-Admin-Key`)
- `GET /admin/inventory/variants/{sku}`: on hand, reserved, available per location
- `POST /admin/inventory/variants/{sku}/receive` `{quantity, note?, location_code?}`
- `POST /admin/inventory/variants/{sku}/adjust` `{delta, note, location_code?}` (note required; cannot go below reserved or zero)
- `GET /admin/inventory/variants/{sku}/movements?limit=50`

## Public display
Variants carry `availability`: `in_stock`, `low_stock` (3 or fewer; constant `LOW_STOCK_THRESHOLD`), `out_of_stock` or `preorder`. No exact quantities are exposed. Out-of-stock variants stay visible. Availability counts on-hand minus unexpired holds, so an expired but unswept hold does not distort it.

## Known limits
One location only (no admin endpoint to add more yet); no HTTP reservation API (by design); the expiry sweeper is not scheduled until the worker exists; a direct SQL change to balances bypasses the movement log (the constraints still protect the invariants).
