#!/usr/bin/env bash
# Usage: bash scripts/backup-local.sh DATABASE /absolute/private/backup-directory
# Stops the local Odoo service to capture a consistent database/filestore pair.
# Requires exclusive maintenance access: no other database or filestore writers.
set -euo pipefail
umask 077
[[ $# == 2 ]] || { echo "Usage: $0 DATABASE /absolute/backup-directory" >&2; exit 2; }
db="$1"
dest="$2"
[[ "$db" =~ ^[a-zA-Z][a-zA-Z0-9_]{0,62}$ && "$dest" == /* ]] || exit 2
cd "$(dirname "$0")/.."
mkdir -p "$dest"
[[ -d "$dest" && ! -L "$dest" ]] || exit 2
[[ "$(stat -c %a "$dest")" == 700 ]] || { echo "Backup directory must have mode 700" >&2; exit 2; }
exec 9>"$dest/.backup.lock"
flock -n 9 || { echo "Backup already running" >&2; exit 1; }
stage="$(mktemp -d "$dest/.partial.XXXXXXXX")"
restart=0
cleanup() {
  rc=$?
  trap - EXIT
  if [[ "$restart" == 1 ]]; then
    docker compose start odoo || rc=1
  fi
  [[ ! -d "$stage" ]] || rm -rf -- "$stage"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose exec -T db sh -ec 'pg_isready -U "$POSTGRES_USER" -d postgres'
if docker compose ps --status running --services | grep -qx odoo; then
  restart=1
  docker compose stop odoo
fi
docker compose exec -T db sh -ec 'pg_dump -U "$POSTGRES_USER" -Fc --no-owner --no-privileges "$1"' sh "$db" > "$stage/database.dump"
docker compose exec -T db pg_restore --list < "$stage/database.dump" > "$stage/database.toc"
# Override entrypoint so no Odoo process or HTTP listener is started.
docker compose run --rm --no-deps -T --entrypoint /bin/sh odoo -ec '
  root=/var/lib/odoo/filestore
  if [ -d "$root/$1" ]; then
    tar -C "$root" -cf - "$1"
  else
    tar -cf - --files-from /dev/null
  fi
' sh "$db" > "$stage/filestore.tar"
tar -tf "$stage/filestore.tar" > "$stage/filestore.toc"
printf '%s\n' "$db" > "$stage/database.name"
printf '%s\n' "$(date -u +%FT%TZ)" > "$stage/created.utc"
(cd "$stage"; sha256sum database.dump database.toc filestore.tar filestore.toc database.name created.utc > SHA256SUMS; sha256sum -c SHA256SUMS)
final="$dest/$db-$(date -u +%Y%m%dT%H%M%SZ)-${stage##*.}"
mv -T -- "$stage" "$final"
echo "Backup captured: $final"
echo "Restore validation is required before treating this backup as recoverable."
