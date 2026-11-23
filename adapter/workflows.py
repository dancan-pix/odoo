"""Governed Odoo receiving workflow. No automatic stock release."""
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from adapter.odoo_rpc import OdooClient
from adapter.approval_ledger import ApprovalLedger

ALLOWED_ACTIONS = frozenset({"confirm_purchase_order", "validate_receipt"})
@dataclass
class ERPWorkflow:
    client: OdooClient
    ledger: ApprovalLedger

    @classmethod
    def from_env(cls):
        return cls(OdooClient.from_env(), ApprovalLedger(os.environ.get("ERP_LEDGER_PATH", "erp-actions.sqlite3")))

    def propose(self, action: str, record_id: int, actor: str, key: str) -> dict[str, Any]:
        if action not in ALLOWED_ACTIONS:
            raise ValueError("Action is not supported")
        if not isinstance(record_id, int) or record_id <= 0 or not actor.strip() or not key.strip():
            raise ValueError("A positive record ID, actor and idempotency key are required")
        payload = json.dumps({"record_id": record_id}, sort_keys=True)
        created = self.ledger.propose(key, action, payload, actor)
        existing = self.ledger.get(key)
        if existing["action"] != action or existing["payload_json"] != payload:
            raise ValueError("Idempotency key was reused for a different action")
        return {"idempotency_key": key, "created": created, "status": existing["status"]}

    def approve(self, key: str, approver: str) -> dict:
        if not approver.strip():
            raise ValueError("Approver is required")
        return {"approved": self.ledger.approve(key, approver), "idempotency_key": key}

    def execute(self, key: str, executor: str) -> dict:
        """Explicit sandbox-only execution; never releases QC stock automatically."""
        if os.environ.get("ERP_WRITE_ENABLED", "").lower() != "true":
            raise PermissionError("ERP writes are disabled")
        if os.environ.get("ERP_SANDBOX_ONLY", "").lower() != "true":
            raise PermissionError("ERP_SANDBOX_ONLY=true is required")
        row = self.ledger.get(key)
        if not row or row["status"] != "approved":
            raise PermissionError("Action must be approved before execution")
        if executor in (row["requested_by"], row["approved_by"]):
            raise PermissionError("Executor must be independent of requester and approver")
        record_id = json.loads(row["payload_json"])["record_id"]
        if row["action"] == "confirm_purchase_order":
            model, method = "purchase.order", "button_confirm"
        elif row["action"] == "validate_receipt":
            raise NotImplementedError("Receipt validation requires QC hold-location mapping and QA evidence")
        else:
            raise ValueError("Unsupported action")
        # Note: distributed execution safety requires a transactional outbox before production.
        result = self.client.execute(model, method, [record_id])
        self.ledger.mark_executed(key, executor)
        return {"idempotency_key": key, "status": "executed", "result": result}
