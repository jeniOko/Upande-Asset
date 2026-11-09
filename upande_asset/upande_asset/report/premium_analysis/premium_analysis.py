# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate

from upande_asset.upande_asset.report_utils import premium_dimension_fields, premium_line_share

from upande_asset.upande_asset.insurance.utils import split_amount

GROUP_FIELDS = {
	"Insurer": ("Supplier", "Insurer"),
	"Asset Category": ("Asset Category", "Asset Category"),
	"Cost Center": ("Cost Center", "Cost Center"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	filters.group_by = filters.get("group_by") or "Insurer"
	if filters.group_by not in GROUP_FIELDS:
		frappe.throw(_("Invalid Group By: {0}").format(filters.group_by))

	years = get_fiscal_years(filters)
	if not years:
		return get_columns(filters, []), [], None, None, None

	data = get_data(filters, years)
	columns = get_columns(filters, years)
	return columns, data, None, get_chart(data, years), get_summary(data)


def get_fiscal_years(filters):
	"""Fiscal Years from `from_fiscal_year` to `to_fiscal_year` inclusive, oldest first."""
	from erpnext.accounts.utils import get_fiscal_year

	from_fy = filters.get("from_fiscal_year")
	to_fy = filters.get("to_fiscal_year") or from_fy
	if not from_fy:
		fy = get_fiscal_year(getdate(), company=filters.get("company"), raise_on_missing=False)
		if not fy:
			return []
		from_fy = to_fy = fy[0]

	start = frappe.db.get_value("Fiscal Year", from_fy, "year_start_date")
	end = frappe.db.get_value("Fiscal Year", to_fy, "year_end_date")
	if not start or not end:
		return []
	if getdate(start) > getdate(end):
		frappe.throw(_("From Fiscal Year must be on or before To Fiscal Year"))

	fy = frappe.qb.DocType("Fiscal Year")
	years = (
		frappe.qb.from_(fy)
		.select(fy.name, fy.year_start_date, fy.year_end_date)
		.where(fy.disabled == 0)
		.where(fy.year_start_date >= start)
		.where(fy.year_end_date <= end)
		.orderby(fy.year_start_date)
		.run(as_dict=True)
	)
	for y in years:
		y.key = frappe.scrub(y.name)
	return years


def get_columns(filters, years):
	link_doctype, label = GROUP_FIELDS[filters.group_by]
	columns = [
		{"label": _(label), "fieldname": "group", "fieldtype": "Link", "options": link_doctype, "width": 200},
	]
	for y in years:
		columns.append({"label": y.name, "fieldname": y.key, "fieldtype": "Currency", "width": 140})
	columns.append({"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "width": 150})
	return columns


def get_data(filters, years):
	params = {"from_date": years[0].year_start_date, "to_date": years[-1].year_end_date}
	conditions = [
		"pi.docstatus = 1",
		"IFNULL(pii.insurance_policy, '') != ''",
		"pi.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	if filters.get("company"):
		conditions.append("pi.company = %(company)s")
		params["company"] = filters.company

	# Item-level net amounts in company currency; debit notes (is_return = 1)
	# already carry negative amounts so they net off automatically.
	dims = "".join(f", pii.`{f}`" for f in premium_dimension_fields() if f != "cost_center")
	lines = frappe.db.sql(
		"""
		SELECT
			pi.posting_date,
			pi.supplier,
			pii.insurance_policy,
			pii.cost_center,
			pii.base_net_amount AS amount{dims}
		FROM `tabPurchase Invoice Item` pii
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE {conditions}
		""".format(conditions=" AND ".join(conditions), dims=dims),
		params,
		as_dict=True,
	)
	# Location / dimension filters: keep each line's share inside them (see report_utils).
	for line, share in zip(lines, premium_line_share(lines, filters), strict=True):
		line.amount = flt(line.amount) * share
	lines = [line for line in lines if line.amount]

	category_weights = {}
	if filters.group_by == "Asset Category":
		category_weights = get_category_weights({l.insurance_policy for l in lines})

	not_set = _("Not Set")
	out = {}
	for line in lines:
		fy_key = _year_key(years, getdate(line.posting_date))
		if not fy_key:
			continue

		if filters.group_by == "Insurer":
			parts = [(line.supplier or not_set, flt(line.amount))]
		elif filters.group_by == "Cost Center":
			parts = [(line.cost_center or not_set, flt(line.amount))]
		else:
			weights = category_weights.get(line.insurance_policy) or [(not_set, 1)]
			amounts = split_amount(line.amount, [w for _c, w in weights])
			parts = [(cat, amt) for (cat, _w), amt in zip(weights, amounts)]

		for group, amount in parts:
			row = out.setdefault(group, frappe._dict({"group": group, "total": 0}))
			row[fy_key] = flt(row.get(fy_key)) + amount
			row.total += amount

	data = sorted(out.values(), key=lambda r: -flt(r.total))
	for row in data:
		for y in years:
			row.setdefault(y.key, 0)
	return data


def get_category_weights(policies):
	"""Asset Category allocation method:
	each premium line is split across asset categories pro-rata to the sum insured
	of the policy's insured-asset rows per category. Only rows currently Active are
	used; if a policy has no active rows (e.g. all removed), all its rows are used;
	if it has no rows at all the line goes to "Not Set". Category comes from the
	policy row (snapshot) and falls back to the Asset's current category.

	Returns {policy: [(asset_category, weight), ...]}."""
	if not policies:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT
			row.parent AS policy,
			IFNULL(NULLIF(row.asset_category, ''), a.asset_category) AS asset_category,
			row.row_status,
			row.sum_insured
		FROM `tabInsurance Policy Asset` row
		LEFT JOIN `tabAsset` a ON a.name = row.asset
		WHERE row.parenttype = 'Insurance Policy'
			AND row.parent IN %(policies)s
		""",
		{"policies": tuple(policies)},
		as_dict=True,
	)
	by_policy = {}
	for r in rows:
		by_policy.setdefault(r.policy, []).append(r)

	not_set = _("Not Set")
	out = {}
	for policy, prow in by_policy.items():
		active = [r for r in prow if r.row_status == "Active"] or prow
		weights = {}
		for r in active:
			cat = r.asset_category or not_set
			weights[cat] = weights.get(cat, 0) + flt(r.sum_insured)
		out[policy] = sorted(weights.items())
	return out


def _year_key(years, posting_date):
	for y in years:
		if getdate(y.year_start_date) <= posting_date <= getdate(y.year_end_date):
			return y.key
	return None


def get_chart(data, years):
	if not data:
		return None
	top = data[:15]
	return {
		"data": {
			"labels": [r.group for r in top],
			"datasets": [{"name": y.name, "values": [flt(r.get(y.key)) for r in top]} for y in years],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"title": _("Premium by Fiscal Year"),
	}


def get_summary(data):
	return [
		{"label": _("Total Net Premium"), "value": sum(flt(r.total) for r in data), "datatype": "Currency", "indicator": "blue"},
		{"label": _("Groups"), "value": len(data), "datatype": "Int", "indicator": "gray"},
	]
