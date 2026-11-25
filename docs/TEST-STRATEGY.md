# OutfitMaxing — Test Strategy

## Principles
- Pytest. Tests that touch inventory or orders run against a real PostgreSQL (Docker service), never SQLite, because locking and constraint behaviour must be genuine.
- Every business rule has an automated test. Failing tests are fixed, never disabled.
- Each test isolated via transaction rollback or fresh schema; Alembic migrations tested by running them on an empty database in CI.
- The AI provider is replaced by a deterministic fake in unit/integration tests; a small, separately-marked live suite may run manually with a real key.

## Layers
1. Unit: domain rules, state transitions, money calculations.
2. Integration: services + PostgreSQL.
3. API: FastAPI TestClient with auth and validation.
4. Concurrency: multiple real DB connections/threads.
5. AI behaviour: scripted tool-call scenarios with the fake model.

## Matrix (from the master prompt)
**Inventory:** reservation success; insufficient stock; concurrent last-item purchase (N parallel attempts, exactly one wins, no negatives); expired reservation ignored and swept; release; no double deduction on repeated commit; preorder capacity limit and no conversion to physical stock.

**Orders:** valid checkout; invalid checkout (empty cart, bad variant, expired offer); duplicate checkout with same idempotency key returns same order; price change after order leaves order snapshot unchanged; COD confirmation requires explicit customer confirmation; unverified digital payment rejected; cancellation releases stock; partial fulfilment; illegal state transitions rejected.

**Delivery:** quote calculation by zone; provider unavailable handled; duplicate shipment request is idempotent; tracking update changes status and emits event; failed delivery and return-to-sender.

**AI:** product search; natural-language discovery (Burmese and English fixtures); tool input validation; unauthorized operation rejected; customer isolation (customer A cannot read B); no fabricated stock/prices (answers must match tool results); tool-loop limit.

**Telegram:** invalid secret rejected; duplicate update ignored; failed send retried via outbox; restart keeps conversation.

**Security:** role checks on admin API, rate limiting, log redaction, no secrets in repo (CI scan).

## CI
GitHub Actions: lint, type check, tests with Postgres and Redis services, migration check, dependency audit. Coverage reported; thresholds set once Phase 3 stabilises.

## Added by owner decisions
- AI: quota exhaustion returns the graceful message and creates a handover; AI tools cannot change inventory, price, payment verification or order state (assert no such tool is registered and services reject direct state requests that skip rules).
- Payments: COD limit and manual-review threshold behaviour with configurable values; unset policy refuses/escalates instead of guessing; manual verification requires an authorized actor.
- Policies: no hardcoded policy constants (test that settings default to unset).

## Phase 2 review additions
Fake, recognisable secrets (database password, API key, Telegram-token-shaped string, and an unknown-format string) are injected into exception messages, including chained exceptions and query strings, and the tests assert they appear in neither captured logs nor HTTP responses, while the exception type, path and request id still appear. Also covered: readiness-check failure logging, uvicorn logger routing, access-log query stripping, and the documented best-effort limitation. A CI step fails if a `.env` file is tracked.

## Phase 2 tests (delivered)
Health liveness/readiness (incl. DB-down path), configuration defaults and production guard, log redaction, constant-time secret comparison. A real-Postgres readiness test runs when `TEST_DATABASE_URL` is set (CI sets it).

## Running tests on Termux
`bash scripts/termux/test.sh` runs pytest against the local `outfitmaxing_test` database (real PostgreSQL). Lint runs in CI (and optionally via `scripts/termux/lint.sh`).

## CI jobs
- `backend` (Python 3.12, PostgreSQL 16): `.env` tracking check, ruff, `alembic upgrade head`, pytest. This is the merge gate.
- `backend-py314` (informational, may fail without blocking): same steps on Python 3.14, the version Termux runs, to surface version drift early.
- `docker-build`: builds the API image without pushing it.
- Locally on Termux, ruff is usually not installable: `bash scripts/termux/lint.sh` runs the stdlib line-length check (same E501 limit); CI remains the authority for ruff.

## Catalog tests (Phase 3 task 1)
`test_catalog_schemas.py` (no database), `test_catalog_service.py` (real PostgreSQL, savepoint rollback per test: category rules and the excluded-category guard, public codes, variant uniqueness incl. NULL size/color, status rules, listing/search/pagination, database-level constraints), `test_catalog_api.py` (auth, 503 when no key, end-to-end flow, hidden inactive variants, error envelope, no internal ids), `test_config_admin.py`.

## Offer tests (Phase 3 task 2a)
`test_offer_schemas.py` (strict whole-kyat prices, compare-at, timezones), `test_offers_service.py` (time-window price resolution, overlap and adjacency matrix, cancel, history, database constraints, price-dependent publishing and visibility), `test_offers_api.py`, and `test_offers_concurrency.py`: six simultaneous overlapping offers from separate connections with real commits; exactly one must win and one active offer must remain (the test cleans up its own rows).

## Inventory tests (task 2b)
`test_inventory_schemas.py` (strict whole-number quantities, notes, labels), `test_inventory_service.py` (receive/adjust rules, reserve/release/commit, expiry, sweeper, idempotency, movement-sum invariant, database constraints), `test_inventory_api.py` (auth, validation, public labels, preorder never in stock), `test_inventory_concurrency.py` with real commits: 20 buyers for the last item (exactly one wins), 40 buyers for 10 units (exactly 10 win; reserved equals active holds), 6 simultaneous commits of one reservation (deducted once), and an expiry-versus-new-buyers race (never oversells). Committed test rows are removed by the `make_committed_variant` fixture.

## Preorder tests (task 2c)
`test_supplier_schemas.py`, `test_suppliers_service.py` (capacity rules, Myanmar-date windows, supplier choice, idempotency, release, overdue flags and one-time notifications), `test_sourcing_service.py` (deposit gate, status order, oldest-first allocation incl. shortfall, surplus handling, fail/cancel/retry, 3-day owner alert), `test_suppliers_api.py` (auth, validation, full flow, public labels), and `test_preorder_concurrency.py` with real commits: 20 customers for 5 units (exactly 5 win), 5 simultaneous requests (one bundle), 6 simultaneous releases (capacity returned once).
