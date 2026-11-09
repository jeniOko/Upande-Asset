# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Why-Why (root cause) analysis on Asset Check Sheet and Asset Repair (its own tab).

Fields on each parent (same fieldnames so a repair raised from a check sheet
starts with the sheet's analysis):
  problem_statement     what happened (the first "why?")
  why_why_analysis      Table → Why Why Analysis (one "Because…" per numbered why)
  root_cause            the last answer in the chain (set automatically)
  root_cause_category   Select "Cause Type" (ROOT_CAUSE_CATEGORIES) – optional, for reporting
  corrective_actions    Table → Corrective Action
"""

import frappe
from frappe import _

ROOT_CAUSE_CATEGORIES = ["People", "Maintenance", "Equipment", "Environment", "Animal Health", "Security", "Other"]
ANALYSIS_DOCTYPES = ("Asset Check Sheet", "Asset Repair")


def set_root_cause(doc):
	"""root_cause = last answer in the why chain (kept editable only via the chain)."""
	answers = [(r.cause or "").strip() for r in doc.get("why_why_analysis") or [] if (r.cause or "").strip()]
	doc.root_cause = answers[-1] if answers else None


def has_analysis(doc):
	return bool(doc.get("problem_statement") or doc.get("why_why_analysis"))


def copy_analysis(source, target, include_actions=False):
	"""Copy the analysis from `source` to `target` (both docs) if target has none."""
	if not source or has_analysis(target):
		return False
	target.problem_statement = source.get("problem_statement")
	target.root_cause_category = source.get("root_cause_category")
	target.set("why_why_analysis", [])
	for r in source.get("why_why_analysis") or []:
		target.append("why_why_analysis", {"cause": r.cause})
	if include_actions and target.meta.has_field("corrective_actions"):
		target.set("corrective_actions", [])
		for a in source.get("corrective_actions") or []:
			target.append("corrective_actions", {
				"action": a.action, "assigned_to": a.assigned_to, "due_date": a.due_date, "status": a.status,
			})
	set_root_cause(target)
	return True


def sync_corrective_actions(doc):
	"""One ToDo per assigned open action; Done/Cancelled rows close their ToDo."""
	for row in doc.get("corrective_actions") or []:
		status = row.status or "Open"
		if row.todo and frappe.db.exists("ToDo", row.todo):
			todo_status = frappe.db.get_value("ToDo", row.todo, "status")
			wanted = {"Done": "Closed", "Cancelled": "Cancelled"}.get(status, "Open")
			if todo_status != wanted:
				frappe.db.set_value("ToDo", row.todo, "status", wanted)
			continue
		if status != "Open" or not row.assigned_to:
			continue
		todo = frappe.get_doc({
			"doctype": "ToDo",
			"status": "Open",
			"allocated_to": row.assigned_to,
			"date": row.due_date,
			"priority": "Medium",
			"reference_type": doc.doctype,
			"reference_name": doc.name,
			"assigned_by": frappe.session.user,
			"description": _("Corrective action for {0} {1} ({2}): {3}").format(
				_(doc.doctype), doc.name, doc.get("asset") or "", frappe.utils.escape_html(row.action or "")
			)
			+ (f"<br>{_('Root cause')}: {frappe.utils.escape_html(doc.root_cause)}" if doc.get("root_cause") else ""),
		})
		todo.flags.ignore_permissions = True
		todo.insert()
		row.db_set("todo", todo.name, update_modified=False)


# ── doc_events ────────────────────────────────────────────────────────────────


def validate(doc, method=None):
	set_root_cause(doc)


def on_update(doc, method=None):
	sync_corrective_actions(doc)


def asset_repair_validate(doc, method=None):
	"""Carry the analysis over from the originating check sheet on a new repair."""
	if doc.is_new() and doc.get("check_sheet") and not has_analysis(doc):
		copy_analysis(frappe.get_doc("Asset Check Sheet", doc.check_sheet), doc)
	set_root_cause(doc)


def todo_on_update(doc, method=None):
	"""Closing / cancelling the ToDo updates the corrective action row."""
	if doc.reference_type not in ANALYSIS_DOCTYPES or doc.status not in ("Closed", "Cancelled"):
		return
	status = "Done" if doc.status == "Closed" else "Cancelled"
	for name in frappe.get_all("Corrective Action", filters={"todo": doc.name, "status": ["!=", status]}, pluck="name"):
		frappe.db.set_value("Corrective Action", name, "status", status, update_modified=False)


@frappe.whitelist()
def show_guide():
	"""Asset Settings → Display → Why-Why switches (not sensitive; readable by any desk user)."""
	def on(field):
		value = frappe.db.get_single_value("Asset Settings", field)
		return value is None or bool(value)

	return {"enabled": on("enable_why_why"), "guide": on("enable_why_why") and on("show_why_why_guide")}
