# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Acceptance tests for Insurance Policy (Phase 1 + Phase 3).

Run:
    bench --site <site> run-tests --module upande_asset.upande_asset.doctype.insurance_policy.test_insurance_policy
"""

import unittest

import frappe
from frappe.utils import add_days, add_months, add_years, flt, getdate, today

from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import (
	endorse,
	generate_release_schedule,
	get_endorsement_preview,
	make_premium_invoice,
	raise_purchase_request,
	raise_renewal_request,
	reverse_endorsement,
)
from upande_asset.upande_asset.insurance import tasks
from upande_asset.upande_asset.insurance.test_fixtures import (
	CC_A,
	CC_B,
	COMPANY,
	INSURER,
	PREMIUM_ITEM,
	bootstrap,
	make_asset,
	make_policy,
)
from upande_asset.upande_asset.insurance.utils import policy_days, remaining_days


class TestInsurancePolicy(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.accounts = bootstrap()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.clear_document_cache("Asset Settings", "Asset Settings")

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Asset Settings", "Asset Settings")

	# ── Phase 1 ────────────────────────────────────────────────────────────

	def test_submit_fills_and_cancel_clears_asset_fields(self):
		a = make_asset()
		p = make_policy([a])
		v = frappe.db.get_value("Asset", a.name, ["insurance_policy", "policy_number", "insurer", "insured_value",
			"insurance_start_date", "insurance_end_date"], as_dict=True)
		self.assertEqual(v.insurance_policy, p.name)
		self.assertEqual(v.policy_number, p.policy_number)
		self.assertEqual(v.insurer, INSURER)
		self.assertEqual(flt(v.insured_value), 1_000_000)
		self.assertEqual(getdate(v.insurance_end_date), getdate(p.end_date))

		p.reload()
		p.cancel()
		v = frappe.db.get_value("Asset", a.name, ["insurance_policy", "policy_number", "insured_value"], as_dict=True)
		self.assertFalse(v.insurance_policy or v.policy_number or v.insured_value)

	def test_cancel_leaves_manual_asset_fields(self):
		a = make_asset()
		frappe.db.set_value("Asset", a.name, "policy_number", "MANUAL-1")
		p = make_policy([a], start=add_days(today(), 400), end=add_days(today(), 700))
		frappe.db.set_value("Asset", a.name, {"insurance_policy": None, "policy_number": "MANUAL-1"})
		p.reload()
		p.cancel()
		self.assertEqual(frappe.db.get_value("Asset", a.name, "policy_number"), "MANUAL-1")

	def test_overlap_blocked_for_same_type_only(self):
		a = make_asset()
		make_policy([a])
		with self.assertRaises(frappe.ValidationError):
			make_policy([a], start=add_days(today(), -10))
		other = make_policy([a], start=add_days(today(), -10), policy_type="Machinery Breakdown")
		self.assertEqual(other.docstatus, 1)

	def test_asset_validations(self):
		unflagged = make_asset(insurance_required=0)
		with self.assertRaises(frappe.ValidationError):
			make_policy([unflagged], submit=False)
		draft = make_asset(submit=False)
		with self.assertRaises(frappe.ValidationError):
			make_policy([draft], submit=False)
		a = make_asset()
		p = frappe.get_doc({"doctype": "Insurance Policy", "company": COMPANY, "insurer": INSURER, "policy_type": "Motor",
			"start_date": today(), "end_date": add_days(today(), 100),
			"insured_assets": [{"asset": a.name, "sum_insured": 1}, {"asset": a.name, "sum_insured": 1}]})
		with self.assertRaises(frappe.ValidationError):
			p.insert()

	def test_insurer_required_on_submit(self):
		a = make_asset()
		p = make_policy([a], submit=False)
		p.insurer = None
		p.save()
		with self.assertRaises(frappe.ValidationError):
			p.submit()

	def test_premium_invoice_split_and_deferred_release(self):
		a = make_asset(cost_center=CC_A)
		b = make_asset(cost_center=CC_B)
		c = make_asset(cost_center=CC_B)
		p = make_policy([a, b, c], basic=100_000.01, levies=999.99,
			start=add_months(getdate(today()).replace(day=1), -2))
		pi = frappe.get_doc("Purchase Invoice", make_premium_invoice(p.name))
		self.assertTrue(all(d.insurance_policy == p.name for d in pi.items))
		self.assertEqual(pi.supplier, INSURER)
		self.assertAlmostEqual(flt(pi.net_total, 2), 101_000.00, places=2)
		basic_lines = [d for d in pi.items if d.description.startswith("Basic premium")]
		self.assertEqual({d.cost_center for d in basic_lines}, {CC_A, CC_B})
		self.assertAlmostEqual(sum(flt(d.amount) for d in basic_lines), 100_000.01, places=2)
		by_cc = {d.cost_center: flt(d.amount) for d in basic_lines}
		self.assertAlmostEqual(by_cc[CC_A], 33_333.34, places=2)  # 1/3, rounded; remainder goes last
		for d in pi.items:
			self.assertEqual(d.enable_deferred_expense, 1)
			self.assertEqual(d.deferred_expense_account, self.accounts.prepaid_insurance_account)
			self.assertEqual(d.expense_account, self.accounts.insurance_expense_account)
			self.assertEqual(getdate(d.service_start_date), getdate(p.start_date))
			self.assertEqual(getdate(d.service_end_date), getdate(p.end_date))

		pi.bill_no = "INV-1"
		pi.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "purchase_invoice"), pi.name)
		prepaid = frappe.db.sql(
			"select sum(debit) from `tabGL Entry` where voucher_no=%s and account=%s and is_cancelled=0",
			(pi.name, self.accounts.prepaid_insurance_account),
		)[0][0]
		self.assertAlmostEqual(flt(prepaid), 101_000.00, places=2)

		# Process Deferred Accounting releases elapsed months to insurance expense.
		pda = frappe.get_doc({
			"doctype": "Process Deferred Accounting",
			"company": COMPANY,
			"type": "Expense",
			"posting_date": today(),
			"start_date": p.start_date,
			"end_date": add_days(getdate(today()).replace(day=1), -1),
		}).insert()
		pda.submit()
		# v16 books deferred releases as GL rows on the PI voucher, against the PDA document.
		expense = frappe.db.sql(
			"""select sum(debit) from `tabGL Entry` where account=%s and voucher_no=%s and is_cancelled=0""",
			(self.accounts.insurance_expense_account, pi.name),
		)[0][0]
		self.assertGreater(flt(expense), 0)
		self.assertLess(flt(expense), 101_000)

		pi.reload()
		pi.cancel()
		self.assertFalse(frappe.db.get_value("Insurance Policy", p.name, "purchase_invoice"))

	def test_status_active_expiring_expired(self):
		a, b, c = make_asset(), make_asset(), make_asset()
		active = make_policy([a])
		self.assertEqual(active.status, "Active")
		expiring = make_policy([b], start=add_days(today(), -300), end=add_days(today(), 10))
		self.assertEqual(frappe.db.get_value("Insurance Policy", expiring.name, "status"), "Expiring")
		expired = make_policy([c], start=add_days(today(), -400), end=add_days(today(), -5))
		self.assertEqual(frappe.db.get_value("Insurance Policy", expired.name, "status"), "Expired")

		# backdate the active policy and let the scheduler move it along
		frappe.db.set_value("Insurance Policy", active.name, "end_date", add_days(today(), 5))
		tasks.update_policy_statuses()
		self.assertEqual(frappe.db.get_value("Insurance Policy", active.name, "status"), "Expiring")
		frappe.db.set_value("Insurance Policy", active.name, "end_date", add_days(today(), -1))
		tasks.update_policy_statuses()
		self.assertEqual(frappe.db.get_value("Insurance Policy", active.name, "status"), "Expired")

	def test_renewal_alerts_are_idempotent(self):
		a = make_asset()
		p = make_policy([a], start=add_days(today(), -340), end=add_days(today(), 25))
		tasks.send_renewal_alerts()
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "last_alert_days"), 30)
		frappe.db.set_value("Insurance Policy", p.name, "last_alert_on", "2000-01-01")
		tasks.send_renewal_alerts()
		# Same band → not re-sent (last_alert_on untouched)
		self.assertEqual(str(frappe.db.get_value("Insurance Policy", p.name, "last_alert_on")), "2000-01-01")
		frappe.db.set_value("Insurance Policy", p.name, "end_date", add_days(today(), 5))
		tasks.send_renewal_alerts()
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "last_alert_days"), 7)

	def test_opening_policy_hides_premium_invoice(self):
		a = make_asset()
		p = make_policy([a], is_opening="Yes", prepaid_balance_at_go_live=5000, go_live_date=today())
		with self.assertRaises(frappe.ValidationError):
			make_premium_invoice(p.name)

	# ── Phase 3 ────────────────────────────────────────────────────────────

	def test_add_asset_mid_term_prorata_invoice(self):
		a = make_asset()
		p = make_policy([a], basic=120_000, levies=0)
		new = make_asset(cost_center=CC_B)
		eff = today()
		payload = {"endorsement_no": "END-1", "effective_date": eff, "assets": [{"asset": new.name, "sum_insured": 500_000}]}
		preview = get_endorsement_preview(p.name, "Add Assets", frappe.as_json(payload))
		expected = flt(120_000 / 1_000_000 * 500_000 * remaining_days(eff, p.end_date) / policy_days(p.start_date, p.end_date), 2)
		self.assertAlmostEqual(preview["total"], expected, places=2)

		res = endorse(p.name, "Add Assets", frappe.as_json(payload))
		self.assertEqual(res["reference_doctype"], "Purchase Invoice")
		pi = frappe.get_doc("Purchase Invoice", res["reference_name"])
		self.assertEqual(pi.is_return, 0)
		self.assertAlmostEqual(flt(pi.net_total), expected, places=2)
		self.assertEqual(getdate(pi.items[0].service_start_date), getdate(eff))
		self.assertEqual(getdate(pi.items[0].service_end_date), getdate(p.end_date))
		self.assertEqual(pi.items[0].cost_center, CC_B)
		p.reload()
		self.assertEqual(p.total_sum_insured, 1_500_000)
		self.assertEqual(len(p.endorsements), 1)
		self.assertEqual(frappe.db.get_value("Asset", new.name, "insurance_policy"), p.name)
		# Submitting the endorsement PI must not hijack the policy's premium invoice link.
		pi.bill_no = "E-1"
		pi.submit()
		self.assertFalse(frappe.db.get_value("Insurance Policy", p.name, "purchase_invoice"))

	def test_remove_asset_debit_note_and_reverse(self):
		a, b = make_asset(), make_asset()
		p = make_policy([a, b])
		res = endorse(p.name, "Remove Assets", frappe.as_json({
			"endorsement_no": "END-2", "effective_date": today(), "assets": [{"asset": b.name}],
		}))
		dn = frappe.get_doc("Purchase Invoice", res["reference_name"])
		self.assertEqual(dn.is_return, 1)
		self.assertLess(flt(dn.grand_total), 0)
		self.assertEqual(getdate(dn.items[0].service_start_date), getdate(today()))
		p.reload()
		row = next(r for r in p.insured_assets if r.asset == b.name)
		self.assertEqual(row.row_status, "Removed")
		self.assertEqual(p.total_sum_insured, 1_000_000)
		self.assertFalse(frappe.db.get_value("Asset", b.name, "insurance_policy"))

		reverse_endorsement(p.name, res["endorsement_group"])
		p.reload()
		row = next(r for r in p.insured_assets if r.asset == b.name)
		self.assertEqual(row.row_status, "Active")
		self.assertEqual(p.endorsements[0].status, "Reversed")
		self.assertFalse(frappe.db.exists("Purchase Invoice", dn.name))
		self.assertEqual(frappe.db.get_value("Asset", b.name, "insurance_policy"), p.name)

	def test_remove_endorsement_closes_disposal_todo(self):
		a, b = make_asset(), make_asset()
		p = make_policy([a, b])
		todo = frappe.get_doc({"doctype": "ToDo", "status": "Open", "reference_type": "Insurance Policy", "reference_name": p.name,
			"description": f"Asset [{b.name}] was scrapped. Endorse the policy."}).insert(ignore_permissions=True)
		endorse(p.name, "Remove Assets", frappe.as_json({"endorsement_no": "END-TD", "effective_date": today(),
			"assets": [{"asset": b.name}], "create_premium_document": 0}))
		self.assertEqual(frappe.db.get_value("ToDo", todo.name, "status"), "Closed")

	def test_reverse_blocked_when_document_submitted(self):
		a, b = make_asset(), make_asset()
		p = make_policy([a])
		res = endorse(p.name, "Add Assets", frappe.as_json({
			"endorsement_no": "END-3", "effective_date": today(), "assets": [{"asset": b.name, "sum_insured": 10_000}],
		}))
		pi = frappe.get_doc("Purchase Invoice", res["reference_name"])
		pi.bill_no = "E-3"
		pi.submit()
		with self.assertRaises(frappe.ValidationError):
			reverse_endorsement(p.name, res["endorsement_group"])

	def test_change_sum_and_cancel_policy(self):
		a = make_asset()
		p = make_policy([a])
		endorse(p.name, "Change Sum Insured", frappe.as_json({
			"endorsement_no": "END-4", "effective_date": today(), "assets": [{"asset": a.name, "new_sum_insured": 1_200_000}],
			"premium_adjustment": 1234.56,
		}))
		p.reload()
		self.assertEqual(p.total_sum_insured, 1_200_000)
		self.assertAlmostEqual(flt(p.endorsements[0].premium_adjustment), 1234.56, places=2)

		res = endorse(p.name, "Cancel Policy", frappe.as_json({"endorsement_no": "END-5", "effective_date": today()}))
		p.reload()
		self.assertEqual(p.status, "Cancelled")
		self.assertTrue(all(r.row_status == "Removed" for r in p.insured_assets))
		dn = frappe.get_doc("Purchase Invoice", res["reference_name"])
		self.assertEqual(dn.is_return, 1)

	def test_endorsement_rolls_back_on_failure(self):
		a, b = make_asset(), make_asset()
		p = make_policy([a])
		frappe.db.set_single_value("Asset Settings", "premium_item", None)
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		with self.assertRaises(frappe.ValidationError):
			endorse(p.name, "Add Assets", frappe.as_json({
				"endorsement_no": "END-6", "effective_date": today(), "assets": [{"asset": b.name, "sum_insured": 1}],
			}))
		p.reload()
		self.assertEqual(len(p.insured_assets), 1)
		self.assertEqual(len(p.endorsements), 0)

	def test_renewal_via_material_request_excludes_disposed_and_unflagged(self):
		keep, scrapped, unflagged = make_asset(), make_asset(), make_asset()
		p = make_policy([keep, scrapped, unflagged], start=add_days(today(), -340), end=add_days(today(), 24))
		frappe.db.set_value("Asset", scrapped.name, "status", "Scrapped")
		frappe.db.set_value("Asset", unflagged.name, "insurance_required", 0)
		res = raise_renewal_request(p.name, "Material Request")
		new = frappe.get_doc("Insurance Policy", res["policy"])
		self.assertEqual([r.asset for r in new.insured_assets], [keep.name])
		self.assertEqual(getdate(new.start_date), add_days(getdate(p.end_date), 1))
		self.assertEqual(getdate(new.end_date), add_days(add_years(add_days(getdate(p.end_date), 1), 1), -1))
		self.assertEqual(new.renewed_from, p.name)
		mr = frappe.get_doc("Material Request", res["procurement_name"])
		self.assertEqual(mr.material_request_type, "Purchase")
		self.assertEqual(mr.items[0].insurance_policy, new.name)
		self.assertEqual(mr.items[0].item_code, PREMIUM_ITEM)
		self.assertAlmostEqual(flt(mr.items[0].rate), flt(p.total_premium))
		self.assertEqual(new.procurement_doctype, "Material Request")
		with self.assertRaises(frappe.ValidationError):
			raise_renewal_request(p.name, "Material Request")

		new.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "status"), "Renewed")
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "renewed_to"), new.name)
		new.reload()
		new.cancel()
		self.assertNotEqual(frappe.db.get_value("Insurance Policy", p.name, "status"), "Renewed")

	def test_renewal_via_purchase_order_and_invoice_from_po(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice

		a = make_asset()
		p = make_policy([a], start=add_days(today(), -340), end=add_days(today(), 24))
		res = raise_renewal_request(p.name, "Purchase Order")
		new = frappe.get_doc("Insurance Policy", res["policy"])
		frappe.db.set_value("Insurance Policy", new.name, "insurer", None)
		po = frappe.get_doc("Purchase Order", res["procurement_name"])
		self.assertEqual(po.items[0].insurance_policy, new.name)
		self.assertAlmostEqual(flt(po.grand_total), flt(p.total_premium), places=2)
		po.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", new.name, "insurer"), INSURER)

		pi = make_purchase_invoice(po.name)
		self.assertEqual(pi.items[0].insurance_policy, new.name)  # mapped PO row → PI row
		pi.bill_no = "R-1"
		pi.insert()
		for d in pi.items:
			self.assertEqual(d.enable_deferred_expense, 1)
			self.assertEqual(getdate(d.service_start_date), getdate(new.start_date))
			self.assertEqual(getdate(d.service_end_date), getdate(new.end_date))
			self.assertEqual(d.deferred_expense_account, self.accounts.prepaid_insurance_account)
		pi.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", new.name, "purchase_invoice"), pi.name)

	def test_purchase_request_on_draft_policy(self):
		a = make_asset()
		p = make_policy([a], submit=False, start=add_days(today(), 10))
		res = raise_purchase_request(p.name, "Material Request")
		self.assertEqual(res["policy"], p.name)
		self.assertEqual(frappe.db.get_value("Material Request Item", {"parent": res["procurement_name"]}, "insurance_policy"), p.name)
		with self.assertRaises(frappe.ValidationError):
			raise_purchase_request(p.name, "Material Request")

	def test_opening_release_schedule_totals(self):
		a, b = make_asset(cost_center=CC_A), make_asset(cost_center=CC_B)
		start = add_months(getdate(today()).replace(day=1), -3)
		p = make_policy([a, b], start=start, is_opening="Yes", prepaid_balance_at_go_live=100_000.03,
			go_live_date=add_months(start, 1))
		names = generate_release_schedule(p.name)
		jes = [frappe.get_doc("Journal Entry", n) for n in names]
		self.assertEqual(len(jes), 12 - 1)
		total = sum(flt(j.total_credit) for j in jes)
		self.assertAlmostEqual(total, 100_000.03, places=2)
		self.assertTrue(all(j.insurance_policy == p.name for j in jes))
		due = [j for j in jes if getdate(j.posting_date) <= getdate(today())]
		self.assertTrue(due and all(j.docstatus == 1 for j in due))
		self.assertTrue(all(j.docstatus == 0 for j in jes if getdate(j.posting_date) > getdate(today())))
		self.assertTrue(all(getdate(j.posting_date) <= getdate(p.end_date) for j in jes))
		with self.assertRaises(frappe.ValidationError):
			generate_release_schedule(p.name)

	def test_role_checks_server_side(self):
		a = make_asset()
		p = make_policy([a])
		user = "insurance-test-user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc({"doctype": "User", "email": user, "first_name": "Ins", "send_welcome_email": 0,
				"roles": [{"role": "Maintenance Manager"}]}).insert(ignore_permissions=True)
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				endorse(p.name, "Cancel Policy", frappe.as_json({"endorsement_no": "X"}))
			with self.assertRaises(frappe.PermissionError):
				raise_renewal_request(p.name, "Material Request")
		finally:
			frappe.set_user("Administrator")

	# ── Overlap by cover window ──────────────────────────────────────────

	def test_overlap_uses_cover_window_after_removal(self):
		a, keep = make_asset(), make_asset()
		old = make_policy([a, keep], start=add_days(today(), -200), end=add_days(today(), 160))
		removal = add_days(today(), 30)
		endorse(old.name, "Remove Assets", frappe.as_json({
			"endorsement_no": "END-W1", "effective_date": removal, "assets": [{"asset": a.name}],
			"create_premium_document": 0,
		}))
		# New cover starting before the removal date overlaps the old row's remaining window.
		with self.assertRaises(frappe.ValidationError):
			make_policy([a], start=today(), end=add_days(today(), 364))
		frappe.db.rollback()

		a, keep = make_asset(), make_asset()
		old = make_policy([a, keep], start=add_days(today(), -200), end=add_days(today(), 160))
		endorse(old.name, "Remove Assets", frappe.as_json({
			"endorsement_no": "END-W2", "effective_date": removal, "assets": [{"asset": a.name}],
			"create_premium_document": 0,
		}))
		# Starting on the removal date is fine: old cover ends the day before.
		new = make_policy([a], start=removal, end=add_days(removal, 364))
		self.assertEqual(new.docstatus, 1)

	# ── Renewal premium invoice lands on the renewal policy ──────────────

	def test_renewal_mr_to_po_to_pi_links_the_active_policy(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
		from erpnext.stock.doctype.material_request.material_request import make_purchase_order

		from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import get_policy_context
		from upande_asset.upande_asset.insurance.premium import linked_documents

		a = make_asset()
		old = make_policy([a], start=add_days(today(), -340), end=add_days(today(), 24))
		res = raise_renewal_request(old.name, "Material Request")
		new_name = res["policy"]

		mr = frappe.get_doc("Material Request", res["procurement_name"])
		mr.submit()
		po = make_purchase_order(mr.name)
		po.supplier = INSURER
		po.schedule_date = po.schedule_date or today()
		po.insert()
		self.assertEqual(po.items[0].insurance_policy, new_name)
		po.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", new_name, "procurement_name"), po.name)

		pi = make_purchase_invoice(po.name)
		pi.bill_no = "REN-1"
		pi.insert()
		self.assertEqual(pi.items[0].insurance_policy, new_name)
		pi.submit()

		new = frappe.get_doc("Insurance Policy", new_name)
		new.submit()
		new.reload()
		self.assertEqual(new.purchase_invoice, pi.name)
		self.assertEqual(linked_documents("Purchase Invoice", new_name), [pi.name])
		self.assertEqual(linked_documents("Purchase Invoice", old.name), [])
		self.assertEqual(frappe.db.get_value("Insurance Policy", old.name, "status"), "Renewed")
		self.assertTrue(get_policy_context(new_name)["premium_invoiced"])

	# ── Non-asset insurance ──────────────────────────────────────────────

	def _other_policy(self, **kw):
		return make_policy([], insurance_for="Other", policy_type="Medical", cover_description="Staff medical, 40 employees",
			cost_center=CC_B, total_sum_insured=5_000_000, basic=200_000, levies=2_000, **kw)

	def test_non_asset_policy_premium_and_endorsements(self):
		p = self._other_policy()
		self.assertEqual(p.docstatus, 1)
		self.assertEqual(p.total_sum_insured, 5_000_000)
		self.assertEqual(p.sum_insured_at_inception, 5_000_000)

		pi = frappe.get_doc("Purchase Invoice", make_premium_invoice(p.name))
		self.assertEqual({d.cost_center for d in pi.items}, {CC_B})
		self.assertAlmostEqual(flt(pi.net_total), 202_000, places=2)

		with self.assertRaises(frappe.ValidationError):
			endorse(p.name, "Add Assets", frappe.as_json({"endorsement_no": "X", "assets": [{"asset": make_asset().name, "sum_insured": 1}]}))

		res = endorse(p.name, "Change Sum Insured", frappe.as_json({
			"endorsement_no": "MED-1", "effective_date": today(), "new_sum_insured": 6_000_000,
		}))
		p.reload()
		self.assertEqual(p.total_sum_insured, 6_000_000)
		self.assertGreater(flt(p.endorsements[0].premium_adjustment), 0)
		self.assertEqual(frappe.db.get_value("Purchase Invoice Item", {"parent": res["reference_name"]}, "cost_center"), CC_B)

		reverse_endorsement(p.name, res["endorsement_group"])
		p.reload()
		self.assertEqual(p.total_sum_insured, 5_000_000)

		res = endorse(p.name, "Cancel Policy", frappe.as_json({"endorsement_no": "MED-2", "effective_date": today()}))
		p.reload()
		self.assertEqual(p.status, "Cancelled")
		self.assertEqual(frappe.db.get_value("Purchase Invoice", res["reference_name"], "is_return"), 1)

	def test_non_asset_policy_rejects_asset_rows_and_renews(self):
		with self.assertRaises(frappe.ValidationError):
			make_policy([make_asset()], insurance_for="Other", policy_type="Medical", cover_description="x", submit=False)
		p = self._other_policy(start=add_days(today(), -340), end=add_days(today(), 24))
		res = raise_renewal_request(p.name, "Material Request")
		new = frappe.get_doc("Insurance Policy", res["policy"])
		self.assertEqual(new.insurance_for, "Other")
		self.assertEqual(new.total_sum_insured, 5_000_000)
		self.assertEqual(new.cost_center, CC_B)
		self.assertFalse(new.insured_assets)
		new.submit()
		self.assertEqual(frappe.db.get_value("Insurance Policy", p.name, "status"), "Renewed")

	# ── Connections & Address / Contact ──────────────────────────────────

	def _connection_names(self, policy, doctype):
		from frappe.desk.notifications import get_open_count

		counts = get_open_count("Insurance Policy", policy)["count"]
		for row in counts["internal_links_found"]:
			if row["doctype"] == doctype:
				return set(row["names"])
		return set()

	def test_renewal_chain_shows_both_directions_in_connections(self):
		a = make_asset()
		first = make_policy([a], start=add_days(today(), -700), end=add_days(today(), -336))
		frappe.db.set_value("Insurance Policy", first.name, "status", "Expired")
		second_name = raise_renewal_request(first.name, "Material Request")["policy"]
		# Draft renewal already shows on the old policy
		self.assertEqual(self._connection_names(first.name, "Insurance Policy"), {second_name})
		second = frappe.get_doc("Insurance Policy", second_name)
		second.submit()
		self.assertEqual(self._connection_names(second_name, "Insurance Policy"), {first.name})

		frappe.db.set_value("Insurance Policy", second_name, "end_date", add_days(today(), 20))
		third = raise_renewal_request(second_name, "Material Request")
		third_name = third["policy"]
		self.assertEqual(self._connection_names(second_name, "Insurance Policy"), {first.name, third_name})
		self.assertEqual(self._connection_names(third_name, "Insurance Policy"), {second_name})

		frappe.delete_doc("Insurance Policy", third_name)
		self.assertFalse(frappe.db.exists("Material Request", third["procurement_name"]))
		self.assertEqual(self._connection_names(second_name, "Insurance Policy"), {first.name})

	def test_insurer_address_and_contact_defaults(self):
		addr = frappe.get_doc({
			"doctype": "Address", "address_title": INSURER, "address_type": "Billing", "address_line1": "Upper Hill Rd",
			"city": "Nairobi", "country": "Kenya", "is_primary_address": 1,
			"links": [{"link_doctype": "Supplier", "link_name": INSURER}],
		}).insert()
		contact = frappe.get_doc({
			"doctype": "Contact", "first_name": "Claims", "last_name": "Desk", "is_primary_contact": 1,
			"email_ids": [{"email_id": "claims@insurer.example", "is_primary": 1}],
			"links": [{"link_doctype": "Supplier", "link_name": INSURER}],
		}).insert()
		p = make_policy([make_asset()], submit=False)
		self.assertEqual(p.supplier_address, addr.name)
		self.assertIn("Upper Hill Rd", p.address_display)
		self.assertEqual(p.contact_person, contact.name)
		self.assertEqual(p.contact_email, "claims@insurer.example")

	# ── Procurement document state (Settings) ────────────────────────────

	def test_procurement_created_as_draft_or_submitted(self):
		a = make_asset()
		old = make_policy([a], start=add_days(today(), -340), end=add_days(today(), 24))
		frappe.db.set_single_value("Asset Settings", "material_request_status", "Submitted")
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		res = raise_renewal_request(old.name, "Material Request")
		mr = frappe.get_doc("Material Request", res["procurement_name"])
		self.assertEqual(mr.docstatus, 1)
		self.assertAlmostEqual(flt(mr.items[0].rate), flt(old.total_premium))

		b = make_asset()
		old2 = make_policy([b], start=add_days(today(), -340), end=add_days(today(), 24))
		frappe.db.set_single_value("Asset Settings", "purchase_order_status", "Submitted")
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		res = raise_renewal_request(old2.name, "Purchase Order")
		po = frappe.get_doc("Purchase Order", res["procurement_name"])
		self.assertEqual(po.docstatus, 1)
		self.assertAlmostEqual(flt(po.grand_total), flt(old2.total_premium), places=2)

		frappe.db.set_single_value("Asset Settings", "purchase_order_status", "Draft")
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		c = make_asset()
		old3 = make_policy([c], start=add_days(today(), -340), end=add_days(today(), 24))
		res = raise_renewal_request(old3.name, "Purchase Order")
		self.assertEqual(frappe.db.get_value("Purchase Order", res["procurement_name"], "docstatus"), 0)


	# ── Insurance digest schedule ────────────────────────────────────────

	def test_digest_frequency(self):
		from upande_asset.upande_asset.insurance.tasks import digest_next_due, send_digest_if_due

		d = getdate("2026-01-31")
		self.assertIsNone(digest_next_due("", d))
		self.assertEqual(digest_next_due("Weekly", None), getdate(today()))  # never sent → due now
		self.assertEqual(digest_next_due("Daily", d), getdate("2026-02-01"))
		self.assertEqual(digest_next_due("Weekly", d), getdate("2026-02-07"))
		self.assertEqual(digest_next_due("Monthly", d), getdate("2026-02-28"))
		self.assertEqual(digest_next_due("Quarterly", d), getdate("2026-04-30"))
		self.assertEqual(digest_next_due("Half-yearly", d), getdate("2026-07-31"))
		self.assertEqual(digest_next_due("Yearly", d), getdate("2027-01-31"))
		self.assertEqual(digest_next_due("Custom", d, 10), getdate("2026-02-10"))
		self.assertIsNone(digest_next_due("Custom", d, 0))

		frappe.db.set_single_value("Asset Settings", {"digest_frequency": "Monthly", "digest_last_sent": None})
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		send_digest_if_due()
		self.assertEqual(str(frappe.db.get_single_value("Asset Settings", "digest_last_sent")), today())
		frappe.db.set_single_value("Asset Settings", "digest_last_sent", add_days(today(), -10))
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		send_digest_if_due()  # not a month yet → not sent again
		self.assertEqual(str(frappe.db.get_single_value("Asset Settings", "digest_last_sent")), str(add_days(today(), -10)))

	def test_premium_invoice_accounts_are_defaults(self):
		from upande_asset.upande_asset.insurance.test_fixtures import _account

		p = make_policy([make_asset()])
		pi = frappe.get_doc("Purchase Invoice", make_premium_invoice(p.name))
		self.assertEqual(pi.items[0].expense_account, self.accounts.insurance_expense_account)
		other = _account("Other Insurance Expense", "Expense")
		pi.items[0].expense_account = other
		pi.save()
		pi.reload()
		self.assertEqual(pi.items[0].expense_account, other)  # user's change kept


class TestInsurancePolicyType(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		from upande_asset.upande_asset.insurance.test_fixtures import bootstrap

		bootstrap()

	def tearDown(self):
		frappe.db.rollback()

	def _type(self, name, **kw):
		if not frappe.db.exists("Insurance Policy Type", name):
			frappe.get_doc({"doctype": "Insurance Policy Type", "policy_type_name": name, **kw}).insert()
		return name

	def test_ira_classes_are_standard_and_cannot_be_deleted(self):
		self.assertTrue(frappe.db.get_value("Insurance Policy Type", "Engineering", "is_standard"))
		self.assertEqual(frappe.db.get_value("Insurance Policy Type", "Engineering", "ira_class"), "Engineering")
		with self.assertRaisesRegex(frappe.ValidationError, "Disable it instead"):
			frappe.delete_doc("Insurance Policy Type", "Engineering")
		with self.assertRaisesRegex(frappe.ValidationError, "standard IRA classes"):
			self._type("_Test Bad Class", ira_class="Comprehensive")  # Comprehensive is a user type

	def test_disabled_type_blocks_new_policies_not_renewals(self):
		t = self._type("_Test Greenhouse Cover", ira_class="Fire Industrial")
		a = make_asset()
		old = make_policy([a], policy_type=t, start=add_days(today(), -340), end=add_days(today(), 24))
		frappe.db.set_value("Insurance Policy Type", t, "enabled", 0)
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			make_policy([make_asset()], policy_type=t)
		res = raise_renewal_request(old.name, "Material Request")  # the renewal keeps its type
		self.assertEqual(frappe.db.get_value("Insurance Policy", res["policy"], "policy_type"), t)

	def test_comprehensive_flag_drives_asset_field(self):
		t = self._type("_Test Farm Comprehensive", ira_class="Motor Commercial", comprehensive_cover=1)
		a = make_asset()
		make_policy([a], policy_type=t)
		self.assertEqual(frappe.db.get_value("Asset", a.name, "comprehensive_insurance"), "Yes")
		b = make_asset()
		make_policy([b], policy_type=self._type("_Test Plain Cover", ira_class="Miscellaneous"))
		self.assertEqual(frappe.db.get_value("Asset", b.name, "comprehensive_insurance"), "No")

	def test_claim_matches_preference_through_ira_class(self):
		from upande_asset.upande_asset.doctype.insurance_claim.insurance_claim import resolve_policy

		mb = self._type("_Test Pump Breakdown", ira_class="Engineering")
		other = self._type("_Test Pump All Risks", ira_class="Miscellaneous")
		if not frappe.db.exists("Insurance Incident Type", "_Test Pump Failure"):
			frappe.get_doc({"doctype": "Insurance Incident Type", "incident_type_name": "_Test Pump Failure",
				"preferred_policy_types": [{"policy_type": "Engineering"}]}).insert()  # an IRA class
		a = make_asset()
		make_policy([a], policy_type=other)
		p_mb = make_policy([a], policy_type=mb)
		self.assertEqual(resolve_policy(a.name, today(), "_Test Pump Failure"), p_mb.name)
