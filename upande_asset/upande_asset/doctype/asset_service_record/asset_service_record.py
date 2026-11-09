import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt, get_link_to_form, getdate, nowdate

from upande_asset.upande_asset.repair_materials import default_parts_warehouse

# The invoices table uses ERPNext's own "Asset Repair Purchase Invoice" child
# (purchase_invoice, expense_account, repair_cost). Its allocation check sums
# every submitted row of that child regardless of parent, so cost allocated on a
# service record and on Asset Repairs share one balance per invoice + account.
from erpnext.assets.doctype.asset_repair.asset_repair import (
	_get_expense_accounts_for_purchase_invoice,
	get_allocated_repair_cost,
	get_total_expense_amount,
)


class AssetServiceRecord(Document):
	def onload(self):
		from upande_asset.upande_asset.insurance.utils import get_settings

		s = get_settings()
		self.set_onload("allow_direct_material_issue", int(s.allow_direct_material_issue or 0))
		self.set_onload("service_materials_warehouse", default_parts_warehouse(self.company))

	def validate(self):
		from upande_asset.upande_asset.repair_materials import validate_locked_rows

		validate_locked_rows(self, "materials")
		self._apply_service_type_defaults()
		self._set_material_rows()
		self._validate_maintenance_log()
		self._validate_purchase_invoices()
		self._calculate_total_cost()
		if self.docstatus == 0 and self.status not in ("Pending", "In Progress"):
			self.status = "Pending"
		if self.service_by == "Internal":
			self.supplier = self.supplier_name = self.reference_no = None

	def before_submit(self):
		if not self.service_type:
			frappe.throw(_("Set the Service Type before submitting."))
		if self.service_by == "External" and not self.supplier:
			frappe.throw(_("Set the Supplier who did the work before completing an external service."))
		for row in self.tasks:
			if row.certificate_required and not row.certificate:
				frappe.throw(_("Row {0}: task {1} requires a certificate. Attach it in Maintenance Tasks before completing.").format(
					row.idx, frappe.bold(row.maintenance_task or row.asset_maintenance_log)))
			log = frappe.db.get_value("Asset Maintenance Log", row.asset_maintenance_log, ["docstatus", "maintenance_status"], as_dict=True)
			if not log or log.docstatus == 2 or log.maintenance_status == "Cancelled":
				frappe.throw(_("Row {0}: Maintenance Log {1} was cancelled; remove the row.").format(row.idx, row.asset_maintenance_log))
		self.status = "Completed"
		self.completed_by = frappe.session.user

	def on_submit(self):
		if self.tasks:
			from upande_asset.upande_asset.maintenance import complete_maintenance_logs

			complete_maintenance_logs(self)
		from frappe.desk.form.assign_to import close_all_assignments

		close_all_assignments(self.doctype, self.name, ignore_permissions=True)

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		done = [r.asset_maintenance_log for r in self.tasks
			if frappe.db.get_value("Asset Maintenance Log", r.asset_maintenance_log, "docstatus") == 1]
		if done:
			frappe.msgprint(
				_("Maintenance Logs {0} stay completed and their tasks keep their next due dates. Amend this record to correct its details.").format(
					", ".join(get_link_to_form("Asset Maintenance Log", n) for n in done)),
				indicator="orange",
			)

	def _set_material_rows(self):
		"""Default warehouse and current Actual Qty on each Materials & Services row."""
		from erpnext.stock.get_item_details import _get_bin_details

		from upande_asset.upande_asset.insurance.utils import get_settings

		default_wh = default_parts_warehouse(self.company)
		for row in self.materials:
			if not row.item_code:
				continue
			is_stock = frappe.get_cached_value("Item", row.item_code, "is_stock_item")
			row.is_stock_item = is_stock
			if is_stock and not row.warehouse and default_wh:
				row.warehouse = default_wh
			row.actual_qty = flt((_get_bin_details(row.item_code, row.warehouse, self.company) or {}).get("actual_qty")) \
				if is_stock and row.warehouse else 0

	def _apply_service_type_defaults(self):
		"""Suggest Service By / Supplier / service item from the Service Type, without
		overwriting anything already chosen on this record. Runs again when the record is
		switched to External, so the usual supplier is offered then."""
		if not self.service_type:
			return
		if self.is_new() or self.has_value_changed("service_type") or (
			self.has_value_changed("service_by") and self.service_by == "External"
		):
			apply_service_type_defaults(self, self.service_type)

	def _validate_maintenance_log(self):
		from upande_asset.upande_asset.maintenance import records_for_log

		seen = set()
		for row in self.tasks:
			log = frappe.db.get_value("Asset Maintenance Log", row.asset_maintenance_log,
				["asset_maintenance", "task_name", "due_date", "has_certificate", "maintenance_status", "docstatus"], as_dict=True)
			if not log:
				continue
			if row.asset_maintenance_log in seen:
				frappe.throw(_("Row {0}: Maintenance Log {1} is listed twice.").format(row.idx, row.asset_maintenance_log))
			seen.add(row.asset_maintenance_log)
			log_asset = frappe.db.get_value("Asset Maintenance", log.asset_maintenance, "asset_name")
			if log_asset and log_asset != self.asset:
				frappe.throw(_("Row {0}: Maintenance Log {1} belongs to asset {2}.").format(row.idx, row.asset_maintenance_log, log_asset))
			other = [n for n in records_for_log(row.asset_maintenance_log, (0, 1)) if n != self.name]
			if other:
				frappe.throw(_("Row {0}: Maintenance Log {1} is already covered by {2}.").format(
					row.idx, row.asset_maintenance_log, get_link_to_form("Asset Service Record", other[0])))
			row.asset_maintenance, row.maintenance_task, row.due_date = log.asset_maintenance, log.task_name, log.due_date
			row.certificate_required, row.log_status = log.has_certificate, log.maintenance_status

	def _validate_purchase_invoices(self):
		seen = set()
		for row in self.invoices:
			if not row.purchase_invoice:
				continue

			key = (row.purchase_invoice, row.expense_account)
			if key in seen:
				frappe.throw(
					_("Row {0}: Purchase Invoice {1} with account {2} is duplicated.").format(
						row.idx, frappe.bold(row.purchase_invoice), frappe.bold(row.expense_account)
					)
				)
			seen.add(key)

			pi = frappe.db.get_value("Purchase Invoice", row.purchase_invoice, ["docstatus", "company"], as_dict=True)
			if not pi or pi.docstatus != 1:
				frappe.throw(
					_("Row {0}: Purchase Invoice {1} must be submitted before it can be linked here.").format(
						row.idx, frappe.bold(row.purchase_invoice)
					)
				)
			if pi.company != self.company:
				frappe.throw(
					_("Row {0}: Purchase Invoice {1} belongs to company {2}.").format(
						row.idx, frappe.bold(row.purchase_invoice), pi.company
					)
				)

			if row.expense_account not in _get_expense_accounts_for_purchase_invoice(row.purchase_invoice):
				frappe.throw(
					_(
						"Row {0}: Expense account {1} is not valid for Purchase Invoice {2}. "
						"Only expense accounts from non-stock items are allowed."
					).format(row.idx, frappe.bold(row.expense_account), get_link_to_form("Purchase Invoice", row.purchase_invoice))
				)

			available = available_amount(row.purchase_invoice, row.expense_account, exclude=self.name)
			row.repair_cost = flt(row.repair_cost, row.precision("repair_cost"))
			if row.repair_cost > available:
				frappe.throw(
					_(
						"Row {0}: Service cost {1} exceeds the unallocated amount {2} on Purchase Invoice {3} for {4} "
						"(already used by other Asset Repairs / Service Records)."
					).format(
						row.idx,
						frappe.bold(frappe.format_value(row.repair_cost, {"fieldtype": "Currency"})),
						frappe.bold(frappe.format_value(available, {"fieldtype": "Currency"})),
						get_link_to_form("Purchase Invoice", row.purchase_invoice),
						frappe.bold(row.expense_account),
					)
				)

	def _calculate_total_cost(self):
		# Actual values only: issued stock (Stock Entries) + invoice rows. Estimated rates are
		# only a guide copied onto Material Requests.
		if not self.is_new():
			self.materials_cost = issued_materials_value(self.name)
		self.services_cost = sum(flt(row.repair_cost) for row in self.invoices)
		self.total_service_cost = flt(self.services_cost) + flt(self.materials_cost)


