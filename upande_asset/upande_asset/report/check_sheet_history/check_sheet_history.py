import frappe
from frappe import _
from frappe.utils import add_months, today


def execute(filters=None):
    filters = filters or {}
    columns = _columns()
    data    = _data(filters)
    chart   = _chart(data)
    summary = _summary(data)
    return columns, data, None, chart, summary


def _columns():
    return [
        {"label": _("Check Date"),     "fieldname": "check_date",      "fieldtype": "Date",   "width": 110},
        {"label": _("Check Sheet"),    "fieldname": "name",            "fieldtype": "Link",   "options": "Asset Check Sheet", "width": 160},
        {"label": _("Asset"),          "fieldname": "asset",           "fieldtype": "Link",   "options": "Asset",             "width": 130},
        {"label": _("Asset Name"),     "fieldname": "asset_name",      "fieldtype": "Data",                                   "width": 160},
        {"label": _("Category"),       "fieldname": "asset_category",  "fieldtype": "Link",   "options": "Asset Category",    "width": 130},
        {"label": _("Template"),       "fieldname": "template",        "fieldtype": "Link",   "options": "Asset Check Sheet Template", "width": 160},
        {"label": _("Overall Status"), "fieldname": "overall_status",  "fieldtype": "Data",                                   "width": 130},
        {"label": _("Fault Items"),    "fieldname": "fault_count",     "fieldtype": "Int",                                    "width": 100},
        {"label": _("Checked By"),     "fieldname": "owner",           "fieldtype": "Link",   "options": "User",              "width": 140},
    ]


def _data(filters):
    conditions = ["cs.docstatus = 1"]
    params = {}

    if filters.get("from_date"):
        conditions.append("cs.check_date >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("cs.check_date <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("asset"):
        conditions.append("cs.asset = %(asset)s")
        params["asset"] = filters["asset"]
    if filters.get("asset_category"):
        conditions.append("cs.asset_category = %(asset_category)s")
        params["asset_category"] = filters["asset_category"]
    if filters.get("overall_status"):
        conditions.append("cs.overall_status = %(overall_status)s")
        params["overall_status"] = filters["overall_status"]

    where = "WHERE " + " AND ".join(conditions)

    # Count fault items across all sections
    fault_sub = """
        SELECT parent, COUNT(*) AS cnt
        FROM `tabAsset Check Sheet Item`
        WHERE status IN ('Fault / Defect', 'Affected')
        GROUP BY parent
    """

    rows = frappe.db.sql(f"""
        SELECT
            cs.check_date,
            cs.name,
            cs.asset,
            cs.asset_name,
            cs.asset_category,
            cs.check_sheet_template  AS template,
            cs.overall_status,
            IFNULL(fi.cnt, 0)        AS fault_count,
            cs.owner
        FROM `tabAsset Check Sheet` cs
        LEFT JOIN ({fault_sub}) fi ON fi.parent = cs.name
        {where}
        ORDER BY cs.check_date DESC, cs.asset_name
    """, params, as_dict=True)

    return rows


def _chart(data):
    from collections import Counter
    # Count by status
    counter = Counter(r.get("overall_status") or "Unknown" for r in data)
    statuses = ["Passed", "Needs Attention", "Under Repair", "Repaired", "Maintained", "Cancelled"]
    labels   = [s for s in statuses if counter.get(s)]
    values   = [counter[s] for s in labels]
    colors   = {
        "Passed":          "#10b981",
        "Needs Attention": "#ef4444",
        "Under Repair":    "#f97316",
        "Repaired":        "#22c55e",
        "Maintained":      "#8b5cf6",
        "Cancelled":       "#9ca3af",
    }
    return {
        "data": {
            "labels": labels,
            "datasets": [{"values": values}],
        },
        "type": "bar",
        "colors": [colors.get(s, "#6b7280") for s in labels],
        "title": _("Check Sheets by Status"),
    }


def _summary(data):
    total    = len(data)
    passed   = sum(1 for r in data if r.get("overall_status") == "Passed")
    faulted  = sum(1 for r in data if r.get("overall_status") == "Needs Attention")
    return [
        {"label": _("Total Checks"),     "value": total,   "indicator": "blue"},
        {"label": _("Passed"),           "value": passed,  "indicator": "green"},
        {"label": _("Needs Attention"),  "value": faulted, "indicator": "red"},
    ]
