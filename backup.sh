#!/bin/sh
# Full MangaBrain database backup (catalog, embeddings, and user lists) to a
# timestamped dump file. Keeps the newest 8 backups in the destination.
#
# Usage:  ./backup.sh /path/to/backup/dir
# Meant for a weekly scheduled job (e.g. OpenMediaVault: Services -> Scheduled
# Jobs). Restore with:
#   docker cp <file> mangabrain-worker-1:/tmp/restore.dump
#   docker exec mangabrain-worker-1 python -m pipeline.restore --file /tmp/restore.dump --force
set -eu

DEST=${1:?usage: backup.sh <dest-dir>}
WORKER=${WORKER_CONTAINER:-mangabrain-worker-1}
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$DEST"
docker exec "$WORKER" python -m pipeline.snapshot --all --out /tmp/mangabrain-backup.dump
docker cp "$WORKER":/tmp/mangabrain-backup.dump "$DEST/mangabrain-$STAMP.dump"
docker exec "$WORKER" rm -f /tmp/mangabrain-backup.dump
echo "backup written to $DEST/mangabrain-$STAMP.dump"

# Prune: keep the 8 newest backups.
ls -1t "$DEST"/mangabrain-*.dump 2>/dev/null | tail -n +9 | xargs -r rm -f