def available_amount(purchase_invoice, expense_account, exclude=None):
	precision = frappe.get_precision("Asset Repair Purchase Invoice", "repair_cost")
	return flt(
		get_total_expense_amount(purchase_invoice, expense_account)
		- get_allocated_repair_cost(purchase_invoice, expense_account, exclude_asset_repair=exclude),
		precision,
	)


# ── Pickers (ERPNext's equivalents require Asset Repair permission) ───────────


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_purchase_invoice(doctype, txt, searchfield, start, page_len, filters):
	"""Submitted Purchase Invoices with at least one non-stock, non-asset expense line."""
	frappe.has_permission("Asset Service Record", throw=True)
	pi = DocType("Purchase Invoice")
	pi_item = DocType("Purchase Invoice Item")
	item = DocType("Item")
	query = (
		frappe.qb.from_(pi)
		.join(pi_item)
		.on(pi_item.parent == pi.name)
		.left_join(item)
		.on(item.name == pi_item.item_code)
		.select(pi.name, pi.supplier_name, pi.posting_date)
		.distinct()
		.where(pi.company == filters.get("company"))
		.where(pi.docstatus == 1)
		.where(pi_item.is_fixed_asset == 0)
		.where(pi_item.expense_account.isnotnull())
		.where(pi_item.expense_account != "")
		.where(pi_item.item_code.isnull() | (item.is_stock_item == 0))
		.orderby(pi.posting_date, order=frappe.qb.desc)
		.limit(page_len)
		.offset(start)
	)
	if filters.get("supplier"):
		query = query.where(pi.supplier == filters.get("supplier"))
	if txt:
		query = query.where(pi.name.like(f"%{txt}%") | pi.supplier_name.like(f"%{txt}%"))
	return query.run(as_list=1)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_expense_accounts(doctype, txt, searchfield, start, page_len, filters):
	frappe.has_permission("Asset Service Record", throw=True)
	purchase_invoice = filters.get("purchase_invoice")
	if not purchase_invoice:
		return []
	frappe.has_permission("Purchase Invoice", "read", purchase_invoice, throw=True)
	return [[a] for a in _get_expense_accounts_for_purchase_invoice(purchase_invoice) if not txt or txt.lower() in a.lower()]


