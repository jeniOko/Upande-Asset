# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form, nowdate

from upande_asset.upande_asset.insurance.utils import (
	get_asset_book_value,
	get_company_accounts,
	get_covering_rows,
	get_settings,
)

# Preferred cover per incident type when an asset sits on several policies.


def preferred_policy_types(incident_type):
	"""Policy types in preference order, from the Insurance Incident Type record."""
	if not incident_type:
		return []
	return frappe.get_all(
		"Insurance Incident Policy Type",
		filters={"parent": incident_type, "parenttype": "Insurance Incident Type"},
		pluck="policy_type",
		order_by="idx",
	)


def _sort_by_preference(rows, incident_type):
	"""A preference names a policy type, or a standard IRA class that any type in the class matches;
	an exact type match counts before a match through its class."""
	pref = preferred_policy_types(incident_type)

	def rank(r):
		if r.policy_type in pref:
			return (pref.index(r.policy_type), 0)
		ira = frappe.get_cached_value("Insurance Policy Type", r.policy_type, "ira_class") if r.policy_type else None
		return (pref.index(ira), 1) if ira in pref else (len(pref), 2)

	rows.sort(key=rank)
	return rows

OPEN_STATUSES = ("Draft", "Recorded", "Under Assessment")


class InsuranceClaim(Document):
	ignore_linked_doctypes = ("GL Entry", "Payment Ledger Entry", "Journal Entry")

	def validate(self):
		self.status = self.status or "Draft"
		self._validate_incident_type()
		self._fetch_asset()
		self._resolve_policy()
		self._default_amount_claimed()
		self._seed_documents()
		self._set_accounts()
		self._compute_excess()
		self._validate_amounts()
		self._validate_transition()

	def before_update_after_submit(self):
		self._compute_outstanding()

	def after_insert(self):
		if self.asset_check_sheet:
			frappe.db.set_value("Asset Check Sheet", self.asset_check_sheet, "insurance_claim", self.name, update_modified=False)

	def on_trash(self):
		self._unlink_check_sheet()

	def before_submit(self):
		if self.status not in ("Approved", "Rejected"):
			frappe.throw(_("Claims are submitted through the workflow by Approving or Rejecting them."))
		if self.status == "Approved":
			if flt(self.amount_approved) <= 0:
				frappe.throw(_("Enter the Amount Approved."))
			if self.loss_treatment == "Total Loss":
				status = frappe.db.get_value("Asset", self.asset, "status")
				if status != "Scrapped":
					frappe.throw(
						_(
							"Asset {0} is {1}. For a Total Loss claim, scrap the asset first (Asset → Manage → Scrap Asset) "
							"so its book value is written off to the disposal account; the claim then credits the same account."
						).format(get_link_to_form("Asset", self.asset), frappe.bold(status)),
						title=_("Scrap Asset First"),
					)
			self.approved_on = self.approved_on or nowdate()
		elif self.status == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Enter a Rejection Reason."))

	def on_submit(self):
		if self.status == "Approved":
			je = self._make_claim_journal_entry()
			self.db_set({"claim_journal_entry": je.name, "outstanding_amount": flt(self.amount_approved), "amount_received": 0})
		else:
			self.db_set("outstanding_amount", 0)

	def before_cancel(self):
		pes = frappe.get_all("Payment Entry", filters={"insurance_claim": self.name, "docstatus": 1}, pluck="name")
		if pes:
			frappe.throw(
				_("Cancel these Payment Entries first:<br>{0}").format(
					"<br>".join(get_link_to_form("Payment Entry", p) for p in pes)
				)
			)

	def on_cancel(self):
		for field in ("shortfall_journal_entry", "claim_journal_entry"):
			name = self.get(field)
			if name and frappe.db.get_value("Journal Entry", name, "docstatus") == 1:
				je = frappe.get_doc("Journal Entry", name)
				je.flags.ignore_permissions = True
				je.cancel()
		self.db_set("status", "Cancelled")
		self._unlink_check_sheet()

	# ── helpers ────────────────────────────────────────────────────────────

	def _unlink_check_sheet(self):
		if self.asset_check_sheet and frappe.db.get_value("Asset Check Sheet", self.asset_check_sheet, "insurance_claim") == self.name:
			frappe.db.set_value("Asset Check Sheet", self.asset_check_sheet, "insurance_claim", None, update_modified=False)

	def _validate_incident_type(self):
		if not self.incident_type or not (self.is_new() or self.has_value_changed("incident_type")):
			return
		if not frappe.db.get_value("Insurance Incident Type", self.incident_type, "enabled"):
			frappe.throw(_("Incident Type {0} is disabled.").format(frappe.bold(self.incident_type)))

	def _fetch_asset(self):
		a = frappe.db.get_value(
			"Asset", self.asset, ["asset_name", "asset_category", "cost_center", "company", "insurance_required", "location"],
			as_dict=True,
		)
		if not a:
			return
		if not a.insurance_required:
			frappe.throw(_("Asset {0} is not flagged Insurance Required.").format(self.asset))
		self.asset_name, self.asset_category, self.cost_center = a.asset_name, a.asset_category, a.cost_center
		self.company = self.company or a.company
		if a.company != self.company:
			frappe.throw(_("Asset {0} belongs to company {1}.").format(self.asset, a.company))
		self.location = self.location or a.location

	def _resolve_policy(self):
		if not self.asset or not self.incident_date:
			return
		rows = [r for r in get_covering_rows(self.asset, self.incident_date)]
		if self.policy and self.docstatus == 0 and self._policy_was_auto_picked_for_other_incident(rows):
			self.policy = None  # re-pick below for the new incident type
		if self.policy:
			if not any(r.policy == self.policy for r in rows):
				frappe.throw(
					_("Policy {0} does not cover asset {1} on {2}.").format(
						self.policy, self.asset, frappe.format(self.incident_date, "Date")
					),
					title=_("Not Covered"),
				)
		else:
			if not rows:
				frappe.throw(
					_("No submitted Insurance Policy covers asset {0} on {1}.").format(
						self.asset, frappe.format(self.incident_date, "Date")
					),
					title=_("Not Covered"),
				)
			_sort_by_preference(rows, self.incident_type)
			self.policy = rows[0].policy
		p = frappe.db.get_value("Insurance Policy", self.policy, ["insurer", "policy_number", "policy_type"], as_dict=True)
		self.insurer, self.policy_number, self.policy_type = p.insurer, p.policy_number, p.policy_type

	def _policy_was_auto_picked_for_other_incident(self, rows):
		"""True when the policy is the automatic pick for an earlier incident type that
		has since changed (e.g. a claim made from a check sheet defaults to Breakdown and
		the user then sets Lightning). A policy picked by hand for the same incident stays."""
		before = self.flags.pop("auto_policy_for", None) if self.is_new() else None  # set by make_claim_from_check_sheet
		if before is None and not self.is_new():
			prev = self.get_doc_before_save()
			before = prev.incident_type if prev and prev.policy == self.policy else None
		if before is None or before == self.incident_type:
			return False
		ranked = _sort_by_preference(list(rows), before)
		return bool(ranked) and ranked[0].policy == self.policy

	def _default_amount_claimed(self):
		if flt(self.amount_claimed):
			return
		if self.loss_treatment == "Total Loss":
			self.amount_claimed = get_asset_book_value(self.asset)
		elif self.asset_repair:
			r = frappe.db.get_value("Asset Repair", self.asset_repair, ["total_repair_cost", "repair_cost"], as_dict=True)
			if r:
				self.amount_claimed = flt(r.total_repair_cost) or flt(r.repair_cost)

	def _seed_documents(self):
		if not self.incident_type:
			return
		existing = {d.document_name for d in self.documents}
		for req in get_settings().get("claim_required_documents") or []:
			if req.incident_type and req.incident_type != self.incident_type:
				continue
			if req.document_name in existing:
				continue
			self.append("documents", {"document_name": req.document_name, "mandatory": req.mandatory})
			existing.add(req.document_name)

	def _compute_excess(self):
		if not self.policy:
			self.excess = 0
		else:
			p = frappe.db.get_value("Insurance Policy", self.policy, ["excess_type", "excess_value", "minimum_excess"], as_dict=True)
			if p.excess_type == "Percent of Claim":
				self.excess = max(flt(self.amount_claimed) * flt(p.excess_value) / 100, flt(p.minimum_excess))
			else:
				self.excess = flt(p.excess_value)
			self.excess = flt(min(self.excess, flt(self.amount_claimed)), 2)
		self.net_claimable = flt(self.amount_claimed) - flt(self.excess)

	def _validate_amounts(self):
		if flt(self.amount_approved) > flt(self.amount_claimed):
			frappe.throw(_("Amount Approved cannot exceed Amount Claimed."))
		if flt(self.amount_approved) > flt(self.net_claimable) and flt(self.amount_approved):
			frappe.msgprint(
				_("Amount Approved is higher than the claim net of excess ({0}).").format(
					frappe.format(self.net_claimable, {"fieldtype": "Currency"})
				),
				indicator="orange",
				alert=True,
			)

	def _validate_transition(self):
		before = self.get_doc_before_save()
		prev = before.status if before else "Draft"
		if self.status != "Draft" and prev == "Draft":
			missing = [d.document_name for d in self.documents if d.mandatory and not d.attachment]
			if missing:
				frappe.throw(
					_("Attach the mandatory documents before recording the claim:<br>{0}").format("<br>".join(f"• {m}" for m in missing)),
					title=_("Documents Missing"),
				)
			self.lodged_on = self.lodged_on or nowdate()
		if self.status == "Under Assessment" and prev == "Recorded" and not flt(self.amount_approved):
			self.amount_approved = flt(self.net_claimable)

	def _compute_outstanding(self):
		if self.status in ("Rejected", "Cancelled"):
			self.outstanding_amount = 0
			return
		self.outstanding_amount = max(
			0, flt(self.amount_approved) - flt(self.amount_received) - flt(self.shortfall_amount)
		)

	def _default_credit_account(self):
		if self.loss_treatment == "Total Loss":
			return frappe.get_cached_value("Company", self.company, "disposal_account")
		return get_company_accounts(self.company).claim_recovery_income_account

	def _set_accounts(self):
		"""Asset Settings accounts are defaults; a claim may use different ones."""
		if not self.company:
			return
		accounts = get_company_accounts(self.company)
		self.recoverable_account = self.recoverable_account or accounts.claim_recoverable_account
		self.shortfall_account = self.shortfall_account or accounts.claim_shortfall_account
		before = self.get_doc_before_save()
		if not self.credit_account or (before and before.loss_treatment != self.loss_treatment
				and self.credit_account in (accounts.claim_recovery_income_account,
					frappe.get_cached_value("Company", self.company, "disposal_account"))):
			self.credit_account = self._default_credit_account()
		for field in ("recoverable_account", "credit_account", "shortfall_account"):
			account = self.get(field)
			if not account:
				continue
			acc = frappe.db.get_value("Account", account, ["company", "is_group", "account_type"], as_dict=True)
			label = self.meta.get_label(field)
			if not acc or acc.company != self.company or acc.is_group:
				frappe.throw(_("{0}: {1} is not a ledger account of {2}.").format(label, account, self.company))
			if field == "recoverable_account" and acc.account_type != "Payable":
				frappe.throw(_("{0} must be a Payable account so the insurer can be the party.").format(label))

	def _make_claim_journal_entry(self):
		accounts = get_company_accounts(self.company)
		recoverable = self.recoverable_account or accounts.claim_recoverable_account
		credit_account = self.credit_account or self._default_credit_account()
		if not recoverable or not credit_account:
			frappe.throw(_("Set the Claims Recoverable and Credit accounts on the claim's Accounting tab (or in Asset Settings)."))
		cc = self.cost_center or frappe.get_cached_value("Company", self.company, "cost_center")
		je = frappe.new_doc("Journal Entry")
		je.update({
			"voucher_type": "Journal Entry",
			"company": self.company,
			"posting_date": self.approved_on or nowdate(),
			"insurance_claim": self.name,
			"insurance_policy": self.policy,
			"user_remark": _("Insurance claim {0} approved – {1} ({2}), policy {3}").format(
				self.name, self.asset, self.loss_treatment, self.policy_number or self.policy
			),
		})
		je.append("accounts", {
			"account": recoverable,
			"party_type": "Supplier",
			"party": self.insurer,
			"debit_in_account_currency": flt(self.amount_approved),
			"cost_center": cc,
		})
		je.append("accounts", {
			"account": credit_account,
			"credit_in_account_currency": flt(self.amount_approved),
			"cost_center": cc,
		})
		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		return je


