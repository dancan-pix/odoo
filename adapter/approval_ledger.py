"""SQLite-backed approval and idempotency ledger for proposed ERP actions."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    idempotency_key TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('proposed','approved','executed','rejected')),
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);
"""

class ApprovalLedger:
    def __init__(self, path: str):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)

    def propose(self, key: str, action: str, payload_json: str, actor: str) -> bool:
        with sqlite3.connect(self.path) as db:
            result = db.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,?,?,?)",
                (key, action, payload_json, "proposed", actor, None,
                 datetime.now(timezone.utc).isoformat(), None))
            return result.rowcount == 1

    def approve(self, key: str, approver: str) -> bool:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT requested_by FROM actions WHERE idempotency_key=? AND status='proposed'", (key,)).fetchone()
            if not row or row[0] == approver:
                return False
            return db.execute("UPDATE actions SET status='approved', approved_by=? WHERE idempotency_key=? AND status='proposed'",
                              (approver, key)).rowcount == 1

    def get(self, key: str):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
            return dict(row) if row else None
