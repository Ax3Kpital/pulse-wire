#!/usr/bin/env bash
# Restore a backup. Stop the app (or the LaunchAgent) first.
# Usage: scripts/restore.sh backups/news-YYYYmmdd-HHMMSS.db
source "$(dirname "$0")/_env.sh"
SRC="${1:?usage: scripts/restore.sh <backup.db>}"
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "The app is running on :8765 — stop it first (scripts/launchagent.sh stop)." >&2; exit 1
fi
mkdir -p "$REPO/data"
[ -f "$REPO/data/news.db" ] && mv "$REPO/data/news.db" "$REPO/data/news.db.before-restore-$(date +%s)"
rm -f "$REPO/data/news.db-wal" "$REPO/data/news.db-shm"
cp "$SRC" "$REPO/data/news.db"
echo "Restored $SRC -> data/news.db"