def update_settlement(claim_name):
	"""Recompute amount received / outstanding / status from submitted Payment Entries."""
	claim = frappe.get_doc("Insurance Claim", claim_name)
	if claim.docstatus != 1 or claim.status in ("Rejected", "Cancelled"):
		return
	received = flt(
		frappe.db.sql(
			"select sum(received_amount) from `tabPayment Entry` where insurance_claim=%s and docstatus=1",
			claim_name,
		)[0][0]
	)
	outstanding = max(0, flt(claim.amount_approved) - received - flt(claim.shortfall_amount))
	if claim.status == "Closed":
		status = "Closed"
	elif received and outstanding <= 0.005:
		status = "Settled"
	elif received:
		status = "Partly Settled"
	else:
		status = "Approved"
	claim.db_set({"amount_received": received, "outstanding_amount": outstanding, "status": status})


# ── Whitelisted actions ───────────────────────────────────────────────────────


@frappe.whitelist()
def make_payment_entry(claim):
	frappe.only_for(("Accounts User", "Accounts Manager", "System Manager"))
	doc = frappe.get_doc("Insurance Claim", claim)
	doc.check_permission("read")
	if doc.docstatus != 1 or doc.status not in ("Approved", "Partly Settled") or flt(doc.outstanding_amount) <= 0:
		frappe.throw(_("Nothing outstanding to receive on this claim."))
	recoverable = doc.recoverable_account or get_company_accounts(doc.company, ("claim_recoverable_account",)).claim_recoverable_account
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	bank = get_default_bank_cash_account(doc.company, "Bank") or get_default_bank_cash_account(doc.company, "Cash") or {}
	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	pe = frappe.new_doc("Payment Entry")
	pe.update({
		"payment_type": "Receive",
		"company": doc.company,
		"posting_date": nowdate(),
		"party_type": "Supplier",
		"party": doc.insurer,
		"paid_from": recoverable,
		"paid_from_account_currency": currency,
		"paid_to": bank.get("account"),
		"paid_to_account_currency": bank.get("account_currency") or currency,
		"paid_amount": flt(doc.outstanding_amount),
		"received_amount": flt(doc.outstanding_amount),
		"source_exchange_rate": 1,
		"target_exchange_rate": 1,
		"insurance_claim": doc.name,
		"cost_center": doc.cost_center,
		"remarks": _("Insurance claim settlement {0} – {1}").format(doc.name, doc.asset),
	})
	# Posted on-account (no reference row): ERPNext treats a debit-balance JE on a
	# Payable account as "fully paid", so it cannot be allocated against. The
	# insurer's party balance still nets to zero; reconcile via Payment Reconciliation.
	pe.setup_party_account_field()
	pe.set_missing_values()
	return pe


