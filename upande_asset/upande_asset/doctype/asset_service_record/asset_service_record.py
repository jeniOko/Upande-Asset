import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt, get_link_to_form, getdate, nowdate, nowtime

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
		from upande_asset.upande_asset.repair_materials import enabled

		self.set_onload("materials_enabled", enabled())
		from upande_asset.upande_asset.materials import client_options

		self.set_onload("materials_options", client_options())
		self.set_onload("allow_direct_material_issue", int(s.allow_direct_material_issue or 0))
		self.set_onload("service_materials_warehouse", default_parts_warehouse(self.company))
		from upande_asset.upande_asset.materials import refresh_rows

		refresh_rows(self)  # live stock and status on the Materials rows

	def validate(self):
		from upande_asset.upande_asset.repair_materials import validate_locked_rows

		from upande_asset.upande_asset.dimensions import default_from_asset

		validate_locked_rows(self, "materials")
		default_from_asset(self)
		self._apply_service_type_defaults()
		from upande_asset.upande_asset.materials import refresh_rows

		refresh_rows(self)
		self._validate_maintenance_log()
		self._validate_purchase_invoices()
		self._validate_stock_items()
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
		self._issue_stock_items()
		if self.tasks:
			from upande_asset.upande_asset.maintenance import complete_maintenance_logs

			complete_maintenance_logs(self)
		from frappe.desk.form.assign_to import close_all_assignments

		close_all_assignments(self.doctype, self.name, ignore_permissions=True)

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self._cancel_stock_issue()
		done = [r.asset_maintenance_log for r in self.tasks
			if frappe.db.get_value("Asset Maintenance Log", r.asset_maintenance_log, "docstatus") == 1]
		if done:
			frappe.msgprint(
				_("Maintenance Logs {0} stay completed and their tasks keep their next due dates. Amend this record to correct its details.").format(
					", ".join(get_link_to_form("Asset Maintenance Log", n) for n in done)),
				indicator="orange",
			)

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
		# Issued stock (Stock Entries) + invoice rows + Consumed Stock Items (as on Asset Repair).
		if not self.is_new():
			self.materials_cost = issued_materials_value(self.name)
		self.services_cost = sum(flt(row.repair_cost) for row in self.invoices)
		self.total_service_cost = flt(self.services_cost) + flt(self.materials_cost) + flt(self.consumed_items_cost)

	# ── Consumed Stock Items (ERPNext Asset Repair's way, used when Material Requests are off) ──

	def _validate_stock_items(self):
		total = 0
		for row in self.get("stock_items") or []:
			item = frappe.get_cached_value("Item", row.item_code, ["is_stock_item", "has_serial_no", "has_batch_no"], as_dict=True)
			if not item or not item.is_stock_item:
				frappe.throw(_("Row {0}: {1} is not a stock item. Add services through Service Invoices.").format(row.idx, row.item_code))
			if (item.has_serial_no or item.has_batch_no) and not row.serial_and_batch_bundle:
				frappe.throw(_("Row {0}: {1} is tracked by serial / batch number. Issue it with a Stock Entry instead.").format(
					row.idx, row.item_code), title=_("Serial / Batch Item"))
			if flt(row.consumed_quantity) <= 0:
				frappe.throw(_("Row {0}: enter the Consumed Quantity of {1}.").format(row.idx, row.item_code))
			if not flt(row.valuation_rate) and row.warehouse:
				from erpnext.stock.utils import get_incoming_rate

				row.valuation_rate = flt(get_incoming_rate({"item_code": row.item_code, "warehouse": row.warehouse,
					"qty": flt(row.consumed_quantity), "company": self.company, "posting_date": nowdate(),
					"posting_time": nowtime()}, raise_error_if_no_rate=False))
			row.total_value = flt(row.valuation_rate) * flt(row.consumed_quantity)
			total += row.total_value
		self.consumed_items_cost = total

	def _issue_stock_items(self):
		if not self.get("stock_items"):
			return
		from upande_asset.upande_asset.dimensions import get_dimensions

		se = frappe.get_doc({
			"doctype": "Stock Entry", "stock_entry_type": "Material Issue", "purpose": "Material Issue", "company": self.company,
			"remarks": _("Stock consumed on Asset Service Record {0} ({1})").format(self.name, self.asset),
			**get_dimensions(self, "Stock Entry"),
		})
		dims = get_dimensions(self, "Stock Entry Detail")
		for row in self.stock_items:
			se.append("items", {"s_warehouse": row.warehouse, "item_code": row.item_code, "qty": row.consumed_quantity,
				"basic_rate": row.valuation_rate, "serial_and_batch_bundle": row.serial_and_batch_bundle, **dims})
		se.insert()
		se.submit()
		self.db_set("consumption_stock_entry", se.name)
		# The entry's actual valuation is the cost.
		actual = flt(sum(flt(d.amount) for d in se.items))
		if actual != flt(self.consumed_items_cost):
			self.db_set({"consumed_items_cost": actual,
				"total_service_cost": flt(self.services_cost) + flt(self.materials_cost) + actual})

	def _cancel_stock_issue(self):
		name = self.get("consumption_stock_entry")
		if name and frappe.db.get_value("Stock Entry", name, "docstatus") == 1:
			frappe.get_doc("Stock Entry", name).cancel()


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


@frappe.whitelist()
def make_material_request(service_record, purpose, rows=None):
	"""Unsaved Material Request for the Materials rows (see materials.py)."""
	from upande_asset.upande_asset.materials import make_material_request as _make

	return _make("Asset Service Record", service_record, purpose, rows)


@frappe.whitelist()
def make_material_issue(service_record, rows=None):
	"""Unsaved Material Issue for parts the workshop already holds (see materials.py)."""
	from upande_asset.upande_asset.materials import make_material_issue as _make

	return _make("Asset Service Record", service_record, rows)


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
	consumed = flt(frappe.db.get_value("Asset Service Record", record_name, "consumed_items_cost"))
	frappe.db.set_value("Asset Service Record", record_name,
		{"materials_cost": flt(issued), "services_cost": invoices, "total_service_cost": invoices + flt(issued) + consumed},
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
		from upande_asset.upande_asset.repair_materials import enabled

		if enabled() and st.default_service_item and not any(r.item_code == st.default_service_item for r in rec.get("materials") or []):
			rec.append("materials", {"item_code": st.default_service_item, "qty": 1,
				"uom": frappe.db.get_value("Item", st.default_service_item, "stock_uom")})


@frappe.whitelist()
def get_service_type_defaults(service_type):
	frappe.has_permission("Asset Service Type", "read", service_type, throw=True)
	return frappe.db.get_value("Asset Service Type", service_type,
		["default_service_by", "default_supplier", "default_service_item", "default_service_rate"], as_dict=True)


def stock_entry_released(doc, method=None):
	from upande_asset.upande_asset.materials import stock_entry_released as _release

	_release(doc)
	stock_entry_on_change(doc)
