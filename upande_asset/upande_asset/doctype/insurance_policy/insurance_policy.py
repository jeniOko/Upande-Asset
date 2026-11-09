# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, flt, fmt_money, get_link_to_form, getdate, today

from upande_asset.upande_asset.insurance.asset import sync_policy_assets
from upande_asset.upande_asset.insurance.utils import get_asset_book_value, get_expiring_threshold

ACCOUNTS_WRITE_ROLES = ("Accounts User", "Accounts Manager", "System Manager")
ACCOUNTS_MANAGER_ROLES = ("Accounts Manager", "System Manager")


def is_asset_policy(doc):
	"""Asset policies list insured assets; "Other" policies (medical, WIBA, liability,
	goods in transit…) carry a sum insured and cost center on the policy itself."""
	return (doc.get("insurance_for") or "Assets") == "Assets"


def row_cover_window(row, start_date, end_date):
	"""(from, to) dates a policy row actually covers, or None.
	A removed row covers up to the day before its removed_on date (same rule as
	get_covering_rows / the register reports)."""
	start = max(getdate(row.added_on or start_date), getdate(start_date))
	if row.row_status == "Removed":
		if not row.removed_on:
			return None
		end = min(add_days(getdate(row.removed_on), -1), getdate(end_date))
	else:
		end = getdate(end_date)
	return (start, end) if start <= end else None


