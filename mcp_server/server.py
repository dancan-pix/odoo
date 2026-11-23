"""Read-only PyxAI MCP bridge. Install 'mcp' and set Odoo credentials to run."""
from mcp.server.fastmcp import FastMCP
from adapter.odoo_rpc import OdooClient
from adapter.receipts import ReceiptReader

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

if __name__ == "__main__":
    mcp.run()
