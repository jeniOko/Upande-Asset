# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from upande_asset.upande_asset.report_utils import asset_condition

from upande_asset.upande_asset.insurance.utils import get_asset_book_value


def execute(filters=None):
	filters = frappe._dict(filters or {})
	filters.as_on_date = getdate(filters.get("as_on_date") or today())
	columns = get_columns()
	data = get_data(filters)
	return columns, data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"label": _("Asset"), "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 130},
		{"label": _("Asset Name"), "fieldname": "asset_name", "fieldtype": "Data", "width": 180},
		{"label": _("Asset Category"), "fieldname": "asset_category", "fieldtype": "Link", "options": "Asset Category", "width": 140},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 140},
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Link", "options": "Location", "width": 130},
		{"label": _("Book Value"), "fieldname": "book_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Last Policy"), "fieldname": "last_policy", "fieldtype": "Link", "options": "Insurance Policy", "width": 160},
		{"label": _("Last Policy End Date"), "fieldname": "last_policy_end_date", "fieldtype": "Date", "width": 130},
	]


def get_data(filters):
	conditions = [
		"a.docstatus = 1",
		"a.status NOT IN ('Scrapped', 'Sold', 'Cancelled')",
		"a.insurance_required = 1",
		# no policy row covering the asset on the date (see Insurance Register for the rule)
		"""NOT EXISTS (
			SELECT 1
			FROM `tabInsurance Policy Asset` row
			INNER JOIN `tabInsurance Policy` pol ON pol.name = row.parent
			WHERE row.parenttype = 'Insurance Policy'
				AND row.asset = a.name
				AND pol.docstatus = 1
				AND pol.status != 'Cancelled'
				AND pol.start_date <= %(d)s
				AND pol.end_date >= %(d)s
				AND (row.added_on IS NULL OR row.added_on <= %(d)s)
				AND (row.row_status = 'Active' OR row.removed_on > %(d)s)
		)""",
	]
	params = {"d": filters.as_on_date}

	if filters.get("company"):
		conditions.append("a.company = %(company)s")
		params["company"] = filters.company
	if filters.get("asset_category"):
		conditions.append("a.asset_category = %(asset_category)s")
		params["asset_category"] = filters.asset_category

	if scope := asset_condition(filters, "a.name", params):
		conditions.append(scope)

	rows = frappe.db.sql(
		"""
		SELECT
			a.name AS asset,
			a.asset_name,
			a.asset_category,
			a.cost_center,
			a.location
		FROM `tabAsset` a
		WHERE {conditions}
		ORDER BY a.asset_category, a.name
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)
	if not rows:
		return rows

	last_policy = get_last_policies([r.asset for r in rows])
	for r in rows:
		r.book_value = flt(get_asset_book_value(r.asset))
		lp = last_policy.get(r.asset)
		r.last_policy = lp.policy if lp else None
		r.last_policy_end_date = lp.end_date if lp else None
	return rows


def get_last_policies(assets):
	"""Most recent (by end date) submitted policy that ever listed each asset."""
	out = {}
	for r in frappe.db.sql(
		"""
		SELECT row.asset, pol.name AS policy, pol.end_date
		FROM `tabInsurance Policy Asset` row
		INNER JOIN `tabInsurance Policy` pol ON pol.name = row.parent
		WHERE row.parenttype = 'Insurance Policy'
			AND pol.docstatus = 1
			AND row.asset IN %(assets)s
		ORDER BY pol.end_date DESC
		""",
		{"assets": tuple(assets)},
		as_dict=True,
	):
		out.setdefault(r.asset, r)
	return out


def get_chart(data):
	if not data:
		return None
	by_cat = {}
	for r in data:
		key = r.asset_category or _("Not Set")
		by_cat[key] = by_cat.get(key, 0) + r.book_value
	labels = list(by_cat)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Uninsured Book Value"), "values": [by_cat[k] for k in labels]}]},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#ef4444"],
		"title": _("Uninsured Book Value by Category"),
	}


def get_summary(data):
	return [
		{"label": _("Uninsured Assets"), "value": len(data), "datatype": "Int", "indicator": "red" if data else "green"},
		{"label": _("Uninsured Book Value"), "value": sum(r.book_value for r in data), "datatype": "Currency", "indicator": "red" if data else "green"},
	]
