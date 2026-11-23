# QC stock release implementation status

The release service now supports proposal, separate approval, and **draft Odoo internal transfer creation**. It intentionally does not validate the transfer or move stock.

## Guardrails
- A PixeSci QC handoff must exist, be approved, and reference a PixeSci QC request.
- QA authorization is checked through an authenticated server-to-server PixeSci verifier, not through user-provided approval flags.
- Receipt moves must be completed, in the configured QC hold location, and unchanged since approval.
- Proposer, approver and executor must be distinct.
- ERP_SANDBOX_ONLY=true and ERP_WRITE_ENABLED=true must both be set for draft transfer creation.
- The action ledger claims execution atomically. Errors after claim become 'uncertain'; no automatic retry is allowed.
- Odoo stock picking is left in draft; no button_validate call is made.

## Integration gaps
- The PixeSci QA verification endpoint is a proposed contract, **not an existing confirmed PixeSci route**. It must be implemented and validated against real PixeSci approval and release-package services.
- A durable reconciliation process must detect partially created pickings and moves after RPC failures.
- Odoo product units of measure, lot/serial tracking, stock reservation and location permissions need validation for the deployed Odoo version.
- The draft picking creation is not a completed inventory release.
- This is development code and has not been exercised against a live Odoo database.
