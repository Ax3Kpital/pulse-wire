# Shared helpers for scripts/*.sh (sourced, not executed).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
# Prefer a project-local Node (scripts/setup.sh installs one into .tools/ if none is on PATH).
if [ -x "$REPO/.tools/node/bin/node" ]; then
  export PATH="$REPO/.tools/node/bin:$PATH"
fi
