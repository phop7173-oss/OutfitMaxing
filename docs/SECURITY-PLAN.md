# OutfitMaxing — Security Plan

## Assets
Customer identity and addresses, order and payment data, inventory and pricing integrity, API keys and bot token, owner admin access.

## Threats and controls
| Threat | Control |
|---|---|
| Forged Telegram webhook calls | Verify secret token header; reject otherwise; HTTPS only |
| Replay / duplicate updates | `update_id` uniqueness; idempotent processing |
| Prompt injection via customer text or product data | Tools enforce authorization server-side; model has no privileged tools; tool outputs treated as data; no tool for policy changes, SQL, or other customers |
| Cross-customer data leak | `customer_id` injected by backend from verified Telegram identity; every query scoped; isolation tests |
| Price / stock fabrication | Prices and stock only from tool results; server-side recomputation at checkout; tests |
| Fake payment proof | Screenshots never verify payment; only provider-verified callbacks/API |
| Overselling / race conditions | Conditional updates, DB constraints, concurrency tests |
| Admin API abuse | API keys or JWT, role-based authorization (owner, staff, read-only), audit logs, rate limiting |
| Secret leakage | Env-based secrets, `.env` in `.gitignore`, `.env.example` placeholders only, GitHub secret scanning (repo is public), log redaction |
| Abuse / cost exhaustion | Per-user rate limits, max tool iterations, max message length, daily AI spend cap |
| Data loss | Backups plus tested restore |

## Logging: what is guaranteed and what is not
Enforced by construction (tests in `test_logging_security.py` and `test_exception_logging.py`):
- Exception messages and traceback text are never logged. Unexpected errors log only the exception type chain, code locations (file, line, function), HTTP method, path without query string, and request id. Local variables are never logged.
- Unexpected errors are handled in middleware and answered with a generic 500 plus request id, so the server does not log the raw exception afterwards. As a second line of defence, uvicorn's loggers are routed through the same JSON formatter (so even a re-raised exception is sanitised).
- Query strings are stripped from uvicorn access-log lines.

Best effort only (NOT guaranteed): redaction of secrets inside ordinary log messages and extra fields. It covers the exact values of configured secrets (AI key, Telegram token and webhook secret, database and Redis passwords), credentials in URLs, bearer tokens, `key=value` pairs with sensitive names, Telegram bot tokens, and Google/`sk-` style keys. A secret in an unrecognised format interpolated into a log message would be logged; a test documents this limitation. Rule: never put secrets or personal data into log messages.

Other remaining logging risks: third-party libraries that write directly to stdout/stderr outside the logging module, and Docker/host-level logs outside the app's control.

## Rules
- Never log tokens, passwords, payment secrets, or full addresses/phones unless required; redact by default.
- Application DB role has no DDL rights in production and no UPDATE/DELETE on append-only tables.
- Dependency pinning plus automated vulnerability audit in CI.
- Error responses reveal no internals.
- Personal data: collect the minimum; deletion/export procedure defined before go-live (legal requirements to be confirmed by the owner).

## AI-provider specific risks (Gemini free tier)
- Data use: free tiers of hosted AI APIs commonly allow the provider to use submitted content to improve its products. This must be verified in Google's current terms before real customers are served. Until then, send the model the minimum: no phone numbers, full addresses or payment details in prompts; use order/variant ids and item names only. Consider moving to a paid tier or another provider before launch if the terms are unsuitable (U7).
- Quota exhaustion and outages: handled by caps, backoff and owner handover (ARCHITECTURE, AI layer). Free-tier limits change; confirm current limits before launch.
- The AI has no tool for inventory, pricing, payment verification, policy or order-state changes.

## Manual payment verification
Only staff/owner roles can mark a digital payment verified, with actor and evidence note recorded in `audit_logs`. The AI can tell customers the payment is awaiting verification, never that it is verified.

## Roles
- `owner`: everything including prices, policies, exceptional refunds.
- `staff`: orders, inventory adjustments, shipments (scope to be confirmed).
- `bot` (service identity): only the whitelisted application services behind AI tools.

## Open items
- Admin authentication method (API key vs. login) to be confirmed in Phase 6.
- Data retention periods to be set by the owner.

## Development on a phone (Termux)
- Local PostgreSQL and Redis bind to 127.0.0.1 only; local socket access is trust-authenticated (single-user device), TCP requires a password. Use a device lock screen and encryption.
- `.env` is created with a random password and mode 600; it is git-ignored and CI fails if a `.env` file is tracked.
- Use SSH keys for GitHub; never store tokens in the repository or paste them into chats. Do not run the production system or hold production secrets on the phone.

## Interim admin key (catalog task)
Catalog write endpoints need `X-Admin-Key` (`ADMIN_API_KEY`, min 16 chars, compared in constant time, registered for log redaction). It fails closed when unset. It is a single shared key with no roles and no rate limiting; it is replaced by login plus role-based authorization in Phase 6, and rate limiting is still missing (see audit). Validation errors never echo submitted values.
