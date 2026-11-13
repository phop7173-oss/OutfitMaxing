# OutfitMaxing

Telegram-first fashion shop for Myanmar: a public channel, an AI sales assistant bot, and a backend API (single source of truth). Single-seller shop, not a marketplace.

**Status:** Phase 2 (foundation scaffold). No business features yet. See `docs/` for the plan, architecture, database design, security plan and test strategy, and `docs/POLICIES.md` for business policies that are still pending approval.

## Option A: Termux on an Android phone (primary development path)
Full instructions, Git/GitHub workflow and tips: **[docs/DEVELOPMENT-TERMUX.md](docs/DEVELOPMENT-TERMUX.md)**.
```bash
bash scripts/termux/setup.sh      # once: packages, venv, PostgreSQL, Redis, local .env (random password)
bash scripts/termux/run-dev.sh    # API on http://127.0.0.1:8000
bash scripts/termux/test.sh       # tests against local PostgreSQL
curl -i http://127.0.0.1:8000/health && curl -i http://127.0.0.1:8000/health/ready
```
On Termux you do not use `cp .env.example .env`: `setup.sh` creates `.env` for you with a random local password. `.env` is git-ignored; never commit it or any real credentials.

## Option B: Docker (PC, laptop or VPS; not available on a normal Android phone)

Requirements: Docker with the Compose plugin (`docker compose version` should work).

### 1. Create your local `.env`
```bash
cp .env.example .env
```
(Windows PowerShell: `Copy-Item .env.example .env`.)

The `api` service reads `.env`, and Compose also reads it for `POSTGRES_PASSWORD`. Without it, `docker compose up` fails with an "env file ... not found" error.

**The credentials in `.env.example` (for example `change-me`) are example values for local development only.** Use them only on your own machine. Do not reuse them anywhere reachable from the internet; the app refuses to start in `APP_ENV=production` with them. `.env` is git-ignored: **never commit `.env` or any real credentials** (CI fails if a `.env` file is tracked). Real tokens (Telegram, AI provider) go only in your own untracked `.env` or your server's secret store, and are not needed for Phase 2.

### 2. Start
```bash
docker compose up --build        # foreground, Ctrl+C to stop
# or detached:
docker compose up --build -d
docker compose logs -f api       # follow the API logs when detached
```
Services: `db` (PostgreSQL 16), `redis`, `api` (http://127.0.0.1:8000). Ports are bound to `127.0.0.1` only. The API container runs `alembic upgrade head` (no migrations yet) and then uvicorn.

### 3. Verify
```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/ready
```
- `/health` returns `200` with `{"status":"ok"}` (the process is up; no dependencies checked).
- `/health/ready` returns `200` with `{"status":"ok","database":"up"}` when PostgreSQL is reachable, and `503` with `{"status":"unavailable","database":"down"}` otherwise.
- Both responses carry an `X-Request-ID` header.

### 4. Stop
```bash
docker compose stop              # stop containers, keep them and the database data
docker compose down              # remove containers, KEEP the database volume
docker compose down -v           # remove containers AND the database volume (deletes local dev data)
```
If you change `POSTGRES_PASSWORD` after the database was first created, run `docker compose down -v` once so PostgreSQL re-initialises with the new password.

## Tests (without Docker for the app, any Linux/macOS/Windows machine)
```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate                    # Windows: .venv\Scripts\activate
pip install -r requirements-docker.txt -r requirements-dev.txt ruff==0.8.4
ruff check .
pytest -q                                    # tests that need PostgreSQL are skipped
```
To include the real-PostgreSQL test, start the database and point the tests at it:
```bash
docker compose up -d db
export TEST_DATABASE_URL=postgresql+psycopg://outfitmaxing:change-me@localhost:5432/outfitmaxing
export DATABASE_URL=$TEST_DATABASE_URL       # use the POSTGRES_PASSWORD from your .env
pytest -q
```

## Logging and secrets
Logs are JSON on stdout. Exception messages and traceback text are never logged (only exception types and code locations), query strings are stripped from access logs, and configured secrets plus common credential patterns are redacted from log messages on a best-effort basis. Redaction is **not** a guarantee for secrets in arbitrary formats, so never log secrets or personal data. Details: `docs/SECURITY-PLAN.md`.
