# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, escape_html, flt, fmt_money, today

from upande_asset.upande_asset.report_utils import asset_condition, premium_dimension_fields, premium_line_share


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	message = get_loss_ratio_html(filters)
	return columns, data, message, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"label": _("Claim"), "fieldname": "claim", "fieldtype": "Link", "options": "Insurance Claim", "width": 150},
		{"label": _("Asset"), "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 120},
		{"label": _("Asset Name"), "fieldname": "asset_name", "fieldtype": "Data", "width": 160},
		{"label": _("Incident Date"), "fieldname": "incident_date", "fieldtype": "Date", "width": 105},
		{"label": _("Incident Type"), "fieldname": "incident_type", "fieldtype": "Data", "width": 110},
		{"label": _("Policy"), "fieldname": "policy", "fieldtype": "Link", "options": "Insurance Policy", "width": 150},
		{"label": _("Insurer"), "fieldname": "insurer", "fieldtype": "Link", "options": "Supplier", "width": 140},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Amount Claimed"), "fieldname": "amount_claimed", "fieldtype": "Currency", "width": 125},
		{"label": _("Excess"), "fieldname": "excess", "fieldtype": "Currency", "width": 110},
		{"label": _("Amount Approved"), "fieldname": "amount_approved", "fieldtype": "Currency", "width": 125},
		{"label": _("Amount Received"), "fieldname": "amount_received", "fieldtype": "Currency", "width": 125},
		{"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Days Since Recorded"), "fieldname": "days_since_lodged", "fieldtype": "Int", "width": 120},
	]


