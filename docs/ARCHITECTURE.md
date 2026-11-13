# OutfitMaxing — Architecture

## Style
Modular monolith (single deployable, strict module boundaries). Stack: Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, Redis, Celery (from Phase 3), Docker Compose. Pin dependency versions.

Decision (Phase 2): synchronous SQLAlchemy with psycopg 3. It keeps transaction and row-locking logic simple and testable; the load of a single shop does not justify async complexity. Revisit only if profiling shows a need.

Business: single-seller shop (B1). There is no marketplace/seller dimension anywhere in the model.

## Layers (dependency direction: downward only)
1. **Interfaces**: REST API routers, Telegram webhook handler, admin API, Celery task entrypoints. Thin; no business logic.
2. **Application services** (per module): transactional use cases (`reserve_stock`, `validate_checkout`, `confirm_checkout`, ...). Own the transaction boundary.
3. **Domain**: rules, state machines, value objects (Money = int MMK).
4. **Infrastructure**: SQLAlchemy repositories, Telegram client, Claude client, courier adapters, payment adapters.

Telegram handlers and the AI provider depend on application services. Services never import Telegram or Anthropic code.

## Modules
customers, conversations, products, offers, inventory, suppliers, cart, orders, payments, delivery, promotions, notifications, telegram, ai, admin, workers (as in the master prompt).
Boundary note: `inventory` owns physical stock and reservations; `suppliers` owns sourcing requests and supplier availability; preorder orders coordinate both through service calls, never by sharing tables.

## Inventory
- `available = on_hand - reserved`, per variant and location.
- Reserve: single transaction; `UPDATE inventory_balances SET reserved = reserved + :q WHERE id = :id AND on_hand - reserved >= :q` (conditional update; rowcount 0 means insufficient stock). CHECK constraints prevent negatives.
- Reservation rows carry `expires_at` (default 15 min, configurable). Availability computations ignore expired reservations at read time; a Celery beat job releases them and writes movements. A stalled worker therefore cannot cause overselling.
- Commit reservation (on confirmation/fulfilment): deducts `on_hand` and `reserved` once, guarded by reservation status transition (`ACTIVE -> COMMITTED`), so double deduction is impossible.
- Every change writes an append-only `inventory_movements` row.
- Preorder: separate path. Checks `supplier_products` availability and preorder capacity, creates a sourcing request, never touches physical `on_hand` until a receiving event is recorded.

## Orders
States: CART, CHECKOUT_VALIDATION, PENDING_PAYMENT, RESERVED, CONFIRMED, PROCESSING, PARTIALLY_SHIPPED, SHIPPED, DELIVERED, CANCELLED, EXPIRED, RETURN_REQUESTED, REFUNDED.
Transitions are a whitelist table in code (one function applies them, writes `order_events`). Arbitrary status updates are not possible.
Proposed whitelist (to be confirmed in Phase 3):
- CART -> CHECKOUT_VALIDATION -> PENDING_PAYMENT | RESERVED | CANCELLED
- PENDING_PAYMENT -> RESERVED | EXPIRED | CANCELLED
- RESERVED -> CONFIRMED | EXPIRED | CANCELLED
- CONFIRMED -> PROCESSING | CANCELLED
- PROCESSING -> PARTIALLY_SHIPPED | SHIPPED | CANCELLED
- PARTIALLY_SHIPPED -> SHIPPED
- SHIPPED -> DELIVERED
- DELIVERED -> RETURN_REQUESTED
- RETURN_REQUESTED -> REFUNDED | DELIVERED (return rejected)
Gap flagged: payment failure and partial cancellation are not in the requested list; decision needed (B4).
- Prices computed server-side; order items snapshot product name, variant attributes, unit price, discounts.
- Checkout idempotency: client-supplied idempotency key with a unique constraint (customer_id, key).
- Fulfilment groups: stock group and preorder group per order; each ships independently.
- COD: auto-confirm after validation + explicit customer confirmation. Digital: CONFIRMED only after trusted payment verification (provider callback/API); screenshots never qualify.

## Delivery
`DeliveryProvider` interface: `get_quote`, `create_shipment`, `cancel_shipment`, `track_shipment`. Implementations: `OwnDeliveryProvider`, `MockCourierProvider` (PENDING real integration). Shipment creation runs as a background job with an idempotency key. Multiple shipments per order. Failed-delivery and return-to-sender are shipment states that emit order events.

