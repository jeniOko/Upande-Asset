# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset Maintenance ↔ Asset Service Record.

ERPNext keeps one open Asset Maintenance Log (Planned / Overdue) per maintenance
task; submitting it as Completed rolls the task to its next due date and opens
the next log. This module puts an accountable Asset Service Record on each of
those occurrences:

  log comes due     → a Pending service record is created and assigned to the task owner
                      (Asset Settings: N days before the due date; checked on save and daily)
  before / past due → reminders to the assignee and the chosen roles
  record submitted  → the log is completed + submitted (ERPNext then marks the task
                      done, sets the next due date and opens the next log → next record)
  log completed directly while a record is open → blocked (complete the record instead)
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, escape_html, get_link_to_form, get_url_to_form, getdate, strip_html, today

OPEN_LOG_STATUSES = ("Planned", "Overdue")


def auto_create_enabled():
	return bool(frappe.db.get_single_value("Asset Settings", "auto_create_service_records"))


def lead_days():
	return cint(frappe.db.get_single_value("Asset Settings", "service_record_lead_days"))


def within_lead_time(due_date):
	"""Records are created `service_record_lead_days` before the log's due date (overdue: at once)."""
	return not due_date or getdate(due_date) <= getdate(add_days(today(), lead_days()))


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
	if not within_lead_time(log.due_date):
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
	  → Create a Service Record for every maintenance due is on).
	- Service Records without a Cost Center take their asset's.
	- Materials rows keep purchase / transfer requests in their own field."""
	from upande_asset.upande_asset.materials import migrate_purchase_requests, sync_rows

	migrate_purchase_requests()
	if frappe.db.get_single_value("Asset Settings", "parts_purchase_document") == "Both":
		frappe.db.set_single_value("Asset Settings", "parts_purchase_document", "Material Request and Purchase Order")
	_drop_stale_grid_settings()
	# Rows without a stored Status yet (added before rows had one).
	sync_rows({(r.parenttype, r.parent) for r in frappe.get_all("Asset Service Material",
		filters={"material_status": ["in", ["", None]], "parenttype": ["in", ["Asset Service Record", "Asset Repair"]]},
		fields=["parenttype", "parent"])})
	frappe.db.sql(
		"""UPDATE `tabAsset Service Record` r JOIN `tabAsset` a ON a.name = r.asset
		SET r.cost_center = a.cost_center WHERE IFNULL(r.cost_center, '') = '' AND IFNULL(a.cost_center, '') != ''"""
	)
	if frappe.db.has_column("Asset Maintenance Task", "service_type"):
		for name in frappe.get_all("Asset Maintenance Task", filters={"service_type": ["in", ["", None]]}, pluck="name"):
			frappe.db.set_value("Asset Maintenance Task", name, "service_type", service_type_for_task(name), update_modified=False)
	if auto_create_enabled():
		for name in frappe.get_all("Asset Maintenance Log",
				filters={"docstatus": 0, "maintenance_status": ["in", list(OPEN_LOG_STATUSES)]}, pluck="name"):
			create_pending_service_record(frappe.get_doc("Asset Maintenance Log", name))


@frappe.whitelist()
def create_missing_service_records():
	"""Backfill: a Pending record for every open log within the lead time that has none."""
	frappe.only_for(("System Manager", "Maintenance Manager"))
	created = []
	for name in frappe.get_all(
		"Asset Maintenance Log", filters={"docstatus": 0, "maintenance_status": ["in", list(OPEN_LOG_STATUSES)]}, pluck="name"
	):
		rec = create_pending_service_record(frappe.get_doc("Asset Maintenance Log", name))
		if rec:
			created.append(rec)
	return created


# ── Daily: create records as they come due, and remind ───────────────────────


def daily():
	for job in (create_due_service_records, send_service_reminders):
		try:
			job()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Asset maintenance job failed: {job.__name__}")


def create_due_service_records():
	if not auto_create_enabled():
		return
	for name in frappe.get_all("Asset Maintenance Log", pluck="name", filters={
			"docstatus": 0, "maintenance_status": ["in", list(OPEN_LOG_STATUSES)],
			"due_date": ["<=", add_days(today(), lead_days())]}):
		create_pending_service_record(frappe.get_doc("Asset Maintenance Log", name))


def send_service_reminders():
	"""Same scheme as insurance renewal reminders: one reminder per configured band
	before the due date (the band last sent is kept on the record), then every N days
	while overdue."""
	from upande_asset.upande_asset.insurance.utils import get_settings, notify, parse_days, users_for_setting

	settings = get_settings()
	bands = parse_days(settings.service_reminder_days)
	overdue_every = cint(settings.service_overdue_reminder_days)
	if not bands and not overdue_every:
		return
	role_users = users_for_setting("service_reminder_roles")
	from upande_asset.upande_asset.emails import Batch

	# One email per person a day, listing every record due a reminder.
	batch = Batch("Asset Service Record", single=lambda i: service_reminder_email(i["r"], i["days_left"]), grouped=service_group_email)
	for r in frappe.get_all("Asset Service Record", filters={"docstatus": 0, "status": ["in", ["Pending", "In Progress"]],
			"service_date": ["is", "set"]}, fields=["name", "title", "asset", "asset_name", "service_date", "assigned_to",
			"status", "last_reminder_days", "last_reminder_on", "service_type", "service_by", "supplier_name", "company"]):
		days_left = date_diff(r.service_date, today())
		if days_left >= 0:
			hit = [b for b in bands if days_left <= b]
			if not hit:
				continue
			band = min(hit)
			if r.last_reminder_days and band >= r.last_reminder_days:
				continue
			subject = _("Maintenance due in {0} days: {1}").format(days_left, r.title or r.name) if days_left else \
				_("Maintenance due today: {0}").format(r.title or r.name)
		else:
			if not overdue_every or (r.last_reminder_on and date_diff(today(), r.last_reminder_on) < overdue_every
					and cint(r.last_reminder_days) < 0):
				continue
			band = -1  # marks "overdue reminder sent"; bands before the due date are positive
			subject = _("Maintenance overdue by {0} days: {1}").format(-days_left, r.title or r.name)
		users = [*role_users, *([r.assigned_to] if settings.notify_service_assignee and r.assigned_to else [])]
		if not users:
			continue
		batch.add(r.name, {"r": r, "days_left": days_left}, users=users)
		frappe.db.set_value("Asset Service Record", r.name, {"last_reminder_days": band, "last_reminder_on": today()},
			update_modified=False)
	batch.send()


def _drop_stale_grid_settings():
	"""Saved column layouts of the Materials table that still list removed fields
	(e.g. Estimated Rate) would keep hiding the new Status column."""
	meta = frappe.get_meta("Asset Service Material")
	for r in frappe.db.sql("select user, doctype, data from `__UserSettings` where data like %s",
			("%Asset Service Material%",), as_dict=True):
		data = frappe.parse_json(r.data) or {}
		cols = (data.get("GridView") or {}).get("Asset Service Material")
		if cols and any(not meta.has_field(c.get("fieldname")) or c.get("fieldname") == "material_request" for c in cols):
			data["GridView"].pop("Asset Service Material")
			frappe.db.sql("update `__UserSettings` set data=%s where user=%s and doctype=%s", (frappe.as_json(data), r.user, r.doctype))
			frappe.cache.hdel("_user_settings", f"{r.doctype}::{r.user}")


def service_reminder_email(r, days_left):
	"""Due soon / due today / overdue, in the shared reminder layout."""
	from upande_asset.upande_asset import emails as em

	title = r.title or r.name
	if days_left > 0:
		subject = _("Maintenance due in {0} days: {1}").format(days_left, title)
		tone, status = ("warning" if days_left <= 1 else "info"), _("Due in {0} days").format(days_left)
		intro = _("This maintenance is coming up. Plan the work and the parts it needs.")
	elif days_left == 0:
		subject = _("Maintenance due today: {0}").format(title)
		tone, status = "warning", _("Due today")
		intro = _("This maintenance is due today.")
	else:
		subject = _("Maintenance overdue by {0} days: {1}").format(-days_left, title)
		tone, status = "danger", _("Overdue by {0} days").format(-days_left)
		intro = _("This maintenance is past its due date. Do the work, or reschedule the maintenance task.")
	intro += " " + _("When the work is done, record it on the Service Record and click Complete.")
	tasks = frappe.get_all("Asset Service Record Task", filters={"parent": r.name}, pluck="maintenance_task")
	message = em.card(
		area=_("Maintenance"), tone=tone, status=status, title=title, intro=escape_html(intro),
		facts=[
			(_("Asset"), em.form_link("Asset", r.asset, f"{r.asset_name or r.asset} ({r.asset})")),
			(_("Due date"), em.date(r.service_date)),
			(_("Tasks"), escape_html(", ".join(filter(None, tasks)))),
			(_("Service type"), escape_html(r.get("service_type") or "")),
			(_("Done by"), escape_html(" · ".join(filter(None, [_(r.service_by) if r.get("service_by") else None, r.get("supplier_name")])))),
			(_("Assigned to"), escape_html(frappe.utils.get_fullname(r.assigned_to)) if r.assigned_to else _("Nobody yet")),
			(_("Status"), escape_html(_(r.status))),
		],
		button=(_("Open Service Record"), get_url_to_form("Asset Service Record", r.name)), company=r.get("company"),
	)
	return subject, message


def service_group_email(items):
	from frappe.utils import get_url_to_list

	from upande_asset.upande_asset import emails as em

	items = sorted(items, key=lambda i: i["days_left"])
	overdue = [i for i in items if i["days_left"] < 0]
	subject = _("{0} maintenance jobs need attention ({1} overdue)").format(len(items), len(overdue)) if overdue else \
		_("{0} maintenance jobs due soon").format(len(items))

	def due(d):
		if d < 0:
			return em.pill(_("Overdue {0} days").format(-d), "danger")
		return em.pill(_("Today") if d == 0 else _("In {0} days").format(d), "warning" if d <= 1 else "info")

	rows = [[
		em.form_link("Asset Service Record", i["r"].name, i["r"].title or i["r"].name),  # the title names the asset
		em.date(i["r"].service_date),
		escape_html(frappe.utils.get_fullname(i["r"].assigned_to)) if i["r"].assigned_to else _("Nobody yet"),
		due(i["days_left"]),
	] for i in items]
	message = em.grouped(
		area=_("Maintenance"), tone="danger" if overdue else "warning",
		title=_("{0} maintenance jobs need attention").format(len(items)),
		intro=escape_html(_("Do the work, then record it on each Service Record and click Complete. Overdue jobs are listed first.")),
		headers=[_("Job"), _("Due date"), _("Assigned to"), _("When")], rows=rows, company=items[0]["r"].get("company"),
		status=_("{0} overdue").format(len(overdue)) if overdue else _("Due soon"),
		button=(_("Open Service Records"), get_url_to_list("Asset Service Record")),
	)
	return subject, message
