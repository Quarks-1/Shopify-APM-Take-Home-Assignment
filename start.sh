#!/usr/bin/env bash
# Pull latest, sync deps, migrate, then (re)start the API and web dev servers.
set -euo pipefail

if [ -t 1 ]; then
  BLUE=$'\033[1;34m'; YEL=$'\033[1;33m'; RED=$'\033[1;31m'; GRN=$'\033[1;32m'; OFF=$'\033[0m'
else
  BLUE=""; YEL=""; RED=""; GRN=""; OFF=""
fi

step() { echo "${BLUE}==>${OFF} $*"; }
warn() { echo "${YEL}warning:${OFF} $*" >&2; }
die()  { echo "${RED}error:${OFF} $*" >&2; exit 1; }

# Everything procedural lives in main() so that bash parses the whole body
# before running it. `git pull` may rewrite this file mid-run; without this
# wrapper bash could read a half-updated script.
main() {
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$ROOT"

  # Per-checkout ports (main = 5173/8000, linked worktrees get their own slot).
  command -v node >/dev/null 2>&1 || die "node is required (it resolves this checkout's dev ports)"
  eval "$(node scripts/dev-ports.mjs --shell)"
  # The script path resolves symlinks; keep ROOT from the eval for consistency.
  cd "$ROOT"

  # --- 1. Pull -----------------------------------------------------------------
  step "Pulling latest changes"
  OLD="$(git rev-parse HEAD)"
  if [ "$IS_WORKTREE" = 1 ]; then
    echo "Linked worktree: skipping pull"
    NEW="$OLD"
  elif ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    echo "No upstream branch configured: skipping pull"
    NEW="$OLD"
  else
    if ! git diff --quiet HEAD --; then
      warn "uncommitted changes to tracked files; attempting pull anyway"
    fi
    git pull --ff-only || die "git pull failed; servers left untouched"
    NEW="$(git rev-parse HEAD)"
  fi
  if [ "$OLD" = "$NEW" ]; then
    echo "Already up to date"
  else
    git log --oneline "$OLD..$NEW"
  fi

  # --- 2. Dependencies ---------------------------------------------------------
  changed() { ! git diff --quiet "$OLD" "$NEW" -- "$@"; }

  step "Checking dependencies"
  fresh_venv=0
  if [ -x "$ROOT/.venv/bin/python" ]; then
    VENV="$ROOT/.venv"
  elif [ "$IS_WORKTREE" = 1 ] && [ -x "$MAIN_ROOT/.venv/bin/python" ]; then
    VENV="$MAIN_ROOT/.venv"
    echo "Using the main checkout's virtualenv"
  else
    echo "Creating virtualenv"
    VENV="$ROOT/.venv"
    python3 -m venv "$VENV"
    fresh_venv=1
  fi
  if [ "$fresh_venv" = 1 ] || changed requirements.txt; then
    echo "Installing Python dependencies"
    "$VENV/bin/pip" install -q -r requirements.txt
  fi
  if [ ! -d frontend/node_modules ] || changed frontend/package.json frontend/package-lock.json; then
    echo "Installing Node dependencies"
    (cd frontend && npm install)
  fi

  # --- 3. Migrations -----------------------------------------------------------
  # Also seeds a worktree's database from the main checkout on first run.
  step "Running migrations"
  node scripts/dev.mjs prepare

  # --- 4. Stop old servers -----------------------------------------------------
  listeners() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; }

  step "Stopping old servers"
  victims=""
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    for pid in $(listeners "$port"); do
      victims="$victims $pid"
      if [ "$port" = "$BACKEND_PORT" ]; then
        ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
        if [ -n "$ppid" ] && ps -o command= -p "$ppid" 2>/dev/null | grep -q "manage.py runserver"; then
          victims="$victims $ppid"
        fi
      fi
    done
  done
  if [ -n "$victims" ]; then
    echo "Stopping PIDs:$victims"
    # shellcheck disable=SC2086
    kill $victims 2>/dev/null || true
    for _ in $(seq 1 25); do
      [ -z "$(listeners "$BACKEND_PORT")$(listeners "$FRONTEND_PORT")" ] && break
      sleep 0.2
    done
    left="$(listeners "$BACKEND_PORT") $(listeners "$FRONTEND_PORT")"
    if [ -n "${left// /}" ]; then
      echo "Force killing:$left"
      # shellcheck disable=SC2086
      kill -9 $left 2>/dev/null || true
    fi
  else
    echo "Nothing running"
  fi

  # --- 5. Start servers --------------------------------------------------------
  # Job control gives each background job its own process group, so we can kill
  # a whole server tree with `kill -- -PGID`.
  set -m
  API_PID=""; WEB_PID=""
  cleaned=0

  cleanup() {
    [ "$cleaned" = 1 ] && return
    cleaned=1
    trap '' INT TERM
    echo
    step "Shutting down"
    for pid in $API_PID $WEB_PID; do
      kill -TERM -- "-$pid" 2>/dev/null || true
    done
    for _ in $(seq 1 25); do
      [ -z "$(listeners "$BACKEND_PORT")$(listeners "$FRONTEND_PORT")" ] && break
      sleep 0.2
    done
    for pid in $API_PID $WEB_PID; do
      kill -KILL -- "-$pid" 2>/dev/null || true
    done
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  step "Starting servers"
  (exec node scripts/dev.mjs backend </dev/null 2>&1 | sed -u 's/^/[api] /') &
  API_PID=$!
  (exec node scripts/dev.mjs frontend </dev/null 2>&1 | sed -u 's/^/[web] /') &
  WEB_PID=$!

  echo
  echo "${GRN}Ready${OFF}"
  echo "  Frontend: http://localhost:$FRONTEND_PORT"
  echo "  API:      http://127.0.0.1:$BACKEND_PORT"
  echo "  Ctrl+C to stop"
  echo

  # Block until either server exits, then tear down the other.
  while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
    sleep 1
  done
  echo "${RED}A server exited unexpectedly${OFF}" >&2
  exit 1
}

main "$@"; exit $?