@frappe.whitelist()
def get_unallocated_cost(purchase_invoice, expense_account, service_record=None):
	frappe.has_permission("Asset Service Record", throw=True)
	frappe.has_permission("Purchase Invoice", "read", purchase_invoice, throw=True)
	return available_amount(purchase_invoice, expense_account, exclude=service_record)


# ── Materials: Material Requests, issues and invoices that feed the record ────


def _purposes(service_by):
	return ("Purchase",) if service_by == "External" else ("Material Issue", "Purchase")


@frappe.whitelist()
def make_material_request(service_record, purpose, rows=None):
	"""Material Request from the record's Materials & Services rows.

	Internal work: Material Issue (parts from stores) or Purchase (parts to buy).
	External work: Purchase (the service itself, e.g. a supplier's service item).
	Each MR row carries asset_service_record, which ERPNext's own mappers copy onto the
	Stock Entry / Purchase Order / Purchase Invoice rows made from it."""
	rec = frappe.get_doc("Asset Service Record", service_record)
	rec.check_permission("write" if rec.docstatus == 0 else "read")
	frappe.has_permission("Material Request", "create", throw=True)
	if rec.docstatus == 2:
		frappe.throw(_("The service record is cancelled."))
	if purpose not in _purposes(rec.service_by):
		frappe.throw(_("{0} is not available for {1} service.").format(_(purpose), _(rec.service_by or "")))
	names = set(frappe.parse_json(rows) or []) if rows else None
	lines = [r for r in rec.materials if not r.material_request and not r.stock_entry and (names is None or r.name in names)]
	if not lines:
		frappe.throw(_("Add rows to Materials & Services (without a Material Request yet) first."))

	from upande_asset.upande_asset.insurance.utils import get_settings

	default_wh = default_parts_warehouse(rec.company)
	due = min([getdate(t.due_date) for t in rec.tasks if t.due_date] or [getdate(rec.service_date or nowdate())])
	schedule = max(due, getdate(nowdate()))
	mr = frappe.new_doc("Material Request")
	mr.update({"material_request_type": purpose, "company": rec.company, "transaction_date": nowdate(), "schedule_date": schedule})
	for r in lines:
		warehouse = r.warehouse or default_wh
		if purpose == "Material Issue" and not warehouse:
			frappe.throw(_("Row {0}: set the Warehouse to issue {1} from.").format(r.idx, r.item_code))
		stock_uom = frappe.db.get_value("Item", r.item_code, "stock_uom")
		uom = r.uom or stock_uom
		factor = 1
		if uom != stock_uom:
			from erpnext.stock.get_item_details import get_conversion_factor

			factor = flt((get_conversion_factor(r.item_code, uom) or {}).get("conversion_factor")) or 1
		mr.append("items", {
			"item_code": r.item_code, "qty": r.qty, "uom": uom, "stock_uom": stock_uom, "conversion_factor": factor,
			"warehouse": warehouse, "schedule_date": schedule, "rate": r.estimated_rate,
			"description": f"{r.item_name or r.item_code} – {rec.title} ({rec.name})",
			"asset_service_record": rec.name,
		})
	mr.insert()
	if (get_settings().maintenance_mr_status or "Draft") == "Submitted":
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Material Request"):
			mr.submit()
	for r in lines:
		frappe.db.set_value("Asset Service Material", r.name, "material_request", mr.name, update_modified=False)
	if rec.docstatus == 0 and rec.status == "Pending":
		frappe.db.set_value("Asset Service Record", rec.name, "status", "In Progress", update_modified=False)
	return mr.name


