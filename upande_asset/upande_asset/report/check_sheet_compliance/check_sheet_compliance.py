import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

FREQ_DAYS = {"Daily": 1, "Weekly": 7, "Monthly": 30, "Quarterly": 90, "Yearly": 365}


def execute(filters=None):
    filters = filters or {}
    columns = _columns()
    data    = _data(filters)
    chart   = _chart(data)
    summary = _summary(data)
    return columns, data, None, chart, summary


def _columns():
    return [
        {"label": _("Schedule"),       "fieldname": "schedule",     "fieldtype": "Link",   "options": "Asset Check Sheet Schedule", "width": 140},
        {"label": _("Asset"),          "fieldname": "asset",        "fieldtype": "Link",   "options": "Asset",                      "width": 130},
        {"label": _("Asset Name"),     "fieldname": "asset_name",   "fieldtype": "Data",                                             "width": 160},
        {"label": _("Category"),       "fieldname": "asset_category","fieldtype": "Link",  "options": "Asset Category",             "width": 130},
        {"label": _("Template"),       "fieldname": "template",     "fieldtype": "Link",   "options": "Asset Check Sheet Template", "width": 160},
        {"label": _("Frequency"),      "fieldname": "frequency",    "fieldtype": "Data",                                             "width": 110},
        {"label": _("Last Check"),     "fieldname": "last_check",   "fieldtype": "Date",                                             "width": 110},
        {"label": _("Next Due"),       "fieldname": "next_due",     "fieldtype": "Date",                                             "width": 110},
        {"label": _("Status"),         "fieldname": "check_status", "fieldtype": "Data",                                             "width": 110},
        {"label": _("Days Overdue"),   "fieldname": "days_overdue", "fieldtype": "Int",                                              "width": 110},
    ]


def _data(filters):
    conditions = ["cs.enabled = 1"]
    params = {}

    if filters.get("asset_category"):
        conditions.append("cs.asset_category = %(asset_category)s")
        params["asset_category"] = filters["asset_category"]
    if filters.get("check_status"):
        conditions.append("cs.check_status = %(check_status)s")
        params["check_status"] = filters["check_status"]

    where = "WHERE " + " AND ".join(conditions)

    rows = frappe.db.sql(f"""
        SELECT
            cs.name         AS schedule,
            cs.asset,
            cs.asset_name,
            cs.asset_category,
            cs.check_sheet_template AS template,
            tmpl.frequency,
            tmpl.custom_frequency_days,
            cs.last_check_date   AS last_check,
            cs.next_due_date     AS next_due,
            cs.check_status,
            cs.days_overdue
        FROM `tabAsset Check Sheet Schedule` cs
        LEFT JOIN `tabAsset Check Sheet Template` tmpl
               ON tmpl.name = cs.check_sheet_template
        {where}
        ORDER BY
            CASE cs.check_status WHEN 'Overdue' THEN 0 WHEN 'Up to Date' THEN 1 ELSE 2 END,
            cs.days_overdue DESC,
            cs.asset_name
    """, params, as_dict=True)

    for r in rows:
        if r.get("frequency") == "Custom":
            r["frequency"] = f"Every {r.get('custom_frequency_days') or '?'} days"

    return rows


def _chart(data):
    counts = {"Overdue": 0, "Up to Date": 0}
    for r in data:
        key = r.get("check_status") or "Up to Date"
        counts[key] = counts.get(key, 0) + 1

    return {
        "data": {
            "labels": list(counts.keys()),
            "datasets": [{"values": list(counts.values())}],
        },
        "type": "donut",
        "colors": ["#ef4444", "#10b981"],
        "title": _("Schedule Status Distribution"),
    }


def _summary(data):
    overdue   = sum(1 for r in data if r.get("check_status") == "Overdue")
    up_to_date = sum(1 for r in data if r.get("check_status") == "Up to Date")
    return [
        {"label": _("Total Schedules"),  "value": len(data),    "indicator": "blue"},
        {"label": _("Up to Date"),       "value": up_to_date,   "indicator": "green"},
        {"label": _("Overdue"),          "value": overdue,      "indicator": "red"},
    ]
