"""Governed sandbox supplier creation with persistent execution claims."""
import json
import os

class SupplierCreation:
    def __init__(self, client, ledger):
        self.client, self.ledger = client, ledger

    def propose(self, name, email, key, actor):
        name, email = name.strip(), email.strip().lower()
        if not name or len(name) > 200 or len(email) > 254 or (email and "@" not in email):
            raise ValueError("Invalid supplier details")
        if not key or len(key) > 128:
            raise ValueError("Invalid idempotency key")
        payload = json.dumps({"name": name, "email": email}, sort_keys=True)
        if not self.ledger.propose(key, "supplier_create", payload, actor):
            row = self.record(key)
            if row["payload_json"] != payload or row["requested_by"] != actor:
                raise ValueError("Idempotency conflict")
        return self.record(key)

    def record(self, key):
        row = self.ledger.get(key)
        if not row or row["action"] != "supplier_create":
            raise ValueError("Supplier proposal not found")
        return row

    def approve(self, key, actor):
        self.record(key)
        return self.ledger.approve(key, actor)

    def matches(self, values):
        domain = [["name", "=ilike", values["name"]]]
        if values["email"]:
            domain = ["|", *domain, ["email", "=ilike", values["email"]]]
        return self.client.execute("res.partner", "search_read", [domain],
            fields=["id", "name", "email", "supplier_rank"], limit=20,
            context={"active_test": False})

    def execute(self, key, actor):
        if os.environ.get("ERP_WRITE_ENABLED") != "true" or os.environ.get("ERP_SANDBOX_ONLY") != "true":
            raise PermissionError("Sandbox writes disabled")
        row = self.record(key)
        values = json.loads(row["payload_json"])
        if row["status"] == "executed":
            return self.reconcile(key)
        if self.matches(values):
            raise ValueError("Existing partner matches supplier details; review before creation")
        if not self.ledger.claim_execution(key, actor):
            raise PermissionError("Independent executor and approved action required")
        try:
            if self.matches(values):
                raise ValueError("Partner appeared after proposal")
            supplier_id = self.client.execute("res.partner", "create",
                {**values, "supplier_rank": 1, "is_company": True})
            if type(supplier_id) is not int or supplier_id <= 0:
                raise RuntimeError("Invalid Odoo supplier identity")
            if not self.ledger.mark_executed(key, actor):
                raise RuntimeError("Execution audit could not be finalized")
        except Exception:
            self.ledger.mark_uncertain(key)
            raise
        return {"status": "executed", "supplier_id": supplier_id}

    def reconcile(self, key):
        row = self.record(key)
        return {"ledger_status": row["status"],
                "matching_partners": self.matches(json.loads(row["payload_json"])),
                "manual_review_required": True}