@frappe.whitelist()
def set_status(service_record, status):
	"""Pending ⇄ In Progress on a draft record (Completed = submit, Cancelled = cancel)."""
	if status not in ("Pending", "In Progress"):
		frappe.throw(_("Use Complete (submit) or Cancel for {0}.").format(_(status)))
	rec = frappe.get_doc("Asset Service Record", service_record)
	rec.check_permission("write")
	if rec.docstatus != 0:
		frappe.throw(_("Only draft records can change between Pending and In Progress."))
	# Only the status moves: other fields (e.g. a Service By not chosen yet) are
	# checked when the record itself is saved or completed, not on a status flip.
	rec.db_set("status", status, notify=True)
	return status


def issued_materials_value(record_name):
	"""Value of parts issued from stores against the record (submitted Stock Entry rows)."""
	sed = frappe.qb.DocType("Stock Entry Detail")
	se = frappe.qb.DocType("Stock Entry")
	issued = (
		frappe.qb.from_(sed).join(se).on(se.name == sed.parent)
		.select(frappe.query_builder.functions.Sum(sed.amount))
		.where(sed.asset_service_record == record_name)
		.where(se.docstatus == 1)
		.where(sed.s_warehouse.isnotnull() & (sed.s_warehouse != ""))
		.where(sed.t_warehouse.isnull() | (sed.t_warehouse == ""))
		.run()
	)[0][0]
	return flt(issued)


def recompute_materials_cost(record_name):
	issued = issued_materials_value(record_name)
	rec = frappe.db.get_value("Asset Service Record", record_name, ["docstatus"], as_dict=True)
	if not rec or rec.docstatus == 2:
		return
	invoices = flt(frappe.db.sql(
		"select sum(repair_cost) from `tabAsset Repair Purchase Invoice` where parenttype='Asset Service Record' and parent=%s",
		record_name)[0][0])
	frappe.db.set_value("Asset Service Record", record_name,
		{"materials_cost": flt(issued), "services_cost": invoices, "total_service_cost": invoices + flt(issued)},
		update_modified=False)


def stock_entry_on_change(doc, method=None):
	for name in {d.get("asset_service_record") for d in doc.items if d.get("asset_service_record")}:
		recompute_materials_cost(name)


def purchase_invoice_on_submit(doc, method=None):
	"""Add the invoice's service lines to the draft record they were raised for."""
	_sync_invoice_rows(doc, add=True)


def purchase_invoice_on_cancel(doc, method=None):
	_sync_invoice_rows(doc, add=False)


def _sync_invoice_rows(pi, add):
	by_record = {}
	for d in pi.items:
		rec = d.get("asset_service_record")
		if not rec or d.get("is_fixed_asset") or frappe.db.get_value("Item", d.item_code, "is_stock_item"):
			continue  # stock parts go to a warehouse and are costed when issued
		key = (rec, d.expense_account)
		by_record[key] = by_record.get(key, 0) + flt(d.base_net_amount)
	for (rec_name, account), amount in by_record.items():
		rec = frappe.get_doc("Asset Service Record", rec_name)
		if rec.docstatus != 0:
			continue
		rows = [r for r in rec.invoices if r.purchase_invoice == pi.name and r.expense_account == account]
		if add and not rows:
			rec.append("invoices", {"purchase_invoice": pi.name, "expense_account": account,
				"repair_cost": min(flt(amount), available_amount(pi.name, account, exclude=rec.name))})
		elif not add:
			for r in rows:
				rec.remove(r)
		else:
			continue
		rec.flags.ignore_permissions = True
		rec.save()


