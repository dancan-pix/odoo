"""SQLite-backed approval and idempotency ledger for proposed ERP actions."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    idempotency_key TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('proposed','approved','executing','executed','rejected','uncertain')),
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

    def claim_execution(self, key: str, executor: str) -> bool:
        """Atomically claim action; never retry uncertain remote writes automatically."""
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "UPDATE actions SET status='executing' WHERE idempotency_key=? AND status='approved' AND requested_by<>? AND approved_by<>?",
                (key, executor, executor)).rowcount == 1

    def mark_executed(self, key: str, executor: str) -> bool:
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "UPDATE actions SET status='executed', executed_at=? WHERE idempotency_key=? AND status='executing'",
                (datetime.now(timezone.utc).isoformat(), key)).rowcount == 1

    def mark_uncertain(self, key: str) -> bool:
        """Remote RPC may have succeeded; require manual reconciliation before any retry."""
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "UPDATE actions SET status='uncertain' WHERE idempotency_key=? AND status='executing'",
                (key,)).rowcount == 1