class InsurancePolicy(Document):
	# Release-schedule JEs (insurance_policy) and premium PI rows link back; their
	# own cancellation is guarded in before_cancel.
	ignore_linked_doctypes = ("GL Entry", "Payment Ledger Entry")

	def validate(self):
		self._set_defaults()
		self._set_party_details()
		self.set("renewal_links", renewal_link_rows(self.name, self.renewed_from))
		self._validate_dates()
		self._validate_policy_number()
		self._validate_rows()
		self._calculate_totals()
		self._validate_policy_type()
		self._validate_overlap()
		self._warn_under_insured()
		if self.docstatus == 0:
			self.status = "Draft"

	def before_update_after_submit(self):
		self.set("renewal_links", renewal_link_rows(self.name, self.renewed_from))
		self._validate_rows()
		self._calculate_totals()
		self._validate_overlap()

	def before_submit(self):
		if not self.insurer:
			frappe.throw(_("Insurer is required before submitting the policy."), title=_("Missing Insurer"))
		if is_asset_policy(self) and not [r for r in self.insured_assets if r.row_status == "Active"]:
			frappe.throw(_("Add at least one insured asset."))
		self.sum_insured_at_inception = self.total_sum_insured
		for r in self.insured_assets:
			r.added_on = r.added_on or self.start_date

	def after_insert(self):
		if self.renewed_from:
			write_renewal_links(self.renewed_from)
			refresh_status(self.renewed_from)  # renewal raised: no longer just "Expiring"

	def after_delete(self):
		if self.renewed_from:
			refresh_status(self.renewed_from)

	def on_trash(self):
		# The MR / PO raised for this (draft) policy points back at it on its item
		# rows, so neither could be deleted first. Remove our own draft with it.
		if self.procurement_doctype and self.procurement_name and frappe.db.exists(self.procurement_doctype, self.procurement_name):
			if frappe.db.get_value(self.procurement_doctype, self.procurement_name, "docstatus") == 1:
				frappe.throw(
					_("Cancel {0} {1} before deleting this policy.").format(
						self.procurement_doctype, get_link_to_form(self.procurement_doctype, self.procurement_name)
					)
				)
			frappe.delete_doc(self.procurement_doctype, self.procurement_name, ignore_permissions=True, force=True)
		if self.renewed_from:
			write_renewal_links(self.renewed_from, exclude=self.name)

	def on_submit(self):
		self.db_set("status", compute_status(self), update_modified=False)
		sync_policy_assets(self)
		if self.renewed_from:
			frappe.db.set_value("Insurance Policy", self.renewed_from, "renewed_to", self.name)
			write_renewal_links(self.renewed_from)
			old = frappe.get_doc("Insurance Policy", self.renewed_from)
			old.db_set("status", compute_status(old), update_modified=False)
			sync_policy_assets(old)

	def on_update_after_submit(self):
		sync_policy_assets(self)

	def before_cancel(self):
		from upande_asset.upande_asset.insurance.premium import linked_documents

		blockers = [
			get_link_to_form("Purchase Invoice", name)
			for name in linked_documents("Purchase Invoice", self.name, docstatus=1)
		]
		for dt, field in (("Insurance Claim", "policy"), ("Journal Entry", "insurance_policy")):
			for name in frappe.get_all(dt, filters={field: self.name, "docstatus": 1}, pluck="name"):
				blockers.append(get_link_to_form(dt, name))
		if blockers:
			frappe.throw(
				_("Cancel these submitted documents first:<br>{0}").format("<br>".join(blockers)),
				title=_("Linked documents exist"),
			)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		sync_policy_assets(self)
		if self.renewed_from:
			old = frappe.get_doc("Insurance Policy", self.renewed_from)
			if old.renewed_to == self.name:
				old.db_set("renewed_to", None)
			old.db_set("status", compute_status(old))
			sync_policy_assets(old)
			write_renewal_links(self.renewed_from, exclude=self.name)

	# ── helpers ────────────────────────────────────────────────────────────

	def _validate_policy_type(self):
		if self.docstatus != 0 or not self.policy_type or self.renewed_from:
			return
		before = self.get_doc_before_save()
		if (self.is_new() or (before and before.policy_type != self.policy_type)) and not frappe.db.get_value(
				"Insurance Policy Type", self.policy_type, "enabled"):
			frappe.throw(_("Policy Type {0} is disabled. Enable it in Insurance Policy Type, or choose another.").format(
				frappe.bold(self.policy_type)))

	def _set_defaults(self):
		self.total_premium = flt(self.basic_premium) + flt(self.levies)
		self.is_opening = self.is_opening or "No"
		self.insurance_for = self.insurance_for or "Assets"
		if is_asset_policy(self):
			self.cost_center = None
		elif self.insured_assets:
			frappe.throw(_("Remove the insured assets, or set Insurance For to Assets."))
		if self.is_opening != "Yes":
			self.opening_journal_entry = None

	def _set_party_details(self):
		"""Insurer address / contact, company billing address and terms – the same
		defaults ERPNext applies on a Purchase Order."""
		from frappe.contacts.doctype.address.address import get_address_display, get_default_address
		from frappe.contacts.doctype.contact.contact import get_default_contact

		if self.insurer:
			self.supplier_address = self.supplier_address or get_default_address("Supplier", self.insurer)
			self.contact_person = self.contact_person or get_default_contact("Supplier", self.insurer)
		if self.company and not self.billing_address:
			self.billing_address = get_default_address("Company", self.company)
		for link, display in (
			("supplier_address", "address_display"),
			("dispatch_address", "dispatch_address_display"),
			("shipping_address", "shipping_address_display"),
			("billing_address", "billing_address_display"),
		):
			self.set(display, get_address_display(self.get(link)) if self.get(link) else None)
		contact = (
			frappe.db.get_value("Contact", self.contact_person, ["full_name", "mobile_no", "phone", "email_id"], as_dict=True)
			if self.contact_person
			else None
		) or {}
		self.contact_display = contact.get("full_name")
		self.contact_mobile = contact.get("mobile_no") or contact.get("phone")
		self.contact_email = contact.get("email_id")
		if self.tc_name and not self.terms:
			from erpnext.setup.doctype.terms_and_conditions.terms_and_conditions import get_terms_and_conditions

			self.terms = get_terms_and_conditions(self.tc_name, self.as_dict())

	def _validate_dates(self):
		if self.start_date and self.end_date and getdate(self.end_date) <= getdate(self.start_date):
			frappe.throw(_("End Date must be after Start Date."))

	def _validate_policy_number(self):
		if not self.policy_number or not self.insurer:
			return
		dup = frappe.db.get_value(
			"Insurance Policy",
			{
				"policy_number": self.policy_number,
				"insurer": self.insurer,
				"docstatus": ["!=", 2],
				"name": ["!=", self.name],
				# Insurers often keep the number on renewal, so uniqueness is per
				# insurer within overlapping periods.
				"start_date": ["<=", self.end_date],
				"end_date": [">=", self.start_date],
			},
		)
		if dup:
			frappe.throw(
				_("Policy number {0} already exists for insurer {1} in {2}.").format(
					frappe.bold(self.policy_number), self.insurer, get_link_to_form("Insurance Policy", dup)
				)
			)

	def _validate_rows(self):
		seen = set()
		for row in self.insured_assets:
			asset = frappe.db.get_value(
				"Asset",
				row.asset,
				["docstatus", "insurance_required", "company", "asset_name", "asset_category", "cost_center", "status"],
				as_dict=True,
			)
			if not asset:
				frappe.throw(_("Row {0}: Asset {1} not found.").format(row.idx, row.asset))
			row.asset_name = asset.asset_name
			row.asset_category = asset.asset_category
			row.cost_center = asset.cost_center
			row.row_status = row.row_status or "Active"
			if row.row_status != "Active":
				continue
			if asset.docstatus != 1:
				frappe.throw(_("Row {0}: Asset {1} must be submitted.").format(row.idx, row.asset))
			if not asset.insurance_required:
				frappe.throw(_("Row {0}: Asset {1} is not flagged Insurance Required.").format(row.idx, row.asset))
			if asset.company != self.company:
				frappe.throw(_("Row {0}: Asset {1} belongs to company {2}.").format(row.idx, row.asset, asset.company))
			if row.asset in seen:
				frappe.throw(_("Row {0}: Asset {1} is listed more than once.").format(row.idx, row.asset))
			seen.add(row.asset)
			if self.docstatus == 0 or row.is_new() or not row.book_value_at_inception:
				row.book_value_at_inception = get_asset_book_value(row.asset)

	def _calculate_totals(self):
		if is_asset_policy(self):
			self.total_sum_insured = sum(flt(r.sum_insured) for r in self.insured_assets if r.row_status == "Active")

	def _validate_overlap(self):
		"""Block two policies of the same type covering one asset on the same day.

		Compares each row's real cover window (added_on → day before removed_on,
		within the policy dates) rather than the row's current status, so an asset
		endorsed off an old policy from 1 Dec cannot start new cover on 1 Nov."""
		mine = {}
		for r in self.insured_assets:
			window = row_cover_window(r, self.start_date, self.end_date)
			if window:
				mine.setdefault(r.asset, []).append(window)
		if not mine:
			return
		pol = frappe.qb.DocType("Insurance Policy")
		row = frappe.qb.DocType("Insurance Policy Asset")
		others = (
			frappe.qb.from_(row)
			.join(pol)
			.on(pol.name == row.parent)
			.select(row.asset, row.row_status, row.added_on, row.removed_on, pol.name, pol.start_date, pol.end_date)
			.where(row.parenttype == "Insurance Policy")
			.where(row.asset.isin(list(mine)))
			.where(pol.docstatus == 1)
			.where(pol.name != self.name)
			.where(pol.policy_type == self.policy_type)
			.where(pol.start_date <= self.end_date)
			.where(pol.end_date >= self.start_date)
			.run(as_dict=True)
		)
		clashes = []
		for o in others:
			theirs = row_cover_window(o, o.start_date, o.end_date)
			if not theirs:
				continue
			for start, end in mine[o.asset]:
				lo, hi = max(start, theirs[0]), min(end, theirs[1])
				if lo <= hi:
					clashes.append((o, lo, hi))
					break
		if clashes:
			lines = "<br>".join(
				_("{0} is already covered by {1} from {2} to {3}").format(
					o.asset, get_link_to_form("Insurance Policy", o.name), frappe.format(lo, "Date"), frappe.format(hi, "Date")
				)
				for o, lo, hi in clashes
			)
			frappe.throw(
				_("Overlapping {0} cover:<br>{1}<br>Endorse the asset off the other policy (Remove Assets) with an effective date on or before this cover starts.").format(
					self.policy_type, lines
				),
				title=_("Overlapping Cover"),
			)

	def _warn_under_insured(self):
		under = [
			r for r in self.insured_assets
			if r.row_status == "Active" and flt(r.sum_insured) < flt(r.book_value_at_inception)
		]
		if under:
			frappe.msgprint(
				_("These assets are insured below their current book value:<br>{0}").format(
					"<br>".join(
						f"{r.asset} – {fmt_money(r.sum_insured, currency=frappe.get_cached_value("Company", self.company, "default_currency"))} vs {fmt_money(r.book_value_at_inception, currency=frappe.get_cached_value("Company", self.company, "default_currency"))}"
						for r in under
					)
				),
				title=_("Under-insured assets"),
				indicator="orange",
			)


