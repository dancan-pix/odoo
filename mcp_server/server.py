"""Standalone scoped Odoo MCP service; synthetic QA is opt-in only."""
from mcp.server.fastmcp import FastMCP
from adapter.odoo_rpc import OdooClient
from adapter.receipts import ReceiptReader
from adapter.qc_bridge import QCBridge
from adapter.receipt_handoff import ReceiptHandoffService
from adapter.release_plan import ReleasePlanner
import os
import json
import hashlib
import hmac
from mcp.server.fastmcp import Context
from adapter.quality_release import QualityRelease
from adapter.approval_ledger import ApprovalLedger

mcp = FastMCP("pixesci-odoo-erp")

def principal(ctx: Context, scope: str) -> str:
    """Authenticate per HTTP request; stdio cannot perform governed writes."""
    request = ctx.request_context.request
    headers = getattr(request, "headers", {})
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionError("Authenticated ERP principal required")
    digest = hashlib.sha256(auth[7:].encode()).hexdigest()
    records = json.loads(os.environ.get("ERP_MCP_PRINCIPALS_JSON", "{}"))
    for expected, record in records.items():
        if hmac.compare_digest(expected, digest):
            actor = record.get("actor")
            if isinstance(actor, str) and actor.strip() and scope in record.get("scopes", []):
                return actor
    raise PermissionError("ERP scope denied")

def release_service():
    from adapter.sandbox_qa import Client, enabled
    verifier = Client().verify if enabled() else lambda request_id: False
    # No real QA authority is configured; default remains fail closed.
    return QualityRelease(OdooClient.from_env(),
        QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3")),
        ApprovalLedger(os.environ.get("ERP_APPROVAL_DB_PATH", "erp-approvals.sqlite3")),
        verifier)

@mcp.tool()
def propose_stock_release(handoff_id: str, source_location_id: int,
                           destination_location_id: int, idempotency_key: str,
                           ctx: Context) -> dict:
    """Propose an exact QA-authorized release; requires independent approval."""
    actor = principal(ctx, "stock:propose")
    return release_service().propose(handoff_id, source_location_id,
        destination_location_id, actor, idempotency_key)

@mcp.tool()
def approve_stock_release(idempotency_key: str, ctx: Context) -> bool:
    """Independently approve an existing stock release proposal."""
    actor = principal(ctx, "stock:approve")
    return release_service().approve(idempotency_key, actor)

@mcp.tool()
def execute_stock_release(idempotency_key: str, ctx: Context) -> dict:
    """Execute a previously approved release, subject to current external QA."""
    actor = principal(ctx, "stock:execute")
    return release_service().execute(idempotency_key, actor)


@mcp.tool()
def list_suppliers(ctx: Context, limit: int = 25) -> list[dict]:
    """List Odoo suppliers. Read-only; no stock or purchase changes."""
    principal(ctx, "suppliers:read")
    return OdooClient.from_env().suppliers(limit)

@mcp.tool()
def list_purchase_orders(ctx: Context, limit: int = 25) -> list[dict]:
    """List purchase orders with their Odoo states."""
    principal(ctx, "purchase:read")
    return OdooClient.from_env().purchase_orders(limit)

@mcp.tool()
def list_stock_quantities(ctx: Context, limit: int = 25) -> list[dict]:
    """Inspect inventory quantities without changing stock."""
    principal(ctx, "stock:read")
    return OdooClient.from_env().stock_quantities(limit)

@mcp.tool()
def list_incoming_receipts(ctx: Context, limit: int = 25) -> list[dict]:
    """List incoming receipts and their current Odoo state."""
    principal(ctx, "receipts:read")
    return ReceiptReader(OdooClient.from_env()).incoming_receipts(limit)

@mcp.tool()
def get_purchase_order_lines(purchase_order_id: int, ctx: Context) -> list[dict]:
    """Read ordered and received quantities for a purchase order."""
    principal(ctx, "purchase:read")
    return ReceiptReader(OdooClient.from_env()).purchase_lines(purchase_order_id)

