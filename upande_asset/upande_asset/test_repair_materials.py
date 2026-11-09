# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset Repair materials through Material Requests (Asset Settings → Request Repair Materials)."""

import unittest

import frappe
from frappe.utils import add_to_date, flt, now_datetime, today

from upande_asset.upande_asset.insurance.test_fixtures import COMPANY, INSURER, bootstrap, make_asset, set_parts_warehouse
from upande_asset.upande_asset.repair_materials import make_material_issue, make_material_request

PART = "_Test Repair Hose"
SERVICE = "_Test Repair Service"


def _settings(**values):
	frappe.db.set_single_value("Asset Settings", values)
	frappe.clear_document_cache("Asset Settings", "Asset Settings")


class TestRepairMaterials(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		bootstrap()

	def setUp(self):
		self.store = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0, "warehouse_name": "Stores"}, "name")
		self.expense = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Insurance Claim Shortfall"}, "name")
		for item, stock in ((PART, 1), (SERVICE, 0)):
			if not frappe.db.exists("Item", item):
				frappe.get_doc({"doctype": "Item", "item_code": item, "item_group": "All Item Groups" if stock else "Services",
					"stock_uom": "Nos", "is_stock_item": stock}).insert()
		frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
			"items": [{"item_code": PART, "qty": 10, "t_warehouse": self.store, "basic_rate": 1500}]}).insert().submit()
		set_parts_warehouse(self.store)
		_settings(repair_material_requests=1, maintenance_mr_status="Draft",
			allow_direct_material_issue=1, direct_issue_status="Submitted")

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Asset Settings", "Asset Settings")

	def _repair(self, materials):
		return frappe.get_doc({"doctype": "Asset Repair", "company": COMPANY, "asset": make_asset().name,
			"failure_date": add_to_date(now_datetime(), hours=-5), "repair_status": "Pending",
			"repair_materials": materials}).insert()

	def _complete(self, repair):
		repair.reload()
		repair.repair_status, repair.completion_date = "Completed", now_datetime()
		repair.save()
		return repair

	def test_off_keeps_erpnext_behaviour(self):
		_settings(repair_material_requests=0)
		repair = self._repair([])
		with self.assertRaises(frappe.ValidationError):
			make_material_request(repair.name, "Material Issue")
		self.assertEqual(flt(repair.requested_materials_cost), 0)

	def test_request_from_stores_then_issue(self):
		from erpnext.stock.doctype.material_request.material_request import make_stock_entry

		repair = self._repair([{"item_code": PART, "qty": 2}])
		self.assertEqual(repair.repair_materials[0].warehouse, self.store)  # Asset Settings default
		self.assertEqual(flt(repair.repair_materials[0].actual_qty), 10)

		mr = frappe.get_doc("Material Request", make_material_request(repair.name, "Material Issue"))
		self.assertEqual((mr.material_request_type, mr.docstatus, mr.items[0].asset_repair), ("Material Issue", 0, repair.name))
		self.assertEqual(frappe.db.get_value("Asset Service Material", repair.repair_materials[0].name, "material_request"), mr.name)
		with self.assertRaises(frappe.ValidationError):
			make_material_request(repair.name, "Material Issue")  # row already requested

		mr.submit()  # approval
		repair = self._complete(repair)
		with self.assertRaisesRegex(frappe.ValidationError, "not issued everything"):
			repair.submit()  # stores have not issued yet

		se = make_stock_entry(mr.name)
		self.assertEqual(se.items[0].asset_repair, repair.name)  # mapped by ERPNext
		se.insert()
		se.submit()
		repair.reload()
		self.assertEqual(flt(repair.requested_materials_cost), 3000)  # 2 × 1,500 actual valuation
		self.assertEqual(flt(repair.total_repair_cost), 3000)
		repair.submit()
		# ERPNext's own issue on submit did not run again: the only issue is ours (linked in its header too).
		self.assertEqual(frappe.get_all("Stock Entry", {"asset_repair": repair.name, "docstatus": 1}, pluck="name"), [se.name])
		self.assertEqual(flt(frappe.db.get_value("Bin", {"item_code": PART, "warehouse": self.store}, "actual_qty")), 8)

	def test_purchase_service_feeds_repair_invoices(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order

		repair = self._repair([{"item_code": SERVICE, "qty": 1, "estimated_rate": 12_000}])
		with self.assertRaises(frappe.ValidationError):
			make_material_request(repair.name, "Material Issue")  # services are bought, not issued
		mr = frappe.get_doc("Material Request", make_material_request(repair.name, "Purchase"))
		mr.submit()
		po = make_purchase_order(mr.name)
		po.supplier = INSURER
		po.items[0].rate = 12_500
		po.insert()
		po.submit()
		self.assertEqual(po.items[0].asset_repair, repair.name)
		pi = make_purchase_invoice(po.name)
		pi.bill_no = frappe.generate_hash(length=6)
		for d in pi.items:
			d.expense_account = self.expense
		pi.insert()
		pi.submit()
		repair.reload()
		self.assertEqual([r.purchase_invoice for r in repair.invoices], [pi.name])
		self.assertEqual(flt(repair.repair_cost), 12_500)
		self.assertEqual(flt(repair.total_repair_cost), 12_500)
		self._complete(repair).submit()

		pi.reload()
		with self.assertRaises(frappe.ValidationError):
			pi.cancel()  # ERPNext protects an invoice used by a submitted repair

	def test_direct_issue_and_cancel(self):
		repair = self._repair([{"item_code": PART, "qty": 3}, {"item_code": SERVICE, "qty": 1}])
		se = frappe.get_doc("Stock Entry", make_material_issue(repair.name))
		self.assertEqual(se.docstatus, 1)
		self.assertEqual([d.item_code for d in se.items], [PART])  # the service row is skipped
		repair.reload()
		self.assertEqual(repair.repair_materials[0].stock_entry, se.name)
		self.assertEqual(flt(repair.requested_materials_cost), 4500)
		se.cancel()
		repair.reload()
		self.assertFalse(repair.repair_materials[0].stock_entry)
		self.assertEqual(flt(repair.requested_materials_cost), 0)

	def test_direct_issue_needs_its_setting(self):
		_settings(allow_direct_material_issue=0)
		repair = self._repair([{"item_code": PART, "qty": 1}])
		with self.assertRaises(frappe.ValidationError):
			make_material_issue(repair.name)

	def test_capitalising_requested_materials_blocked(self):
		repair = self._repair([{"item_code": PART, "qty": 1}])
		make_material_issue(repair.name)
		repair = self._complete(repair)
		repair.capitalize_repair_cost = 1
		with self.assertRaisesRegex(frappe.ValidationError, "only capitalises Stock Items"):
			repair.submit()

	def test_cancelled_request_frees_rows(self):
		repair = self._repair([{"item_code": PART, "qty": 1}])
		mr = frappe.get_doc("Material Request", make_material_request(repair.name, "Material Issue"))
		mr.submit()
		mr.cancel()
		self.assertFalse(frappe.db.get_value("Asset Service Material", repair.repair_materials[0].name, "material_request"))
		self.assertTrue(make_material_request(repair.name, "Material Issue"))

	def test_requested_rows_are_locked(self):
		repair = self._repair([{"item_code": PART, "qty": 2}])
		mr = frappe.get_doc("Material Request", make_material_request(repair.name, "Material Issue"))
		repair.reload()
		for field, value in (("qty", 5), ("warehouse", None), ("estimated_rate", 99), ("item_code", SERVICE)):
			repair.reload()
			repair.repair_materials[0].set(field, value)
			with self.assertRaisesRegex(frappe.ValidationError, "cannot change", msg=field):
				repair.save()
		repair.reload()
		repair.remove(repair.repair_materials[0])
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be removed"):
			repair.save()

		# A stale form that never saw the request cannot wipe the link either.
		repair.reload()
		repair.repair_materials[0].material_request = None
		with self.assertRaisesRegex(frappe.ValidationError, "Reload the form"):
			repair.save()

		# Extra material goes on a new row; cancelling the request frees the old one.
		repair.reload()
		repair.append("repair_materials", {"item_code": PART, "qty": 1})
		repair.save()
		frappe.get_doc("Material Request", mr.name).delete()
		repair.reload()
		repair.repair_materials[0].qty = 3
		repair.save()
		self.assertEqual(flt(repair.repair_materials[0].qty), 3)

	def test_connections_show_requests_issues_and_purchases(self):
		from frappe.desk.notifications import get_open_count

		repair = self._repair([{"item_code": PART, "qty": 1}, {"item_code": SERVICE, "qty": 1, "estimated_rate": 5000}])
		issue_mr = make_material_request(repair.name, "Material Issue")
		buy_mr = make_material_request(repair.name, "Purchase")
		repair.reload()
		repair.append("repair_materials", {"item_code": PART, "qty": 2})
		repair.save()
		se = make_material_issue(repair.name)
		self.assertEqual(frappe.db.get_value("Stock Entry", se, "asset_repair"), repair.name)  # header link
		counts = {d["doctype"]: d for d in get_open_count("Asset Repair", repair.name)["count"]["external_links_found"]}
		self.assertEqual(counts["Material Request"]["count"], 2, (issue_mr, buy_mr))
		self.assertEqual(counts["Stock Entry"]["count"], 1)

	def test_capitalising_blocked_even_after_issue_cancelled(self):
		repair = self._repair([{"item_code": PART, "qty": 1}])
		frappe.get_doc("Stock Entry", make_material_issue(repair.name)).cancel()
		repair = self._complete(repair)
		repair.capitalize_repair_cost = 1
		with self.assertRaisesRegex(frappe.ValidationError, "only capitalises Stock Items"):
			repair.submit()