def renewal_link_rows(name, renewed_from, exclude=None):
	"""Rows for the hidden renewal_links table that drives the Connections tab:
	the policy this one was renewed from, and every (non-cancelled) renewal of it."""
	rows = [{"relation": "Renewed From", "policy": renewed_from}] if renewed_from else []
	if name and not str(name).startswith("new-"):
		filters = {"renewed_from": name, "docstatus": ["!=", 2]}
		if exclude:
			filters["name"] = ["!=", exclude]
		for p in frappe.get_all("Insurance Policy", filters=filters, pluck="name", order_by="creation"):
			rows.append({"relation": "Renewed To", "policy": p})
	return rows


def write_renewal_links(name, exclude=None):
	"""Rewrite another policy's renewal_links rows directly (it may be submitted)."""
	if not name or not frappe.db.exists("Insurance Policy", name):
		return
	renewed_from = frappe.db.get_value("Insurance Policy", name, "renewed_from")
	frappe.db.delete("Insurance Policy Renewal Link", {"parent": name, "parenttype": "Insurance Policy"})
	for idx, row in enumerate(renewal_link_rows(name, renewed_from, exclude), start=1):
		frappe.get_doc({
			"doctype": "Insurance Policy Renewal Link",
			"parent": name,
			"parenttype": "Insurance Policy",
			"parentfield": "renewal_links",
			"idx": idx,
			**row,
		}).db_insert()