@mcp.tool()
def get_receipt_moves(picking_id: int, ctx: Context) -> list[dict]:
    """Read item movements belonging to an incoming receipt."""
    principal(ctx, "receipts:read")
    return ReceiptReader(OdooClient.from_env()).receipt_moves(picking_id)

@mcp.tool()
def prepare_receipt_qc_handoff(picking_id: int, handoff_id: str, ctx: Context) -> dict:
    """Prepare a local, idempotent QC request; does not create a PixeSci record."""
    if os.environ.get("ERP_SANDBOX_ONLY", "").lower() != "true":
        raise PermissionError("QC handoff preparation is sandbox-only")
    actor = principal(ctx, "qc:prepare")
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return ReceiptHandoffService(OdooClient.from_env(), bridge).prepare(
        picking_id, handoff_id, actor)

@mcp.tool()
def sync_synthetic_qa(handoff_id: str, ctx: Context) -> dict:
    """Submit or refresh receipt evidence with the isolated synthetic QA authority."""
    actor = principal(ctx, "qc:sync")
    from adapter.sandbox_qa import Client
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return Client().sync(bridge, handoff_id, actor)

@mcp.tool()
def inspect_receipt_qc_handoff(handoff_id: str, ctx: Context) -> dict:
    """Inspect persisted handoff status; no quality disposition is inferred."""
    principal(ctx, "qc:read")
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return bridge.get(handoff_id) or {"found": False}

@mcp.tool()
def preview_qc_stock_release(handoff_id: str, hold_location_id: int,
                             usable_location_id: int, ctx: Context) -> dict:
    """Read-only stock release eligibility; never executes a stock transfer."""
    principal(ctx, "stock:read")
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return ReleasePlanner(OdooClient.from_env(), bridge).inspect(
        handoff_id, hold_location_id, usable_location_id)


@mcp.tool()
def reconcile_stock_release(idempotency_key: str, ctx: Context) -> dict:
    """Read-only inspection of an Odoo transfer after an uncertain release write.

    Does not retry, approve, validate or move stock. Returns mismatch/ambiguity
    explicitly for human reconciliation.
    """
    principal(ctx, "stock:read")
    from adapter.approval_ledger import ApprovalLedger
    from adapter.stock_reconciliation import StockTransferReconciler
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("A valid idempotency key is required")
    ledger = ApprovalLedger(os.environ.get("ERP_APPROVAL_DB_PATH", "erp-approvals.sqlite3"))
    return StockTransferReconciler(OdooClient.from_env(), ledger).inspect(idempotency_key)


def purchase_service():
    from adapter.purchase_confirmation import PurchaseConfirmation
    return PurchaseConfirmation(OdooClient.from_env(),
        ApprovalLedger(os.environ.get("ERP_APPROVAL_DB_PATH", "erp-approvals.sqlite3")))

@mcp.tool()
def propose_purchase_confirmation(order_id: int, idempotency_key: str, ctx: Context) -> dict:
    """Capture draft RFQ terms for independent approval."""
    return purchase_service().propose(order_id, idempotency_key, principal(ctx, "purchase:propose"))

@mcp.tool()
def approve_purchase_confirmation(idempotency_key: str, ctx: Context) -> bool:
    """Approve captured purchase terms as an independent principal."""
    return purchase_service().approve(idempotency_key, principal(ctx, "purchase:approve"))

@mcp.tool()
def execute_purchase_confirmation(idempotency_key: str, ctx: Context) -> dict:
    """Confirm an approved unchanged RFQ in the sandbox."""
    return purchase_service().execute(idempotency_key, principal(ctx, "purchase:execute"))

@mcp.tool()
def reconcile_purchase_confirmation(idempotency_key: str, ctx: Context) -> dict:
    """Inspect current Odoo state without retrying uncertain writes."""
    principal(ctx, "purchase:read")
    return purchase_service().reconcile(idempotency_key)

if __name__ == "__main__":
    mcp.run(transport="streamable-http" if os.environ.get("ERP_MCP_HTTP") == "true" else "stdio")
