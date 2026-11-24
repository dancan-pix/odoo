#!/bin/sh
# Restore a coordinated recovery set into a NEW, isolated Compose project.
# The target project must already have db and odoo running, but no integration writers.
# Never enable writes until restored actions have been reconciled.
set -eu
umask 077
src=${1:?Usage: sh deploy/restore_integration.sh BACKUP NEW_DATABASE --isolated-writers-disabled}
target=${2:?Specify new database}
[ "${3:-}" = "--isolated-writers-disabled" ] || exit 1
: "${COMPOSE_PROJECT_NAME:?Set an isolated recovery project name}"
: "${ODOO_DB:?Set ODOO_DB to the recovery database}"
[ "$ODOO_DB" = "$target" ] || exit 1
src=$(cd "$src" && pwd)
(cd "$src" && sha256sum -c SHA256SUMS)
(cd "$src/erp" && sha256sum -c SHA256SUMS)
dc() { docker compose -f compose.yaml -f deploy/compose.integration.yaml --profile integration "$@"; }
[ -z "$(dc ps --status running -q erp-purchasing)" ] || {
  echo "Stop integration writers first" >&2; exit 1;
}
# Preflight the destination and source ledger before touching ERP data.
dc run --rm --no-deps -T --entrypoint python erp-purchasing -c '
import os, sqlite3, sys, tempfile
from pathlib import Path
target = Path("/state/approvals.sqlite3")
for suffix in ("", "-wal", "-shm", "-journal"):
    if os.path.lexists(str(target)+suffix):
        raise SystemExit("Recovery ledger destination already exists")
with tempfile.TemporaryDirectory() as tmp:
    source = Path(tmp)/"source.sqlite3"
    source.write_bytes(sys.stdin.buffer.read())
    with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise SystemExit("Invalid ledger")
        db.execute("SELECT idempotency_key, status FROM actions LIMIT 1")
' < "$src/approvals.sqlite3"
COMPOSE_FILE=compose.yaml:deploy/compose.integration.yaml COMPOSE_PROFILES=integration sh deploy/restore.sh "$src/erp" "$target"
# Preserve all historical actions; interrupted execution is uncertain, never retried.
dc run --rm --no-deps -T --entrypoint python erp-purchasing -c '
import os, sqlite3, sys, tempfile
from pathlib import Path
os.umask(0o077)
target = Path("/state/approvals.sqlite3")
with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
    source = Path(tmp)/"source.sqlite3"
    restored = Path(tmp)/"restored.sqlite3"
    source.write_bytes(sys.stdin.buffer.read())
    with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src:
        with sqlite3.connect(restored) as dst:
            src.backup(dst)
            dst.execute("UPDATE actions SET status=\x27uncertain\x27 WHERE status=\x27executing\x27")
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise SystemExit("Invalid restored ledger")
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.lexists(str(target)+suffix):
            raise SystemExit("Recovery destination exists")
    os.link(restored, target)
' < "$src/approvals.sqlite3"
printf 'Recovered ERP database %s and matching ledger. Purchasing remains stopped; reconcile before enabling writes.\n' "$target"
