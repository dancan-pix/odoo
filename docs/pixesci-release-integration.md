# PixeSci release boundary (repository inspection, 2026-11-23)

Inspected `dancan-pix/pixesciv2` on its indexed default branch.

- `backend/services/regulated_os/laboratory/review_release_service.py`: release is a guarded domain operation with independent reviewers and blockers. Do **not** infer inventory release from a sample status string.
- `backend/services/regulated_os/laboratory/release_package_service.py`: release packages include immutable evidence manifests and explicit technical, quality, release and CoA signoffs.
- `backend/services/regulated_os/laboratory/sample_accessioning_service.py`: accessioning is a distinct laboratory service; preserve its existing domain model.

## Integration boundary

Odoo receipt/lot and PO IDs should be linked to a PixeSci accessioning or QC request using `adapter/qc_bridge.py`. An eventual PixeSci integration API must validate tenant/site scope, authenticate a service identity, persist receipt-to-sample correlation, and return a canonical QC request ID.

For disposition, use a server-to-server verification endpoint returning immutable release package identifier, sample/lot correlation, signed reviewer identities, evidence digest, release authorization and version. The ERP bridge must reject mismatched tenant, site, lot, product, receipt and disposition. Never trust user-supplied `approved=true`.

Current code only prepares the handoff and read-only release preview; it does not call PixeSci, attest signatures, or move stock. The existing PixeSci LIMS remains unchanged.
