#!/usr/bin/env bash
# Development: backend with auto-reload + Vite dev server (http://127.0.0.1:5173).
source "$(dirname "$0")/_env.sh"
cd "$REPO/backend"
"$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765 --reload &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT
cd "$REPO/frontend" && npm run dev
