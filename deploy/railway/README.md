# Railway sandbox deployment blueprint (not deployed)

This is a **manual blueprint**, not a production-ready deployment. Provision an Odoo service from the repository Dockerfile, plus a managed PostgreSQL service and a persistent volume for /var/lib/odoo. Odoo and PostgreSQL must be on a private Railway network. Supply secrets through Railway environment settings, not Git. Set the Odoo service HOST, USER, PASSWORD and an independently generated Odoo database manager password.

Never expose PostgreSQL publicly. Do not make Odoo accessible from the public internet until admin credentials, proxy mode, backups, health checks, TLS, network restrictions and application hardening have been reviewed.

Current Compose binds Odoo to 127.0.0.1 for local testing. Railway's PORT binding, proxy mode and health check must be adapted before deployment. This document does not authorize provisioning or charges.
