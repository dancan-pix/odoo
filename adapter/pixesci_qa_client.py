"""Fail-closed QA verifier for an explicitly configured PixeSci integration endpoint.

A dedicated trusted endpoint must be implemented in PixeSci. No local or
caller-supplied 'approved' boolean is accepted as a substitute.
"""
import json
import os
import ssl
import urllib.request
from urllib.parse import quote


class PixeSciQAVerifier:
    def __init__(self, base_url: str, token: str):
        if not base_url.startswith("https://"):
            raise ValueError("PixeSci QA verification endpoint requires HTTPS")
        if not token:
            raise ValueError("Dedicated PixeSci service credential required")
        self.base_url = base_url.rstrip("/")
        self.token = token

    @classmethod
    def from_env(cls):
        return cls(os.environ["PIXESCI_QA_BASE_URL"], os.environ["PIXESCI_QA_SERVICE_TOKEN"])

    def __call__(self, request_id: str) -> bool:
        if not request_id or "/" in request_id or len(request_id) > 128:
            raise ValueError("Invalid PixeSci QC request identifier")
        # Endpoint contract must be implemented by PixeSci and audited before use.
        url = self.base_url + "/integrations/erp/qa-releases/" + quote(request_id, safe="")
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as response:
            if response.status != 200:
                return False
            evidence = json.load(response)
        # Require server-sourced identity, explicit approved state and release scope.
        return (evidence.get("request_id") == request_id and
                evidence.get("status") == "approved" and
                evidence.get("scope") == "inventory_release" and
                isinstance(evidence.get("approval_id"), str) and
                bool(evidence["approval_id"]) and
                isinstance(evidence.get("approved_by"), str) and
                bool(evidence["approved_by"]))
