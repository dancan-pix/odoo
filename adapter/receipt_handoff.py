"""Prepare receipt QC requests from actual Odoo records without sending them."""
from dataclasses import dataclass
from adapter.odoo_rpc import OdooClient
from adapter.receipts import ReceiptReader
from adapter.qc_bridge import QCBridge

@dataclass
class ReceiptHandoffService:
    client: OdooClient
    bridge: QCBridge

    def prepare(self, picking_id: int, handoff_id: str, actor: str) -> dict:
        if picking_id <= 0:
            raise ValueError("Positive picking ID required")
        rows = self.client.execute("stock.picking", "read", [picking_id],
            fields=["id", "picking_type_code", "state", "purchase_id", "origin"])
        if len(rows) != 1 or rows[0].get("picking_type_code") != "incoming":
            raise ValueError("Only an incoming receipt can create a QC handoff")
        receipt = rows[0]
        purchase = receipt.get("purchase_id")
        if not purchase or not isinstance(purchase, (list, tuple)) or not purchase[0]:
            raise ValueError("Incoming receipt must reference an Odoo purchase order")
        moves = ReceiptReader(self.client).receipt_moves(picking_id)
        lines = []
        for move in moves:
            product = move.get("product_id")
            if move.get("state") == "cancel":
                continue
            if not product or not isinstance(product, (list, tuple)):
                raise ValueError("Receipt move missing product")
            qty = move.get("quantity") if move.get("state") == "done" else move.get("product_uom_qty")
            if not isinstance(qty, (int, float)) or qty <= 0:
                raise ValueError("Receipt contains an invalid quantity")
            lines.append({"move_id": move["id"], "product_id": product[0],
                          "quantity": qty})
        return self.bridge.prepare(handoff_id, picking_id, purchase[0], lines, actor)
