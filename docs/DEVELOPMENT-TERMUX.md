# Developing OutfitMaxing from a phone (Termux)

Docker cannot run on a normal (non-rooted) Android phone, so on Termux the same stack runs natively: PostgreSQL and Redis from Termux packages, Python in a virtual environment. Application code, tests, migrations and the API are identical to Docker/VPS; only the packaging differs. Production on a VPS still uses Docker Compose.

Differences on Termux (and why): `uvicorn` without the `[standard]` extra (uvloop/httptools do not build reliably on Android; plain asyncio + h11 is ample for one shop), `psycopg` pure-Python or C build instead of the prebuilt binary wheel (not published for Android), and ruff runs in CI instead of locally (optional). Nothing else is reduced: real PostgreSQL (needed for the Phase 3 concurrency tests), real Redis, same pinned application dependencies.

Install Termux from F-Droid or the official GitHub releases (the Play Store build is outdated).

## 1. One-time Termux and Git setup
```bash
pkg update -y && pkg upgrade -y
pkg install -y git openssh unzip nano
termux-setup-storage                       # allow storage access (taps "Allow"); creates ~/storage
git config --global user.name  "Your Name"
git config --global user.email "your-github-email@example.com"
git config --global init.defaultBranch main
ssh-keygen -t ed25519 -C "your-github-email@example.com"     # press Enter to accept defaults
cat ~/.ssh/id_ed25519.pub                  # long-press to copy
```
In GitHub (browser): Settings > SSH and GPG keys > New SSH key > paste. Then:
```bash
ssh -T git@github.com                      # answer "yes"; expect "Hi phop7173-oss!"
```
Never paste private keys or tokens into chats, files in the repo, or commits.

## 2. Get the repository and add the scaffold
Download `outfitmaxing-phase2-termux.zip` from the chat to your phone's Downloads folder.
```bash
cd ~
git clone git@github.com:phop7173-oss/OutfitMaxing.git
cd OutfitMaxing
git switch -c phase-2-foundation
unzip -o ~/storage/downloads/outfitmaxing-phase2-termux.zip     # files go to the repo root
chmod +x scripts/termux/*.sh
```

## 3. Install and run
```bash
bash scripts/termux/setup.sh        # once; first run may take 10-30+ minutes (compiles pydantic-core). Keep screen on.
bash scripts/termux/run-dev.sh      # starts PostgreSQL/Redis, migrations, API on 127.0.0.1:8000
```
Open a second Termux session (swipe in from the left edge > New session) and verify:
```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/ready
```
Expected: `200 {"status":"ok"}` and `200 {"status":"ok","database":"up"}`.

Tests (uses the separate `outfitmaxing_test` database):
```bash
bash scripts/termux/test.sh
bash scripts/termux/test.sh -k logging -x        # subset / stop at first failure
bash scripts/termux/lint.sh                      # optional
```
Stop/start background services:
```bash
bash scripts/termux/services.sh status
bash scripts/termux/services.sh stop
bash scripts/termux/services.sh start
```
`setup.sh` creates `.env` with a random local database password (file mode 600). `.env` is git-ignored: never commit it.

## 4. Edit, commit, push
```bash
nano backend/app/some_file.py        # Ctrl+O Enter = save, Ctrl+X = exit (optional: pkg install micro)
git status --short
git diff                             # review changes (q to quit)
git add -A                           # confirm no .env in `git status` first
git commit -m "Short description of the change"
git push -u origin phase-2-foundation        # first push of a branch
git push                                     # later pushes of the same branch
```
Open a Pull Request on GitHub (browser or GitHub app): "Compare & pull request" on the branch. CI runs lint and tests. Merge only after it is green. (Optional CLI: `pkg install gh && gh auth login && gh pr create --fill`.)

## 5. Keep your repo updated
```bash
git switch main
git pull --rebase
git switch -c phase-3-catalog        # one branch per task or phase
```
Update an existing branch with new `main` changes: `git switch my-branch && git rebase main`.
Undo local edits to a file: `git restore path/to/file`. See history: `git log --oneline -10`.

## 6. Phone-specific tips
- Keep Termux alive: `termux-wake-lock`, disable battery optimisation for Termux, and on Android 12+ turn off "child process restrictions" in Developer options if your phone offers it; otherwise Android may kill PostgreSQL in the background. If it dies: `bash scripts/termux/services.sh start`.
- Use `tmux` (`pkg install tmux`) to keep long sessions.
- Phase 4 note: Telegram webhooks need a public HTTPS address; a phone is not a good production host. Use a VPS (or a temporary tunnel) for webhook testing. Decision U12 in IMPLEMENTATION-PLAN.md.
