"""Read-only PyxAI MCP bridge. Install 'mcp' and set Odoo credentials to run."""
from mcp.server.fastmcp import FastMCP
from adapter.odoo_rpc import OdooClient

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

if __name__ == "__main__":
    mcp.run()
