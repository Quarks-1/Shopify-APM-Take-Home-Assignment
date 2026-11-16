# Shopify APM Take-Home Assignment

Full-stack starter workspace combining a Django JSON API backend with a Vite, React, TypeScript, and BoardUI frontend.

## Project Structure

- `backend/` houses the Django 5.2 project (`config`) and starter JSON API app (`api`).
- `frontend/` houses the Vite, React, TypeScript, and Tailwind CSS v4 frontend built with source-owned BoardUI components.
- `requirements.txt` pins the Python backend dependencies for `.venv`.

## Running Locally

### Backend

Create or use the virtual environment at the repository root, apply migrations, and start the Django development server on port 8000.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd backend
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py seed_demo_data
../.venv/bin/python manage.py runserver 127.0.0.1:8000
```

`seed_demo_data` is safe to rerun. It only inserts demo spaces and objects when the database has no spaces yet.

Run backend system checks and unit tests from `backend/`.

```bash
cd backend
../.venv/bin/python manage.py check
../.venv/bin/python manage.py test
```

### Frontend

Install npm packages in `frontend/` and start the Vite development server on port 5173. Requests to `/api/*` proxy automatically to `http://127.0.0.1:8000`.

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

## Spaces workflow

1. Open the Vite app at `http://127.0.0.1:5173/`. The default hash is `#overview`, which lists spaces from `GET /api/spaces/`.
2. Choose **Open in editor** on a card, or use the sidebar **Space editor** link (`#space-editor`). Cards deep-link with `#space-editor/<space-id>`.
3. Right-click an object on the floor plan to edit attributes end-to-end against the Django JSON API.

If the overview is empty after migrate, run `../.venv/bin/python manage.py seed_demo_data` from `backend/`.