def premium_complete(doc):
	"""Has this policy's premium been settled enough to count as renewed? Asset Settings →
	"Renewal is complete when" decides: the premium invoice submitted, or fully paid.
	Opening policies and policies without a premium need no invoice."""
	if doc.get("is_opening") in (1, "1", "Yes") or not flt(doc.get("total_premium")):
		return True
	pi = doc.get("purchase_invoice")
	if not pi:
		return False
	inv = frappe.db.get_value("Purchase Invoice", pi, ["docstatus", "outstanding_amount"], as_dict=True)
	if not inv or inv.docstatus != 1:
		return False
	from upande_asset.upande_asset.insurance.utils import get_settings

	if (get_settings().renewal_complete_on or RENEWAL_ON_SUBMIT) == RENEWAL_ON_PAID:
		return flt(inv.outstanding_amount) <= 0
	return True


RENEWAL_ON_SUBMIT = "Premium Invoice Submitted"
RENEWAL_ON_PAID = "Premium Invoice Paid"
_RENEWAL_FIELDS = ["name", "docstatus", "purchase_invoice", "is_opening", "total_premium"]


def renewal_of(name):
	"""The renewal raised from policy `name` (a submitted one first), or None."""
	rows = frappe.get_all("Insurance Policy", filters={"renewed_from": name, "docstatus": ["<", 2]},
		fields=_RENEWAL_FIELDS, order_by="docstatus desc, creation desc", limit=1)
	return rows[0] if rows else None


def compute_status(doc):
	"""Draft → Active → Expiring → Expired, with the renewal states in between:

	- the old policy is *Renewal In Progress* once a renewal is raised (or submitted but its
	  premium not yet settled), and *Renewed* only once the renewal is complete;
	- a submitted renewal is *Awaiting Premium* until its premium is settled."""
	if doc.docstatus == 0:
		return "Draft"
	if doc.docstatus == 2 or doc.status == "Cancelled":
		return "Cancelled"
	renewal = renewal_of(doc.name) if doc.get("name") else None
	if renewal and renewal.docstatus == 1:
		return "Renewed" if premium_complete(renewal) else "Renewal In Progress"
	end = getdate(doc.end_date)
	if getdate(today()) > end:
		return "Expired"  # a renewal still in draft does not keep the old cover alive
	if doc.get("renewed_from") and not premium_complete(doc):
		return "Awaiting Premium"
	if renewal:
		return "Renewal In Progress"
	if date_diff(end, today()) <= get_expiring_threshold():
		return "Expiring"
	return "Active"


