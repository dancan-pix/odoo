#!/bin/sh
# Restore a trusted backup into a NEW database. Run from repository root.
# Stop external writers/adapter before running; never overwrite a live database.
set -eu
umask 077
src=${1:?Usage: sh deploy/restore.sh BACKUP_DIRECTORY NEW_DATABASE}
target=${2:?Specify a new database name}
case "$target" in *[!a-zA-Z0-9_-]*|'') echo "Invalid target" >&2; exit 1;; esac
case "$target" in postgres|template0|template1) exit 1;; esac
src=$(cd "$src" && pwd)
(cd "$src" && sha256sum -c SHA256SUMS)
original=$(cat "$src/database-name")
case "$original" in *[!a-zA-Z0-9_-]*|'') exit 1;; esac
[ "$original" != "$target" ] || { echo "Restore requires a different database name" >&2; exit 1; }
# Validate archive paths and types before extracting. Reject links and special files.
python3 - "$src/filestore.tar.gz" "$original" <<'PY'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for item in archive:
        p = PurePosixPath(item.name)
        if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != sys.argv[2]:
            raise SystemExit("Unexpected archive path")
        if not (item.isfile() or item.isdir()):
            raise SystemExit("Unsupported archive member")
PY
docker compose exec -T db pg_restore -l < "$src/database.dump" >/dev/null
[ -n "$(docker compose ps --status running -q odoo)" ] || { echo "Odoo must be running" >&2; exit 1; }
stopped=0
cleanup() {
  if [ "$stopped" = 1 ]; then docker compose start odoo >&2 || echo "Restart Odoo manually" >&2; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
stopped=1
docker compose stop odoo
# Refuse existing filestore before creating database.
docker compose run --rm --no-deps -T --entrypoint sh odoo -ec 'test ! -e /var/lib/odoo/filestore/"$1"' sh "$target"
# createdb fails atomically if target already exists.
docker compose exec -T db sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; createdb -U "$POSTGRES_USER" -- "$1"' sh "$target"
# On failure preserve the new database for operator inspection; never delete existing data.
docker compose exec -T db sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore -U "$POSTGRES_USER" --exit-on-error --single-transaction --no-owner --no-privileges -d "$1"' sh "$target" < "$src/database.dump"
docker compose run --rm --no-deps -T --entrypoint sh odoo -ec '
  base=/var/lib/odoo/filestore
  mkdir -p "$base"
  tmp=$(mktemp -d "$base/.restore.XXXXXX")
  trap '\''rm -rf "$tmp"'\'' EXIT
  tar -xzf - -C "$tmp" --no-same-owner --no-same-permissions
  test -d "$tmp/$1"
  test ! -e "$base/$2"
  mv -T "$tmp/$1" "$base/$2"
' sh "$original" "$target" < "$src/filestore.tar.gz"
docker compose start odoo
stopped=0
printf 'Restored database %s. Validate access, attachments and disable outbound jobs before use.\n' "$target"
