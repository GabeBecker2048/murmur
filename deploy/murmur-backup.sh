#!/usr/bin/env bash
# Nightly snapshot of Murmur's SQLite store on gabe-server.
# Install: /home/gabe/bin/murmur-backup.sh, cron at 04:40 (after the 04:30/04:35
# backups, before docker-autopatch at 05:15). Logs to ~/.murmur-backup.log.
#
# The DB is in WAL mode, so it is copied with SQLite's online backup API, never
# cp. The copy is integrity-checked, checksummed, and the newest 14 are kept.
set -euo pipefail

SRC="${MURMUR_DB:-/home/gabe/murmur/data/murmur.sqlite}"
DEST_DIR="${MURMUR_BACKUP_DIR:-/mnt/storage/backups/murmur}"
KEEP="${MURMUR_BACKUP_KEEP:-14}"

stamp="$(date +%Y-%m-%d_%H%M%S)"
dest="$DEST_DIR/murmur_$stamp.sqlite"
tmp="$dest.part"

[ -r "$SRC" ] || { echo "$(date -Is) source missing: $SRC" >&2; exit 1; }
[ -d "$DEST_DIR" ] || { echo "$(date -Is) destination missing: $DEST_DIR" >&2; exit 1; }

python3 - "$SRC" "$tmp" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
    s.backup(d)
    (ok,) = d.execute("PRAGMA integrity_check").fetchone()
    if ok != "ok":
        raise SystemExit(f"integrity_check: {ok}")
PY

sha256sum "$tmp" | sed "s|$tmp|$(basename "$dest")|" > "$dest.sha256"
mv "$tmp" "$dest"
(cd "$DEST_DIR" && sha256sum -c --quiet "$(basename "$dest").sha256")

# prune: keep the newest $KEEP snapshots (and their checksums)
ls -1t "$DEST_DIR"/murmur_*.sqlite | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old.sha256"
done

echo "$(date -Is) ok $(basename "$dest") $(du -h "$dest" | cut -f1)"
