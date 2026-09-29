#!/usr/bin/env bash
# ChainTrace - macOS / Linux launcher: first run creates the environment, later runs just start.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "Python 3.11+ is required"; exit 1; }
"$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || { echo "Python 3.11 or newer is required"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
# Reinstall whenever requirements.txt differs from the copy made at the last install.
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Installing dependencies, this takes a few minutes the first time..."
  pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
  cp requirements.txt .venv/requirements.installed
fi
[ -f .env ] || cp .env.example .env
# run.py picks a usable port and opens the browser once the server answers.
exec python run.py