def apply_service_type_defaults(rec, service_type):
	st = frappe.db.get_value("Asset Service Type", service_type,
		["default_service_by", "default_supplier", "default_service_item", "default_service_rate"], as_dict=True)
	if not st:
		return
	if not rec.get("service_by") and st.default_service_by:
		rec.service_by = st.default_service_by
	if rec.get("service_by") == "External":
		if not rec.get("supplier") and st.default_supplier:
			rec.supplier = st.default_supplier
		if st.default_service_item and not any(r.item_code == st.default_service_item for r in rec.get("materials") or []):
			rec.append("materials", {"item_code": st.default_service_item, "qty": 1, "estimated_rate": st.default_service_rate,
				"uom": frappe.db.get_value("Item", st.default_service_item, "stock_uom")})


@frappe.whitelist()
def get_service_type_defaults(service_type):
	frappe.has_permission("Asset Service Type", "read", service_type, throw=True)
	return frappe.db.get_value("Asset Service Type", service_type,
		["default_service_by", "default_supplier", "default_service_item", "default_service_rate"], as_dict=True)


@frappe.whitelist()
def make_material_issue(service_record, rows=None):
	"""Record parts the workshop already holds: a Material Issue Stock Entry straight
	from the record's rows, without a Material Request (Asset Settings switch)."""
	from upande_asset.upande_asset.insurance.utils import get_settings

	settings = get_settings()
	if not settings.allow_direct_material_issue:
		frappe.throw(_("Recording used materials directly is switched off in Asset Settings → Maintenance."))
	rec = frappe.get_doc("Asset Service Record", service_record)
	rec.check_permission("write" if rec.docstatus == 0 else "read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	if rec.docstatus == 2:
		frappe.throw(_("The service record is cancelled."))
	names = set(frappe.parse_json(rows) or []) if rows else None
	lines = [r for r in rec.materials if not r.material_request and not r.stock_entry
		and (names is None or r.name in names) and frappe.get_cached_value("Item", r.item_code, "is_stock_item")]
	if not lines:
		frappe.throw(_("No stock items left to issue. Service items are bought, not issued; rows already requested or issued are skipped."))
	cost_center = frappe.db.get_value("Asset", rec.asset, "cost_center") or frappe.get_cached_value("Company", rec.company, "cost_center")
	se = frappe.new_doc("Stock Entry")
	se.update({"stock_entry_type": "Material Issue", "purpose": "Material Issue", "company": rec.company,
		"remarks": _("Materials used on Asset Service Record {0} ({1})").format(rec.name, rec.asset)})
	for r in lines:
		warehouse = r.warehouse or default_parts_warehouse(rec.company)
		if not warehouse:
			frappe.throw(_("Row {0}: set the Warehouse the {1} came from.").format(r.idx, r.item_code))
		stock_uom = frappe.get_cached_value("Item", r.item_code, "stock_uom")
		uom = r.uom or stock_uom
		factor = 1
		if uom != stock_uom:
			from erpnext.stock.get_item_details import get_conversion_factor

			factor = flt((get_conversion_factor(r.item_code, uom) or {}).get("conversion_factor")) or 1
		se.append("items", {"item_code": r.item_code, "qty": r.qty, "uom": uom, "stock_uom": stock_uom,
			"conversion_factor": factor, "s_warehouse": warehouse, "cost_center": cost_center, "asset_service_record": rec.name})
	se.insert()
	if (settings.direct_issue_status or "Draft") == "Submitted":
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Stock Entry"):
			se.submit()
	for r in lines:
		frappe.db.set_value("Asset Service Material", r.name, "stock_entry", se.name, update_modified=False)
	if rec.docstatus == 0 and rec.status == "Pending":
		frappe.db.set_value("Asset Service Record", rec.name, "status", "In Progress", update_modified=False)
	return se.name


def _free_rows(field, name):
	"""A cancelled / deleted Stock Entry or Material Request frees its rows for a new request."""
	frappe.db.sql(f"update `tabAsset Service Material` set `{field}` = NULL where `{field}` = %s", name)


def stock_entry_released(doc, method=None):
	_free_rows("stock_entry", doc.name)
	stock_entry_on_change(doc)


def material_request_released(doc, method=None):
	_free_rows("material_request", doc.name)