def refresh_status(name):
	"""Recompute a submitted policy's status (and mirror it on its assets when it changes)."""
	if not name or not frappe.db.exists("Insurance Policy", name):
		return
	doc = frappe.get_doc("Insurance Policy", name)
	if doc.docstatus != 1:
		return
	new = compute_status(doc)
	if new != doc.status:
		doc.db_set("status", new, update_modified=False)
		sync_policy_assets(doc)


def refresh_renewal_chain(name):
	"""A policy and the one it renews: both statuses depend on the renewal's progress."""
	refresh_status(name)
	refresh_status(frappe.db.get_value("Insurance Policy", name, "renewed_from"))


# ── Whitelisted actions ───────────────────────────────────────────────────────


def _get_policy_for_action(name, ptype="write"):
	doc = frappe.get_doc("Insurance Policy", name)
	doc.check_permission(ptype)
	return doc


@frappe.whitelist()
def make_premium_invoice(policy):
	frappe.only_for(ACCOUNTS_WRITE_ROLES)
	from upande_asset.upande_asset.insurance.premium import make_premium_invoice as _make

	return _make(_get_policy_for_action(policy)).as_dict()


@frappe.whitelist()
def get_endorsement_preview(policy, action, payload):
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance.endorsement import preview

	return preview(_get_policy_for_action(policy, "read"), action, frappe.parse_json(payload))


@frappe.whitelist()
def endorse(policy, action, payload):
	"""Endorsement request. With the insurer's Endorsement No already in hand, it is confirmed at once."""
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance import endorsement

	payload = frappe.parse_json(payload)
	doc = _get_policy_for_action(policy)
	if (payload.get("endorsement_no") or "").strip():
		return endorsement.endorse(doc, action, payload)
	return endorsement.request(doc, action, payload)


@frappe.whitelist()
def confirm_endorsement(policy, endorsement_group, payload):
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance.endorsement import confirm

	return confirm(_get_policy_for_action(policy), endorsement_group, frappe.parse_json(payload))


@frappe.whitelist()
def withdraw_endorsement(policy, endorsement_group, reason=None):
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance.endorsement import withdraw

	return withdraw(_get_policy_for_action(policy), endorsement_group, reason)


@frappe.whitelist()
def reverse_endorsement(policy, endorsement_group):
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance.endorsement import reverse

	return reverse(_get_policy_for_action(policy), endorsement_group)


@frappe.whitelist()
def make_endorsement_document(policy, endorsement_group, procurement=None):
	"""Unsaved MR / PO (extra premium) or Journal Entry (returned premium) for an endorsement."""
	frappe.only_for(ACCOUNTS_MANAGER_ROLES)
	from upande_asset.upande_asset.insurance.premium import make_endorsement_document as _make

	return _make(_get_policy_for_action(policy), endorsement_group, procurement).as_dict()


@frappe.whitelist()
def raise_renewal_request(policy, procurement=None):
	frappe.only_for(ACCOUNTS_WRITE_ROLES)
	from upande_asset.upande_asset.insurance.renewal import raise_renewal_request as _raise

	return _as_dict(_raise(_get_policy_for_action(policy), procurement))


def _as_dict(result):
	result["doc"] = result["doc"].as_dict()
	return result


@frappe.whitelist()
def raise_purchase_request(policy, procurement=None):
	frappe.only_for(ACCOUNTS_WRITE_ROLES)
	from upande_asset.upande_asset.insurance.renewal import raise_purchase_request as _raise

	return _as_dict(_raise(_get_policy_for_action(policy), procurement))


