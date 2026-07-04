#!/bin/bash
set -euo pipefail

APP_DIR="/home/damai/projects/website-collect-bot"
BRANCH="main"

cd "${APP_DIR}"

git pull --rebase --autostash origin "${BRANCH}"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
fi
"${PYTHON_BIN}" - <<'PY'
import sqlite3

with sqlite3.connect("data/sites.sqlite3") as conn:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
PY

git add data/sites.sqlite3

if git diff --cached --quiet -- data/sites.sqlite3; then
    echo "No SQLite changes to commit."
    exit 0
fi

git commit -m "Backup SQLite data $(date -u +%Y-%m-%d)"
git push origin "${BRANCH}"
