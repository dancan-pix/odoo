"""Governed creation of draft RFQs; never confirms an order."""
import hashlib
import json
import math
import os
from datetime import datetime

class DraftPurchase:
    def __init__(self, client, ledger):
        self.client, self.ledger = client, ledger

    def record(self, key):
        row = self.ledger.get(key)
        if not row or row["action"] != "purchase_draft_create":
            raise ValueError("Draft purchase proposal not found")
        return row

    def propose(self, supplier_id, lines, key, actor):
        if type(supplier_id) is not int or supplier_id <= 0 or not key or len(key) > 128:
            raise ValueError("Invalid supplier or idempotency key")
        if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
            raise ValueError("Expected 1-100 purchase lines")
        clean = []
        for line in lines:
            if set(line) != {"product_id", "quantity", "unit_price", "planned_date"}:
                raise ValueError("Unexpected purchase line fields")
            product = line["product_id"]
            qty, price = line["quantity"], line["unit_price"]
            if type(product) is not int or product <= 0:
                raise ValueError("Invalid product")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (qty, price)) or qty <= 0 or price < 0:
                raise ValueError("Invalid quantity or price")
            date = datetime.strptime(line["planned_date"], "%Y-%m-%d %H:%M:%S")
            clean.append(dict(product_id=product, quantity=qty, unit_price=price,
                              planned_date=date.strftime("%Y-%m-%d %H:%M:%S")))
        payload = json.dumps({"supplier_id": supplier_id, "lines": clean}, sort_keys=True)
        if not self.ledger.propose(key, "purchase_draft_create", payload, actor):
            row = self.record(key)
            if row["payload_json"] != payload or row["requested_by"] != actor:
                raise ValueError("Idempotency conflict")
        return self.record(key)

    def approve(self, key, actor):
        self.record(key)
        return self.ledger.approve(key, actor)

    def reference(self, key):
        return "ERP-RFQ-" + hashlib.sha256(key.encode()).hexdigest()

    def matches(self, key):
        return self.client.execute("purchase.order", "search_read",
            [[["partner_ref", "=", self.reference(key)]]],
            fields=["id", "name", "state", "partner_id", "order_line"], limit=2)

    def execute(self, key, actor):
        if os.environ.get("ERP_WRITE_ENABLED") != "true" or os.environ.get("ERP_SANDBOX_ONLY") != "true":
            raise PermissionError("Sandbox writes disabled")
        row = self.record(key)
        if row["status"] == "executed":
            return self.reconcile(key)
        payload = json.loads(row["payload_json"])
        supplier = self.client.execute("res.partner", "search_read",
            [[["id", "=", payload["supplier_id"]], ["supplier_rank", ">", 0]]],
            fields=["id"], limit=1)
        if not supplier:
            raise ValueError("Active supplier required")
        commands = []
        for line in payload["lines"]:
            products = self.client.execute("product.product", "search_read",
                [[["id", "=", line["product_id"]], ["purchase_ok", "=", True]]],
                fields=["id", "display_name", "uom_po_id"], limit=1)
            if not products or not products[0]["uom_po_id"]:
                raise ValueError("Purchasable product with purchase UOM required")
            product = products[0]
            commands.append([0, 0, {"product_id": product["id"], "name": product["display_name"],
                "product_uom": product["uom_po_id"][0], "product_qty": line["quantity"],
                "price_unit": line["unit_price"], "date_planned": line["planned_date"]}])
        if not self.ledger.claim_execution(key, actor):
            raise PermissionError("Independent executor and approved action required")
        try:
            if self.matches(key):
                raise RuntimeError("Existing RFQ reference; manual reconciliation required")
            order_id = self.client.execute("purchase.order", "create",
                {"partner_id": payload["supplier_id"], "partner_ref": self.reference(key),
                 "order_line": commands})
            if type(order_id) is not int or order_id <= 0:
                raise RuntimeError("Invalid Odoo order identity")
            matches = self.matches(key)
            if len(matches) != 1 or matches[0]["id"] != order_id or matches[0]["state"] != "draft":
                raise RuntimeError("Draft RFQ readback mismatch")
            if not self.ledger.mark_executed(key, actor):
                raise RuntimeError("Execution audit could not be finalized")
        except Exception:
            self.ledger.mark_uncertain(key)
            raise
        return {"status": "executed", "order_id": order_id, "order_state": "draft"}

    def reconcile(self, key):
        row = self.record(key)
        return {"ledger_status": row["status"], "matching_orders": self.matches(key),
                "manual_review_required": True}
