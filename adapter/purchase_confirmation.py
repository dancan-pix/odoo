"""Governed confirmation of existing draft purchase orders."""
import json
import os

class PurchaseConfirmation:
    def __init__(self, client, ledger):
        self.client, self.ledger = client, ledger

    def snapshot(self, order_id):
        if type(order_id) is not int or order_id <= 0:
            raise ValueError("Invalid order ID")
        rows = self.client.execute("purchase.order", "read", [order_id],
            fields=["id","state","partner_id","company_id","currency_id","amount_total","write_date","order_line"])
        if len(rows) != 1:
            raise ValueError("Order not found")
        order = rows[0]
        order["lines"] = self.client.execute("purchase.order.line", "read", order["order_line"],
            fields=["id","product_id","product_qty","product_uom","price_unit","taxes_id","date_planned","write_date"])
        order["lines"].sort(key=lambda line: line["id"])
        return order

    def propose(self, order_id, key, actor):
        if not key or len(key) > 128:
            raise ValueError("Invalid idempotency key")
        order = self.snapshot(order_id)
        if order["state"] not in ("draft", "sent") or not order["lines"]:
            raise ValueError("Only nonempty draft RFQs may be confirmed")
        payload = json.dumps(order, sort_keys=True)
        if not self.ledger.propose(key, "purchase_confirm", payload, actor):
            existing = self.ledger.get(key)
            if (existing["action"], existing["payload_json"], existing["requested_by"]) != ("purchase_confirm", payload, actor):
                raise ValueError("Idempotency conflict")
        return self.ledger.get(key)

    def record(self, key):
        row = self.ledger.get(key)
        if not row or row["action"] != "purchase_confirm":
            raise ValueError("Purchase confirmation not found")
        return row

    def approve(self, key, actor):
        self.record(key)
        return self.ledger.approve(key, actor)

    def execute(self, key, actor):
        if os.environ.get("ERP_WRITE_ENABLED") != "true" or os.environ.get("ERP_SANDBOX_ONLY") != "true":
            raise PermissionError("Sandbox writes disabled")
        row = self.record(key)
        expected = json.loads(row["payload_json"])
        if row["status"] == "executed":
            return {"status": "executed", "order_id": expected["id"]}
        if self.snapshot(expected["id"]) != expected:
            raise ValueError("Order changed since proposal; new approval required")
        if not self.ledger.claim_execution(key, actor):
            raise PermissionError("Independent executor and approved action required")
        try:
            self.client.execute("purchase.order", "button_confirm", [expected["id"]])
            current = self.snapshot(expected["id"])
            if current["state"] not in ("purchase", "done"):
                raise RuntimeError("Odoo did not confirm order; inspect native approval state")
            if not self.ledger.mark_executed(key, actor):
                raise RuntimeError("Execution audit could not be finalized")
        except Exception:
            self.ledger.mark_uncertain(key)
            raise
        return {"status": "executed", "order_id": expected["id"], "odoo_state": current["state"]}

    def reconcile(self, key):
        row = self.record(key)
        expected = json.loads(row["payload_json"])
        current = self.snapshot(expected["id"])
        return {"ledger_status": row["status"], "order_id": expected["id"],
                "odoo_state": current["state"], "confirmed": current["state"] in ("purchase", "done"),
                "manual_review_required": row["status"] in ("executing", "uncertain")}
