# PixeSci Odoo Community Integration

**Development only.** PixeSci retains its laboratory/QC workflows; Odoo Community provides ERP capabilities. PyxAI coordinates governed actions.

## Local sandbox

1. Copy `.env.example` to `.env` and replace placeholder credentials.
2. The database manager is disabled in the sandbox config. Provision the initial database using a controlled local administrative setup; do not expose database management publicly.
3. Run `docker compose up -d`. Odoo is bound to local loopback on port 8069.
4. Initialize an Odoo database locally and install Purchase and Inventory modules.
5. Create a least-privilege integration user/API key; configure the Odoo environment variables.
6. Install `pip install -r requirements.txt` and run `python -m mcp_server.server`.

This is a development skeleton, not a validated regulated deployment. Backups, QC release writes and Railway deployment remain unimplemented. Never expose the current configuration to the public internet.

See `docs/integration-contract.md` for workflow boundaries.
