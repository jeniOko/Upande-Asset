
# Override (or extend) ERPNext's Asset Repair controller to sync
# check sheet status when repair status changes.

import frappe
from frappe import _

# ── Called from hooks.py doc_events, NOT as a controller class ──────────────

def on_asset_repair_save(doc, method):
    """Sync the most recent check sheet for this asset based on repair status.
    Also links the repair back to its originating check sheet if set."""
    repair_status = doc.repair_status

    # If this repair was created from a specific check sheet, link it.
    origin_cs = doc.get("check_sheet")
    if origin_cs:
        frappe.db.set_value(
            "Asset Check Sheet", origin_cs, "linked_asset_repair", doc.name
        )

    # Find the most recent open check sheet for status sync.
    results = frappe.get_all(
        "Asset Check Sheet",
        filters={
            "asset": doc.asset,
            "docstatus": 1,
            "overall_status": ["in", ["Needs Attention", "Under Repair"]],
        },
        fields=["name"],
        order_by="check_date desc",
        limit=1,
    )
    if not results:
        return

    cs_name = results[0]["name"]

    if repair_status in ("Pending", "In Progress"):
        new_status = "Under Repair"
    elif repair_status == "Completed":
        new_status = "Repaired"
    else:
        return

    frappe.db.set_value("Asset Check Sheet", cs_name, "overall_status", new_status)


def on_asset_maintenance_save(doc, method):
    """When a maintenance record is saved, mark the most recent
    Needs Attention check sheet for this asset as Maintained."""
    asset = doc.asset_name
    if not asset:
        return

    results = frappe.get_all(
        "Asset Check Sheet",
        filters={
            "asset": asset,
            "docstatus": 1,
            "overall_status": "Needs Attention",
        },
        fields=["name"],
        order_by="check_date desc",
        limit=1,
    )
    if results:
        frappe.db.set_value(
            "Asset Check Sheet", results[0]["name"], "overall_status", "Maintained"
        )