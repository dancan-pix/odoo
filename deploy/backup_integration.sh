#!/bin/sh
# Run from repository root. Stop external writers and drain in-flight requests first.
# Produces one recovery set; never restore its components independently.
set -eu
umask 077
[ "${2:-}" = "--external-writers-drained" ] || {
  echo "Usage: sh deploy/backup_integration.sh /private/directory --external-writers-drained" >&2
  exit 1
}
dest=${1:?}
case "$dest" in /*) ;; *) exit 1;; esac
mkdir -p "$dest"
stage=$(mktemp -d "$dest/.integration.XXXXXX")
dc() { docker compose -f compose.yaml -f deploy/compose.integration.yaml --profile integration "$@"; }
restart=0
cleanup() {
  if [ "$restart" = 1 ]; then
    echo "Purchasing remains stopped: inspect backup outcome and reconcile before restarting." >&2
  fi
  rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
[ -n "$(dc ps --status running -q erp-purchasing)" ] || {
  echo "Purchasing service must be running before coordinated backup" >&2; exit 1;
}
restart=1
dc stop -t 120 erp-purchasing
# Snapshot the existing volume using the same non-root image, without starting its server.
dc run --rm --no-deps -T --entrypoint python erp-purchasing -c '
import sqlite3, sys, tempfile
from pathlib import Path
source = Path("/state/approvals.sqlite3")
if not source.is_file():
    raise SystemExit("Missing approval ledger")
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "ledger.sqlite3"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise SystemExit("Ledger integrity failure")
    sys.stdout.buffer.write(target.read_bytes())
' > "$stage/approvals.sqlite3"
test -s "$stage/approvals.sqlite3"
# Use the existing ERP backup utility with the same Compose project.
# COMPOSE_FILE is honored by its docker compose commands.
COMPOSE_FILE=compose.yaml:deploy/compose.integration.yaml COMPOSE_PROFILES=integration sh deploy/backup.sh "$stage"
set -- "$stage"/odoo-*
[ "$#" = 1 ] && [ -d "$1" ]
mv "$1" "$stage/erp"
(cd "$stage" && sha256sum approvals.sqlite3 erp/database.dump erp/filestore.tar.gz erp/database-name erp/SHA256SUMS > SHA256SUMS)
final="$dest/integration-$(date -u +%Y%m%dT%H%M%SZ)-${stage##*.}"
[ ! -e "$final" ]
mv "$stage" "$final"
# Keep writes stopped on failure; restart only after publication.
dc start erp-purchasing
restart=0
printf 'Coordinated recovery set: %s\n' "$final"
