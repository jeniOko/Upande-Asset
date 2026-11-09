# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset Maintenance ↔ Asset Service Record.

ERPNext keeps one open Asset Maintenance Log (Planned / Overdue) per maintenance
task; submitting it as Completed rolls the task to its next due date and opens
the next log. This module puts an accountable Asset Service Record on each of
those occurrences:

  log opened        → a Pending service record is created and assigned to the task owner
  record submitted  → the log is completed + submitted (ERPNext then marks the task
                      done, sets the next due date and opens the next log → next record)
  log completed directly while a record is open → blocked (complete the record instead)
"""

import frappe
from frappe import _
from frappe.utils import escape_html, get_link_to_form, getdate, strip_html, today

OPEN_LOG_STATUSES = ("Planned", "Overdue")


def auto_create_enabled():
	return bool(frappe.db.get_single_value("Asset Settings", "auto_create_service_records"))


def records_for_log(log_name, docstatus=None):
	"""Service records whose Maintenance Tasks table holds `log_name`."""
	t = frappe.qb.DocType("Asset Service Record Task")
	r = frappe.qb.DocType("Asset Service Record")
	q = (frappe.qb.from_(t).join(r).on(r.name == t.parent).select(r.name).distinct()
		.where(t.parenttype == "Asset Service Record").where(t.asset_maintenance_log == log_name))
	if docstatus is not None:
		q = q.where(r.docstatus.isin(docstatus) if isinstance(docstatus, (list, tuple)) else (r.docstatus == docstatus))
	return q.run(pluck=True)


def open_service_record(log_name):
	names = records_for_log(log_name, 0)
	return names[0] if names else None


def service_type_for_task(task_name):
	"""Task's own Service Type, else one named after its maintenance type."""
	task = frappe.db.get_value("Asset Maintenance Task", task_name, ["service_type", "maintenance_type"], as_dict=True) or {}
	if task.get("service_type"):
		return task.service_type
	name = task.get("maintenance_type") or "Preventive Maintenance"
	if not frappe.db.exists("Asset Service Type", name):
		frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": name}).insert(ignore_permissions=True)
	return name