@frappe.whitelist()
def close_with_shortfall(claim, remarks=None):
	frappe.only_for(("Accounts Manager", "System Manager"))
	doc = frappe.get_doc("Insurance Claim", claim)
	doc.check_permission("write")
	if doc.docstatus != 1 or doc.status not in ("Approved", "Partly Settled") or flt(doc.outstanding_amount) <= 0:
		frappe.throw(_("Only approved claims with an outstanding balance can be closed with a shortfall."))
	defaults = get_company_accounts(doc.company)
	recoverable = doc.recoverable_account or defaults.claim_recoverable_account
	shortfall_account = doc.shortfall_account or defaults.claim_shortfall_account
	if not recoverable or not shortfall_account:
		frappe.throw(_("Set the Shortfall Account on the claim's Accounting tab (or in Asset Settings)."))
	amount = flt(doc.outstanding_amount)
	cc = doc.cost_center or frappe.get_cached_value("Company", doc.company, "cost_center")
	je = frappe.new_doc("Journal Entry")
	je.update({
		"voucher_type": "Journal Entry",
		"company": doc.company,
		"posting_date": nowdate(),
		"insurance_claim": doc.name,
		"user_remark": _("Insurance claim {0} closed with shortfall. {1}").format(doc.name, remarks or ""),
	})
	je.append("accounts", {"account": shortfall_account, "debit_in_account_currency": amount, "cost_center": cc})
	cr = {
		"account": recoverable,
		"party_type": "Supplier",
		"party": doc.insurer,
		"credit_in_account_currency": amount,
		"cost_center": cc,
	}
	# On-account like the receipts: ERPNext only allows a JE reference on the debit side of a Payable.
	je.append("accounts", cr)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	doc.db_set({
		"shortfall_journal_entry": je.name,
		"shortfall_amount": flt(doc.shortfall_amount) + amount,
		"outstanding_amount": 0,
		"status": "Closed",
	})
	return je.name