def _claim_conditions(filters, params, alias="c"):
	conditions = []
	if filters.get("company"):
		conditions.append(f"{alias}.company = %(company)s")
		params["company"] = filters.company
	if filters.get("insurer"):
		conditions.append(f"{alias}.insurer = %(insurer)s")
		params["insurer"] = filters.insurer
	if filters.get("from_date"):
		conditions.append(f"{alias}.incident_date >= %(from_date)s")
		params["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append(f"{alias}.incident_date <= %(to_date)s")
		params["to_date"] = filters.to_date
	if scope := asset_condition(filters, f"{alias}.asset", params):
		conditions.append(scope)
	return conditions


def get_data(filters):
	params = {}
	conditions = ["c.docstatus < 2"] + _claim_conditions(filters, params)
	if filters.get("status"):
		conditions.append("c.status = %(status)s")
		params["status"] = filters.status

	rows = frappe.db.sql(
		"""
		SELECT
			c.name AS claim,
			c.asset,
			c.asset_name,
			c.incident_date,
			c.incident_type,
			c.policy,
			c.insurer,
			c.status,
			c.amount_claimed,
			c.excess,
			c.amount_approved,
			c.amount_received,
			c.outstanding_amount,
			c.lodged_on
		FROM `tabInsurance Claim` c
		WHERE {conditions}
		ORDER BY c.incident_date DESC, c.name DESC
		""".format(conditions=" AND ".join(conditions)),
		params,
		as_dict=True,
	)
	now = today()
	for r in rows:
		r.days_since_lodged = date_diff(now, r.lodged_on) if r.lodged_on else None
		r.pop("lodged_on", None)
	return rows


def get_loss_ratio_html(filters):
	"""Loss ratio per insurer = approved amounts of submitted claims with an incident
	in the period ÷ net premium invoiced to that insurer in the period (submitted
	Purchase Invoices linked to an Insurance Policy; debit notes net off)."""
	params = {}
	claim_cond = ["c.docstatus = 1"] + _claim_conditions(filters, params)
	approved = frappe.db.sql(
		"""
		SELECT c.insurer, SUM(c.amount_approved) AS approved
		FROM `tabInsurance Claim` c
		WHERE {conditions}
		GROUP BY c.insurer
		""".format(conditions=" AND ".join(claim_cond)),
		params,
		as_dict=True,
	)

	pi_params = {}
	pi_cond = ["pi.docstatus = 1", "IFNULL(pii.insurance_policy, '') != ''"]
	if filters.get("company"):
		pi_cond.append("pi.company = %(company)s")
		pi_params["company"] = filters.company
	if filters.get("insurer"):
		pi_cond.append("pi.supplier = %(insurer)s")
		pi_params["insurer"] = filters.insurer
	if filters.get("from_date"):
		pi_cond.append("pi.posting_date >= %(from_date)s")
		pi_params["from_date"] = filters.from_date
	if filters.get("to_date"):
		pi_cond.append("pi.posting_date <= %(to_date)s")
		pi_params["to_date"] = filters.to_date
	# Premium = item rows linked to a policy; base_net_amount is already negative on debit notes.
	# Read per line so Location / dimension filters can take each line's share.
	dims = "".join(f", pii.`{f}`" for f in premium_dimension_fields() if f != "cost_center")
	lines = frappe.db.sql(
		"""
		SELECT pi.supplier AS insurer, pii.insurance_policy, pii.cost_center, pii.base_net_amount AS amount{dims}
		FROM `tabPurchase Invoice Item` pii
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE {conditions}
		""".format(conditions=" AND ".join(pi_cond), dims=dims),
		pi_params,
		as_dict=True,
	)
	by_insurer = {}
	for line, share in zip(lines, premium_line_share(lines, filters), strict=True):
		by_insurer[line.insurer] = by_insurer.get(line.insurer, 0) + flt(line.amount) * share
	premiums = [frappe._dict(insurer=k, premium=v) for k, v in by_insurer.items()]

	stats = {}
	for r in approved:
		stats.setdefault(r.insurer or "", [0, 0])[0] = flt(r.approved)
	for r in premiums:
		stats.setdefault(r.insurer or "", [0, 0])[1] = flt(r.premium)
	if not stats:
		return None

	currency = None
	if filters.get("company"):
		currency = frappe.get_cached_value("Company", filters.company, "default_currency")

	body = []
	for insurer in sorted(stats):
		appr, prem = stats[insurer]
		ratio = f"{flt(appr / prem * 100, 1)}%" if prem > 0 else "—"
		color = "var(--red-500)" if prem > 0 and appr > prem else "inherit"
		body.append(
			"<tr><td>{}</td><td class='text-right'>{}</td><td class='text-right'>{}</td>"
			"<td class='text-right' style='color:{};font-weight:600'>{}</td></tr>".format(
				escape_html(insurer or _("Not Set")),
				fmt_money(appr, currency=currency),
				fmt_money(prem, currency=currency),
				color,
				ratio,
			)
		)

	return """
		<div style="margin-top:12px">
			<h6 class="text-muted">{title}</h6>
			<table class="table table-bordered table-condensed" style="max-width:720px">
				<thead><tr><th>{h1}</th><th class='text-right'>{h2}</th><th class='text-right'>{h3}</th><th class='text-right'>{h4}</th></tr></thead>
				<tbody>{body}</tbody>
			</table>
		</div>
	""".format(
		title=_("Loss Ratio by Insurer (approved claims ÷ premium invoiced in period)"),
		h1=_("Insurer"),
		h2=_("Approved Claims"),
		h3=_("Net Premium"),
		h4=_("Loss Ratio"),
		body="".join(body),
	)


def get_chart(data):
	if not data:
		return None
	by_status = {}
	for r in data:
		by_status[r.status or _("Not Set")] = by_status.get(r.status or _("Not Set"), 0) + 1
	labels = list(by_status)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Claims"), "values": [by_status[k] for k in labels]}]},
		"type": "donut",
		"title": _("Claims by Status"),
	}


def get_summary(data):
	def total(field):
		return sum(flt(r.get(field)) for r in data)

	return [
		{"label": _("Total Claimed"), "value": total("amount_claimed"), "datatype": "Currency", "indicator": "blue"},
		{"label": _("Total Approved"), "value": total("amount_approved"), "datatype": "Currency", "indicator": "green"},
		{"label": _("Total Received"), "value": total("amount_received"), "datatype": "Currency", "indicator": "green"},
		{"label": _("Total Outstanding"), "value": total("outstanding_amount"), "datatype": "Currency", "indicator": "orange"},
	]
