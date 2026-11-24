"""Guarded Odoo inventory release: verified PixeSci QA decision, independent approval,
atomic claim, and fail-closed remote-write recovery.

This adapter does not accept caller-provided QA approval flags. A verifier must
query the authoritative PixeSci service and independently authenticate its result.
"""
import json
import os
from dataclasses import dataclass
from adapter.approval_ledger import ApprovalLedger
from adapter.qc_bridge import QCBridge
from adapter.release_plan import ReleasePlanner
from adapter.odoo_rpc import OdooClient
from adapter.stock_execution import complete_untracked_transfer, preflight_release


@dataclass
class QualityRelease:
    odoo: OdooClient
    bridge: QCBridge
    ledger: ApprovalLedger
    verify_pixescis_qa: object

    def propose(self, handoff_id: str, source_location_id: int,
                destination_location_id: int, actor: str, key: str) -> dict:
        if not actor.strip() or not key.strip():
            raise ValueError("Actor and idempotency key required")
        plan = ReleasePlanner(self.odoo, self.bridge).inspect(
            handoff_id, source_location_id, destination_location_id)
        if not plan.get("eligible"):
            raise PermissionError(plan.get("reason", "Release not eligible"))
        handoff = self.bridge.get(handoff_id)
        if not handoff or not handoff["pixesci_request_id"]:
            raise PermissionError("PixeSci QC request must be linked")
        if not self.verify_pixescis_qa(handoff["pixesci_request_id"]):
            raise PermissionError("Authoritative PixeSci QA release verification failed")
        moves = plan["moves"]
        if any(not isinstance(m.get("quantity"), (int, float)) or
               isinstance(m.get("quantity"), bool) or m["quantity"] <= 0 for m in moves):
            raise ValueError("Receipt movements must have positive completed quantities")
        payload = {
            "handoff_id": handoff_id,
            "pixesci_request_id": handoff["pixesci_request_id"],
            "picking_id": json.loads(handoff["payload_json"])["picking_id"],
            "source_location_id": source_location_id,
            "destination_location_id": destination_location_id,
            "move_quantities": [{"move_id": m["id"], "product_id": m["product_id"][0],
                                 "quantity": m["quantity"]} for m in moves],
        }
        created = self.ledger.propose(key, "qc_stock_release", json.dumps(payload, sort_keys=True), actor)
        existing = self.ledger.get(key)
        if existing["action"] != "qc_stock_release" or existing["payload_json"] != json.dumps(payload, sort_keys=True):
            raise ValueError("Idempotency key already used with different action or payload")
        return {"idempotency_key": key, "created": created, "status": existing["status"],
                "requires_independent_approval": True, "preview": payload}

    def approve(self, key: str, approver: str) -> bool:
        row = self.ledger.get(key)
        if not row or row["action"] != "qc_stock_release":
            raise LookupError("Release proposal not found")
        return self.ledger.approve(key, approver)

    def execute(self, key: str, executor: str) -> dict:
        if os.environ.get("ERP_SANDBOX_ONLY", "").lower() != "true" or (
            os.environ.get("ERP_WRITE_ENABLED", "").lower() != "true"):
            raise PermissionError("ERP stock release requires explicit sandbox write enablement")
        row = self.ledger.get(key)
        if not row or row["action"] != "qc_stock_release" or row["status"] != "approved":
            raise PermissionError("Independently approved stock-release proposal required")
        payload = json.loads(row["payload_json"])
        fresh = ReleasePlanner(self.odoo, self.bridge).inspect(
            payload["handoff_id"], payload["source_location_id"],
            payload["destination_location_id"])
        if not fresh.get("eligible"):
            raise PermissionError("Release is no longer eligible")
        handoff = self.bridge.get(payload["handoff_id"])
        if not handoff or handoff["pixesci_request_id"] != payload["pixesci_request_id"] or (
            not self.verify_pixescis_qa(handoff["pixesci_request_id"])):
            raise PermissionError("Current PixeSci QA authorization could not be verified")
        actual = [{"move_id": m["id"], "product_id": m["product_id"][0],
                   "quantity": m["quantity"]} for m in fresh["moves"]]
        if actual != payload["move_quantities"]:
            raise PermissionError("Odoo receipt movements changed since approval")
        if not executor.strip():
            raise ValueError("Executor required")
        preflight_release(self.odoo, payload)
        if not self.ledger.claim_execution(key, executor):
            raise PermissionError("Execution claim failed or separation of duties violated")
        # A remote call may succeed even when the connection times out. Never auto-retry.
        try:
            picking_id = complete_untracked_transfer(self.odoo, payload, key)
            if not self.ledger.mark_executed(key, executor):
                raise RuntimeError("Could not persist completed transfer")
            return {"status": "executed", "picking_id": picking_id,
                    "inventory_moved": True, "requires_odoo_validation": False}
        except Exception:
            self.ledger.mark_uncertain(key)
            raise
