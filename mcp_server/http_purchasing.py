"""Sandbox purchasing MCP endpoint with independently provisioned identities.

Set ERP_MCP_PRINCIPALS_FILE to a private JSON file containing a list of
objects with actor, token and role (requester, approver, executor, auditor).
Each actor and token must be unique. Serve behind TLS for nonlocal clients.
"""
import contextvars
import hmac
import json
import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.responses import PlainTextResponse
from adapter.approval_ledger import ApprovalLedger
from adapter.odoo_rpc import OdooClient
from adapter.purchase_execution import PurchaseExecution

principal = contextvars.ContextVar("purchase_principal", default=None)
mcp = FastMCP("odoo-governed-purchasing", stateless_http=True, json_response=True)

def actor_for(role):
    identity = principal.get()
    if identity is None or identity[1] != role:
        raise PermissionError("Required purchasing role not granted")
    if os.environ.get("ERP_SANDBOX_ONLY", "").lower() != "true":
        raise PermissionError("Sandbox mode required")
    return identity[0]

def service():
    return PurchaseExecution(OdooClient.from_env(), ApprovalLedger(
        os.environ["ERP_APPROVAL_DB_PATH"]))

def checked_key(key):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
        raise ValueError("Invalid idempotency key")
    return key

@mcp.tool()
def propose_purchase(order_id: int, operation: str, idempotency_key: str) -> dict:
    """Propose confirmation or cancellation of an existing unconfirmed RFQ."""
    actor = actor_for("requester")
    if order_id <= 0:
        raise ValueError("Invalid order ID")
    return service().propose(order_id, operation, actor, checked_key(idempotency_key))

@mcp.tool()
def approve_purchase(idempotency_key: str) -> dict:
    """Approve an existing purchase proposal as an independent approver."""
    actor = actor_for("approver")
    return {"approved": service().approve(checked_key(idempotency_key), actor)}

@mcp.tool()
def execute_purchase(idempotency_key: str) -> dict:
    """Execute an approved purchase proposal as an independent executor."""
    actor = actor_for("executor")
    return service().execute(checked_key(idempotency_key), actor)

@mcp.tool()
def reconcile_purchase(idempotency_key: str) -> dict:
    """Inspect current and approved purchase state without retrying writes."""
    actor_for("auditor")
    return service().reconcile(checked_key(idempotency_key))

def load_principals():
    rows = json.loads(Path(os.environ["ERP_MCP_PRINCIPALS_FILE"]).read_text())
    if not isinstance(rows, list) or not rows:
        raise ValueError("Nonempty principal list required")
    actors, tokens, result = set(), set(), []
    for row in rows:
        actor, token, role = row["actor"], row["token"], row["role"]
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", actor):
            raise ValueError("Invalid actor")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("Invalid token")
        if role not in {"requester", "approver", "executor", "auditor"}:
            raise ValueError("Invalid role")
        if actor in actors or token in tokens:
            raise ValueError("Duplicate actor or credential")
        actors.add(actor)
        tokens.add(token)
        result.append((("Bearer " + token).encode("ascii"), (actor, role)))
    return result

def create_app():
    identities = load_principals()
    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await inner(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        headers = scope.get("headers", [])
        auth = [v for k, v in headers if k.lower() == b"authorization"]
        identity = None
        if len(auth) == 1:
            for credential, candidate in identities:
                if hmac.compare_digest(auth[0], credential):
                    identity = candidate
        if identity is None:
            await PlainTextResponse("Unauthorized", status_code=401,
                headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        if any(k.lower() == b"origin" for k, _ in headers):
            await PlainTextResponse("Origin not permitted", status_code=403)(scope, receive, send)
            return
        if scope.get("path") != "/mcp":
            await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            return
        marker = principal.set(identity)
        try:
            await inner(scope, receive, send)
        finally:
            principal.reset(marker)
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8766, proxy_headers=False)
