# PixeSci ↔ Odoo integration contract (initial)

## System boundaries
- Odoo Community: suppliers, purchasing, receiving, stock and accounting-related ERP records.
- PixeSci: sample accessioning, QC tests, deviations, controlled documents, laboratory release authorization.
- PyxAI: governed orchestration and human-confirmed actions, never autonomous bypass of quality approval.

## Planned workflow (NOT implemented)
1. Odoo PO confirmed; adapter captures immutable PO/line IDs.
2. Goods receipt is registered in Odoo into a quality-hold location.
3. PixeSci creates QC request linked to Odoo receipt/lot identifiers.
4. Laboratory QC evidence and QA disposition remain within PixeSci.
5. Only an approved disposition with verified approver identity can propose stock release.
6. An idempotent transaction moves inventory from quality-hold to usable stock in Odoo.
7. Persist cross-system action ID, correlation ID, actor, timestamps and audit trail.

## Current implementation
- Odoo/PostgreSQL Compose sandbox (no deployment).
- Read-only Odoo JSON-RPC queries for suppliers, POs, and stock.
- SQLite proposed-action/approval ledger (not yet connected to write tools).
- MCP read tools.

## Required before enabling writes
- Implement server-side authorization, scoped service accounts and signed PixeSci QA release verification.
- Add transaction recovery, inventory location mapping, retry-safe idempotency and complete audit trail.
- Validate target Odoo Community version and module availability.
- Do not use real customer records or regulated production data in sandbox.
