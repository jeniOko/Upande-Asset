# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset Service Record shares ERPNext's Asset Repair invoice allocation."""

import unittest

import frappe
from frappe.utils import flt, today

from upande_asset.upande_asset.insurance.test_fixtures import COMPANY, INSURER, bootstrap, make_asset, set_parts_warehouse
from upande_asset.upande_asset.maintenance import records_for_log


def make_service_invoice(amount):
	account = frappe.db.get_value("Account", {"company": COMPANY, "root_type": "Expense", "is_group": 0,
		"account_name": "Insurance Claim Shortfall"}, "name")
	item = "_Test Service Labour"
	if not frappe.db.exists("Item", item):
		frappe.get_doc({"doctype": "Item", "item_code": item, "item_group": "Services", "stock_uom": "Nos",
			"is_stock_item": 0}).insert()
	pi = frappe.get_doc({
		"doctype": "Purchase Invoice", "supplier": INSURER, "company": COMPANY, "posting_date": today(), "bill_no": frappe.generate_hash(length=6),
		"items": [{"item_code": item, "qty": 1, "rate": amount, "expense_account": account}],
	}).insert()
	pi.submit()
	return pi, account


class TestAssetServiceRecord(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		bootstrap()

	def tearDown(self):
		frappe.db.rollback()

	def _record(self, asset, pi, account, cost):
		if not frappe.db.exists("Asset Service Type", "_Test Servicing"):
			frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Servicing"}).insert()
		return frappe.get_doc({
			"doctype": "Asset Service Record", "title": "Service", "asset": asset.name, "company": COMPANY,
			"service_type": "_Test Servicing", "service_date": today(), "service_by": "External", "supplier": INSURER,
			"invoices": [{"purchase_invoice": pi.name, "expense_account": account, "repair_cost": cost}],
		}).insert()

	def test_allocation_is_shared_with_asset_repair(self):
		asset = make_asset()
		pi, account = make_service_invoice(10_000)
		rec = self._record(asset, pi, account, 6_000)
		self.assertEqual(rec.invoices[0].doctype, "Asset Repair Purchase Invoice")
		self.assertEqual(flt(rec.total_service_cost), 6_000)
		rec.submit()

		# Only 4,000 is left on that invoice line – for another record or an Asset Repair.
		with self.assertRaises(frappe.ValidationError):
			self._record(asset, pi, account, 5_000)
		repair = frappe.get_doc({
			"doctype": "Asset Repair", "company": COMPANY, "asset": asset.name, "failure_date": today(),
			"invoices": [{"purchase_invoice": pi.name, "expense_account": account, "repair_cost": 5_000}],
		})
		with self.assertRaises(frappe.ValidationError):
			repair.insert()

		ok = self._record(asset, pi, account, 4_000)
		self.assertEqual(flt(ok.total_service_cost), 4_000)

	def test_rejects_stock_or_wrong_expense_account(self):
		asset = make_asset()
		pi, _account = make_service_invoice(1_000)
		other = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Insurance Expense"}, "name")
		with self.assertRaises(frappe.ValidationError):
			self._record(asset, pi, other, 500)


class TestServiceRecordMaintenance(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		bootstrap()

	def tearDown(self):
		frappe.db.rollback()

	def _maintenance(self, asset, certificate=0, service_type=None):
		from frappe.utils import add_days

		tech = "maint-tech@example.com"
		if not frappe.db.exists("User", tech):
			frappe.get_doc({"doctype": "User", "email": tech, "first_name": "Maint", "last_name": "Tech", "send_welcome_email": 0,
				"roles": [{"role": "Maintenance User"}]}).insert(ignore_permissions=True)
		team = "_Test Insurance Maint Team"
		if not frappe.db.exists("Asset Maintenance Team", team):
			frappe.get_doc({"doctype": "Asset Maintenance Team", "maintenance_team_name": team, "company": COMPANY,
				"maintenance_manager": "Administrator",
				"maintenance_team_members": [{"team_member": tech, "maintenance_role": "Maintenance User"}]}).insert()
		return frappe.get_doc({
			"doctype": "Asset Maintenance", "asset_name": asset.name, "company": COMPANY, "maintenance_team": team,
			"asset_maintenance_tasks": [{
				"maintenance_task": "250-hour service", "maintenance_type": "Preventive Maintenance", "maintenance_status": "Planned",
				"service_type": service_type or self._service_type(),
				"start_date": add_days(today(), -30), "periodicity": "Monthly", "assign_to": tech,
				"certificate_required": certificate, "description": "Change oil and filters",
			}],
		}).insert()

	def _service_type(self):
		if not frappe.db.exists("Asset Service Type", "_Test Engine Service"):
			frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Engine Service"}).insert()
		return "_Test Engine Service"

	def test_service_type_is_mandatory_on_task(self):
		asset = make_asset()
		with self.assertRaises(frappe.MandatoryError):
			am = self._maintenance(asset)
			am.asset_maintenance_tasks[0].service_type = None
			am.save()

	def _open_log(self, am):
		return frappe.db.get_value("Asset Maintenance Log", {"asset_maintenance": am.name, "docstatus": 0,
			"maintenance_status": ["in", ["Planned", "Overdue"]]}, "name")

	def test_pending_record_completes_task_and_rolls_forward(self):
		asset = make_asset()
		am = self._maintenance(asset)
		log = self._open_log(am)
		self.assertTrue(log)
		rec_name = (records_for_log(log) or [None])[0]
		self.assertTrue(rec_name, "a Pending service record is created for the new log")
		rec = frappe.get_doc("Asset Service Record", rec_name)
		self.assertEqual(rec.status, "Pending")
		self.assertEqual(rec.asset, asset.name)
		self.assertEqual(rec.tasks[0].maintenance_task, "250-hour service")
		self.assertEqual(rec.service_type, "_Test Engine Service")  # taken from the task
		self.assertEqual(rec.assigned_to, "maint-tech@example.com")
		self.assertTrue(frappe.db.exists("ToDo", {"reference_type": "Asset Service Record", "reference_name": rec.name, "status": "Open"}))

		# Completing the log directly is blocked while the record is open.
		lg = frappe.get_doc("Asset Maintenance Log", log)
		lg.maintenance_status, lg.completion_date = "Completed", today()
		with self.assertRaises(frappe.ValidationError):
			lg.save()

		rec.reload()
		rec.service_by = "Internal"
		rec.performed_by = "Workshop A"
		rec.findings = "Oil dark, filters replaced"
		rec.service_date = today()
		rec.save()
		rec.submit()
		self.assertEqual(rec.status, "Completed")
		self.assertEqual(rec.completed_by, "Administrator")
		self.assertEqual(frappe.db.get_value("Asset Maintenance Log", log, ["docstatus", "maintenance_status"]), (1, "Completed"))
		task = frappe.get_doc("Asset Maintenance Task", lg.task)
		self.assertEqual(str(task.last_completion_date), today())
		self.assertGreater(str(task.next_due_date), today())

		# ERPNext opened the next occurrence → it has its own Pending record.
		nxt = self._open_log(am)
		self.assertTrue(nxt and nxt != log)
		self.assertEqual(frappe.db.get_value("Asset Service Record", records_for_log(nxt)[0], "status"), "Pending")

	def test_certificate_required(self):
		asset = make_asset()
		am = self._maintenance(asset, certificate=1)
		rec = frappe.get_doc("Asset Service Record", records_for_log(self._open_log(am))[0])
		rec.service_by = "Internal"
		self.assertEqual(rec.tasks[0].certificate_required, 1)
		with self.assertRaises(frappe.ValidationError):
			rec.submit()


	def test_one_service_completes_several_tasks(self):
		from frappe.utils import add_days

		asset = make_asset()
		am = self._maintenance(asset)
		am.append("asset_maintenance_tasks", {
			"maintenance_task": "Grease all points", "maintenance_type": "Preventive Maintenance", "service_type": self._service_type(),
			"maintenance_status": "Planned", "start_date": add_days(today(), -30), "periodicity": "Monthly",
			"assign_to": "maint-tech@example.com",
		})
		am.save()
		logs = frappe.get_all("Asset Maintenance Log", filters={"asset_maintenance": am.name, "docstatus": 0}, pluck="name")
		self.assertEqual(len(logs), 2)
		first, second = (records_for_log(l)[0] for l in logs)
		# Move the second task onto the first record, drop the now-empty second record.
		frappe.delete_doc("Asset Service Record", second, force=True)
		rec = frappe.get_doc("Asset Service Record", first)
		rec.append("tasks", {"asset_maintenance_log": [l for l in logs if records_for_log(l) != [first]][0]})
		rec.service_by = "Internal"
		rec.save()
		self.assertEqual(len(rec.tasks), 2)
		rec.submit()
		for l in logs:
			self.assertEqual(frappe.db.get_value("Asset Maintenance Log", l, "maintenance_status"), "Completed")
		self.assertTrue(all(t.log_status == "Completed" for t in frappe.get_doc("Asset Service Record", first).tasks))

	def test_totals_split_materials_and_services(self):
		rec = frappe.get_doc("Asset Service Record", records_for_log(self._open_log(self._maintenance(make_asset())))[0])
		rec.service_by = "Internal"
		rec.materials_cost = 1000  # typed in by hand: ignored, only actual stock issues count
		rec.save()
		self.assertEqual((flt(rec.materials_cost), flt(rec.services_cost), flt(rec.total_service_cost)), (0, 0, 0))

	def test_external_service_type_defaults(self):
		svc = "_Test Dealer Service"
		if not frappe.db.exists("Item", svc):
			frappe.get_doc({"doctype": "Item", "item_code": svc, "item_group": "Services", "stock_uom": "Nos", "is_stock_item": 0}).insert()
		st = frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Dealer 500h Service",
			"default_service_by": "External", "default_supplier": INSURER, "default_service_item": svc,
			"default_service_rate": 42_000}).insert()

		# Pending record from a maintenance task of this service type
		asset = make_asset()
		am = self._maintenance(asset, service_type=st.name)
		rec = frappe.get_doc("Asset Service Record", records_for_log(self._open_log(am))[0])
		self.assertEqual((rec.service_type, rec.service_by, rec.supplier), (st.name, "External", INSURER))
		self.assertEqual([(m.item_code, flt(m.estimated_rate)) for m in rec.materials], [(svc, 42_000)])

		# Manual record: defaults fill only what is empty
		manual = frappe.get_doc({"doctype": "Asset Service Record", "title": "Manual", "asset": asset.name, "company": COMPANY,
			"service_type": st.name, "service_date": today(), "service_by": "Internal"}).insert()
		self.assertEqual((manual.service_by, manual.supplier, len(manual.materials)), ("Internal", None, 0))

	def test_service_type_defaults_are_only_suggestions(self):
		st = frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Varies Service",
			"default_supplier": INSURER}).insert()  # no default Service By: it varies
		rec = frappe.get_doc({"doctype": "Asset Service Record", "title": "Varies", "asset": make_asset().name,
			"company": COMPANY, "service_type": st.name, "service_date": today(), "service_by": "Internal"}).insert()
		self.assertIsNone(rec.supplier)
		rec.service_by = "External"
		rec.save()
		self.assertEqual(rec.supplier, INSURER)  # usual supplier offered on switching to External
		other = frappe.db.get_value("Supplier", {"name": ["!=", INSURER]}, "name")
		rec.supplier = other
		rec.save()
		self.assertEqual(rec.supplier, other)  # changed for this job and kept


