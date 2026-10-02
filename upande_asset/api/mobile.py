# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""The five endpoints the mobile page calls, under the names it calls them by.

On kaitet-group.upande.com these are Server Scripts (`Asset Check Lookup`,
`Asset Check Submit`, `Tractor Day Lookup`, `Tractor Day Save`, `Tractor Task
Options`). Here they are app code, so the same page runs on both sites without
being rewritten. Everything real lives in the modules these delegate to.
"""

import json

import frappe

from upande_asset.api import asset_task
from upande_asset.upande_asset.doctype.asset_check_sheet.asset_check_sheet import (
	SECTIONS,
	lookup_for_scan,
)

FAULT_STATUSES = ("Fault / Defect", "Affected")


@frappe.whitelist()
def asset_check_lookup(asset=None, template=None, qr=None):
	"""One scan resolves the asset, its templates and the chosen check factors."""
	return lookup_for_scan(asset=asset, template=template, qr=qr)


@frappe.whitelist()
def asset_check_submit(data):
	"""Turn the filled-in mobile form into an Asset Check Sheet."""
	if isinstance(data, str):
		data = json.loads(data)
	if not data:
		frappe.throw(frappe._("Nothing was sent."))

	asset_id = (data.get("asset") or "").strip()
	template = (data.get("template") or "").strip()
	if not asset_id:
		frappe.throw(frappe._("Asset is required."))
	if not template:
		frappe.throw(frappe._("A check sheet template is required."))

	asset = frappe.db.get_value(
		"Asset", asset_id, ["asset_category", "company"], as_dict=True
	)
	if not asset:
		frappe.throw(frappe._("No asset with the number {0}").format(asset_id))

	tmpl = frappe.get_doc("Asset Check Sheet Template", template) if template else None

	doc = frappe.new_doc("Asset Check Sheet")
	doc.asset = asset_id
	doc.asset_category = asset.asset_category
	doc.company = asset.company
	doc.check_sheet_template = template
	doc.check_date = data.get("check_date") or frappe.utils.nowdate()
	doc.additional_notes = data.get("notes") or data.get("additional_notes") or ""

	sections = data.get("sections") or {}
	total = 0
	faults = 0

	for section in SECTIONS:
		if tmpl:
			doc.set(f"section_{section}_label", tmpl.get(f"section_{section}_name") or "")

		for row in sections.get(section) or []:
			factor = (row.get("check_factor") or "").strip()
			if not factor:
				continue
			status = (row.get("status") or "").strip()
			if not status:
				frappe.throw(
					frappe._("Every line needs a status. Missing on: {0}").format(factor)
				)
			doc.append(
				f"section_{section}_items",
				{
					"check_factor": factor,
					"threshold_percent": row.get("threshold_percent") or 0,
					"status": status,
					"severity": row.get("severity") or "",
					"fault_description": row.get("fault_description") or "",
					"incident_percent": row.get("incident_percent") or 0,
					"proposed_action": row.get("proposed_action") or "",
				},
			)
			total += 1
			if status in FAULT_STATUSES:
				faults += 1

	if not total:
		frappe.throw(frappe._("No observations were recorded."))

	doc.insert(ignore_permissions=True)
	if data.get("submit"):
		doc.submit()

	return {
		"status": "success",
		"name": doc.name,
		"docstatus": doc.docstatus,
		"overall_status": doc.get("overall_status"),
		"items": total,
		"faults": faults,
		"linked_asset_repair": doc.get("linked_asset_repair"),
		"linked_asset_maintenance": doc.get("linked_asset_maintenance"),
	}


@frappe.whitelist()
def tractor_day_lookup(tractor=None, date=None, qr=None):
	"""A machine's plan and actuals for one day."""
	return asset_task.lookup(asset=tractor, date=date, qr=qr)


@frappe.whitelist()
def tractor_day_save(data):
	"""Record or update a day's work."""
	return asset_task.save(data)


@frappe.whitelist()
def tractor_task_options(kind=None, q=None, **kwargs):
	"""Type-ahead lists for the day plan."""
	return asset_task.options(kind, q)
