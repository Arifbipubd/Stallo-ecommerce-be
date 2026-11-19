#!/usr/bin/env bash
# Safe copy of the database (orders, admin edits): run from anywhere, e.g. nightly from cron.
#   ./backup.sh
# Keeps 14 days of .gz copies in ~/stallo-backups (override with BACKUP_DIR) and, if BACKUP_S3_BUCKET is set in
# deploy/.env and the server has AWS access, uploads each one to S3.
set -euo pipefail
cd "$(dirname "$0")"

STAMP=$(date +%Y%m%d-%H%M%S)
DEST="${BACKUP_DIR:-$HOME/stallo-backups}"
mkdir -p "$DEST"

# SQLite's own online backup: consistent even while the site is taking orders.
docker compose exec -T api python - <<'PY'
import os, sqlite3
src = sqlite3.connect(os.environ["DJANGO_DB_PATH"])
dst = sqlite3.connect("/tmp/backup.sqlite3")
src.backup(dst)
dst.close()
src.close()
PY
docker compose cp api:/tmp/backup.sqlite3 "$DEST/stallo-$STAMP.sqlite3"
docker compose exec -T api rm -f /tmp/backup.sqlite3
gzip "$DEST/stallo-$STAMP.sqlite3"
echo "Saved $DEST/stallo-$STAMP.sqlite3.gz"

find "$DEST" -name 'stallo-*.sqlite3.gz' -mtime +14 -delete

BUCKET=$(grep -E '^BACKUP_S3_BUCKET=' .env 2>/dev/null | cut -d= -f2- || true)
if [ -n "$BUCKET" ]; then
  aws s3 cp "$DEST/stallo-$STAMP.sqlite3.gz" "s3://$BUCKET/stallo-$STAMP.sqlite3.gz"
fi
