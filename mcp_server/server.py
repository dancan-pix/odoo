"""Read-only PyxAI MCP bridge. Install 'mcp' and set Odoo credentials to run."""
from mcp.server.fastmcp import FastMCP
from adapter.odoo_rpc import OdooClient
from adapter.receipts import ReceiptReader
from adapter.qc_bridge import QCBridge
from adapter.receipt_handoff import ReceiptHandoffService
from adapter.release_plan import ReleasePlanner
import os

mcp = FastMCP("pixesci-odoo-erp")

@mcp.tool()
def list_suppliers(limit: int = 25) -> list[dict]:
    """List Odoo suppliers. Read-only; no stock or purchase changes."""
    return OdooClient.from_env().suppliers(limit)

@mcp.tool()
def list_purchase_orders(limit: int = 25) -> list[dict]:
    """List purchase orders with their Odoo states."""
    return OdooClient.from_env().purchase_orders(limit)

@mcp.tool()
def list_stock_quantities(limit: int = 25) -> list[dict]:
    """Inspect inventory quantities without changing stock."""
    return OdooClient.from_env().stock_quantities(limit)

@mcp.tool()
def list_incoming_receipts(limit: int = 25) -> list[dict]:
    """List incoming receipts and their current Odoo state."""
    return ReceiptReader(OdooClient.from_env()).incoming_receipts(limit)

@mcp.tool()
def get_purchase_order_lines(purchase_order_id: int) -> list[dict]:
    """Read ordered and received quantities for a purchase order."""
    return ReceiptReader(OdooClient.from_env()).purchase_lines(purchase_order_id)

@mcp.tool()
def get_receipt_moves(picking_id: int) -> list[dict]:
    """Read item movements belonging to an incoming receipt."""
    return ReceiptReader(OdooClient.from_env()).receipt_moves(picking_id)

@mcp.tool()
def prepare_receipt_qc_handoff(picking_id: int, handoff_id: str, actor: str) -> dict:
    """Prepare a local, idempotent QC request; does not create a PixeSci record."""
    if os.environ.get("ERP_SANDBOX_ONLY", "").lower() != "true":
        raise PermissionError("QC handoff preparation is sandbox-only")
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return ReceiptHandoffService(OdooClient.from_env(), bridge).prepare(
        picking_id, handoff_id, actor)

@mcp.tool()
def inspect_receipt_qc_handoff(handoff_id: str) -> dict:
    """Inspect persisted handoff status; no quality disposition is inferred."""
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return bridge.get(handoff_id) or {"found": False}

@mcp.tool()
def preview_qc_stock_release(handoff_id: str, hold_location_id: int,
                             usable_location_id: int) -> dict:
    """Read-only stock release eligibility; never executes a stock transfer."""
    bridge = QCBridge(os.environ.get("ERP_QC_DB_PATH", "erp-qc.sqlite3"))
    return ReleasePlanner(OdooClient.from_env(), bridge).inspect(
        handoff_id, hold_location_id, usable_location_id)


@mcp.tool()
def reconcile_stock_release(idempotency_key: str) -> dict:
    """Read-only inspection of an Odoo transfer after an uncertain release write.

    Does not retry, approve, validate or move stock. Returns mismatch/ambiguity
    explicitly for human reconciliation.
    """
    from adapter.approval_ledger import ApprovalLedger
    from adapter.stock_reconciliation import StockTransferReconciler
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("A valid idempotency key is required")
    ledger = ApprovalLedger(os.environ.get("ERP_APPROVAL_DB_PATH", "erp-approvals.sqlite3"))
    return StockTransferReconciler(OdooClient.from_env(), ledger).inspect(idempotency_key)

if __name__ == "__main__":
    mcp.run()
