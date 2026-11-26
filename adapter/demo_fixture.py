"""Create a disposable Odoo 18 purchase/lot receipt and optionally run QA release.

Run: python -m adapter.demo_fixture --run-release
Requires installed purchase_stock, stock lot tracking and sandbox credentials.
Every invocation creates uniquely named synthetic records. Never retry a failed
remote write automatically; inspect the printed fixture prefix in Odoo.
"""
import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from adapter.odoo_rpc import OdooClient


def create_fixture(odoo):
    if (os.getenv("ERP_SANDBOX_ONLY") != "true" or
            os.getenv("ERP_WRITE_ENABLED") != "true"):
        raise PermissionError("Explicit sandbox write opt-in required")
    url = urlsplit(odoo.base_url)
    if (url.scheme not in ("http", "https") or
            url.hostname not in ("localhost", "127.0.0.1") or
            not odoo.database.startswith("sandbox_")):
        raise PermissionError("Local sandbox_ database required")
    rpc = odoo.execute
    warehouses = rpc("stock.warehouse", "search_read", [[]],
                     fields=["company_id", "lot_stock_id", "in_type_id", "int_type_id"],
                     limit=2)
    if len(warehouses) != 1:
        raise RuntimeError("Fixture requires exactly one sandbox warehouse")
    wh = warehouses[0]
    company = wh["company_id"][0]
    usable = wh["lot_stock_id"][0]
    incoming = wh["in_type_id"][0]
    internal = wh["int_type_id"][0]
    operation = rpc("stock.picking.type", "read", [incoming],
                    fields=["default_location_src_id", "use_create_lots", "use_existing_lots"])[0]
    if not operation["use_create_lots"] and not operation["use_existing_lots"]:
        raise RuntimeError("Enable lot tracking on incoming operation type")
    source = operation["default_location_src_id"]
    if not source:
        raise RuntimeError("Incoming operation needs a supplier source")
    prefix = "SYNTHETIC-" + uuid.uuid4().hex[:12]
    print("Fixture prefix:", prefix, flush=True)
    # Hold is a sibling of usable stock, not a child included in its availability.
    parent = rpc("stock.location", "read", [usable], fields=["location_id"])[0]["location_id"]
    hold = rpc("stock.location", "create", [{"name": prefix + "-QC-HOLD",
               "usage": "internal", "location_id": parent[0], "company_id": company}])
    supplier = rpc("res.partner", "create", [{"name": prefix + "-Supplier",
                   "supplier_rank": 1, "company_id": company}])
    product = rpc("product.product", "create", [{"name": prefix + "-Material",
                  "default_code": prefix, "type": "consu", "is_storable": True,
                  "tracking": "lot", "purchase_ok": True, "company_id": company}])
    uom = rpc("product.product", "read", [product], fields=["uom_id"])[0]["uom_id"][0]
    lot = rpc("stock.lot", "create", [{"name": prefix + "-LOT",
              "product_id": product, "company_id": company}])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    quantity = 5.0
    po = rpc("purchase.order", "create", [{"partner_id": supplier,
             "company_id": company, "picking_type_id": incoming,
             "partner_ref": prefix, "order_line": [[0, 0, {
                 "name": prefix, "product_id": product, "product_qty": quantity,
                 "product_uom": uom, "price_unit": 1.0, "date_planned": now}]]}])
    # This is disposable fixture provisioning, not an exposed ERP approval action.
    rpc("purchase.order", "button_confirm", [po])
    order = rpc("purchase.order", "read", [po], fields=["state", "picking_ids"])[0]
    if order["state"] != "purchase" or len(order["picking_ids"]) != 1:
        raise RuntimeError("Expected one confirmed PO receipt; inspect fixture")
    picking = order["picking_ids"][0]
    moves = rpc("stock.move", "search_read", [[["picking_id", "=", picking]]],
                fields=["id", "product_id", "state", "location_id", "product_uom_qty"])
    if len(moves) != 1 or moves[0]["product_id"][0] != product:
        raise RuntimeError("Unexpected receipt movements")
    move = moves[0]
    rpc("stock.picking", "do_unreserve", [picking])
    if rpc("stock.move.line", "search", [[["picking_id", "=", picking]]], limit=1):
        raise RuntimeError("Receipt contains unexpected allocations")
    rpc("stock.picking", "write", [picking], {"location_dest_id": hold})
    rpc("stock.move", "write", [move["id"]], {"location_dest_id": hold})
    rpc("stock.move.line", "create", [{"picking_id": picking, "move_id": move["id"],
        "company_id": company, "product_id": product, "product_uom_id": uom,
        "lot_id": lot, "quantity": quantity, "location_id": move["location_id"][0],
        "location_dest_id": hold}])
    rpc("stock.move", "write", [move["id"]], {"picked": True})
    result = rpc("stock.picking", "button_validate", [picking])
    if isinstance(result, dict):
        raise RuntimeError("Receipt requires manual validation wizard; inspect fixture")
    if rpc("stock.picking", "read", [picking], fields=["state"])[0]["state"] != "done":
        raise RuntimeError("Receipt completion not confirmed")
    fixture = dict(prefix=prefix, purchase_order=po, picking=picking, hold=hold,
                   usable=usable, internal=internal, product=product, lot=lot,
                   quantity=quantity, company=company)
    assert_stock(odoo, fixture, quantity, 0)
    return fixture


def assert_stock(odoo, fixture, hold_quantity, usable_quantity):
    for location, expected in ((fixture["hold"], hold_quantity),
                               (fixture["usable"], usable_quantity)):
        rows = odoo.execute("stock.quant", "search_read", [[
            ["product_id", "=", fixture["product"]],
            ["lot_id", "=", fixture["lot"]],
            ["company_id", "=", fixture["company"]],
            ["location_id", "=", location]]], fields=["quantity", "reserved_quantity"])
        if abs(sum(row["quantity"] for row in rows) - expected) > 1e-8:
            raise RuntimeError("Unexpected lot quantity at location " + str(location))
        if any(abs(row["reserved_quantity"]) > 1e-8 for row in rows):
            raise RuntimeError("Unexpected outstanding reservation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-release", action="store_true")
    args = parser.parse_args()
    odoo = OdooClient.from_env()
    fixture = create_fixture(odoo)
    print(json.dumps(fixture, indent=2), flush=True)
    if args.run_release:
        from adapter.demo_release import run
        os.environ.update(ERP_QC_HOLD_LOCATION_ID=str(fixture["hold"]),
                          ERP_USABLE_LOCATION_ID=str(fixture["usable"]),
                          ERP_INTERNAL_PICKING_TYPE_ID=str(fixture["internal"]))
        run(fixture["picking"], fixture["hold"], fixture["usable"])
        assert_stock(odoo, fixture, 0, fixture["quantity"])
        print("Synthetic lot inventory reconciled after release", flush=True)


if __name__ == "__main__":
    main()