def create_pending_service_record(log):
	"""Pending service record for an open maintenance log (idempotent)."""
	if log.docstatus != 0 or log.maintenance_status not in OPEN_LOG_STATUSES or not log.task:
		return None
	if records_for_log(log.name, (0, 1)):
		return None
	am = frappe.db.get_value("Asset Maintenance", log.asset_maintenance, ["asset_name", "company"], as_dict=True)
	if not am:
		return None
	task = frappe.db.get_value(
		"Asset Maintenance Task", log.task, ["maintenance_task", "assign_to", "description"], as_dict=True
	) or frappe._dict()
	due = log.due_date or today()
	rec = frappe.get_doc({
		"doctype": "Asset Service Record",
		# Asset Maintenance.asset_name holds the Asset ID; show the asset's real name.
		"title": _("{0} for {1}").format(task.maintenance_task or log.task_name,
			frappe.db.get_value("Asset", am.asset_name, "asset_name") or am.asset_name),
		"asset": am.asset_name,
		"company": am.company,
		"status": "Pending",
		"tasks": [{
			"asset_maintenance_log": log.name, "maintenance_task": task.maintenance_task or log.task_name,
			"due_date": log.due_date, "asset_maintenance": log.asset_maintenance,
			"certificate_required": log.has_certificate, "log_status": log.maintenance_status,
		}],
		"service_type": service_type_for_task(log.task),
		"service_date": due,
		"assigned_to": task.assign_to,
		"description": strip_html(task.description or "") or None,
	})
	from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import apply_service_type_defaults

	apply_service_type_defaults(rec, rec.service_type)
	# Without a service-type default, maintenance is done by the company's own technicians.
	rec.service_by = rec.service_by or "Internal"
	rec.flags.ignore_permissions = True
	rec.flags.ignore_mandatory = True
	rec.insert()
	if task.assign_to:
		# v16: the public add() checks the caller's permission; _add takes ignore_permissions.
		from frappe.desk.form.assign_to import _add as assign

		try:
			assign({
				"assign_to": [task.assign_to],
				"doctype": rec.doctype,
				"name": rec.name,
				"description": _("Maintenance due {0}: {1} on {2}").format(
					frappe.format(due, "Date"), escape_html(rec.title), am.asset_name
				),
				"date": due,
			}, ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Could not assign service record {rec.name}")
	return rec.name


def complete_maintenance_logs(record):
	"""Complete + submit every log in the record's Maintenance Tasks table."""
	for row in record.get("tasks") or []:
		_complete_log(record, row)
		row.db_set("log_status", "Completed", update_modified=False)


def _complete_log(record, row):
	log = frappe.get_doc("Asset Maintenance Log", row.asset_maintenance_log)
	if log.docstatus == 1:
		return
	parts = [f"<p>{_('Completed via Asset Service Record {0} by {1}').format(get_link_to_form(record.doctype, record.name), escape_html(frappe.utils.get_fullname(frappe.session.user)))}</p>"]
	for label, value in ((_("Performed by"), record.performed_by), (_("Supplier"), record.supplier_name or record.supplier),
			(_("Work done"), record.description), (_("Findings"), record.findings)):
		if value:
			parts.append(f"<p><b>{label}:</b> {escape_html(value)}</p>")
	log.maintenance_status = "Completed"
	log.completion_date = record.service_date
	log.actions_performed = "".join(parts)
	if row.get("certificate"):
		log.certificate_attachement = row.certificate
	log.flags.from_service_record = True
	log.flags.ignore_permissions = True
	# Submitting the log makes ERPNext re-save the Asset Task and Asset Maintenance
	# (update_maintenance_task) without ignore_permissions. Whoever may submit the
	# service record must be able to complete the task, so run that bookkeeping
	# with elevated rights; the actual user is recorded in actions_performed above.
	user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		log.submit()
	finally:
		frappe.set_user(user)


# ── doc_events on Asset Maintenance Log ───────────────────────────────────────


def log_after_insert(doc, method=None):
	if auto_create_enabled():
		create_pending_service_record(doc)


def log_on_update(doc, method=None):
	rec = open_service_record(doc.name)
	if rec:
		# Due date moved (e.g. task rescheduled) – keep the open record's row in step.
		for row in frappe.get_all("Asset Service Record Task", filters={"parent": rec, "asset_maintenance_log": doc.name}, pluck="name"):
			frappe.db.set_value("Asset Service Record Task", row,
				{"due_date": doc.due_date, "log_status": doc.maintenance_status}, update_modified=False)
	elif auto_create_enabled():
		create_pending_service_record(doc)


def log_validate(doc, method=None):
	if doc.maintenance_status != "Completed" or doc.flags.from_service_record:
		return
	rec = open_service_record(doc.name)
	if rec:
		frappe.throw(
			_("Record the work on Asset Service Record {0} and submit it – that completes this maintenance log.").format(
				get_link_to_form("Asset Service Record", rec)
			),
			title=_("Complete via Service Record"),
		)


def ensure_maintenance_data():
	"""After every migrate (safe to repeat), for sites that already used
	ERPNext Asset Maintenance before this app:
	- Service Type is mandatory on Asset Maintenance tasks: fill empty ones so those
	  Asset Maintenance records can still be saved.
	- Open maintenance logs without a Service Record get a Pending one (when Asset Settings
	  → Create a Service Record for every maintenance due is on)."""
	if frappe.db.has_column("Asset Maintenance Task", "service_type"):
		for name in frappe.get_all("Asset Maintenance Task", filters={"service_type": ["in", ["", None]]}, pluck="name"):
			frappe.db.set_value("Asset Maintenance Task", name, "service_type", service_type_for_task(name), update_modified=False)
	if auto_create_enabled():
		for name in frappe.get_all("Asset Maintenance Log",
				filters={"docstatus": 0, "maintenance_status": ["in", list(OPEN_LOG_STATUSES)]}, pluck="name"):
			create_pending_service_record(frappe.get_doc("Asset Maintenance Log", name))


@frappe.whitelist()
def create_missing_service_records():
	"""Backfill: a Pending record for every open log that has none."""
	frappe.only_for(("System Manager", "Maintenance Manager"))
	created = []
	for name in frappe.get_all(
		"Asset Maintenance Log", filters={"docstatus": 0, "maintenance_status": ["in", list(OPEN_LOG_STATUSES)]}, pluck="name"
	):
		rec = create_pending_service_record(frappe.get_doc("Asset Maintenance Log", name))
		if rec:
			created.append(rec)
	return created
