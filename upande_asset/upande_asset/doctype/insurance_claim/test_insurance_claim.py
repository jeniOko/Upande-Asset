# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Acceptance tests for Insurance Claim (Phase 2).

Run:
    bench --site <site> run-tests --module upande_asset.upande_asset.doctype.insurance_claim.test_insurance_claim
"""

import unittest

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, today

from upande_asset.upande_asset.doctype.insurance_claim.insurance_claim import (
	close_with_shortfall,
	make_claim_from_check_sheet,
	make_payment_entry,
)
from upande_asset.upande_asset.insurance.test_fixtures import (
	CATEGORY,
	COMPANY,
	INSURER,
	bootstrap,
	make_asset,
	make_policy,
)


def make_claim(asset, incident_date=None, attach=True, **kw):
	claim = frappe.get_doc({
		"doctype": "Insurance Claim",
		"company": COMPANY,
		"asset": asset.name,
		"incident_date": incident_date or add_days(today(), -2),
		"incident_type": kw.pop("incident_type", "Accident"),
		"loss_treatment": kw.pop("loss_treatment", "Repair"),
		"amount_claimed": kw.pop("amount_claimed", 200_000),
		**kw,
	}).insert()
	if attach:
		for d in claim.documents:
			d.attachment = "/files/test-claim-doc.pdf"
		claim.save()
	return claim


def approve(claim, amount=None):
	claim = apply_workflow(claim, "Record")
	claim = apply_workflow(frappe.get_doc("Insurance Claim", claim.name), "Start Assessment")
	claim = frappe.get_doc("Insurance Claim", claim.name)
	if amount is not None:
		claim.amount_approved = amount
		claim.save()
	return apply_workflow(claim, "Approve")


class TestInsuranceClaim(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.accounts = bootstrap()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.clear_document_cache("Asset Settings", "Asset Settings")
		self.asset = make_asset()
		self.policy = make_policy([self.asset])

	def tearDown(self):
		frappe.db.rollback()

	def test_policy_auto_resolved_and_excess(self):
		claim = make_claim(self.asset)
		self.assertEqual(claim.policy, self.policy.name)
		self.assertEqual(claim.insurer, INSURER)
		self.assertEqual(flt(claim.excess), 25_000)
		self.assertEqual(flt(claim.net_claimable), 175_000)

		frappe.db.set_value("Insurance Policy", self.policy.name, {"excess_type": "Percent of Claim", "excess_value": 10, "minimum_excess": 30_000})
		claim.save()
		self.assertEqual(flt(claim.excess), 30_000)  # 10% = 20k < minimum 30k
		claim.amount_claimed = 500_000
		claim.save()
		self.assertEqual(flt(claim.excess), 50_000)

	def test_incident_outside_policy_period_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_claim(self.asset, incident_date=add_days(self.policy.start_date, -1))
		with self.assertRaises(frappe.ValidationError):
			make_claim(self.asset, incident_date=add_days(self.policy.end_date, 1))

	def test_lodging_blocked_without_mandatory_attachments(self):
		claim = make_claim(self.asset, attach=False, incident_type="Theft")
		names = {d.document_name for d in claim.documents if d.mandatory}
		self.assertEqual(names, {"Claim form", "Police abstract"})
		with self.assertRaises(frappe.ValidationError):
			apply_workflow(claim, "Record")
		claim.reload()
		for d in claim.documents:
			d.attachment = "/files/x.pdf"
		claim.save()
		claim = apply_workflow(claim, "Record")
		self.assertEqual(claim.status, "Recorded")
		self.assertTrue(claim.lodged_on)

	def test_mandatory_documents_locked_once_recorded(self):
		claim = apply_workflow(make_claim(self.asset), "Record")
		mandatory = next(d for d in claim.documents if d.mandatory)

		for change in ("clear", "untick", "rename", "remove"):
			claim = frappe.get_doc("Insurance Claim", claim.name)
			row = claim.getone("documents", {"name": mandatory.name})
			if change == "clear":
				row.attachment = None
			elif change == "untick":
				row.mandatory = 0
			elif change == "rename":
				row.document_name = "Something else"
			else:
				claim.remove(row)
			with self.assertRaises(frappe.ValidationError, msg=change):
				claim.save()

		# Replacing the file is fine.
		claim = frappe.get_doc("Insurance Claim", claim.name)
		claim.getone("documents", {"name": mandatory.name}).attachment = "/files/replacement.pdf"
		claim.save()

		# Still locked after submit (fields are allow-on-submit).
		claim = apply_workflow(frappe.get_doc("Insurance Claim", claim.name), "Start Assessment")
		claim = apply_workflow(frappe.get_doc("Insurance Claim", claim.name), "Approve")
		claim = frappe.get_doc("Insurance Claim", claim.name)
		self.assertEqual(claim.docstatus, 1)
		claim.getone("documents", {"name": mandatory.name}).attachment = None
		with self.assertRaises(frappe.ValidationError):
			claim.save()

		# The File behind it cannot be deleted either.
		f = frappe.get_doc({"doctype": "File", "file_name": "replacement.pdf", "file_url": "/files/replacement.pdf",
			"attached_to_doctype": "Insurance Claim", "attached_to_name": claim.name})
		f.flags.ignore_file_validate = True
		f.db_insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("File", f.name)

	def test_approved_amount_cannot_exceed_claimed(self):
		claim = make_claim(self.asset)
		claim.amount_approved = 300_000
		with self.assertRaises(frappe.ValidationError):
			claim.save()

	def test_approve_repair_posts_income_journal(self):
		claim = approve(make_claim(self.asset), 150_000)
		self.assertEqual(claim.docstatus, 1)
		self.assertEqual(claim.status, "Approved")
		claim.reload()
		self.assertEqual(flt(claim.outstanding_amount), 150_000)
		je = frappe.get_doc("Journal Entry", claim.claim_journal_entry)
		self.assertEqual(je.docstatus, 1)
		self.assertEqual(je.insurance_claim, claim.name)
		dr = next(r for r in je.accounts if flt(r.debit) > 0)
		cr = next(r for r in je.accounts if flt(r.credit) > 0)
		self.assertEqual(dr.account, self.accounts.claim_recoverable_account)
		self.assertEqual((dr.party_type, dr.party), ("Supplier", INSURER))
		self.assertEqual(cr.account, self.accounts.claim_recovery_income_account)
		self.assertEqual(flt(dr.debit), 150_000)
		self.assertEqual(dr.cost_center, self.asset.cost_center)

		claim.cancel()
		self.assertEqual(frappe.db.get_value("Journal Entry", je.name, "docstatus"), 2)

	def test_total_loss_requires_scrapped_asset(self):
		claim = make_claim(self.asset, loss_treatment="Total Loss", amount_claimed=900_000)
		with self.assertRaises(frappe.ValidationError):
			approve(claim, 800_000)
		frappe.db.rollback()

		asset = make_asset()
		make_policy([asset])
		claim = make_claim(asset, loss_treatment="Total Loss", amount_claimed=900_000)
		frappe.db.set_value("Asset", asset.name, "status", "Scrapped")
		claim = approve(frappe.get_doc("Insurance Claim", claim.name), 800_000)
		je = frappe.get_doc("Journal Entry", frappe.db.get_value("Insurance Claim", claim.name, "claim_journal_entry"))
		cr = next(r for r in je.accounts if flt(r.credit) > 0)
		self.assertEqual(cr.account, frappe.db.get_value("Company", COMPANY, "disposal_account"))

	def test_rejected_posts_nothing(self):
		claim = make_claim(self.asset)
		claim = apply_workflow(claim, "Record")
		claim = frappe.get_doc("Insurance Claim", claim.name)
		claim = apply_workflow(claim, "Start Assessment")
		claim = frappe.get_doc("Insurance Claim", claim.name)
		claim.rejection_reason = "Not covered peril"
		claim.save()
		claim = apply_workflow(claim, "Reject")
		self.assertEqual(claim.docstatus, 1)
		self.assertFalse(frappe.db.get_value("Insurance Claim", claim.name, "claim_journal_entry"))

	def _pay(self, claim_name, amount):
		pe = make_payment_entry(claim_name)
		pe.paid_amount = pe.received_amount = amount
		pe.reference_no, pe.reference_date = "CHQ", today()
		pe.insert()
		pe.submit()
		return pe

	def test_partial_payments_settle_and_cancel_reverts(self):
		claim = approve(make_claim(self.asset), 150_000)
		pe1 = self._pay(claim.name, 100_000)
		self.assertEqual(pe1.insurance_claim, claim.name)
		self.assertEqual(pe1.paid_from, self.accounts.claim_recoverable_account)
		c = frappe.get_doc("Insurance Claim", claim.name)
		self.assertEqual(c.status, "Partly Settled")
		self.assertEqual(flt(c.outstanding_amount), 50_000)

		pe2 = self._pay(claim.name, 50_000)
		c.reload()
		self.assertEqual(c.status, "Settled")
		self.assertEqual(flt(c.outstanding_amount), 0)

		pe2.cancel()
		c.reload()
		self.assertEqual(c.status, "Partly Settled")
		self.assertEqual(flt(c.outstanding_amount), 50_000)
		with self.assertRaises(frappe.ValidationError):
			c.cancel()

	def test_close_with_shortfall(self):
		claim = approve(make_claim(self.asset), 150_000)
		self._pay(claim.name, 120_000)
		je_name = close_with_shortfall(claim.name, "Insurer final offer")
		c = frappe.get_doc("Insurance Claim", claim.name)
		self.assertEqual(c.status, "Closed")
		self.assertEqual(flt(c.outstanding_amount), 0)
		self.assertEqual(flt(c.shortfall_amount), 30_000)
		je = frappe.get_doc("Journal Entry", je_name)
		dr = next(r for r in je.accounts if flt(r.debit) > 0)
		self.assertEqual(dr.account, self.accounts.claim_shortfall_account)
		self.assertEqual(flt(dr.debit), 30_000)

	def test_claim_from_check_sheet(self):
		cs = frappe.get_doc({
			"doctype": "Asset Check Sheet",
			"asset": self.asset.name,
			"asset_name": self.asset.asset_name,
			"asset_category": CATEGORY,
			"company": COMPANY,
			"check_date": add_days(today(), -1),
			"section_a_label": "Engine",
			"section_a_items": [
				{"check_factor": "Hydraulics", "status": "Fault / Defect", "severity": "Critical",
					"fault_description": "Burst hose", "incident_percent": 40},
				{"check_factor": "Lights", "status": "OK"},
			],
		})
		# check_sheet_template is made mandatory by a Property Setter; the template
		# itself needs a maintenance team – not relevant to the claim flow.
		cs.flags.ignore_mandatory = True
		cs.insert()
		cs.submit()
		claim = make_claim_from_check_sheet(cs.name)
		self.assertEqual(claim.asset, self.asset.name)
		self.assertEqual(claim.policy, self.policy.name)
		self.assertIn("Burst hose", claim.description)
		self.assertNotIn("Lights", claim.description)
		for d in claim.documents:
			d.attachment = "/files/x.pdf"
		claim.amount_claimed = 10_000
		claim.insert()
		self.assertEqual(frappe.db.get_value("Asset Check Sheet", cs.name, "insurance_claim"), claim.name)
		with self.assertRaises(frappe.ValidationError):
			make_claim_from_check_sheet(cs.name)

	def test_changing_incident_type_repicks_auto_policy(self):
		"""A claim from a check sheet defaults to Breakdown; switching to Lightning before
		saving (or later, on the draft) must move it to the Fire policy. A hand-picked policy stays."""
		make_policy([self.asset], policy_type="Machinery Breakdown")  # self.policy is Comprehensive
		fire = make_policy([self.asset], policy_type="Fire & Perils")
		mb = frappe.db.get_value("Insurance Policy", {"policy_type": "Machinery Breakdown", "docstatus": 1,
			"name": ["in", [r.parent for r in frappe.get_all("Insurance Policy Asset", {"asset": self.asset.name}, ["parent"])]]})
		cs = frappe.get_doc({"doctype": "Asset Check Sheet", "asset": self.asset.name, "asset_name": self.asset.asset_name,
			"asset_category": CATEGORY, "company": COMPANY, "check_date": add_days(today(), -1), "section_a_label": "Electrical",
			"section_a_items": [{"check_factor": "AVR", "status": "Fault / Defect", "severity": "Critical", "fault_description": "No output"}]})
		cs.flags.ignore_mandatory = True
		cs.insert()
		cs.submit()
		claim = make_claim_from_check_sheet(cs.name)
		self.assertEqual(claim.policy, mb)  # Breakdown prefers Machinery Breakdown
		claim.incident_type = "Lightning"
		claim.amount_claimed = 10_000
		claim.insert()
		self.assertEqual(claim.policy, fire.name)

		# On the saved draft: back to Breakdown re-picks; a hand-picked policy is then kept.
		claim.incident_type = "Breakdown"
		claim.save()
		self.assertEqual(claim.policy, mb)
		claim.policy = self.policy.name
		claim.save()
		claim.incident_type = "Lightning"
		claim.save()
		self.assertEqual(claim.policy, self.policy.name)

	def test_incident_types_are_configurable(self):
		frappe.db.set_value("Insurance Incident Type", "Fire", "enabled", 0)
		with self.assertRaises(frappe.ValidationError):
			make_claim(self.asset, incident_type="Fire")
		frappe.db.set_value("Insurance Incident Type", "Fire", "enabled", 1)

		# Preference comes from the record: Breakdown prefers Machinery Breakdown cover.
		mb = make_policy([self.asset], policy_type="Machinery Breakdown")
		self.assertEqual(make_claim(self.asset, incident_type="Breakdown").policy, mb.name)
		itype = frappe.get_doc("Insurance Incident Type", "Breakdown")
		itype.preferred_policy_types = []
		itype.append("preferred_policy_types", {"policy_type": "Comprehensive"})
		itype.save()
		self.assertEqual(make_claim(self.asset, incident_type="Breakdown").policy, self.policy.name)

	# ── Why-Why analysis on check sheet → repair → claim ─────────────────

	def _sheet_with_analysis(self):
		cs = frappe.get_doc({
			"doctype": "Asset Check Sheet",
			"asset": self.asset.name,
			"asset_name": self.asset.asset_name,
			"asset_category": CATEGORY,
			"company": COMPANY,
			"check_date": add_days(today(), -1),
			"section_a_label": "Engine",
			"section_a_items": [{"check_factor": "Hydraulics", "status": "Fault / Defect", "severity": "High",
				"fault_description": "No lift", "incident_percent": 30}],
			"problem_statement": "Tractor lost hydraulic lift",
			"why_why_analysis": [{"cause": "Hose burst"}, {"cause": "Hose rubbed on the frame"}, {"cause": "Clamp missing after last service"}],
			"root_cause_category": "Maintenance",
			"corrective_actions": [{"action": "Add clamp check to service sheet", "assigned_to": "Administrator",
				"due_date": add_days(today(), 7), "status": "Open"}],
		})
		cs.flags.ignore_mandatory = True
		cs.insert()
		return cs

	def test_root_cause_and_corrective_action_todo(self):
		cs = self._sheet_with_analysis()
		self.assertEqual(cs.root_cause, "Clamp missing after last service")
		todo = cs.corrective_actions[0].todo or frappe.db.get_value("Corrective Action", cs.corrective_actions[0].name, "todo")
		self.assertTrue(todo)
		self.assertEqual(frappe.db.get_value("ToDo", todo, "allocated_to"), "Administrator")

		t = frappe.get_doc("ToDo", todo)
		t.status = "Closed"
		t.save()
		self.assertEqual(frappe.db.get_value("Corrective Action", cs.corrective_actions[0].name, "status"), "Done")

		cs.reload()
		cs.corrective_actions[0].status = "Open"
		cs.save()
		self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Open")

	def test_analysis_flows_from_sheet_to_repair_not_claim(self):
		from upande_asset.upande_asset.doctype.asset_check_sheet.asset_check_sheet import make_asset_repair

		cs = self._sheet_with_analysis()
		cs.submit()
		repair = make_asset_repair(cs.name)
		self.assertEqual([r.cause for r in repair.why_why_analysis][-1], "Clamp missing after last service")
		self.assertFalse(repair.get("corrective_actions"))
		repair.insert()
		self.assertEqual(repair.root_cause, "Clamp missing after last service")
		frappe.db.set_value("Asset Check Sheet", cs.name, "linked_asset_repair", repair.name)

		self.assertEqual(repair.root_cause_category, "Maintenance")

		claim = make_claim_from_check_sheet(cs.name)
		self.assertFalse(claim.meta.has_field("why_why_analysis"))

	# ── Per-claim accounts (Asset Settings are only defaults) ───────────

	def _other_account(self, name, root_type, account_type=None):
		from upande_asset.upande_asset.insurance.test_fixtures import _account

		return _account(name, root_type, account_type)

	def test_claim_accounts_default_and_override(self):
		claim = make_claim(self.asset)
		self.assertEqual(claim.recoverable_account, self.accounts.claim_recoverable_account)
		self.assertEqual(claim.credit_account, self.accounts.claim_recovery_income_account)
		self.assertEqual(claim.shortfall_account, self.accounts.claim_shortfall_account)

		other_income = self._other_account("Other Claim Income", "Income")
		claim.credit_account = other_income
		claim.save()
		claim = approve(claim, 150_000)
		je = frappe.get_doc("Journal Entry", frappe.db.get_value("Insurance Claim", claim.name, "claim_journal_entry"))
		self.assertEqual(next(r for r in je.accounts if flt(r.credit)).account, other_income)

		other_shortfall = self._other_account("Other Claim Write-off", "Expense")
		frappe.db.set_value("Insurance Claim", claim.name, "shortfall_account", other_shortfall)
		je2 = frappe.get_doc("Journal Entry", close_with_shortfall(claim.name, "write off"))
		self.assertEqual(next(r for r in je2.accounts if flt(r.debit)).account, other_shortfall)

	def test_recoverable_must_be_payable_and_total_loss_switches_credit(self):
		claim = make_claim(self.asset)
		claim.recoverable_account = self.accounts.claim_recovery_income_account
		with self.assertRaises(frappe.ValidationError):
			claim.save()
		claim.reload()
		claim.loss_treatment = "Total Loss"
		claim.save()
		self.assertEqual(claim.credit_account, frappe.db.get_value("Company", COMPANY, "disposal_account"))
