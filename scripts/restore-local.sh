#!/usr/bin/env bash
# Restore a backup into a NEW sandbox database in the local Compose stack.
# Requires exclusive maintenance access and trusted backup provenance.
set -euo pipefail
umask 077
[[ $# == 2 ]] || { echo "Usage: $0 BACKUP_DIRECTORY sandbox_NEW_DATABASE" >&2; exit 2; }
backup="$(realpath "$1")"
target="$2"
[[ "$target" =~ ^sandbox_[a-zA-Z0-9_]{1,55}$ ]] || exit 2
[[ "${ODOO_ALLOW_SANDBOX_RESTORE:-}" == yes ]] || { echo "Explicit sandbox restore authorization required" >&2; exit 2; }
cd "$(dirname "$0")/.."
exec 9> "$backup/../.backup.lock"
flock -n 9
for f in database.dump database.toc filestore.tar filestore.toc database.name created.utc SHA256SUMS; do
  [[ -f "$backup/$f" && ! -L "$backup/$f" ]] || exit 2
done
# Check exact expected files; do not follow paths supplied by the manifest.
(cd "$backup"; sha256sum database.dump database.toc filestore.tar filestore.toc database.name created.utc | cmp - SHA256SUMS)
source_db="$(cat "$backup/database.name")"
[[ "$source_db" =~ ^[a-zA-Z][a-zA-Z0-9_]{0,62}$ && "$source_db" != "$target" ]] || exit 2
stage="$(mktemp -d)"
restart=0
created=0
success=0
cleanup() {
  rc=$?
  trap - EXIT
  if [[ "$created" == 1 && "$success" == 0 ]]; then
    echo "Incomplete restore retained in $target for administrator inspection; do not use it." >&2
    restart=0
  fi
  if [[ "$restart" == 1 ]]; then docker compose start odoo || rc=1; fi
  rm -rf -- "$stage"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Rebuild the archive with only regular files and directories under the expected root.
python3 - "$backup/filestore.tar" "$stage/filestore.tar" "$source_db" "$target" <<'PY'
import sys, tarfile, pathlib
src, dst, old, new = sys.argv[1:]
with tarfile.open(src, 'r:') as a, tarfile.open(dst, 'w') as b:
    seen = set()
    for m in a:
        p = pathlib.PurePosixPath(m.name)
        if p.is_absolute() or not p.parts or p.parts[0] != old or '..' in p.parts:
            raise ValueError("Unsafe archive path")
        if not (m.isdir() or m.isfile()):
            raise ValueError("Unsupported archive member")
        m.name = str(pathlib.PurePosixPath(new, *p.parts[1:]))
        if m.name in seen:
            raise ValueError("Duplicate archive member")
        seen.add(m.name)
        m.mode = 0o700 if m.isdir() else 0o600
        b.addfile(m, a.extractfile(m) if m.isfile() else None)
PY
docker compose exec -T db pg_restore --list < "$backup/database.dump" > /dev/null
if docker compose ps --status running --services | grep -qx odoo; then
  restart=1
  docker compose stop odoo
fi
docker compose run --rm --no-deps -T --entrypoint /bin/sh odoo -ec '
  test ! -e "/var/lib/odoo/filestore/$1"
' sh "$target"
# createdb fails if the target already exists; never overwrite a database.
docker compose exec -T db sh -ec 'createdb -U "$POSTGRES_USER" "$1"' sh "$target"
created=1
docker compose exec -T db sh -ec 'pg_restore -U "$POSTGRES_USER" --exit-on-error --single-transaction --no-owner --no-privileges -d "$1"' sh "$target" < "$backup/database.dump"
docker compose run --rm --no-deps -T --entrypoint /bin/sh odoo -ec '
  mkdir -p /var/lib/odoo/filestore
  mkdir "/var/lib/odoo/filestore/$1"
  tar --no-same-owner -C /var/lib/odoo/filestore -xf -
' sh "$target" < "$stage/filestore.tar"
# Verify core Odoo tables and every database-referenced filestore attachment.
docker compose exec -T db sh -ec '
  psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -Atc "SELECT count(*) FROM ir_module_module"
  psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -Atc "SELECT count(*) FROM res_users"
' sh "$target"
docker compose exec -T db sh -ec '
  psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -Atc "SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL"
' sh "$target" > "$stage/attachments"
docker compose run --rm --no-deps -T --entrypoint /bin/sh odoo -ec '
  while IFS= read -r f; do
    case "$f" in ""|/*|*..*) exit 1;; esac
    test -f "/var/lib/odoo/filestore/$1/$f" || { echo "Missing attachment: $f" >&2; exit 1; }
  done
' sh "$target" < "$stage/attachments"
success=1
echo "Recovered $target; database and attachment checks passed. Odoo login and workflow validation still required."
