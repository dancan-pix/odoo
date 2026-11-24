#!/bin/sh
# Run from repository root: sh deploy/backup.sh /absolute/private/backup-directory
# Requires Docker Compose and an existing running db and odoo service.
set -eu
umask 077
dest=${1:?Provide a private backup directory}
case "$dest" in /*) ;; *) echo "Absolute destination required" >&2; exit 1;; esac
mkdir -p "$dest"
db=${ODOO_DB:?Export ODOO_DB before running}
case "$db" in *[!a-zA-Z0-9_-]*|'') echo "Invalid database name" >&2; exit 1;; esac
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp=$(mktemp -d "$dest/.odoo-$stamp.XXXXXX")
stopped=0
cleanup() {
  if [ "$stopped" = 1 ]; then docker compose start odoo >&2 || echo "ERROR: restart Odoo manually" >&2; fi
  rm -rf "$tmp"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# Refuse to change the state of an already stopped application.
[ -n "$(docker compose ps --status running -q odoo)" ] || { echo "Odoo must be running" >&2; exit 1; }
stopped=1
docker compose stop odoo
# All other writers must be stopped by the operator before running this script.
docker compose exec -T db sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump -U "$POSTGRES_USER" -Fc -- "$1"' sh "$db" > "$tmp/database.dump"
docker compose exec -T db pg_restore -l < "$tmp/database.dump" > /dev/null
docker compose run --rm --no-deps -T --entrypoint sh odoo -ec 'test -d /var/lib/odoo/filestore/"$1"; tar -C /var/lib/odoo/filestore -czf - "$1"' sh "$db" > "$tmp/filestore.tar.gz"
tar -tzf "$tmp/filestore.tar.gz" > /dev/null
printf '%s\n' "$db" > "$tmp/database-name"
(cd "$tmp" && sha256sum database.dump filestore.tar.gz database-name > SHA256SUMS)
docker compose start odoo
stopped=0
final="$dest/odoo-$stamp"
[ ! -e "$final" ]
mv "$tmp" "$final"
printf 'Backup created: %s\n' "$final"
