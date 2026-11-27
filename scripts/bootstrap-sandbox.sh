#!/usr/bin/env bash
# Initialize a NEW synthetic sandbox only. Run from repository root.
# Usage: bash scripts/bootstrap-sandbox.sh
set -euo pipefail
umask 077
project=pixesci-odoo-sandbox
db=pixescisandbox
[[ -f compose.yaml && -f .env ]] || { echo 'Run from repo root with a configured .env' >&2; exit 1; }
command -v docker >/dev/null
docker compose version >/dev/null
# Explicit project and database names prevent targeting a normal deployment.
dc=(docker compose --project-name "$project" --env-file .env -f compose.yaml)
"${dc[@]}" config --quiet
"${dc[@]}" up -d --wait db
# Refuse to modify any pre-existing database, including partially initialized ones.
exists=$("${dc[@]}" exec -T db sh -ec 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '\''pixescisandbox'\''"')
[[ -z "$exists" ]] || { echo 'Sandbox database already exists; refusing initialization.' >&2; exit 1; }
# The official Odoo entrypoint supplies DB credentials from the Compose environment.
# No HTTP listener is published for this one-shot initialization.
"${dc[@]}" run --rm --no-deps odoo odoo -d "$db" --init=purchase_stock --without-demo=all --stop-after-init --no-http
# Set a fresh administrator password BEFORE starting the HTTP service.
# Pass through stdin, not shell arguments or a committed configuration file.
password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')
{
  printf "password = '%s'\n" "$password"
  cat <<'PY'
admin = env.ref('base.user_admin')
admin.write({'password': password})
env.cr.commit()
PY
} | "${dc[@]}" run --rm -T --no-deps odoo odoo shell -d "$db" --no-http
# Store credentials outside the repository, with exclusive creation.
credential_file=$(mktemp "${TMPDIR:-/tmp}/pixesci-odoo-admin.XXXXXXXX")
printf '%s\n' "$password" > "$credential_file"
unset password
"${dc[@]}" up -d odoo
echo "Sandbox initialized at http://127.0.0.1:8069; database: $db"
echo "Administrator password saved to private file: $credential_file"
echo 'Create a scoped integration account and API key before enabling adapter writes.'