class TestServiceRecordMaterials(unittest.TestCase):
	"""Internal work: parts issued from stores. External work: the service purchased via MR → PO → PI."""

	@classmethod
	def setUpClass(cls):
		bootstrap()

	def tearDown(self):
		frappe.db.rollback()

	def _record(self, service_by, **kw):
		if not frappe.db.exists("Asset Service Type", "_Test Servicing"):
			frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Servicing"}).insert()
		return frappe.get_doc({
			"doctype": "Asset Service Record", "title": f"{service_by} job", "asset": make_asset().name, "company": COMPANY,
			"service_type": "_Test Servicing", "service_date": today(), "service_by": service_by, **kw,
		}).insert()

	def test_internal_parts_issued_from_stores(self):
		from erpnext.stock.doctype.material_request.material_request import make_stock_entry

		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import make_material_request

		store = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0, "warehouse_name": "Stores"}, "name")
		part = "_Test Hydraulic Hose"
		if not frappe.db.exists("Item", part):
			frappe.get_doc({"doctype": "Item", "item_code": part, "item_group": "All Item Groups", "stock_uom": "Nos",
				"is_stock_item": 1, "valuation_rate": 2000}).insert()
		receipt = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
			"items": [{"item_code": part, "qty": 5, "t_warehouse": store, "basic_rate": 2000}]}).insert()
		receipt.submit()

		rec = self._record("Internal", materials=[{"item_code": part, "qty": 2, "warehouse": store}])
		with self.assertRaises(frappe.ValidationError):
			make_material_request(rec.name, "Bogus")
		mr_name = make_material_request(rec.name, "Material Issue")
		mr = frappe.get_doc("Material Request", mr_name)
		self.assertEqual(mr.material_request_type, "Material Issue")
		self.assertEqual(mr.items[0].asset_service_record, rec.name)
		self.assertEqual(frappe.db.get_value("Asset Service Record", rec.name, "status"), "In Progress")
		self.assertEqual(frappe.db.get_value("Asset Service Material", rec.materials[0].name, "material_request"), mr_name)

		mr.submit()
		se = make_stock_entry(mr.name)
		self.assertEqual(se.items[0].asset_service_record, rec.name)  # mapped by ERPNext
		se.insert()
		se.submit()
		rec.reload()
		self.assertEqual(flt(rec.materials_cost), 4000)
		self.assertEqual(flt(rec.total_service_cost), 4000)

		se.cancel()
		self.assertEqual(flt(frappe.db.get_value("Asset Service Record", rec.name, "materials_cost")), 0)

	def test_external_service_purchased_and_invoice_added(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order

		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import make_material_request

		svc = "_Test Workshop Service"
		if not frappe.db.exists("Item", svc):
			frappe.get_doc({"doctype": "Item", "item_code": svc, "item_group": "Services", "stock_uom": "Nos", "is_stock_item": 0}).insert()
		rec = self._record("External", supplier=INSURER, materials=[{"item_code": svc, "qty": 1, "estimated_rate": 15000}])
		with self.assertRaises(frappe.ValidationError):
			make_material_request(rec.name, "Material Issue")  # not for external work
		mr = frappe.get_doc("Material Request", make_material_request(rec.name, "Purchase"))
		self.assertEqual(mr.material_request_type, "Purchase")
		mr.submit()
		po = make_purchase_order(mr.name)
		po.supplier = INSURER
		po.items[0].rate = 15000
		po.insert()
		po.submit()
		self.assertEqual(po.items[0].asset_service_record, rec.name)
		pi = make_purchase_invoice(po.name)
		pi.bill_no = frappe.generate_hash(length=6)
		pi.insert()
		pi.submit()

		rec.reload()
		self.assertEqual([r.purchase_invoice for r in rec.invoices], [pi.name])
		self.assertEqual(flt(rec.total_service_cost), 15000)
		rec.submit()
		self.assertEqual(rec.status, "Completed")

	def test_status_changes(self):
		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import set_status

		rec = self._record("Internal")
		self.assertEqual(rec.status, "Pending")
		self.assertEqual(set_status(rec.name, "In Progress"), "In Progress")
		self.assertEqual(set_status(rec.name, "Pending"), "Pending")
		with self.assertRaises(frappe.ValidationError):
			set_status(rec.name, "Completed")
		rec.reload()
		rec.submit()
		self.assertEqual(rec.status, "Completed")
		rec.cancel()
		self.assertEqual(frappe.db.get_value("Asset Service Record", rec.name, "status"), "Cancelled")

	def test_external_requires_supplier_to_complete(self):
		rec = self._record("External")
		with self.assertRaises(frappe.ValidationError):
			rec.submit()


class TestServiceRecordDirectIssue(unittest.TestCase):
	"""Actual Qty on rows, and recording parts the workshop already holds."""

	@classmethod
	def setUpClass(cls):
		bootstrap()

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Asset Settings", "Asset Settings")

	def _setup(self, allow=1):
		store = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0, "warehouse_name": "Stores"}, "name")
		part = "_Test Grease Cartridge"
		if not frappe.db.exists("Item", part):
			frappe.get_doc({"doctype": "Item", "item_code": part, "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 1}).insert()
		frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
			"items": [{"item_code": part, "qty": 12, "t_warehouse": store, "basic_rate": 450}]}).insert().submit()
		frappe.db.set_single_value("Asset Settings", {"allow_direct_material_issue": allow, "direct_issue_status": "Submitted"})
		set_parts_warehouse(store)
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		if not frappe.db.exists("Asset Service Type", "_Test Servicing"):
			frappe.get_doc({"doctype": "Asset Service Type", "service_type_name": "_Test Servicing"}).insert()
		rec = frappe.get_doc({"doctype": "Asset Service Record", "title": "Greasing", "asset": make_asset().name, "company": COMPANY,
			"service_type": "_Test Servicing", "service_date": today(), "service_by": "Internal",
			"materials": [{"item_code": part, "qty": 4}]}).insert()
		return rec, store

	def test_actual_qty_and_default_warehouse(self):
		rec, store = self._setup()
		self.assertEqual(rec.materials[0].warehouse, store)  # from the company's row in Asset Settings → Defaults
		self.assertEqual(flt(rec.materials[0].actual_qty), 12)

	def test_direct_issue_feeds_materials_cost(self):
		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import make_material_issue, make_material_request

		rec, store = self._setup()
		se = frappe.get_doc("Stock Entry", make_material_issue(rec.name))
		self.assertEqual((se.purpose, se.docstatus, se.items[0].s_warehouse, se.items[0].asset_service_record),
			("Material Issue", 1, store, rec.name))
		rec.reload()
		self.assertEqual(rec.materials[0].stock_entry, se.name)
		self.assertEqual(flt(rec.materials_cost), 1800)  # 4 × 450 actual valuation
		self.assertEqual(rec.status, "In Progress")
		with self.assertRaises(frappe.ValidationError):
			make_material_request(rec.name, "Purchase")  # row already issued

		se.cancel()
		rec.reload()
		self.assertFalse(rec.materials[0].stock_entry)  # freed for a new request
		self.assertEqual(flt(rec.materials_cost), 0)

	def test_issued_rows_are_locked(self):
		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import make_material_issue

		rec, _store = self._setup()
		make_material_issue(rec.name)
		rec.reload()
		rec.materials[0].qty = 10
		with self.assertRaisesRegex(frappe.ValidationError, "cannot change"):
			rec.save()
		rec.reload()
		rec.remove(rec.materials[0])
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be removed"):
			rec.save()

	def test_direct_issue_needs_the_setting(self):
		from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import make_material_issue

		rec, _store = self._setup(allow=0)
		with self.assertRaises(frappe.ValidationError):
			make_material_issue(rec.name)
