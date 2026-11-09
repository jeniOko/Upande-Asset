# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset Repair materials through Material Requests (Asset Settings → Request Repair Materials)."""

import unittest
import unittest.mock

import frappe
from frappe.utils import add_to_date, flt, now_datetime, today

from upande_asset.upande_asset.insurance.test_fixtures import COMPANY, INSURER, bootstrap, make_asset, set_parts_warehouse
from upande_asset.upande_asset import repair_materials as api

PART = "_Test Repair Hose"
SERVICE = "_Test Repair Service"


def make_material_request(repair, purpose):
	"""The request opens unsaved; save it as the user would."""
	return frappe.get_doc(api.make_material_request(repair, purpose)).insert().name


def make_material_issue(repair, submit=True):
	se = frappe.get_doc(api.make_material_issue(repair)).insert()
	if submit:
		se.submit()
	return se.name


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
		_settings(repair_material_requests=1, allow_direct_material_issue=1)

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

		repair = self._repair([{"item_code": SERVICE, "qty": 1}])
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
		for field, value in (("qty", 5), ("warehouse", None), ("item_code", SERVICE)):
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

		repair = self._repair([{"item_code": PART, "qty": 1}, {"item_code": SERVICE, "qty": 1}])
		issue_mr = make_material_request(repair.name, "Material Issue")
		buy_mr = make_material_request(repair.name, "Purchase")
		repair.reload()
		repair.append("repair_materials", {"item_code": PART, "qty": 2})
		repair.save()
		se = make_material_issue(repair.name, submit=False)
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

	# ── Stock availability decides the route ────────────────────────────

	def test_request_opens_unsaved(self):
		repair = self._repair([{"item_code": PART, "qty": 2}])
		doc = api.make_material_request(repair.name, "Material Issue")
		self.assertTrue(doc.get("__islocal"))
		self.assertEqual(doc["items"][0]["asset_material_row"], repair.repair_materials[0].name)
		self.assertFalse(frappe.db.get_value("Asset Service Material", repair.repair_materials[0].name, "material_request"))
		self.assertFalse(frappe.get_all("Material Request Item", {"asset_repair": repair.name}))

	def test_out_of_stock_is_purchased_then_issued_from_receipt(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_stock_entry

		repair = self._repair([{"item_code": PART, "qty": 15}])  # only 10 in stores
		row = repair.repair_materials[0]
		self.assertEqual(row.material_status, "Out of Stock")
		with self.assertRaisesRegex(frappe.ValidationError, "request a purchase or a transfer"):
			api.make_material_request(repair.name, "Material Issue")
		with self.assertRaises(frappe.ValidationError):
			api.make_material_issue(repair.name)

		mr = frappe.get_doc("Material Request", make_material_request(repair.name, "Purchase"))
		self.assertEqual(frappe.db.get_value("Asset Service Material", row.name, "purchase_request"), mr.name)
		repair.reload()
		self.assertEqual(repair.repair_materials[0].material_status, "Purchase Requested")
		mr.submit()
		po = make_purchase_order(mr.name)
		po.supplier = INSURER
		for d in po.items:
			d.rate = 1500
		po.insert()
		po.submit()
		pr = make_purchase_receipt(po.name)
		pr.insert()
		self.assertEqual((pr.items[0].asset_repair, pr.items[0].asset_material_row), (repair.name, row.name))
		pr.submit()

		repair.reload()
		self.assertEqual(flt(repair.repair_materials[0].actual_qty), 25)  # live, without saving the repair
		self.assertEqual(repair.repair_materials[0].material_status, "To Issue")

		# Stores' own "Make Stock Entry" on the receipt, switched to Material Issue, is recognised.
		se = make_stock_entry(pr.name)
		se.stock_entry_type = se.purpose = "Material Issue"
		for d in se.items:
			d.s_warehouse, d.t_warehouse = d.t_warehouse or self.store, None
		se.insert()
		self.assertEqual((se.items[0].asset_repair, se.items[0].asset_material_row), (repair.name, row.name))
		se.submit()
		repair.reload()
		self.assertEqual(repair.repair_materials[0].material_status, "Issued")
		self.assertEqual(flt(repair.repair_materials[0].issued_qty), 15)
		self.assertGreater(flt(repair.requested_materials_cost), 0)

	def test_transfer_from_another_warehouse(self):
		other = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0, "name": ["!=", self.store]}, "name")
		frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": COMPANY,
			"items": [{"item_code": PART, "qty": 50, "t_warehouse": other, "basic_rate": 1500}]}).insert().submit()
		repair = self._repair([{"item_code": PART, "qty": 20}])
		mr = api.make_material_request(repair.name, "Material Transfer")
		self.assertEqual((mr["items"][0]["from_warehouse"], mr["items"][0]["warehouse"]), (other, self.store))
		frappe.get_doc(mr).insert()
		repair.reload()
		self.assertEqual(repair.repair_materials[0].material_status, "Transfer Requested")

	def test_removing_a_line_from_the_request_frees_the_row(self):
		repair = self._repair([{"item_code": PART, "qty": 1}, {"item_code": SERVICE, "qty": 1}])
		mr = frappe.get_doc(api.make_material_request(repair.name, "Purchase"))  # only the service: the part is in stock
		self.assertEqual([d.item_code for d in mr.items], [SERVICE])
		mr.append("items", dict(mr.items[0].as_dict(), name=None, idx=2,
			asset_material_row=repair.repair_materials[0].name, item_code=PART))
		mr.insert()
		rows = [r.name for r in repair.repair_materials]
		self.assertEqual([frappe.db.get_value("Asset Service Material", r, "purchase_request") for r in rows], [mr.name, mr.name])
		mr.remove(mr.items[1])
		mr.save()
		self.assertEqual([frappe.db.get_value("Asset Service Material", r, "purchase_request") for r in rows], [None, mr.name])
		mr.material_request_type = "Material Issue"  # a type change moves the link
		mr.items[0].item_code = PART
		mr.items[0].asset_material_row = rows[0]
		mr.save()
		self.assertEqual(frappe.db.get_value("Asset Service Material", rows[0], ["material_request", "purchase_request"]), (mr.name, None))
		self.assertFalse(frappe.db.get_value("Asset Service Material", rows[1], "purchase_request"))

	def test_received_stock_used_elsewhere_is_not_offered_to_stores(self):
		from upande_asset.upande_asset.materials import row_status

		row = frappe._dict(is_stock_item=1, item_code=PART, qty=2, uom="Nos", warehouse=self.store, actual_qty=0,
			purchase_request="X", material_request=None, stock_entry=None)
		frappe.flags.in_test = True
		with unittest.mock.patch("frappe.db.get_value", return_value="Purchase"):
			self.assertEqual(row_status(row, 0, 2), "Received, Out of Stock")
			row.actual_qty = 5
			self.assertEqual(row_status(row, 0, 2), "To Issue")

	# ── Accounting dimensions follow the repair ─────────────────────────

	def test_dimensions_reach_every_document_and_header(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order

		from upande_asset.upande_asset.insurance.test_fixtures import CC_B

		repair = self._repair([{"item_code": PART, "qty": 50}, {"item_code": SERVICE, "qty": 1}])
		repair.cost_center = CC_B
		repair.save()
		mr = api.make_material_request(repair.name, "Purchase")
		self.assertEqual({d["cost_center"] for d in mr["items"]}, {CC_B})
		self.assertEqual(mr["cost_center"], CC_B)  # read by ERPNext's client so it keeps the rows' values
		mr = frappe.get_doc(mr).insert()
		mr.submit()
		po = make_purchase_order(mr.name)  # ERPNext's own mapping
		self.assertEqual((po.cost_center, {d.cost_center for d in po.items}), (CC_B, {CC_B}))  # header from rows
		po.supplier = INSURER
		for d in po.items:
			d.rate = 100
		po.insert()
		po.submit()
		pr = make_purchase_receipt(po.name)
		self.assertEqual((pr.cost_center, {d.cost_center for d in pr.items}), (CC_B, {CC_B}))

	def test_purchase_order_straight_from_the_rows(self):
		from upande_asset.upande_asset.materials import make_purchase_order, purchase_documents

		repair = self._repair([{"item_code": SERVICE, "qty": 1}])
		# Asset Settings decide which document starts a purchase.
		_settings(parts_purchase_document="Material Request")
		self.assertEqual(purchase_documents(), ["Material Request"])
		with self.assertRaisesRegex(frappe.ValidationError, "purchased through a Material Request"):
			make_purchase_order("Asset Repair", repair.name)
		_settings(parts_purchase_document="Material Request and Purchase Order")
		self.assertEqual(purchase_documents(), ["Material Request", "Purchase Order"])
		_settings(parts_purchase_document="Purchase Order")
		self.assertEqual(purchase_documents(), ["Purchase Order"])
		with self.assertRaisesRegex(frappe.ValidationError, "purchased through a Purchase Order"):
			api.make_material_request(repair.name, "Purchase")

		po = frappe.get_doc(make_purchase_order("Asset Repair", repair.name))
		self.assertEqual(po.items[0].asset_material_row, repair.repair_materials[0].name)
		self.assertTrue(po.cost_center)
		po.supplier = INSURER
		po.items[0].rate = 2500
		po.insert()
		row = repair.repair_materials[0].name
		self.assertEqual(frappe.db.get_value("Asset Service Material", row, ["purchase_order", "material_status"]), (po.name, "Ordered"))
		po.delete()
		self.assertEqual(frappe.db.get_value("Asset Service Material", row, "purchase_order"), None)
