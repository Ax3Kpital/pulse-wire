#!/usr/bin/env bash
# Full verification: lint, types and tests for backend and frontend.
source "$(dirname "$0")/_env.sh"
echo "== backend: ruff";   (cd "$REPO/backend" && "$REPO/.venv/bin/ruff" check app tests && "$REPO/.venv/bin/ruff" format --check app tests)
echo "== backend: mypy";   (cd "$REPO/backend" && "$REPO/.venv/bin/mypy" app)
echo "== backend: pytest"; (cd "$REPO/backend" && "$PY" -m pytest -q -p no:warnings)
echo "== frontend: tsc";   (cd "$REPO/frontend" && npx tsc -b --noEmit)
echo "== frontend: vitest";(cd "$REPO/frontend" && npx vitest run)
echo "All checks passed."
