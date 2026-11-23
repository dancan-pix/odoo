"""Minimal Odoo JSON-RPC adapter. No production write operations enabled by default."""
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

@dataclass
class OdooClient:
    base_url: str
    database: str
    username: str
    api_key: str

    @classmethod
    def from_env(cls):
        return cls(*(os.environ[name] for name in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")))

    def _rpc(self, service: str, method: str, args: list[Any]) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {
            "service": service, "method": method, "args": args}, "id": 1}).encode()
        req = urllib.request.Request(self.base_url.rstrip("/") + "/jsonrpc", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError("Odoo RPC request failed")
        return result["result"]

    def _uid(self) -> int:
        uid = self._rpc("common", "authenticate", [self.database, self.username, self.api_key, {}])
        if not uid:
            raise PermissionError("Odoo authentication failed")
        return uid

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._rpc("object", "execute_kw", [self.database, self._uid(), self.api_key,
            model, method, list(args), kwargs])

    def suppliers(self, limit: int = 50) -> list[dict]:
        return self.execute("res.partner", "search_read", [[["supplier_rank", ">", 0]]],
            fields=["id", "name", "email"], limit=min(max(limit, 1), 100))

    def purchase_orders(self, limit: int = 50) -> list[dict]:
        return self.execute("purchase.order", "search_read", [[]],
            fields=["id", "name", "state", "partner_id", "date_order"], limit=min(max(limit, 1), 100))

    def stock_quantities(self, limit: int = 50) -> list[dict]:
        return self.execute("stock.quant", "search_read", [[]],
            fields=["id", "product_id", "location_id", "quantity", "reserved_quantity"],
            limit=min(max(limit, 1), 100))
