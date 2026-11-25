"""Run against a disposable local Odoo database with a completed purchase receipt.
python -m adapter.demo_release PICKING HOLD_LOCATION USABLE_LOCATION
Requires ODOO_* credentials and explicit sandbox/write opt-in.
Creates a real stock transfer; never run against customer inventory.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from adapter.odoo_rpc import OdooClient
from adapter.qc_bridge import QCBridge
from adapter.receipt_handoff import ReceiptHandoffService
from adapter.approval_ledger import ApprovalLedger
from adapter.quality_release import QualityRelease
from adapter.stock_reconciliation import StockTransferReconciler
from adapter.sandbox_qa import Client


def run(picking, hold, usable):
    if os.getenv("ERP_SANDBOX_ONLY") != "true" or os.getenv("ERP_WRITE_ENABLED") != "true":
        raise PermissionError("Explicit sandbox write opt-in required")
    odoo = OdooClient.from_env()
    url = urlsplit(odoo.base_url)
    if url.hostname not in ("localhost", "127.0.0.1") or not odoo.database.startswith("sandbox_"):
        raise PermissionError("Requires a local sandbox_ database")
    if min(picking, hold, usable) <= 0 or hold == usable:
        raise ValueError("Invalid receipt or locations")
    # Keep durable evidence even when execution is uncertain.
    directory = tempfile.mkdtemp(prefix="odoo-release-demo-")
    print("Evidence directory:", directory, flush=True)
    os.environ.update(ERP_QA_MODE="synthetic", ERP_QA_URL="http://127.0.0.1:8765",
                      ERP_QA_PORT="8765", ERP_QA_DB_PATH=directory + "/qa.sqlite3",
                      ERP_QA_CLIENT_TOKEN=secrets.token_hex(32),
                      ERP_QA_REVIEWER_TOKEN=secrets.token_hex(32))
    bridge = QCBridge(directory + "/handoff.sqlite3")
    ledger = ApprovalLedger(directory + "/approval.sqlite3")
    qa = Client()
    process = subprocess.Popen([sys.executable, "-m", "adapter.sandbox_qa"])
    key = "demo-" + uuid.uuid4().hex
    try:
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError("Synthetic QA server failed to start")
        handoff = ReceiptHandoffService(odoo, bridge).prepare(picking, key, "demo-receiver")
        row = qa.sync(bridge, key, "demo-receiver")
        if row["state"] != "submitted":
            raise RuntimeError("Expected pending QA review")
        service = QualityRelease(odoo, bridge, ledger, qa.verify)
        try:
            service.propose(key, hold, usable, "demo-proposer", key)
        except PermissionError:
            pass
        else:
            raise RuntimeError("Release proposal accepted before QA")
        request = Request(qa.url + "/review",
                          json.dumps({"request_id": row["pixesci_request_id"],
                                      "decision": "approved"}).encode(),
                          {"Authorization": "Bearer " + os.environ["ERP_QA_REVIEWER_TOKEN"],
                           "Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            json.load(response)
        row = qa.sync(bridge, key, "demo-receiver")
        if row["state"] != "approved":
            raise RuntimeError("QA synchronization failed")
        proposal = service.propose(key, hold, usable, "demo-proposer", key)
        if not service.approve(key, "demo-approver"):
            raise RuntimeError("Independent approval failed")
        result = service.execute(key, "demo-executor")
        reconciliation = StockTransferReconciler(odoo, ledger).inspect(key)
        print(json.dumps({"synthetic_qa": True, "handoff": handoff,
                          "proposal": proposal, "execution": result,
                          "reconciliation": reconciliation}, indent=2))
        if reconciliation["status"] != "matched" or not result["inventory_moved"]:
            raise RuntimeError("Transfer reconciliation failed; do not retry")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("picking", "hold", "usable"):
        parser.add_argument(name, type=int)
    args = parser.parse_args()
    run(args.picking, args.hold, args.usable)
