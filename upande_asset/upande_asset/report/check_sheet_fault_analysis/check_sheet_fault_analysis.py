import frappe
from frappe import _

FAULT_STATUSES = ("Fault / Defect", "Affected")
SECTIONS = ["a", "b", "c", "d", "e", "f"]


def execute(filters=None):
    filters = filters or {}
    columns = _columns()
    data    = _data(filters)
    chart   = _chart(data)
    summary = _summary(data)
    return columns, data, None, chart, summary


def _columns():
    return [
        {"label": _("Check Factor"),       "fieldname": "check_factor",   "fieldtype": "Data", "width": 200},
        {"label": _("Total Faults"),       "fieldname": "total",          "fieldtype": "Int",  "width": 110},
        {"label": _("Fault / Defect"),     "fieldname": "fault_count",    "fieldtype": "Int",  "width": 120},
        {"label": _("Affected"),           "fieldname": "affected_count", "fieldtype": "Int",  "width": 100},
        {"label": _("Critical"),           "fieldname": "critical",       "fieldtype": "Int",  "width": 90},
        {"label": _("High"),               "fieldname": "high",           "fieldtype": "Int",  "width": 80},
        {"label": _("Medium"),             "fieldname": "medium",         "fieldtype": "Int",  "width": 90},
        {"label": _("Low"),                "fieldname": "low",            "fieldtype": "Int",  "width": 80},
        {"label": _("Assets Affected"),    "fieldname": "assets",         "fieldtype": "Int",  "width": 120},
        {"label": _("Most Recent"),        "fieldname": "most_recent",    "fieldtype": "Date", "width": 110},
    ]


def _data(filters):
    date_cond = ""
    params = {}

    if filters.get("from_date"):
        date_cond += " AND cs.check_date >= %(from_date)s"
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        date_cond += " AND cs.check_date <= %(to_date)s"
        params["to_date"] = filters["to_date"]
    if filters.get("asset_category"):
        date_cond += " AND cs.asset_category = %(asset_category)s"
        params["asset_category"] = filters["asset_category"]
    if filters.get("severity"):
        date_cond += " AND ci.severity = %(severity)s"
        params["severity"] = filters["severity"]

    # Build UNION across all 6 section item tables
    unions = []
    for sec in SECTIONS:
        child_table = "tabAsset Check Sheet Item"
        unions.append(f"""
            SELECT
                ci.check_factor,
                ci.status,
                ci.severity,
                cs.asset,
                cs.check_date
            FROM `{child_table}` ci
            INNER JOIN `tabAsset Check Sheet` cs ON cs.name = ci.parent
            WHERE ci.parentfield = 'section_{sec}_items'
              AND ci.status IN ('Fault / Defect', 'Affected')
              AND cs.docstatus = 1
              {date_cond}
        """)

    union_sql = " UNION ALL ".join(unions)

    rows = frappe.db.sql(f"""
        SELECT
            check_factor,
            COUNT(*)                                          AS total,
            SUM(status = 'Fault / Defect')                   AS fault_count,
            SUM(status = 'Affected')                         AS affected_count,
            SUM(severity = 'Critical')                       AS critical,
            SUM(severity = 'High')                           AS high,
            SUM(severity = 'Medium')                         AS medium,
            SUM(severity = 'Low')                            AS low,
            COUNT(DISTINCT asset)                            AS assets,
            MAX(check_date)                                  AS most_recent
        FROM ({union_sql}) combined
        GROUP BY check_factor
        ORDER BY total DESC
        LIMIT 50
    """, params, as_dict=True)

    return rows


def _chart(data):
    top = data[:10]
    return {
        "data": {
            "labels": [r["check_factor"] for r in top],
            "datasets": [
                {"name": _("Fault / Defect"), "values": [r["fault_count"] for r in top]},
                {"name": _("Affected"),       "values": [r["affected_count"] for r in top]},
            ],
        },
        "type": "bar",
        "colors": ["#ef4444", "#f97316"],
        "title": _("Top 10 Fault Items"),
        "barOptions": {"stacked": True},
    }


def _summary(data):
    total_faults = sum(r.get("total", 0) for r in data)
    critical     = sum(r.get("critical", 0) for r in data)
    unique_items = len(data)
    return [
        {"label": _("Total Fault Occurrences"), "value": total_faults,  "indicator": "orange"},
        {"label": _("Critical Faults"),         "value": critical,      "indicator": "red"},
        {"label": _("Unique Check Factors"),    "value": unique_items,  "indicator": "blue"},
    ]
