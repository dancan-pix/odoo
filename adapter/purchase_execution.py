"""Sandbox purchase lifecycle writes with immutable approved snapshots."""
import json
import os
import re
from dataclasses import dataclass
from adapter.approval_ledger import ApprovalLedger
from adapter.odoo_rpc import OdooClient

@dataclass
class PurchaseExecution:
    odoo: OdooClient
    ledger: ApprovalLedger

    def snapshot(self, order_id):
        company = int(os.environ["ERP_COMPANY_ID"])
        rows = self.odoo.execute("purchase.order", "read", [[order_id]],
            fields=["state", "company_id", "partner_id", "currency_id",
                    "amount_total", "order_line", "write_date"])
        if len(rows) != 1 or rows[0]["company_id"][0] != company:
            raise PermissionError("Order outside configured company")
        order = rows[0]
        ids = order["order_line"]
        if not ids or len(ids) > 500:
            raise ValueError("Unsupported order line count")
        lines = self.odoo.execute("purchase.order.line", "read", [ids],
            fields=["product_id", "product_qty", "product_uom", "price_unit",
                    "taxes_id", "date_planned", "write_date", "qty_received"])
        if len(lines) != len(ids):
            raise RuntimeError("Incomplete order snapshot")
        return {"order": order, "lines": sorted(lines, key=lambda x: x["id"])}

    def propose(self, order_id, operation, actor, key):
        if operation not in ("confirm", "cancel"):
            raise ValueError("Unsupported purchase operation")
        if not actor.strip() or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise ValueError("Actor and valid key required")
        snapshot = self.snapshot(order_id)
        if snapshot["order"]["state"] not in ("draft", "sent"):
            raise PermissionError("Only unconfirmed RFQs are supported")
        if any(line["qty_received"] for line in snapshot["lines"]):
            raise PermissionError("Received quantities prevent this operation")
        payload = json.dumps({"order_id": order_id, "snapshot": snapshot},
                             sort_keys=True, allow_nan=False)
        action = "purchase_" + operation
        created = self.ledger.propose(key, action, payload, actor)
        row = self.ledger.get(key)
        if row["action"] != action or row["payload_json"] != payload or row["requested_by"] != actor:
            raise ValueError("Idempotency key conflict")
        return {"created": created, "status": row["status"], "preview": snapshot}

    def approve(self, key, actor):
        row = self.ledger.get(key)
        if not actor.strip() or not row or row["action"] not in ("purchase_confirm", "purchase_cancel"):
            raise PermissionError("Valid purchase proposal and approver required")
        return self.ledger.approve(key, actor)

    def execute(self, key, actor):
        if any(os.environ.get(k, "").lower() != "true" for k in
               ("ERP_SANDBOX_ONLY", "ERP_WRITE_ENABLED")):
            raise PermissionError("Sandbox writes not enabled")
        row = self.ledger.get(key)
        if not actor.strip() or not row or row["status"] != "approved" or row["action"] not in ("purchase_confirm", "purchase_cancel"):
            raise PermissionError("Approved purchase action required")
        payload = json.loads(row["payload_json"])
        order_id = payload["order_id"]
        if self.snapshot(order_id) != payload["snapshot"]:
            raise PermissionError("Order changed since proposal")
        if not self.ledger.claim_execution(key, actor):
            raise PermissionError("Execution claim or separation of duties failed")
        confirm = row["action"] == "purchase_confirm"
        try:
            self.odoo.execute("purchase.order", "button_confirm" if confirm else "button_cancel", [order_id])
            state = self.snapshot(order_id)["order"]["state"]
            if state != ("purchase" if confirm else "cancel"):
                raise RuntimeError("Expected terminal state not reached; reconcile manually")
            if not self.ledger.mark_executed(key, actor):
                raise RuntimeError("Ledger completion failed")
            return {"order_id": order_id, "state": state, "status": "executed"}
        except Exception:
            self.ledger.mark_uncertain(key)
            raise

    def reconcile(self, key):
        row = self.ledger.get(key)
        if not row or row["action"] not in ("purchase_confirm", "purchase_cancel"):
            raise LookupError("Purchase proposal not found")
        payload = json.loads(row["payload_json"])
        current = self.snapshot(payload["order_id"])
        return {"ledger_status": row["status"], "current": current,
                "approved_snapshot": payload["snapshot"],
                "expected_state": "purchase" if row["action"] == "purchase_confirm" else "cancel",
                "manual_review_required": row["status"] in ("executing", "uncertain")}
