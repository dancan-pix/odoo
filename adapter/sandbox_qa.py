"""Synthetic QA authority for isolated local demonstrations only.

Run python -m adapter.sandbox_qa with distinct client/reviewer tokens.
No production QA endpoint is implemented here.
"""
import os
import json
import hashlib
import hmac
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

def enabled():
    return os.getenv("ERP_SANDBOX_ONLY") == "true" and os.getenv("ERP_QA_MODE") == "synthetic"

class Client:
    def __init__(self):
        if not enabled():
            raise PermissionError("Synthetic QA disabled")
        self.url = os.getenv("ERP_QA_URL", "http://127.0.0.1:8765")
        u = urlsplit(self.url)
        if u.scheme != "http" or u.hostname != "127.0.0.1" or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError("Synthetic QA requires loopback HTTP")
        self.token = os.environ["ERP_QA_CLIENT_TOKEN"]
        if len(self.token) < 32:
            raise ValueError("QA token too short")

    def call(self, path, body):
        if not enabled():
            raise PermissionError("Synthetic QA disabled")
        req = Request(self.url + path, json.dumps(body).encode(), {
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urlopen(req, timeout=5) as response:
            return json.loads(response.read(65536))

    def disposition(self, request_id):
        result = self.call("/status", {"request_id": request_id})
        if result.get("request_id") != request_id:
            raise PermissionError("QA identity mismatch")
        return result

    def verify(self, request_id, disposition=None):
        try:
            current = self.disposition(request_id)
            if disposition is not None:
                return current == disposition and current["decision"] in ("approved", "rejected")
            return current["decision"] == "approved"
        except Exception:
            return False

    def sync(self, bridge, handoff_id, actor):
        row = bridge.get(handoff_id)
        if not row:
            raise LookupError("Unknown handoff")
        digest = hashlib.sha256(row["payload_json"].encode()).hexdigest()
        if row["state"] == "pending":
            result = self.call("/submit", {"handoff_id": handoff_id, "receipt_sha256": digest})
            if result.get("receipt_sha256") != digest:
                raise PermissionError("QA digest mismatch")
            bridge.acknowledge_submission(handoff_id, result["request_id"])
            row = bridge.get(handoff_id)
        disposition = self.disposition(row["pixesci_request_id"])
        if disposition.get("receipt_sha256") != digest:
            raise PermissionError("QA digest mismatch")
        if row["state"] in ("submitted", "under_review") and disposition["decision"] in ("approved", "rejected"):
            bridge.record_disposition(handoff_id, disposition, actor, self.verify)
        return bridge.get(handoff_id)

def serve():
    if not enabled():
        raise PermissionError("Synthetic QA disabled")
    client = os.environ["ERP_QA_CLIENT_TOKEN"]
    reviewer = os.environ["ERP_QA_REVIEWER_TOKEN"]
    if min(len(client), len(reviewer)) < 32 or client == reviewer:
        raise ValueError("Distinct strong QA credentials required")
    path = os.environ["ERP_QA_DB_PATH"]
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, handoff_id TEXT UNIQUE NOT NULL, receipt_sha256 TEXT NOT NULL, decision TEXT NOT NULL)")
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                expected = reviewer if self.path == "/review" else client
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + expected):
                    self.send_error(403)
                    return
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 65536:
                    raise ValueError("Invalid body length")
                body = json.loads(self.rfile.read(n))
                with sqlite3.connect(path) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("BEGIN IMMEDIATE")
                    if self.path == "/submit":
                        digest = body["receipt_sha256"]
                        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                            raise ValueError("Invalid digest")
                        handoff = body["handoff_id"]
                        if not isinstance(handoff, str) or not handoff.strip() or len(handoff) > 128:
                            raise ValueError("Invalid handoff")
                        db.execute("INSERT OR IGNORE INTO requests VALUES (?,?,?,?)",
                                   (str(uuid.uuid4()), handoff, digest, "pending"))
                        row = db.execute("SELECT * FROM requests WHERE handoff_id=?", (handoff,)).fetchone()
                        if row["receipt_sha256"] != digest:
                            raise ValueError("Conflicting receipt")
                    elif self.path in ("/status", "/review"):
                        rid = body["request_id"]
                        if self.path == "/review":
                            decision = body["decision"]
                            if decision not in ("approved", "rejected"):
                                raise ValueError("Invalid decision")
                            db.execute("UPDATE requests SET decision=? WHERE request_id=? AND decision='pending'", (decision, rid))
                        row = db.execute("SELECT * FROM requests WHERE request_id=?", (rid,)).fetchone()
                        if row is None:
                            raise ValueError("Unknown request")
                        if self.path == "/review" and row["decision"] != decision:
                            raise ValueError("Conflicting decision")
                    else:
                        self.send_error(404)
                        return
                    result = json.dumps(dict(row)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
    ThreadingHTTPServer(("127.0.0.1", int(os.getenv("ERP_QA_PORT", "8765"))), Handler).serve_forever()

if __name__ == "__main__":
    serve()
