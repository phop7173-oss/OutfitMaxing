# OutfitMaxing — Implementation Plan

Status: Phase 1 documents approved as baseline (with the clarifications below). Phase 2 scaffold delivered; Phases 3+ not started.

## Guiding rules
- Modular monolith; PostgreSQL is the single source of truth.
- Small, verifiable increments; every business rule ships with automated tests.
- No invented business policies: policies are configurable and pending approval (docs/POLICIES.md).
- Integrations without credentials/documentation get a clearly labelled mock adapter marked PENDING.

## Phases
| Phase | Scope | Status | Exit criteria |
|---|---|---|---|
| 0 | Repo inspection | Done (greenfield) | |
| 1 | Planning docs | Approved | |
| 2 | Foundation scaffold: FastAPI app, config, SQLAlchemy base, Alembic (no tables), health endpoints, logging, pytest harness, Docker Compose (api, postgres, redis), `.gitignore`, `.env.example`, CI | Delivered, **not yet executed** (see below) | `docker compose up` works; CI green on GitHub |
| 3 | Business core: products, variants, offers, suppliers, inventory, reservations, orders, checkout, payments abstraction + COD, `shop_settings`; adds Celery worker | Not started | Inventory + order test matrix green incl. concurrent last-item test |
| 4 | Telegram + AI provider abstraction (Gemini first), tools, conversation persistence | Not started | AI + Telegram matrices green |
| 5 | Delivery abstraction, own delivery, mock courier, tracking, manual digital-payment verification flow | Not started | Delivery matrix green |
| 6 | Admin API, operational controls, security tests, deployment + backup/restore docs | Not started | Security checklist passed; restore drill documented |

**Phase 2 code-review fixes applied:** (1) unexpected-error logging no longer records exception messages or traceback text, with tests; (2) Docker development setup and `.env` handling documented in README. See SECURITY-PLAN.md for what is and is not guaranteed.

**Development environment change (owner uses a phone with Termux only):** Docker is not available there, so local development runs PostgreSQL and Redis natively in Termux (`scripts/termux/`, docs/DEVELOPMENT-TERMUX.md). Dependencies are split into portable `requirements.txt` plus `requirements-docker.txt` (uvicorn[standard], psycopg binary) used by Docker, CI and the VPS. Application architecture and quality targets are unchanged; production remains Docker Compose on a VPS.

**Phase 2 verification caveat:** the authoring sandbox had no network access, so dependencies could not be installed and tests could not be run. Files were only syntax-checked (Python compile, YAML parse). The first CI run on GitHub, or `pytest` on your machine, is the real verification. Pinned versions in `backend/requirements*.txt` are conservative choices from memory and must be confirmed by that install.

## Approved decisions (owner)
| ID | Decision |
|---|---|
| B1 | Single-seller shop; no marketplace. Offers have no seller dimension. |
| B2 | COD is first fully automated method. Payment-provider abstraction. Digital payments manually verified by the owner until a provider/API is confirmed. No invented payment APIs. |
| B3 | Mock courier for dev; courier abstraction supporting multiple providers; real courier chosen after verifying Myanmar services. |
| B4 | Policies configurable and pending approval; see docs/POLICIES.md. |
| B5 | Gemini API (free tier initially) behind an AI provider abstraction; AI cannot directly control inventory, pricing, payment verification or order state; usage limits and graceful quota handling. |
| B6 | Low-cost Singapore-region VPS, ~2 vCPU / 4 GB RAM, Docker Compose, PostgreSQL, HTTPS, automated backups, provider-independent. Provider and domain chosen before production. |

## Remaining unresolved decisions (before the relevant phase)
| # | Decision | Needed by |
|---|---|---|
| U1 | Final business approval of every policy value in docs/POLICIES.md | Before launch (code needs only the settings mechanism in Phase 3) |
| U2 | COD value limit and manual-review threshold amounts (MMK) | Phase 3 tests use configurable values; real values before launch |
| U3 | Delivery zones, their fees, and which regions are COD-eligible | Phase 5 |
| U4 | Preorder deposit mechanics: how a 30% deposit is collected if digital payments are manual, and refund/forfeit rules if a preorder is cancelled | Phase 3 (preorder checkout) |
| U5 | Order states for payment failure and partial cancellation (spec list lacks them) | Phase 3 |
| U6 | Manual digital-payment verification: who verifies, what evidence, how the customer is told | Phase 5 |
| U7 | Gemini model name; confirm current free-tier limits and data-use terms (see SECURITY-PLAN) | Phase 4 |
| U8 | Admin authentication method (API key vs login) and staff roles | Phase 6 |
| U9 | VPS provider, domain, backup storage location | Before production |
| U10 | Customer data retention/deletion rules and legal requirements in Myanmar | Before launch |
| U12 | How to test Telegram webhooks (public HTTPS needed): VPS staging, a temporary tunnel, or dev-only long polling | Phase 4 |
| U11 | Burmese search approach (Unicode vs Zawgyi input handling) | Phase 4 |

## Next task (after you review the scaffold)
1. Run CI / local tests and report the result so Phase 2 can be marked verified.
2. Then Phase 3 task 1: product, category and variant models + migrations + catalog API, with tests.
