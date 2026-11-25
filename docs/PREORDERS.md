# Preorders and supplier sourcing (Phase 3, task 2c)

Preorders are supplier-sourced items sold before they are physically in the shop. They are tracked completely separately from physical stock (`inventory_*` tables): a preorder is never counted, reserved or shown as in-stock inventory, and arrivals never become ordinary stock automatically.

## Concepts
- **Supplier / supplier product**: a supplier and the PREORDER variants it can source, with `lead_days_min/max` (customer-facing estimate), private `unit_cost_mmk`, and **preorder capacity** set by the owner. `available capacity = capacity - committed`. No capacity means no preorders.
- **Commitment** (`preorder_commitments`): units promised to a customer order line. HELD, then FULFILLED (goods allocated) or RELEASED (cancelled). Created only by the service (`commit_capacity`), atomically, so customers can never exceed the owner's capacity; the expected window is today (Myanmar date) plus the supplier's lead days. There is deliberately no HTTP endpoint to create one: the order engine will call it.
- **Deposit verification**: a request goes to the supplier only for commitments whose deposit is verified (`deposit-verified` endpoint, manual by the owner for now; a payment provider can call the same service later).
- **Sourcing request**: REQUESTED, then CONFIRMED (supplier gives a window), SHIPPED, RECEIVED; or FAILED / CANCELLED. Creating a request bundles every deposit-verified, not-yet-requested commitment of that supplier product.
- **Outbox events**: the system queues notifications in `outbox_events` (same transaction as the change); delivery to Telegram comes with the Telegram phase. Payloads hold ids and dates only.

## Rules (owner-approved)
1. Supplier is asked only after the deposit is verified. If the supplier has not confirmed within 3 days (`SUPPLIER_CONFIRMATION_DAYS`) the owner is alerted once (`owner.supplier_unconfirmed`); nothing is cancelled automatically.
2. Customers see an estimated range of lead days (never a promise). Confirming a request replaces the estimate with the supplier's window and queues one `preorder.window_changed` per affected customer; a passed window flags the preorder for owner review and queues one `preorder.window_passed`.
3. Arrivals go to waiting preorders strictly oldest first (`preorder.ready` per fulfilled preorder). A shortfall flags the first uncovered preorder and every later one for owner review (no queue jumping) and queues `owner.short_delivery`.
4. Units left over stay on the request (`surplus_remaining`). The owner may move them into physical stock explicitly (`move-surplus-to-stock`), which adds a normal logged stock receipt; the product stays a PREORDER product until the owner changes its mode.
5. Failed supplier: preorders are unlinked and flagged for owner review (`owner.sourcing_failed`). Refunds and cancellation terms are manual owner decisions under the pending policy (U4/B4). A cancelled request returns its preorders to waiting.

## Admin API (header `X-Admin-Key`)
Suppliers: `POST/GET /admin/suppliers`, `POST/GET /admin/suppliers/{code}/products`, `PATCH /admin/suppliers/products/{id}`, `POST /admin/suppliers/products/{id}/stock-checks` (what the supplier reported; informational).
Preorders: `GET /admin/preorders/commitments?status=&needs_review=`, `POST .../{id}/deposit-verified`, `POST .../{id}/release`, `POST /admin/preorders/flag-overdue`.
Sourcing: `POST/GET /admin/sourcing/requests`, `POST .../{id}/confirm|ship|receive|fail|cancel|move-surplus-to-stock`, `GET /admin/sourcing/unconfirmed`, `POST /admin/sourcing/flag-unconfirmed`.

Example:
```bash
KEY=$(grep ^ADMIN_API_KEY= .env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:8000/admin/suppliers -H "X-Admin-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"code":"acme","name":"Acme Imports"}'
curl -s -X POST http://127.0.0.1:8000/admin/suppliers/acme/products -H "X-Admin-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"sku":"<a PREORDER variant sku>","lead_days_min":10,"lead_days_max":20,"capacity":5}'
```

## Public display
Variants of PREORDER products show `availability: "preorder"` (with `preorder_lead_days_min/max`) while capacity remains, otherwise `"preorder_closed"`. No capacity numbers are exposed.

## Known limits
No periodic scheduler yet: `flag_overdue_commitments` and `flag_unconfirmed_requests` are functions (and admin trigger endpoints) until the worker exists. The outbox has no dispatcher until the Telegram phase. A direct SQL change bypasses the rules (constraints still protect capacity invariants). Supplier stock checks are informational and do not limit capacity. Dates use the Myanmar calendar (UTC+6:30).