@frappe.whitelist()
def get_required_documents(incident_type):
	return [
		{"document_name": r.document_name, "mandatory": r.mandatory}
		for r in get_settings().get("claim_required_documents") or []
		if not r.incident_type or r.incident_type == incident_type
	]


@frappe.whitelist()
def resolve_policy(asset, incident_date, incident_type=None):
	frappe.has_permission("Asset", "read", asset, throw=True)
	rows = _sort_by_preference(get_covering_rows(asset, incident_date), incident_type)
	return rows[0].policy if rows else None


@frappe.whitelist()
def make_claim_from_check_sheet(check_sheet):
	from upande_asset.upande_asset.doctype.asset_check_sheet.asset_check_sheet import SECTIONS

	cs = frappe.get_doc("Asset Check Sheet", check_sheet)
	cs.check_permission("read")
	if cs.docstatus != 1:
		frappe.throw(_("Submit the check sheet first."))
	if not frappe.db.get_value("Asset", cs.asset, "insurance_required"):
		frappe.throw(_("Asset {0} is not flagged Insurance Required.").format(cs.asset))
	if cs.get("insurance_claim") and frappe.db.get_value("Insurance Claim", cs.insurance_claim, "docstatus") in (0, 1):
		frappe.throw(_("Claim {0} already exists for this check sheet.").format(cs.insurance_claim))

	items = [row for sec in SECTIONS for row in (cs.get(f"section_{sec}_items") or [])]
	serious = [r for r in items if r.severity in ("High", "Critical")]
	if not serious and cs.overall_status not in ("Needs Attention", "Under Repair"):
		frappe.throw(_("No High / Critical faults on this check sheet."))

	desc_lines = []
	for r in serious:
		bits = [frappe.utils.escape_html(r.check_factor or "")]
		if r.fault_description:
			bits.append(frappe.utils.escape_html(r.fault_description))
		if r.incident_percent is not None:
			bits.append(f"{flt(r.incident_percent)}%")
		desc_lines.append(f"<li><b>[{r.severity}]</b> {' — '.join(bits)}</li>")

	claim = frappe.new_doc("Insurance Claim")
	claim.update({
		"company": cs.company,
		"asset": cs.asset,
		"asset_check_sheet": cs.name,
		"asset_repair": cs.linked_asset_repair,
		"incident_date": cs.check_date,
		"reported_date": nowdate(),
		"incident_type": "Breakdown" if frappe.db.get_value("Insurance Incident Type", "Breakdown", "enabled") else None,
		"loss_treatment": "Repair",
		"description": f"<p>{_('Faults recorded on check sheet {0}:').format(cs.name)}</p><ul>{''.join(desc_lines)}</ul>"
		if desc_lines else "",
	})

	if cs.linked_asset_repair:
		r = frappe.db.get_value("Asset Repair", cs.linked_asset_repair, ["total_repair_cost", "repair_cost"], as_dict=True)
		if r:
			claim.amount_claimed = flt(r.total_repair_cost) or flt(r.repair_cost)

	policy = resolve_policy(cs.asset, cs.check_date, claim.incident_type)
	if policy:
		claim.policy = policy
		claim.flags.auto_policy_for = claim.incident_type
	for req in get_required_documents(claim.incident_type):
		claim.append("documents", req)
	return claim