@frappe.whitelist()
def get_policy_context(policy):
	"""Small payload the form uses to decide which buttons to show."""
	doc = _get_policy_for_action(policy, "read")
	from upande_asset.upande_asset.insurance.premium import (
		draft_premium_invoice,
		endorsement_has_document,
		has_open_procurement,
	)
	from upande_asset.upande_asset.insurance.utils import get_settings

	draft_renewal = frappe.db.get_value("Insurance Policy", {"renewed_from": doc.name, "docstatus": 0}, "name")
	pi_docstatus = frappe.db.get_value("Purchase Invoice", doc.purchase_invoice, "docstatus") if doc.purchase_invoice else None
	draft_premium_pi = draft_premium_invoice(doc)
	# Endorsements by group: requests waiting for the insurer, and premium documents not raised yet
	# (extra premium: from the request on; returned premium: once confirmed).
	groups = {}
	for e in doc.get("endorsements") or []:
		if e.status in ("Requested", "Active") and e.endorsement_group:
			g = groups.setdefault(e.endorsement_group, {"group": e.endorsement_group, "status": e.status, "total": 0, "assets": [],
				"type": e.endorsement_type, "effective_date": e.effective_date, "endorsement_no": e.endorsement_no})
			g["total"] = flt(g["total"] + flt(e.premium_adjustment), 2)
			if e.asset:
				g["assets"].append(e.asset)
	for g in groups.values():
		g["label"] = " – ".join(filter(None, [g["endorsement_no"] or _("Request"), _(g["type"]), ", ".join(g["assets"])]))
	unraised = [
		g for g in groups.values()
		if g["total"] and (g["total"] > 0 or g["status"] == "Active") and not endorsement_has_document(doc, g["group"])
	] if doc.docstatus == 1 else []
	return {
		"endorsements_without_document": unraised,
		"pending_endorsements": [g for g in groups.values() if g["status"] == "Requested"],
		"open_procurement": has_open_procurement(doc),
		"draft_renewal": draft_renewal,
		"premium_invoiced": bool(pi_docstatus in (0, 1)) or bool(draft_premium_pi),
		"draft_premium_pi": draft_premium_pi,
		"renewal_procurement": get_settings().renewal_procurement or "Material Request",
		"is_accounts_manager": bool(set(frappe.get_roles()) & set(ACCOUNTS_MANAGER_ROLES)),
		"is_accounts_user": bool(set(frappe.get_roles()) & set(ACCOUNTS_WRITE_ROLES)),
	}


@frappe.whitelist()
def get_insurable_assets(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the insured_assets.asset field."""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	asset = frappe.qb.DocType("Asset")
	q = (
		frappe.qb.from_(asset)
		.select(asset.name, asset.asset_name, asset.asset_category)
		.where(asset.docstatus == 1)
		.where(asset.insurance_required == 1)
		.where(asset.status.notin(["Scrapped", "Sold", "Cancelled"]))
		.where((asset.name.like(f"%{txt}%")) | (asset.asset_name.like(f"%{txt}%")))
		.orderby(asset.name)
		.limit(page_len)
		.offset(start)
	)
	if filters.get("company"):
		q = q.where(asset.company == filters["company"])
	if filters.get("exclude"):
		q = q.where(asset.name.notin(filters["exclude"]))
	return q.run()


@frappe.whitelist()
def get_insurer_details(insurer):
	"""Default address / contact of an insurer for the Address & Contact tab."""
	from frappe.contacts.doctype.address.address import get_address_display, get_default_address
	from frappe.contacts.doctype.contact.contact import get_default_contact

	frappe.has_permission("Supplier", "read", insurer, throw=True)
	address = get_default_address("Supplier", insurer)
	contact = get_default_contact("Supplier", insurer)
	c = frappe.db.get_value("Contact", contact, ["full_name", "mobile_no", "phone", "email_id"], as_dict=True) if contact else {}
	c = c or {}
	return {
		"supplier_address": address,
		"address_display": get_address_display(address) if address else None,
		"contact_person": contact,
		"contact_display": c.get("full_name"),
		"contact_mobile": c.get("mobile_no") or c.get("phone"),
		"contact_email": c.get("email_id"),
	}
