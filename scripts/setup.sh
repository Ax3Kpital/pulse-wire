#!/usr/bin/env bash
# One-time setup: Python venv + backend deps, Node (local if missing), frontend build, .env.
source "$(dirname "$0")/_env.sh"
cd "$REPO"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
[ -d .venv ] || "$PYTHON_BIN" -m venv .venv
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e "backend[dev,extract]"

if ! command -v node >/dev/null 2>&1; then
  echo "Node not found: installing the official LTS binary into .tools/ (checksum verified)"
  ARCH="$(uname -m)"; [ "$ARCH" = "x86_64" ] && ARCH="x64"
  V="$(curl -fsSL https://nodejs.org/dist/index.json | "$PY" -c 'import json,sys; print(next(x["version"] for x in json.load(sys.stdin) if x["lts"]))')"
  mkdir -p .tools && cd .tools
  TAR="node-$V-darwin-$ARCH.tar.gz"
  curl -fsSLO "https://nodejs.org/dist/$V/$TAR"
  curl -fsSL "https://nodejs.org/dist/$V/SHASUMS256.txt" | grep " $TAR\$" | shasum -a 256 -c -
  tar xzf "$TAR" && rm "$TAR" && ln -sfn "node-$V-darwin-$ARCH" node
  cd "$REPO"
  export PATH="$REPO/.tools/node/bin:$PATH"
fi

(cd frontend && npm ci --no-fund --no-audit && npm run build)
[ -f .env ] || cp .env.example .env
mkdir -p data logs backups
echo "Setup complete. Start with: scripts/start.sh  (then open http://127.0.0.1:8765)"
