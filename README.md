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
../.venv/bin/python manage.py runserver 127.0.0.1:8000
```

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
