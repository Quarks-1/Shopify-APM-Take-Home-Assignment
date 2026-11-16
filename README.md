# Shopify APM Take-Home Assignment

Full-stack starter workspace combining a Django JSON API backend with a Vite, React, TypeScript, and BoardUI frontend.

This is a single-user local app. It has no login, accounts, sessions, or Django admin. All data lives in the SQLite database at `backend/db.sqlite3`.

## Project Structure

- `backend/` houses the Django 5.2 project (`config`) and starter JSON API app (`api`).
- `frontend/` houses the Vite, React, TypeScript, and Tailwind CSS v4 frontend built with source-owned BoardUI components.
- `requirements.txt` pins the Python backend dependencies for `.venv`.

## Running Locally

Run `./start.sh` to pull, sync dependencies, migrate and (re)start both dev servers, or start `frontend` / `backend` from the app's preview configs (`.claude/launch.json`).

Ports are per checkout, so the main checkout and each linked git worktree (`.claude/worktrees/<name>/`) can run side by side:

- Main checkout: frontend 5173, backend 8000.
- Worktree slot N: frontend 5173+N, backend 8000+N. Slots are remembered in `.git/dev-ports.json` (shared by all worktrees).
- `node scripts/dev-ports.mjs` prints this checkout's ports (`--json` and `--shell` for scripts).
- `.claude/launch.json` is generated, not committed: the SessionStart hook in `.claude/settings.json` and every `node scripts/dev.mjs` run rewrite it with this checkout's ports.
- A worktree reuses the main checkout's `.venv` if it has none, installs its own `frontend/node_modules`, and gets its own `backend/db.sqlite3`, seeded from main's on first backend start and independent afterwards.

### Backend

Create or use the virtual environment at the repository root, apply migrations, and start the Django development server (port 8000 in the main checkout).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd backend
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Run backend system checks and unit tests from `backend/`.

```bash
cd backend
../.venv/bin/python manage.py check
../.venv/bin/python manage.py test
```

#### Searcher

`POST /api/objects/<id>/searches/` starts a background product search for an object; poll `GET /api/searches/<run id>/` until `status` is `done`, `empty`, `failed` or `canceled`. Accepted candidates come back best first; add `?include=all` to see rejected and filtered ones. `POST /api/searches/<run id>/cancel/` stops a run and `POST /api/objects/<id>/select/` with `{"candidateId": …}` picks one. `GET /api/overview/` lists every route.

- Each run is a worker process (`manage.py run_search <run id>`) that drives a headless Claude Code session (`claude -p`, model `sonnet`) through the `searcher next / judge / status` commands. The session uses your Claude subscription login (no API key) with a scrubbed environment, an empty working directory outside the repo (`SEARCHER_AGENT_CWD`, default `$TMPDIR/shopify-searcher/agent-cwd`) and `--safe-mode`, so no CLAUDE.md, memory, hooks or plugins load.
- If the agent fails, times out or can't start before reaching the target, the deterministic loop takes over from where it stopped (`SEARCHER_FALLBACK=none` disables this). `SEARCHER_AGENT=deterministic` skips the agent entirely.
- At most `SEARCHER_MAX_CONCURRENT` (2) runs execute at once; the rest queue. The agent gets `SEARCHER_RUN_TIMEOUT` (420 s) of wall clock and `SEARCHER_MAX_TURNS` (40) turns.
- Logs, the agent's raw JSON output and the generated `searcher` wrapper live in `backend/var/` (`SEARCHER_VAR_DIR`): see `backend/var/searches/<run id>.log`.
- SQLite runs in WAL mode so the server, the workers and the agent's commands can share `db.sqlite3`. Tests use a file database (`backend/test_db.sqlite3`); pass `--noinput` if an interrupted run left one behind.

### Frontend

Install npm packages in `frontend/` and start the Vite development server (port 5173 in the main checkout). Requests to `/api/*` proxy automatically to this checkout's backend port (8000 in the main checkout).

```bash
cd frontend
npm install
npm run dev
```

Run TypeScript checks and build the production bundle from `frontend/`.

```bash
cd frontend
npm run typecheck
npm run build
```
