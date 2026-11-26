#!/usr/bin/env bash
# Recover a trusted synthetic sandbox backup into fresh Compose volumes.
# No host HTTP port is published; no existing stack is stopped or modified.
set -euo pipefail
umask 077
[[ $# == 1 && "${ODOO_ALLOW_SANDBOX_RESTORE:-}" == yes ]] || {
  echo "Usage: ODOO_ALLOW_SANDBOX_RESTORE=yes bash scripts/recovery-drill.sh BACKUP_DIRECTORY" >&2
  exit 2
}
backup="$(realpath "$1")"
[[ -d "$backup" ]] || exit 2
cd "$(dirname "$0")/.."
project="odoo-recovery-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
export COMPOSE_PROJECT_NAME="$project"
# Pin the Compose file to prevent accidental overrides or inherited project files.
export COMPOSE_FILE="$PWD/compose.yaml"
unset COMPOSE_PROFILES
target=sandbox_recovery
cleanup() {
  rc=$?
  trap - EXIT
  if [[ "$rc" == 0 ]]; then
    docker compose down --volumes --remove-orphans || rc=1
  else
    echo "Recovery drill failed; isolated project retained: $project" >&2
    echo "Inspect with docker compose -p $project ps; remove only this project after investigation." >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose up -d --wait db
bash scripts/restore-local.sh "$backup" "$target"
# Odoo shell loads the real registry against the recovered database.
# Compose run does not publish service ports unless explicitly requested.
docker compose run --rm --no-deps -T odoo odoo shell \
  -d "$target" --no-http <<'PY'
import json
modules = env['ir.module.module'].search([('name', 'in', ['purchase', 'stock'])])
assert set(modules.filtered(lambda m: m.state == 'installed').mapped('name')) == {'purchase', 'stock'}, 'ERP modules missing'
counts = {}
for model in ('res.partner', 'purchase.order', 'stock.picking', 'stock.location', 'stock.quant'):
    counts[model] = env[model].sudo().search_count([])
# Read actual attachment bytes through Odoo's storage implementation.
attachments = env['ir.attachment'].sudo().search([('store_fname', '!=', False)])
for attachment in attachments:
    data = attachment.raw
    assert data is not False and len(data) == attachment.file_size, 'Attachment size mismatch: %s' % attachment.id
    if attachment.checksum:
        import hashlib
        assert hashlib.sha1(data).hexdigest() == attachment.checksum, 'Attachment checksum mismatch: %s' % attachment.id
print(json.dumps({'recovery_registry': 'passed', 'erp_model_counts': counts, 'attachments_verified': len(attachments)}, sort_keys=True))
env.cr.rollback()
PY
echo "Isolated recovery drill passed; removing temporary project $project"
