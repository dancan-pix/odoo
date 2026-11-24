"""Read-only Odoo stock-transfer reconciliation for uncertain remote writes.

Never recreates a transfer, validates stock, or converts an uncertain action
into an authorization. Reconciliation requires an exact origin and move match.
"""
import json


class StockTransferReconciler:
    def __init__(self, odoo, ledger):
        self.odoo = odoo
        self.ledger = ledger

    def inspect(self, key: str) -> dict:
        row = self.ledger.get(key)
        if not row or row["action"] != "qc_stock_release":
            raise LookupError("Stock-release action not found")
        payload = json.loads(row["payload_json"])
        origin = "PixeSci QA release " + key
        pickings = self.odoo.execute(
            "stock.picking", "search_read", [[["origin", "=", origin]]],
            fields=["id", "origin", "state", "location_id", "location_dest_id"],
            limit=3,
        )
        if len(pickings) != 1:
            return {"status": "missing" if not pickings else "ambiguous",
                    "action_status": row["status"], "matches": len(pickings),
                    "safe_to_retry": False}
        picking = pickings[0]
        moves = self.odoo.execute(
            "stock.move", "search_read", [[["picking_id", "=", picking["id"]]]],
            fields=["id", "product_id", "product_uom_qty", "state"],
            limit=1000,
        )
        expected = sorted((int(x["product_id"]), float(x["quantity"]))
                          for x in payload["move_quantities"])
        actual = sorted((int(x["product_id"][0]), float(x["product_uom_qty"]))
                        for x in moves)
        locations_match = (
            picking["location_id"][0] == payload["source_location_id"] and
            picking["location_dest_id"][0] == payload["destination_location_id"]
        )
        exact = expected == actual and locations_match
        return {"status": "matched" if exact else "mismatch",
                "action_status": row["status"],
                "picking_id": picking["id"],
                "picking_state": picking["state"],
                "inventory_moved": picking["state"] == "done",
                "expected_move_count": len(expected),
                "actual_move_count": len(actual),
                "safe_to_retry": False,
                "requires_manual_review": True}
