"""Cross-system correlation and audit events. Synthetic IDs only until integration is validated."""
import json
import sqlite3
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS correlations (
  odoo_model TEXT NOT NULL, odoo_id INTEGER NOT NULL,
  pixesci_type TEXT NOT NULL, pixesci_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(odoo_model, odoo_id, pixesci_type, pixesci_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
  correlation_id TEXT NOT NULL, actor TEXT NOT NULL,
  details_json TEXT NOT NULL, occurred_at TEXT NOT NULL
);
"""

class CorrelationStore:
    def __init__(self, path: str):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript(DDL)

    def link(self, odoo_model: str, odoo_id: int, pixesci_type: str, pixesci_id: str):
        if odoo_id <= 0 or not all([odoo_model, pixesci_type, pixesci_id]):
            raise ValueError("Invalid cross-system reference")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR IGNORE INTO correlations VALUES (?,?,?,?,?)",
                       (odoo_model, odoo_id, pixesci_type, pixesci_id,
                        datetime.now(timezone.utc).isoformat()))

    def record(self, event_id: str, event_type: str, correlation_id: str, actor: str, details: dict):
        if not all([event_id, event_type, correlation_id, actor]):
            raise ValueError("Audit event metadata required")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
                       (event_id, event_type, correlation_id, actor,
                        json.dumps(details, sort_keys=True), datetime.now(timezone.utc).isoformat()))
