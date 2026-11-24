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

    def propose_create(self, supplier_id, lines, actor, key):
        """Approve an exact RFQ payload before creating it in Odoo."""
        import math
        from datetime import datetime
        company = int(os.environ["ERP_COMPANY_ID"])
        if not actor.strip() or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise ValueError("Actor and valid key required")
        if not 1 <= len(lines) <= 100:
            raise ValueError("One to 100 lines required")
        supplier = self.odoo.execute("res.partner", "search_read",
            [[["id", "=", supplier_id], ["active", "=", True],
              ["company_id", "in", [False, company]]]], fields=["id"], limit=1)
        if not supplier:
            raise PermissionError("Supplier unavailable")
        normalized = []
        for line in lines:
            product_id = int(line["product_id"])
            qty, price = float(line["quantity"]), float(line["unit_price"])
            if not math.isfinite(qty) or not math.isfinite(price) or qty <= 0 or price < 0:
                raise ValueError("Invalid quantity or price")
            planned = datetime.strptime(line["date_planned"], "%Y-%m-%d %H:%M:%S")
            products = self.odoo.execute("product.product", "search_read",
                [[["id", "=", product_id], ["purchase_ok", "=", True],
                  ["company_id", "in", [False, company]]]],
                fields=["id", "display_name", "uom_po_id"], limit=1)
            if not products or not products[0]["uom_po_id"]:
                raise PermissionError("Product unavailable for purchasing")
            product = products[0]
            normalized.append({"product_id": product_id, "name": product["display_name"],
                "product_qty": qty, "price_unit": price,
                "product_uom": product["uom_po_id"][0],
                "date_planned": planned.strftime("%Y-%m-%d %H:%M:%S")})
        payload = json.dumps({"company_id": company, "partner_id": supplier_id,
                              "lines": normalized}, sort_keys=True, allow_nan=False)
        created = self.ledger.propose(key, "purchase_create", payload, actor)
        row = self.ledger.get(key)
        if row["action"] != "purchase_create" or row["payload_json"] != payload or row["requested_by"] != actor:
            raise ValueError("Idempotency key conflict")
        return {"created": created, "status": row["status"], "preview": json.loads(payload)}

    def execute_create(self, key, actor, row):
        payload = json.loads(row["payload_json"])
        if payload["company_id"] != int(os.environ["ERP_COMPANY_ID"]):
            raise PermissionError("Company configuration changed")
        if not self.ledger.claim_execution(key, actor):
            raise PermissionError("Execution claim failed")
        try:
            order_id = self.odoo.execute("purchase.order", "create", {
                "company_id": payload["company_id"], "partner_id": payload["partner_id"],
                "origin": "PIXESCI-RFQ:" + key,
                "order_line": [[0, 0, line] for line in payload["lines"]]})
            current = self.snapshot(order_id)
            if current["order"]["state"] != "draft":
                raise RuntimeError("Expected draft RFQ")
            if not self.ledger.mark_executed(key, actor):
                raise RuntimeError("Ledger completion failed")
            return {"order_id": order_id, "state": "draft", "status": "executed"}
        except Exception:
            self.ledger.mark_uncertain(key)
            raise

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
        if not actor.strip() or not row or row["action"] not in ("purchase_confirm", "purchase_cancel", "purchase_create"):
            raise PermissionError("Valid purchase proposal and approver required")
        return self.ledger.approve(key, actor)

    def execute(self, key, actor):
        if any(os.environ.get(k, "").lower() != "true" for k in
               ("ERP_SANDBOX_ONLY", "ERP_WRITE_ENABLED")):
            raise PermissionError("Sandbox writes not enabled")
        row = self.ledger.get(key)
        if not actor.strip() or not row or row["status"] != "approved" or row["action"] not in ("purchase_confirm", "purchase_cancel", "purchase_create"):
            raise PermissionError("Approved purchase action required")
        if row["action"] == "purchase_create":
            return self.execute_create(key, actor, row)
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
        if not row or row["action"] not in ("purchase_confirm", "purchase_cancel", "purchase_create"):
            raise LookupError("Purchase proposal not found")
        payload = json.loads(row["payload_json"])
        if row["action"] == "purchase_create":
            matches = self.odoo.execute("purchase.order", "search_read",
                [[["company_id", "=", payload["company_id"]], ["origin", "=", "PIXESCI-RFQ:" + key]]],
                fields=["id", "name", "state"], limit=100)
            return {"ledger_status": row["status"], "matching_orders": matches,
                    "manual_review_required": row["status"] in ("executing", "uncertain") or len(matches) != 1}
        current = self.snapshot(payload["order_id"])
        return {"ledger_status": row["status"], "current": current,
                "approved_snapshot": payload["snapshot"],
                "expected_state": "purchase" if row["action"] == "purchase_confirm" else "cancel",
                "manual_review_required": row["status"] in ("executing", "uncertain")}
