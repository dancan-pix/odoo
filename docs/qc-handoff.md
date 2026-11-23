# QC hold and release integration — development contract

1. Odoo incoming receipt references an actual purchase order and stock moves.
2. `ReceiptHandoffService.prepare` validates the receipt and persists an idempotent QC handoff.
3. A future authenticated PixeSci API client must create the actual QC request. Only its confirmed response may call `acknowledge_submission`.
4. PixeSci retains test execution, deviations, reviewer identity and final QA disposition.
5. `record_disposition` requires a server-side verifier of PixeSci's signed disposition; an arbitrary caller-supplied 'approved' value must never be trusted.
6. `ReleasePlanner.inspect` refuses to plan stock release without verified QA approval, a completed Odoo receipt, and receipt moves into the configured quality-hold location.
7. Actual Odoo stock transfers are deliberately **not implemented**. They require scoped authorization, location and lot reconciliation, transaction audit, and retry-safe execution.

No Odoo or PixeSci network writes are performed by these modules. They are an integration foundation, not a validated GxP process.
