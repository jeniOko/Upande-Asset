# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, strip_html

from upande_asset.upande_asset.report_utils import asset_scope


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("asset"):
		frappe.throw(_("Please select an Asset"))
	scope = asset_scope(filters)
	if scope is not None and filters.asset not in scope:
		frappe.throw(_("Asset {0} is not in the selected Company, Location or dimensions.").format(filters.asset))

	data = []
	data += get_asset_tasks(filters)
	data += get_check_sheets(filters)
	data += get_repairs(filters)
	data += get_claims(filters)
	data.sort(key=lambda r: (r.date or getdate("1900-01-01"), r.document or ""), reverse=True)

	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 105},
		{"label": _("Type"), "fieldname": "type", "fieldtype": "Data", "width": 130},
		{"label": _("Document Type"), "fieldname": "doctype", "fieldtype": "Data", "width": 10, "hidden": 1},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "doctype", "width": 170},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": _("Summary"), "fieldname": "summary", "fieldtype": "Data", "width": 320},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
	]


def _date_filters(field, filters, is_datetime=False):
	out = []
	if filters.get("from_date"):
		out.append([field, ">=", getdate(filters.from_date)])
	if filters.get("to_date"):
		if is_datetime:
			out.append([field, "<", add_days(getdate(filters.to_date), 1)])
		else:
			out.append([field, "<=", getdate(filters.to_date)])
	return out


def _summary_text(*parts, limit=200):
	text = " — ".join(strip_html(p).strip() for p in parts if p and strip_html(p).strip())
	return text[:limit] + ("…" if len(text) > limit else "")


def _row(date, type_, doctype, name, status=None, summary=None, amount=None):
	return frappe._dict(
		{
			"date": getdate(date) if date else None,
			"type": type_,
			"doctype": doctype,
			"document": name,
			"status": status,
			"summary": summary,
			"amount": flt(amount) if amount is not None else None,
		}
	)


def get_asset_tasks(filters):
	"""Asset Task is optional (another app). Its asset link is `tractor`; the
	date is `posting_date` when the doctype has it, else `creation`; only
	breakdown tasks are included when a `breakdown` field exists."""
	if not frappe.db.exists("DocType", "Asset Task"):
		return []
	meta = frappe.get_meta("Asset Task")
	if not meta.has_field("tractor"):
		return []

	date_field = "posting_date" if meta.has_field("posting_date") else "creation"
	flt_list = [["tractor", "=", filters.asset], ["docstatus", "<", 2]]
	if meta.has_field("breakdown"):
		flt_list.append(["breakdown", "=", 1])
	flt_list += _date_filters(date_field, filters, is_datetime=(date_field == "creation"))

	text_fields = [f for f in ("subject", "title", "description", "remarks") if meta.has_field(f)]
	fields = ["name", f"{date_field} as task_date"] + text_fields
	if meta.has_field("status"):
		fields.append("status")

	return [
		_row(
			t.task_date,
			_("Breakdown Task"),
			"Asset Task",
			t.name,
			t.get("status"),
			_summary_text(*(t.get(f) for f in text_fields)),
		)
		for t in frappe.get_all("Asset Task", filters=flt_list, fields=fields)
	]


def get_check_sheets(filters):
	rows = frappe.get_all(
		"Asset Check Sheet",
		filters=[["asset", "=", filters.asset], ["docstatus", "=", 1]] + _date_filters("check_date", filters),
		fields=["name", "check_date", "overall_status", "check_sheet_template", "additional_notes"],
	)
	return [
		_row(
			r.check_date,
			_("Check Sheet"),
			"Asset Check Sheet",
			r.name,
			r.overall_status,
			_summary_text(r.check_sheet_template, r.additional_notes),
		)
		for r in rows
	]


def get_repairs(filters):
	rows = frappe.get_all(
		"Asset Repair",
		filters=[["asset", "=", filters.asset], ["docstatus", "<", 2]]
		+ _date_filters("failure_date", filters, is_datetime=True),
		fields=["name", "failure_date", "repair_status", "description", "total_repair_cost"],
	)
	return [
		_row(
			r.failure_date,
			_("Repair"),
			"Asset Repair",
			r.name,
			r.repair_status,
			_summary_text(r.description),
			r.total_repair_cost,
		)
		for r in rows
	]


def get_claims(filters):
	rows = frappe.get_all(
		"Insurance Claim",
		filters=[["asset", "=", filters.asset], ["docstatus", "<", 2]] + _date_filters("incident_date", filters),
		fields=["name", "incident_date", "incident_type", "status", "policy", "description", "amount_claimed"],
	)
	return [
		_row(
			r.incident_date,
			_("Insurance Claim"),
			"Insurance Claim",
			r.name,
			r.status,
			_summary_text(r.incident_type, r.policy, r.description),
			r.amount_claimed,
		)
		for r in rows
	]


def get_chart(data):
	if not data:
		return None
	counts = {}
	for r in data:
		counts[r.type] = counts.get(r.type, 0) + 1
	labels = list(counts)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Events"), "values": [counts[k] for k in labels]}]},
		"type": "bar",
		"title": _("Events by Type"),
	}


def get_summary(data):
	repair_cost = sum(flt(r.amount) for r in data if r.doctype == "Asset Repair")
	claimed = sum(flt(r.amount) for r in data if r.doctype == "Insurance Claim")
	return [
		{"label": _("Events"), "value": len(data), "datatype": "Int", "indicator": "blue"},
		{"label": _("Repair Cost"), "value": repair_cost, "datatype": "Currency", "indicator": "orange"},
		{"label": _("Amount Claimed"), "value": claimed, "datatype": "Currency", "indicator": "green"},
	]
