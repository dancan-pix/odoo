"""Read-only receipt and purchase-line queries with explicit fields."""
from adapter.odoo_rpc import OdooClient

class ReceiptReader:
    def __init__(self, client: OdooClient):
        self.client = client

    def incoming_receipts(self, limit: int = 50) -> list[dict]:
        return self.client.execute(
            "stock.picking", "search_read",
            [["picking_type_code", "=", "incoming"]],
            fields=["id", "name", "origin", "state", "partner_id", "scheduled_date",
                    "location_id", "location_dest_id"],
            limit=min(max(limit, 1), 100))

    def purchase_lines(self, purchase_order_id: int) -> list[dict]:
        if purchase_order_id <= 0:
            raise ValueError("Purchase order ID must be positive")
        return self.client.execute(
            "purchase.order.line", "search_read",
            [["order_id", "=", purchase_order_id]],
            fields=["id", "order_id", "product_id", "product_qty", "qty_received", "price_unit"],
            limit=200)

    def receipt_moves(self, picking_id: int) -> list[dict]:
        if picking_id <= 0:
            raise ValueError("Picking ID must be positive")
        return self.client.execute(
            "stock.move", "search_read",
            [["picking_id", "=", picking_id]],
            fields=["id", "product_id", "product_uom_qty", "quantity", "state",
                    "location_id", "location_dest_id"],
            limit=200)
