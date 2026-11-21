#!/usr/bin/env bash
# Consistent online backup of the SQLite database (safe while the app runs, WAL included).
# Usage: scripts/backup.sh [destination_dir]   (default: backups/, keeps the newest 14)
source "$(dirname "$0")/_env.sh"
DEST="${1:-$REPO/backups}"
mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/news-$STAMP.db"
"$PY" - "$REPO/data/news.db" "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit(f"integrity check failed: {ok}")
PY
echo "Backup written: $OUT"
ls -1t "$DEST"/news-*.db 2>/dev/null | tail -n +15 | xargs -I{} rm -f {}
