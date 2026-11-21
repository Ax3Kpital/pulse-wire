#!/usr/bin/env bash
# Run the app in the foreground (API + built UI on http://127.0.0.1:8765).
source "$(dirname "$0")/_env.sh"
cd "$REPO/backend"
exec "$PY" -m app serve
