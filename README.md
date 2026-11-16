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
