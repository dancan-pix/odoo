#!/usr/bin/env bash
# Run from any directory with Docker Compose v2 and Python 3. No production deployment.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
command -v docker >/dev/null
docker compose version >/dev/null
command -v python3 >/dev/null
# A separate project, database, volumes and random loopback port on every run.
suffix="$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
project="odoo-sandbox-$suffix"
export ODOO_DB="sandbox_$suffix"
export ODOO_USER=admin
export ODOO_API_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export ERP_SANDBOX_ONLY=true ERP_WRITE_ENABLED=true
work="$(mktemp -d)"
cleanup() {
  docker compose -p "$project" -f "$work/compose.yaml" down -v --remove-orphans || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$work/compose.yaml" <<'YAML'
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: odoo
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}
      POSTGRES_DB: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U odoo -d postgres"]
      interval: 2s
      timeout: 5s
      retries: 60
    volumes:
      - db:/var/lib/postgresql/data
  odoo:
    image: odoo:18.0
    environment:
      HOST: db
      USER: odoo
      PASSWORD: ${POSTGRES_PASSWORD:?}
    ports:
      - "127.0.0.1::8069"
    volumes:
      - data:/var/lib/odoo
    command: ["odoo", "--no-database-list", "--db-filter=^sandbox_[a-f0-9]+$"]
volumes:
  db:
  data:
YAML
dc() { docker compose -p "$project" -f "$work/compose.yaml" "$@"; }
dc up -d --wait db
# Initialize only the newly generated database, without exposing an HTTP listener.
dc run --rm --no-deps odoo odoo -d "$ODOO_DB" -i purchase_stock --without-demo=all --stop-after-init --no-http
# Set a random password through Odoo ORM, never via a command-line password argument.
dc run --rm -T --no-deps -e ODOO_API_KEY odoo odoo shell -d "$ODOO_DB" --no-http <<'PY'
import os
admin = env.ref("base.user_admin")
admin.write({"login": "admin", "password": os.environ["ODOO_API_KEY"]})
env.cr.commit()
PY
dc up -d odoo
export ODOO_URL="http://$(dc port odoo 8069)"
python3 - <<'PY'
import time
from adapter.odoo_rpc import OdooClient
client = OdooClient.from_env()
for attempt in range(90):
    try:
        client._uid()
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("Sandbox authentication never became ready")
PY
python3 -m adapter.demo_fixture --run-release
echo "Sandbox workflow completed; disposable database and volumes will be removed."
