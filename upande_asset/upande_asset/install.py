import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

WORKSPACE_NAME = "Asset Checksheet"

# Number cards installed/updated alongside the workspace
_NUMBER_CARDS = [
    {
        "label": "Overdue Schedules",
        "document_type": "Asset Check Sheet Schedule",
        "function": "Count",
        "filters_json": json.dumps([
            ["Asset Check Sheet Schedule", "check_status", "=", "Overdue"],
            ["Asset Check Sheet Schedule", "enabled",      "=", 1],
        ]),
        "color": "#ef4444",
    },
    {
        "label": "Active Schedules",
        "document_type": "Asset Check Sheet Schedule",
        "function": "Count",
        "filters_json": json.dumps([
            ["Asset Check Sheet Schedule", "enabled", "=", 1],
        ]),
        "color": "#10b981",
    },
    {
        "label": "Open Issues",
        "document_type": "Asset Check Sheet",
        "function": "Count",
        "filters_json": json.dumps([
            ["Asset Check Sheet", "overall_status", "=", "Needs Attention"],
            ["Asset Check Sheet", "docstatus",      "=", 1],
        ]),
        "color": "#f97316",
    },
    {
        "label": "Checks This Month",
        "document_type": "Asset Check Sheet",
        "function": "Count",
        "filters_json": json.dumps([
            ["Asset Check Sheet", "docstatus", "=", 1],
        ]),
        "timespan": "This Month",
        "color": "#2563eb",
    },
]


def after_install():
    _create_asset_repair_custom_fields()
    _setup_workspace()
    _setup_insurance()


def after_migrate():
    _setup_workspace()
    _setup_insurance()


def _setup_insurance():
    from upande_asset.upande_asset.insurance.setup import run_all

    run_all()


# ── Custom field ───────────────────────────────────────────────────────────────

def _create_asset_repair_custom_fields():
    """Add the check_sheet Link field to Asset Repair so the connections
    tile on Asset Check Sheet can count repairs created from each sheet."""
    if frappe.db.has_column("Asset Repair", "check_sheet"):
        return

    create_custom_field(
        "Asset Repair",
        {
            "fieldname": "check_sheet",
            "label": "Check Sheet",
            "fieldtype": "Link",
            "options": "Asset Check Sheet",
            "insert_after": "asset_name",
            "in_standard_filter": 1,
            "description": "Check sheet that triggered this repair",
        },
    )


# ── Number Cards ───────────────────────────────────────────────────────────────

def _create_number_cards():
    """Idempotently create/update the four dashboard number cards.
    Returns a dict mapping label → actual DB name for workspace content."""
    name_map = {}
    for card in _NUMBER_CARDS:
        label   = card["label"]
        doctype = card["document_type"]

        # Find ALL cards with this label+doctype; keep the oldest, delete the rest
        existing = frappe.db.get_all(
            "Number Card",
            filters={"label": label, "document_type": doctype},
            fields=["name"],
            order_by="creation asc",
        )

        if existing:
            keep_name = existing[0]["name"]
            for extra in existing[1:]:
                frappe.delete_doc("Number Card", extra["name"], ignore_permissions=True, force=True)
            doc = frappe.get_doc("Number Card", keep_name)
        else:
            doc = frappe.new_doc("Number Card")

        doc.label         = label
        doc.document_type = doctype
        doc.function      = card["function"]
        doc.filters_json  = card["filters_json"]
        doc.color         = card["color"]
        doc.is_public     = 1
        doc.module        = "Upande Asset"

        if card.get("timespan"):
            doc.timespan = card["timespan"]

        if doc.is_new():
            doc.insert(ignore_permissions=True)
        else:
            doc.save(ignore_permissions=True)

        name_map[label] = doc.name

    frappe.db.commit()
    return name_map


# ── Workspace ──────────────────────────────────────────────────────────────────

