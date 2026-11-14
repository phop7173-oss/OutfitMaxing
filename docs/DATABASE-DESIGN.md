# OutfitMaxing — Database Design

PostgreSQL. All primary keys are UUID. Money is `BIGINT` MMK (never float). All timestamps `timestamptz` (UTC). Every table has `created_at`; mutable tables have `updated_at`. Public readable identifiers (e.g. `OM-P-000123`, `OM-2026-000045`) are separate unique columns generated from sequences.

## Catalog
- **product_categories**: id, parent_id, name, slug (unique), is_excluded_guard (see rule below).
- **products**: id, public_code (unique), category_id FK, name, description, brand, status, fulfilment_mode (`STOCK` | `PREORDER`), attributes JSONB.
- **product_variants**: id, product_id FK, sku (unique), size, color, extra JSONB, UNIQUE(product_id, size, color).
- **product_images**: id, product_id FK, variant_id nullable FK, url/telegram_file_id, sort_order.
- **offers**: id, variant_id/product_id, price_mmk, compare_at_price_mmk, starts_at, ends_at, status. CHECK price_mmk >= 0.
- **offer_variants**: offer_id, variant_id (composite PK).
- Rule: excluded categories (watches, electronics, battery-powered, cosmetics) are blocked by an application-level validation and an allowlist of category slugs in config; documented, tested.

## Inventory
- **inventory_locations**: id, name, kind.
- **inventory_balances**: id, variant_id, location_id, on_hand INT, reserved INT. UNIQUE(variant_id, location_id). CHECK on_hand >= 0, reserved >= 0, reserved <= on_hand.
- **inventory_reservations**: id, order_id/cart_id, variant_id, location_id, quantity > 0, status (`ACTIVE|COMMITTED|RELEASED|EXPIRED`), expires_at, idempotency_key. Index on (status, expires_at).
- **inventory_movements** (append-only): id, variant_id, location_id, delta, reason, reference_type, reference_id, actor, created_at. No UPDATE/DELETE grants for the app role.

## Suppliers / preorder
- **suppliers**: id, name, contact, status.
- **supplier_products**: id, supplier_id, variant_id, supplier_sku, unit_cost_mmk, lead_time_days_min/max, preorder_capacity INT, capacity_committed INT, CHECK committed <= capacity.
- **supplier_stock_checks**: id, supplier_product_id, available_qty, checked_at, source.
- Sourcing requests (table `sourcing_requests`, added to the required list with a documented reason: the spec requires sourcing status and supplier confirmation tracking and no listed table owns it): id, order_item_id, supplier_product_id, quantity, status (`REQUESTED|CONFIRMED|SHIPPED_TO_US|RECEIVED|FAILED|CANCELLED`), expected_arrival_from/to, confirmed_at, received_at.

## Customers / conversations
- **customers**: id, telegram_user_id (unique), display_name, language, phone (nullable), created_at.
- **customer_addresses**: id, customer_id, label, recipient, phone, region, township, address_line, is_default.
- **conversations**: id, customer_id, channel, summary, state JSONB, last_message_at.
- **conversation_messages**: id, conversation_id, role, content, tool_calls JSONB, telegram_update_id (unique, nullable), created_at.

## Commerce
- **carts**: id, customer_id, status, idempotency scope. **cart_items**: cart_id, variant_id, offer_id, quantity, UNIQUE(cart_id, variant_id).
- **orders**: id, public_number (unique), customer_id, status, payment_method, subtotal_mmk, discount_mmk, delivery_fee_mmk, total_mmk, address_snapshot JSONB, idempotency_key, UNIQUE(customer_id, idempotency_key). CHECK total = subtotal - discount + delivery_fee.
- **order_items**: id, order_id, variant_id, fulfilment_group (`STOCK|PREORDER`), quantity, unit_price_mmk (snapshot), product_snapshot JSONB, status.
- **order_events** (append-only): id, order_id, from_status, to_status, actor, reason, created_at.
- **payments**: id, order_id, method, status, amount_mmk, provider, provider_reference (unique per provider), verified_at, verification_source. COD rows have method `COD`.
- **promotions**: id, code (unique), type, value, min_order_mmk, starts_at, ends_at, usage_limit, used_count.
- **returns**, **refunds**: linked to order/order_items with status, amount_mmk, reason, approved_by.

## Delivery
- **delivery_providers**: id, code (unique), kind (`OWN|EXTERNAL|MOCK`), enabled, config (no secrets; secrets via env).
- **delivery_quotes**: id, provider_id, order_id/cart_id, zone, fee_mmk, estimated_days, expires_at.
- **shipments**: id, order_id, provider_id, status, tracking_ref, idempotency_key (unique), package JSONB; shipment items link to order_items for partial shipment.

## Platform
- **audit_logs**: id, actor_type, actor_id, action, entity_type, entity_id, metadata JSONB (redacted), created_at.
- **outbox_events**: id, topic, payload JSONB, status, attempts, next_attempt_at, dedupe_key (unique).
- **telegram_updates** (support table, documented): update_id PK, received_at, status, attempts, last_error.

## Cross-cutting requirements
- Foreign keys everywhere; `ON DELETE RESTRICT` for commercial history.
- Indexes: FK columns, `orders(customer_id, created_at)`, `reservations(status, expires_at)`, trigram or full-text index for product search (Burmese tokenisation strategy decided in Phase 4).
- Historical orders never join to live prices; snapshots only.
- Migrations via Alembic, reviewed, reversible where feasible.
- Backups: nightly `pg_dump` plus WAL archiving if the VPS allows; restore drill documented in Phase 6.

## Updates after owner decisions
- B1: single seller. The `offers` table remains (price history, promotional prices, time windows) but has no seller reference; it is not a marketplace construct.
- New table `shop_settings` (Phase 3): key, value JSONB, updated_by, updated_at, plus changes recorded in `audit_logs`. Holds policy values (docs/POLICIES.md) and delivery/COD configuration. Documented reason: policies must be owner-editable without code changes or redeploys.
- `payments`: add `verified_by` (staff user id, nullable) and `verification_note`; manual digital payments move to VERIFIED only through the admin service with a named actor.
- Preorder deposit: `orders` gains `deposit_amount_mmk` and `balance_due_mmk` (CHECK deposit + balance = total) once U4 is resolved.
- No business tables are created in Phase 2; `migrations/versions/` is intentionally empty.

## Implemented in migration 0001 (catalog)
`product_categories`, `products`, `product_variants`, `product_images`, sequence `product_code_seq` (public codes `OM-P-000123`). Differences from the first design: categories have no guard column (the excluded-category rule is code in `products/service.py`); no `offers` yet; the same-product rule for variant images is a composite foreign key `(product_id, variant_id)`; "same size+color" is a functional unique index `uq_product_variants_combo` (created in the migration, ignored by Alembic autogenerate); `fulfilment_mode` can be changed until inventory exists (to be restricted in the inventory task).
