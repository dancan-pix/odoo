"""Standalone authenticated MCP HTTP endpoint; TLS termination required for remote access."""
import os
import hmac
from starlette.responses import PlainTextResponse
from mcp.server.fastmcp import FastMCP
from mcp_server import server

# Explicit allowlist: never expose caller-supplied actor or mutation tools remotely.
mcp = FastMCP("pixesci-odoo-readonly", stateless_http=True, json_response=True)
for name in (
    "list_suppliers", "list_purchase_orders", "list_stock_quantities",
    "list_incoming_receipts", "get_purchase_order_lines", "get_receipt_moves",
):
    mcp.tool()(getattr(server, name))

def create_app():
    token = os.environ.get("ERP_MCP_READ_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise RuntimeError("Configure a strong ERP_MCP_READ_TOKEN")
    expected = ("Bearer " + token).encode("ascii")
    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await inner(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        headers = scope.get("headers", [])
        auth = [v for k, v in headers if k.lower() == b"authorization"]
        if len(auth) != 1 or not hmac.compare_digest(auth[0], expected):
            await PlainTextResponse("Unauthorized", status_code=401,
                headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        # No browser-origin requests; intended for server-to-server clients.
        if any(k.lower() == b"origin" for k, _ in headers):
            await PlainTextResponse("Origin not permitted", status_code=403)(scope, receive, send)
            return
        if scope.get("path") != "/mcp":
            await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
            return
        await inner(scope, receive, send)
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, proxy_headers=False)
