#!/usr/bin/env bash
# Daily SQLite backup to a Cloud Storage bucket. Run by cron on the VM as the deploy user.
#
# Uses SQLite's own online backup (safe while the worker writes), copies the file out of the
# container and uploads it with the VM's service account token, so no extra tools are needed.
# The VM must have been created with the storage-rw scope and the account must be able to write
# to the bucket (docs/deploy.md).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/lp-radar}"
BUCKET="${BACKUP_BUCKET:?BACKUP_BUCKET is not set}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$APP_DIR"
docker compose exec -T web python - <<'PYEOF'
import sqlite3

src = sqlite3.connect("/data/lp-radar.db")
dst = sqlite3.connect("/data/backup.db")
with dst:
    src.backup(dst)
dst.close()
src.close()
PYEOF
docker compose cp web:/data/backup.db "$TMP/lp-radar.db"
docker compose exec -T web rm -f /data/backup.db

gzip -9 "$TMP/lp-radar.db"

TOKEN="$(curl -fsS -H 'Metadata-Flavor: Google' \
	'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' |
	jq -r .access_token)"
curl -fsS -X POST \
	-H "Authorization: Bearer $TOKEN" \
	-H 'Content-Type: application/gzip' \
	--data-binary "@$TMP/lp-radar.db.gz" \
	"https://storage.googleapis.com/upload/storage/v1/b/$BUCKET/o?uploadType=media&name=lp-radar-$STAMP.db.gz" \
	>/dev/null
echo "$(date -u +%FT%TZ) uploaded lp-radar-$STAMP.db.gz to gs://$BUCKET"
