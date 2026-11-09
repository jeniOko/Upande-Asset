# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

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
		{"label": _("Asset Name"), "fieldname": "asset_name", "fieldtype": "Data", "width": 170},
		{"label": _("Asset Category"), "fieldname": "asset_category", "fieldtype": "Link", "options": "Asset Category", "width": 140},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 140},
		{"label": _("Policy"), "fieldname": "policy", "fieldtype": "Link", "options": "Insurance Policy", "width": 160},
		{"label": _("Insurer"), "fieldname": "insurer", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Policy Type"), "fieldname": "policy_type", "fieldtype": "Link", "options": "Insurance Policy Type", "width": 130},
		{"label": _("Start Date"), "fieldname": "start_date", "fieldtype": "Date", "width": 100},
		{"label": _("End Date"), "fieldname": "end_date", "fieldtype": "Date", "width": 100},
		{"label": _("Sum Insured"), "fieldname": "sum_insured", "fieldtype": "Currency", "width": 130},
		{"label": _("Book Value"), "fieldname": "book_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Under-insured Gap (Book − Sum)"), "fieldname": "gap", "fieldtype": "Currency", "width": 170},
		{"label": _("Days to Expiry"), "fieldname": "days_to_expiry", "fieldtype": "Int", "width": 110},
	]


def get_data(filters):
	# A policy row covers the asset on `d` when the policy is submitted and not
	# cancelled, d falls inside the policy period, the row was added on/before d,
	# and the row is still active or was removed after d (same rule as
	# insurance.utils.get_covering_rows, but set-based).
	conditions = [
		"row.parenttype = 'Insurance Policy'",
		"pol.docstatus = 1",
		"pol.status != 'Cancelled'",
		"pol.start_date <= %(d)s",
		"pol.end_date >= %(d)s",
		"(row.added_on IS NULL OR row.added_on <= %(d)s)",
		"(row.row_status = 'Active' OR row.removed_on > %(d)s)",
	]
	params = {"d": filters.as_on_date}

	if filters.get("company"):
		conditions.append("pol.company = %(company)s")
		params["company"] = filters.company
	if filters.get("insurer"):
		conditions.append("pol.insurer = %(insurer)s")
		params["insurer"] = filters.insurer
	if filters.get("policy_type"):
		conditions.append("pol.policy_type = %(policy_type)s")
		params["policy_type"] = filters.policy_type
	if filters.get("asset_category"):
		conditions.append("IFNULL(a.asset_category, row.asset_category) = %(asset_category)s")
		params["asset_category"] = filters.asset_category

	if scope := asset_condition(filters, "row.asset", params):
		conditions.append(scope)

	rows = frappe.db.sql(
		"""
		SELECT
			row.asset,
			IFNULL(a.asset_name, row.asset_name) AS asset_name,
			IFNULL(a.asset_category, row.asset_category) AS asset_category,
			IFNULL(row.cost_center, a.cost_center) AS cost_center,
			pol.name AS policy,
			pol.insurer,
			pol.policy_type,
			pol.start_date,
			pol.end_date,
			row.sum_insured
		FROM `tabInsurance Policy Asset` row
		INNER JOIN `tabInsurance Policy` pol ON pol.name = row.parent
		LEFT JOIN `tabAsset` a ON a.name = row.asset
		WHERE {conditions}
		ORDER BY pol.end_date ASC, row.asset ASC
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)

	book_values = {}
	for r in rows:
		if r.asset not in book_values:
			book_values[r.asset] = get_asset_book_value(r.asset)
		r.sum_insured = flt(r.sum_insured)
		r.book_value = flt(book_values[r.asset])
		r.gap = r.book_value - r.sum_insured
		r.days_to_expiry = date_diff(r.end_date, filters.as_on_date)
	return rows


def get_chart(data):
	if not data:
		return None
	by_type = {}
	for r in data:
		key = r.policy_type or _("Other")
		agg = by_type.setdefault(key, [0, 0])
		agg[0] += r.sum_insured
		agg[1] += r.book_value
	labels = list(by_type)
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Sum Insured"), "values": [by_type[k][0] for k in labels]},
				{"name": _("Book Value"), "values": [by_type[k][1] for k in labels]},
			],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"title": _("Sum Insured vs Book Value by Policy Type"),
	}


def get_summary(data):
	total_sum = sum(r.sum_insured for r in data)
	# an asset can sit on several policies; count its book value once
	total_book = sum({r.asset: r.book_value for r in data}.values())
	under = sum(1 for r in data if r.gap > 0)
	return [
		{"label": _("Total Sum Insured"), "value": total_sum, "datatype": "Currency", "indicator": "blue"},
		{"label": _("Total Book Value"), "value": total_book, "datatype": "Currency", "indicator": "blue"},
		{"label": _("Under-insured Rows"), "value": under, "datatype": "Int", "indicator": "red" if under else "green"},
	]
