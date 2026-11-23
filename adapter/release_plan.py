"""Read-only stock release planning; no Odoo inventory mutation."""
from dataclasses import dataclass
from adapter.qc_bridge import QCBridge
from adapter.odoo_rpc import OdooClient

@dataclass
class ReleasePlanner:
    client: OdooClient
    bridge: QCBridge

    def inspect(self, handoff_id: str, hold_location_id: int,
                usable_location_id: int) -> dict:
        if min(hold_location_id, usable_location_id) <= 0 or hold_location_id == usable_location_id:
            raise ValueError("Distinct positive source and destination locations required")
        handoff = self.bridge.get(handoff_id)
        if not handoff:
            raise LookupError("QC handoff not found")
        if handoff["state"] != "approved":
            return {"eligible": False, "reason": "Verified QA approval not recorded",
                    "handoff_id": handoff_id}
        import json
        payload = json.loads(handoff["payload_json"])
        moves = self.client.execute("stock.move", "search_read",
            [[["picking_id", "=", payload["picking_id"]]]],
            fields=["id", "product_id", "quantity", "state", "location_dest_id"], limit=200)
        if not moves or any(move["state"] != "done" for move in moves):
            return {"eligible": False, "reason": "Receipt is not fully completed"}
        if any(not move["location_dest_id"] or move["location_dest_id"][0] != hold_location_id for move in moves):
            return {"eligible": False, "reason": "Receipt did not enter configured QC hold location"}
        return {"eligible": True, "handoff_id": handoff_id,
                "source_location_id": hold_location_id,
                "destination_location_id": usable_location_id,
                "moves": moves, "execution_enabled": False,
                "reason": "Read-only release plan; stock transfer requires separately governed implementation"}
