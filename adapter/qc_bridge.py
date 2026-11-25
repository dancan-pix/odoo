"""Durable Odoo receipt → PixeSci QC handoff with no implicit release.

This module stores an outbox for a future authenticated PixeSci API integration.
It never fabricates QC approval or moves inventory.
"""
import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS qc_handoffs (
  handoff_id TEXT PRIMARY KEY,
  odoo_picking_id INTEGER NOT NULL,
  odoo_purchase_id INTEGER NOT NULL,
  pixesci_request_id TEXT,
  state TEXT NOT NULL CHECK(state IN ('pending','submitted','under_review','approved','rejected')),
  payload_json TEXT NOT NULL,
  disposition_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS qc_handoff_receipt ON qc_handoffs(odoo_picking_id);
CREATE TABLE IF NOT EXISTS qc_handoff_events (
  event_id TEXT PRIMARY KEY,
  handoff_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  details_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);
"""

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class QCBridge:
    path: str

    def __post_init__(self):
        with sqlite3.connect(self.path) as db:
            db.executescript(DDL)

    def prepare(self, handoff_id: str, picking_id: int, purchase_id: int,
                lines: list[dict[str, Any]], actor: str) -> dict:
        if not handoff_id.strip() or not actor.strip() or picking_id <= 0 or purchase_id <= 0:
            raise ValueError("Valid handoff ID, receipt, purchase and actor required")
        if not lines or any(not isinstance(line.get("product_id"), int) or
                            line["product_id"] <= 0 or
                            not isinstance(line.get("quantity"), (int, float)) or
                            isinstance(line.get("quantity"), bool) or line["quantity"] <= 0
                            for line in lines):
            raise ValueError("Every QC line needs a product ID and positive quantity")
        payload = json.dumps({"picking_id": picking_id, "purchase_id": purchase_id,
                              "lines": lines}, sort_keys=True)
        now = utcnow()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO qc_handoffs VALUES (?,?,?,?,?,?,?,?,?)",
                       (handoff_id, picking_id, purchase_id, None, "pending", payload, None, now, now))
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM qc_handoffs WHERE handoff_id=? OR odoo_picking_id=?",
                             (handoff_id, picking_id)).fetchone()
            if row["handoff_id"] != handoff_id or row["payload_json"] != payload:
                raise ValueError("Conflicting handoff or receipt already exists")
            return {"handoff_id": handoff_id, "state": row["state"],
                    "receipt_sha256": hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest(),
                    "payload": json.loads(row["payload_json"])}

    def acknowledge_submission(self, handoff_id: str, pixesci_request_id: str) -> bool:
        """Call only after a verified PixeSci API response; no fabricated IDs."""
        if not pixesci_request_id.strip():
            raise ValueError("PixeSci request ID required")
        with sqlite3.connect(self.path) as db:
            return db.execute("UPDATE qc_handoffs SET state='submitted', pixesci_request_id=?, updated_at=? "
                              "WHERE handoff_id=? AND state='pending'",
                              (pixesci_request_id, utcnow(), handoff_id)).rowcount == 1

    def get(self, handoff_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM qc_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
            return dict(row) if row else None

    def record_disposition(self, handoff_id: str, disposition: dict, actor: str,
                           verifier) -> bool:
        """Verifier MUST validate signed server-to-server QA evidence against PixeSci.

        Caller-supplied booleans and actor names are never sufficient authorization.
        """
        if not actor.strip() or disposition.get("decision") not in ("approved", "rejected"):
            raise ValueError("Valid actor and disposition required")
        row = self.get(handoff_id)
        if not row or row["state"] not in ("submitted", "under_review"):
            raise ValueError("Handoff is not awaiting QC disposition")
        # Bind the authoritative disposition to the immutable receipt snapshot.
        # Legacy approvals without this binding must not authorize stock release.
        expected_digest = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
        if disposition.get("receipt_sha256") != expected_digest:
            raise PermissionError("QA disposition does not identify the exact receipt snapshot")
        if disposition.get("request_id") != row["pixesci_request_id"]:
            raise PermissionError("QA disposition request identity mismatch")
        if not verifier(row["pixesci_request_id"], disposition):
            raise PermissionError("PixeSci QA evidence verification failed")
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "UPDATE qc_handoffs SET state=?, disposition_json=?, updated_at=? "
                "WHERE handoff_id=? AND state IN ('submitted','under_review')",
                (disposition["decision"], json.dumps(disposition, sort_keys=True),
                 utcnow(), handoff_id)).rowcount == 1
