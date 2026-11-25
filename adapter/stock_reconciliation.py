"""Read-only Odoo stock-transfer reconciliation for uncertain remote writes.

Never recreates a transfer, validates stock, or converts an uncertain action
into an authorization. Reconciliation requires an exact origin and move match.
"""
import json
from .stock_execution import verify_transfer_allocations, _same
from collections import Counter


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
            fields=["id", "product_id", "product_uom_qty", "quantity", "state"],
            limit=1001,
        )
        expected = sorted((int(x["product_id"]), float(x["quantity"]))
                          for x in payload["move_quantities"])
        actual = sorted((int(x["product_id"][0]), float(x["product_uom_qty"]))
                        for x in moves)
        locations_match = (
            picking["location_id"][0] == payload["source_location_id"] and
            picking["location_dest_id"][0] == payload["destination_location_id"]
        )
        allocation_status = "missing_approval_evidence"
        if payload.get("allocations"):
            try:
                verify_transfer_allocations(self.odoo, picking["id"], payload)
                allocation_status = "matched"
            except (RuntimeError, PermissionError, ValueError, KeyError, TypeError):
                allocation_status = "mismatch_or_unavailable"
        expected_totals = Counter()
        completed_totals = Counter()
        for product, quantity in expected:
            expected_totals[product] += quantity
        for move in moves:
            completed_totals[move["product_id"][0]] += move["quantity"]
        completed = (
            picking["state"] == "done" and bool(moves) and len(moves) <= 1000
            and all(m["state"] == "done" for m in moves)
            and completed_totals.keys() == expected_totals.keys()
            and all(_same(completed_totals[k], expected_totals[k]) for k in expected_totals)
        )
        exact = (expected == actual and locations_match and completed
                 and allocation_status == "matched")
        return {"status": "matched" if exact else "mismatch",
                "action_status": row["status"],
                "picking_id": picking["id"],
                "picking_state": picking["state"],
                "inventory_moved": picking["state"] == "done",
                "completed_quantities_match": completed,
                "allocation_status": allocation_status,
                "locations_match": locations_match,
                "expected_move_count": len(expected),
                "actual_move_count": len(actual),
                "safe_to_retry": False,
                "requires_manual_review": True}