## Telegram
- Webhook endpoint verifies `X-Telegram-Bot-Api-Secret-Token`.
- Idempotency: `update_id` stored with a unique constraint; duplicates acknowledged and skipped.
- Processing: webhook persists the update and enqueues a job, returns 200 quickly; failed jobs retry with backoff; replies go through the `outbox_events` pattern so sends are retry-safe.
- Identity: Telegram user id mapped to `customers`; customer id comes only from this mapping.
- Channel publishing is a one-way capability (new product, price, stock, promotion, media); the channel is never a data source.

## AI layer (provider-agnostic; Gemini first)
- `AIProvider` interface (Phase 4): `generate(messages, tools) -> response` plus quota/error signalling. First implementation: Google Gemini API using the free tier initially. A fake provider is used in all tests. Switching to another model means adding an implementation, not touching business code. (The master prompt named Claude; the owner's B5 decision replaces this with Gemini behind the abstraction, and Claude can be added later as another provider.)
- **The AI proposes; the backend decides.** The AI never writes inventory, prices, payment status or order state. Tools call application services that apply their own rules and state machine; for example `confirm_checkout` only submits the customer's explicit confirmation, and the service validates, prices server-side, reserves stock and applies the transition (or rejects it). Payment verification, price changes, stock adjustments and policy changes have no AI tool at all.
- The backend injects `customer_id` from the verified Telegram identity; tool schemas contain no customer identifiers.
- Tools (as in the master prompt) use Pydantic inputs, authorization, typed outputs, error mapping, audit logs for state changes.
- Prices/stock shown to customers come from tool results only; tests check this.
- Conversation history persisted in `conversations` / `conversation_messages`; recent messages plus stored summary; survives restarts.
- Limits: per-user hourly and global daily request caps (`AI_PER_USER_HOURLY_LIMIT`, `AI_DAILY_REQUEST_LIMIT`), max tool iterations per turn, timeouts.
- Quota exhaustion (provider 429 or local cap): the bot sends a polite "busy, please try again shortly / the shop will follow up" message, records a handover request for the owner, and keeps non-AI paths (order status lookups by command, notifications) working. No retries that burn quota; exponential backoff with a cap.

## Background jobs
Celery + Redis: reservation expiry sweep, outbox dispatcher, Telegram update processing, shipment creation/tracking polls, preorder delay notifications. All tasks idempotent.

## Cross-cutting
Structured JSON logging that never records exception messages, strips query strings, and redacts known secrets on a best-effort basis (see SECURITY-PLAN.md), request ids, consistent error envelope, rate limiting (per Telegram user and per API key), Alembic migrations only (no `create_all` in production).

## Payments
`PaymentProvider` interface (Phase 3 abstraction): `initiate`, `verify`, `refund`. Implementations: `CodPaymentProvider` (first, fully automated: confirm after validation + explicit customer confirmation; subject to configurable COD limits and manual review above the review threshold) and `ManualPaymentProvider` (digital payments verified by the owner/staff through the admin API; no AI tool can mark a payment verified). No provider API is invented; real providers are added only after API access is confirmed (B2).

## Delivery abstraction
As specified in the master prompt. Phase 2-5 uses `OwnDeliveryProvider` and `MockCourierProvider`. Real Myanmar couriers are added after verification of available services (B3); each as its own adapter behind the same interface.

## Configurable policies
Policy values (docs/POLICIES.md) come from settings, never constants. Phase 3 adds an owner-editable `shop_settings` store with audit logging; environment variables bootstrap it.

## Hosting recommendation (B6; provider chosen later)
- Region: Singapore (closest low-latency region to Myanmar for most budget VPS providers). Specific providers and prices must be compared at purchase time; none is endorsed here.
- Size: 2 vCPU / 4 GB RAM / SSD is adequate for API + PostgreSQL + Redis + worker at launch scale; monitor memory (PostgreSQL and Celery are the main consumers).
- Docker Compose with a reverse proxy (Caddy or Nginx) terminating HTTPS with automatic certificates; Postgres and Redis not exposed publicly (compose binds them to localhost already); firewall allowing only 22 (key auth) and 80/443.
- Automated backups: nightly `pg_dump` to off-server storage (any S3-compatible bucket), retention policy set by the owner, plus a documented restore drill. Provider snapshots are an extra, not the only backup.
- Provider independence: nothing in the code depends on a specific cloud; configuration is by environment variables.

## Development environments
- Termux/Android (owner's primary): native PostgreSQL + Redis, venv, `scripts/termux/`. Uses plain `uvicorn` and `psycopg` (pure or C build) because uvloop/httptools and psycopg-binary wheels are unavailable on Android. Behaviour and tests are identical; real PostgreSQL is used so the Phase 3 concurrency tests stay meaningful.
- Docker Compose: PC/laptop and the production VPS, with `uvicorn[standard]` and `psycopg[binary]` for best performance.