def _setup_workspace():
    """Create (or idempotently update) the Asset Checksheet workspace sub-page
    that appears under the standard ERPNext Assets workspace."""

    nc = _create_number_cards()   # returns label→name map; also deduplicates

    content = json.dumps([
        # ── Page header ──────────────────────────────────────────────────────
        {
            "id": "acs-h1",
            "type": "header",
            "data": {
                "text": "<span class=\"h4\"><b>Asset Check Sheet Overview</b></span>",
                "col": 12,
            },
        },
        # ── KPI number cards ─────────────────────────────────────────────────
        {"id": "acs-nc-1", "type": "number_card", "data": {"number_card_name": nc["Overdue Schedules"],  "col": 3}},
        {"id": "acs-nc-2", "type": "number_card", "data": {"number_card_name": nc["Active Schedules"],   "col": 3}},
        {"id": "acs-nc-3", "type": "number_card", "data": {"number_card_name": nc["Open Issues"],        "col": 3}},
        {"id": "acs-nc-4", "type": "number_card", "data": {"number_card_name": nc["Checks This Month"],  "col": 3}},
        # ── Spacer ───────────────────────────────────────────────────────────
        {"id": "acs-sp-1", "type": "spacer", "data": {"col": 12}},
        # ── Quick-access cards ───────────────────────────────────────────────
        {
            "id": "acs-h2",
            "type": "header",
            "data": {
                "text": "<span class=\"h6 text-muted\">Quick Access</span>",
                "col": 12,
            },
        },
        {"id": "acs-card-1", "type": "card", "data": {"card_name": "Asset Check Sheets",       "col": 4}},
        {"id": "acs-card-2", "type": "card", "data": {"card_name": "Schedules & Notifications", "col": 4}},
        {"id": "acs-card-3", "type": "card", "data": {"card_name": "Maintenance & Repairs",     "col": 4}},
        # ── Spacer ───────────────────────────────────────────────────────────
        {"id": "acs-sp-2", "type": "spacer", "data": {"col": 12}},
        # ── Reports ──────────────────────────────────────────────────────────
        {
            "id": "acs-h3",
            "type": "header",
            "data": {
                "text": "<span class=\"h6 text-muted\">Reports</span>",
                "col": 12,
            },
        },
        {"id": "acs-card-4", "type": "card", "data": {"card_name": "Check Sheet Reports", "col": 12}},
    ])

    links = [
        # ── Card: Asset Check Sheets ──────────────────────────────────────────
        {"type": "Card Break", "label": "Asset Check Sheets",       "hidden": 0, "onboard": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Asset Check Sheet",          "link_to": "Asset Check Sheet",          "link_type": "DocType", "onboard": 1, "hidden": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Asset Check Sheet Template", "link_to": "Asset Check Sheet Template", "link_type": "DocType", "onboard": 1, "hidden": 0, "is_query_report": 0, "link_count": 0},
        # ── Card: Schedules & Notifications ──────────────────────────────────
        {"type": "Card Break", "label": "Schedules & Notifications", "hidden": 0, "onboard": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Asset Check Sheet Schedule", "link_to": "Asset Check Sheet Schedule", "link_type": "DocType", "onboard": 1, "hidden": 0, "is_query_report": 0, "link_count": 0},
        # ── Card: Maintenance & Repairs ───────────────────────────────────────
        {"type": "Card Break", "label": "Maintenance & Repairs",     "hidden": 0, "onboard": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Asset Repair",      "link_to": "Asset Repair",      "link_type": "DocType", "onboard": 0, "hidden": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Asset Maintenance", "link_to": "Asset Maintenance", "link_type": "DocType", "onboard": 0, "hidden": 0, "is_query_report": 0, "link_count": 0},
        # ── Card: Check Sheet Reports ─────────────────────────────────────────
        {"type": "Card Break", "label": "Check Sheet Reports",       "hidden": 0, "onboard": 0, "is_query_report": 0, "link_count": 0},
        {"type": "Link", "label": "Check Sheet Compliance",    "link_to": "Check Sheet Compliance",    "link_type": "Report", "onboard": 1, "hidden": 0, "is_query_report": 1, "link_count": 0},
        {"type": "Link", "label": "Check Sheet Fault Analysis","link_to": "Check Sheet Fault Analysis","link_type": "Report", "onboard": 1, "hidden": 0, "is_query_report": 1, "link_count": 0},
        {"type": "Link", "label": "Check Sheet History",       "link_to": "Check Sheet History",       "link_type": "Report", "onboard": 1, "hidden": 0, "is_query_report": 1, "link_count": 0},
    ]

    shortcuts = [
        {
            "type": "DocType",
            "label": "Asset Check Sheet",
            "link_to": "Asset Check Sheet",
            "doc_view": "List",
            "color": "#2563eb",
        },
        {
            "type": "URL",
            "label": "Mobile Check Sheet",
            "url": "/checksheet",
            "color": "#10b981",
        },
        {
            "type": "DocType",
            "label": "Check Sheet Template",
            "link_to": "Asset Check Sheet Template",
            "doc_view": "List",
            "color": "#6366f1",
        },
        {
            "type": "DocType",
            "label": "Check Schedules",
            "link_to": "Asset Check Sheet Schedule",
            "doc_view": "List",
            "color": "#f59e0b",
        },
        {
            "type": "DocType",
            "label": "Asset Repair",
            "link_to": "Asset Repair",
            "doc_view": "List",
            "color": "#ef4444",
        },
        {
            "type": "Report",
            "label": "Compliance Report",
            "link_to": "Check Sheet Compliance",
            "color": "#8b5cf6",
        },
    ]

    if frappe.db.exists("Workspace", WORKSPACE_NAME):
        ws = frappe.get_doc("Workspace", WORKSPACE_NAME)
        ws.content   = content
        ws.links     = []
        ws.shortcuts = []
        for lnk in links:
            ws.append("links", lnk)
        for sc in shortcuts:
            ws.append("shortcuts", sc)
        ws.save(ignore_permissions=True)
    else:
        ws = frappe.new_doc("Workspace")
        ws.name        = WORKSPACE_NAME
        ws.label       = WORKSPACE_NAME
        ws.title       = WORKSPACE_NAME
        ws.module      = "Upande Asset"
        ws.parent_page = "Assets"
        ws.public      = 1
        ws.type        = "Workspace"
        ws.content     = content
        for lnk in links:
            ws.append("links", lnk)
        for sc in shortcuts:
            ws.append("shortcuts", sc)
        ws.insert(ignore_permissions=True)

    frappe.db.commit()
