"""Prepare receipt QC requests from actual Odoo records without sending them."""
from dataclasses import dataclass
from math import isfinite
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
        if receipt.get("state") != "done":
            raise ValueError("QC handoff requires a completed receipt")
        allocations = self.client.execute("stock.move.line", "search_read",
            [[["picking_id", "=", picking_id]]],
            fields=["id", "move_id", "product_id", "lot_id", "quantity",
                    "product_uom_id", "location_dest_id"], order="id")
        def relation(row, field, optional=False):
            value = row.get(field)
            if optional and not value:
                return None
            if not isinstance(value, (list, tuple)) or not value or not isinstance(value[0], int) or value[0] <= 0:
                raise ValueError("Invalid receipt allocation relation: " + field)
            return value[0]
        by_move = {}
        for allocation in allocations:
            quantity = allocation.get("quantity")
            if not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or not isfinite(quantity) or quantity < 0:
                raise ValueError("Invalid receipt allocation quantity")
            if quantity == 0:
                continue
            move_id = relation(allocation, "move_id")
            by_move.setdefault(move_id, []).append({
                "move_line_id": allocation["id"],
                "product_id": relation(allocation, "product_id"),
                "lot_id": relation(allocation, "lot_id", optional=True),
                "quantity": quantity,
                "uom_id": relation(allocation, "product_uom_id"),
                "destination_location_id": relation(allocation, "location_dest_id"),
            })
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
            if move.get("state") != "done" or not isfinite(qty):
                raise ValueError("Receipt contains an incomplete movement")
            details = by_move.pop(move["id"], [])
            if not details or any(d["product_id"] != product[0] for d in details):
                raise ValueError("Missing or inconsistent receipt allocations")
            products = self.client.execute("product.product", "read", [product[0]],
                                           fields=["tracking"])
            if len(products) != 1 or products[0].get("tracking") not in ("none", "lot", "serial"):
                raise ValueError("Unknown product tracking configuration")
            tracking = products[0]["tracking"]
            if tracking != "none" and any(d["lot_id"] is None for d in details):
                raise ValueError("Tracked receipt allocation is missing its lot")
            lines.append({"move_id": move["id"], "product_id": product[0],
                          "quantity": qty, "tracking": tracking,
                          "allocations": details})
        if by_move:
            raise ValueError("Receipt allocations reference unexpected movements")
        lines.sort(key=lambda line: line["move_id"])
        return self.bridge.prepare(handoff_id, picking_id, purchase[0], lines, actor)
