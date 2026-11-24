"""Odoo 18 sandbox release execution for untracked products only.

Tracked lots/serials require a separate exact lot allocation contract.
No wizard is automatically accepted and uncertain writes are never retried.
"""
import math
import os
from collections import Counter


def _same(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def preflight_release(odoo, payload):
    source = payload["source_location_id"]
    dest = payload["destination_location_id"]
    if source != int(os.environ["ERP_QC_HOLD_LOCATION_ID"]) or dest != int(os.environ["ERP_USABLE_LOCATION_ID"]):
        raise PermissionError("Release locations do not match administrator configuration")
    locations = odoo.execute("stock.location", "read", [ [source, dest] ],
                             fields=["usage", "company_id"])
    if len(locations) != 2 or any(x["usage"] != "internal" for x in locations):
        raise PermissionError("Two internal locations required")
    companies = {x["company_id"][0] for x in locations if x["company_id"]}
    if len(companies) != 1 or any(not x["company_id"] for x in locations):
        raise PermissionError("Locations must belong to the same explicit company")
    company = companies.pop()
    picking_type = int(os.environ["ERP_INTERNAL_PICKING_TYPE_ID"])
    types = odoo.execute("stock.picking.type", "read", [[picking_type]],
                         fields=["code", "company_id"])
    if len(types) != 1 or types[0]["code"] != "internal" or types[0]["company_id"][0] != company:
        raise PermissionError("Configured internal operation type is invalid")
    for line in payload["move_quantities"]:
        products = odoo.execute("product.product", "read", [[line["product_id"]]],
                                fields=["tracking", "uom_id"])
        if len(products) != 1 or products[0]["tracking"] != "none":
            raise PermissionError("Lot/serial-tracked release is not supported")
        receipt = odoo.execute("stock.move", "read", [[line["move_id"]]],
                               fields=["product_uom", "product_id", "quantity", "state"])
        if len(receipt) != 1 or receipt[0]["state"] != "done" or receipt[0]["product_id"][0] != line["product_id"] or not _same(receipt[0]["quantity"], line["quantity"]):
            raise PermissionError("Receipt move changed")
        if receipt[0]["product_uom"][0] != products[0]["uom_id"][0]:
            raise PermissionError("Receipt UOM conversion is not supported")
    return picking_type, company


def complete_untracked_transfer(odoo, payload, key):
    picking_type, company = preflight_release(odoo, payload)
    source, dest = payload["source_location_id"], payload["destination_location_id"]
    origin = "PixeSci QA release " + key
    if odoo.execute("stock.picking", "search", [[["origin", "=", origin]]], limit=1):
        raise RuntimeError("Existing transfer requires reconciliation")
    picking = odoo.execute("stock.picking", "create", [{
        "picking_type_id": picking_type, "company_id": company,
        "location_id": source, "location_dest_id": dest, "origin": origin,
    }])
    expected = Counter()
    for line in payload["move_quantities"]:
        product = line["product_id"]
        quantity = line["quantity"]
        expected[product] += quantity
        p = odoo.execute("product.product", "read", [[product]], fields=["uom_id"])[0]
        odoo.execute("stock.move", "create", [{
            "name": origin, "picking_id": picking, "company_id": company,
            "product_id": product, "product_uom_qty": quantity,
            "product_uom": p["uom_id"][0], "location_id": source,
            "location_dest_id": dest,
        }])
    odoo.execute("stock.picking", "action_confirm", [picking])
    odoo.execute("stock.picking", "action_assign", [picking])
    moves = odoo.execute("stock.move", "search_read", [[["picking_id", "=", picking]]],
                         fields=["id", "product_id", "quantity", "product_uom_qty", "state"], limit=1000)
    actual = Counter()
    for move in moves:
        if move["state"] != "assigned" or not _same(move["quantity"], move["product_uom_qty"]):
            raise RuntimeError("Full reservation required; manual reconciliation")
        actual[move["product_id"][0]] += move["quantity"]
    if actual.keys() != expected.keys() or any(not _same(actual[k], expected[k]) for k in expected):
        raise RuntimeError("Reserved quantities differ from approved quantities")
    lines = odoo.execute("stock.move.line", "search_read", [[["picking_id", "=", picking]]],
                         fields=["location_id", "location_dest_id", "lot_id", "owner_id", "package_id", "result_package_id"], limit=1000)
    if not lines or len(lines) >= 1000 or any(
        x["location_id"][0] != source or x["location_dest_id"][0] != dest or
        x["lot_id"] or x["owner_id"] or x["package_id"] or x["result_package_id"] for x in lines):
        raise RuntimeError("Unexpected reservation allocation")
    odoo.execute("stock.move", "write", [[m["id"] for m in moves], {"picked": True}])
    result = odoo.execute("stock.picking", "button_validate", [picking])
    if isinstance(result, dict):
        raise RuntimeError("Odoo requested a validation wizard; manual reconciliation")
    state = odoo.execute("stock.picking", "read", [[picking]], fields=["state"])
    if len(state) != 1 or state[0]["state"] != "done":
        raise RuntimeError("Transfer completion not confirmed")
    done = odoo.execute("stock.move", "search_read", [[["picking_id", "=", picking]]],
                        fields=["product_id", "quantity", "state"], limit=1000)
    totals = Counter()
    for move in done:
        if move["state"] != "done":
            raise RuntimeError("Transfer has incomplete moves")
        totals[move["product_id"][0]] += move["quantity"]
    if totals.keys() != expected.keys() or any(not _same(totals[k], expected[k]) for k in expected):
        raise RuntimeError("Completed quantities differ from approval")
    return picking
