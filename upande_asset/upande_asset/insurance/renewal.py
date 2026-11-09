# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Renewal requests (draft renewal policy + Material Request / Purchase Order)."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_link_to_form, getdate

from upande_asset.upande_asset.insurance.premium import (
	has_open_procurement,
	make_material_request,
	make_purchase_order,
)
from upande_asset.upande_asset.insurance.utils import get_asset_book_value, get_settings, next_period

COPY_FIELDS = (
	"company", "insurer", "broker", "policy_type", "policy_number",
	"insurance_for", "cover_description", "cost_center",
	"excess_type", "excess_value", "minimum_excess", "basic_premium", "levies",
)


def _resolve_procurement(procurement):
	mode = procurement or get_settings().renewal_procurement or "Material Request"
	if mode == "Ask":
		frappe.throw(_("Choose Material Request or Purchase Order."))
	if mode not in ("Material Request", "Purchase Order"):
		frappe.throw(_("Invalid procurement type {0}.").format(mode))
	return mode


def _create_procurement(policy, mode, rate, schedule_date):
	active = [r for r in policy.insured_assets if r.row_status == "Active"]
	if (policy.get("insurance_for") or "Assets") == "Assets":
		scope = _("{0} asset(s)").format(len(active))
	else:
		scope = policy.get("cover_description") or _("non-asset cover")
	description = _("Insurance premium – {0} {1}, cover {2} to {3}, {4}").format(
		policy.policy_type,
		policy.policy_number or policy.name,
		frappe.format(policy.start_date, "Date"),
		frappe.format(policy.end_date, "Date"),
		scope,
	)
	if mode == "Purchase Order":
		doc = make_purchase_order(policy, rate=rate, description=description, schedule_date=schedule_date)
	else:
		doc = make_material_request(policy, rate=rate, description=description, schedule_date=schedule_date)
	frappe.db.set_value(
		"Insurance Policy", policy.name, {"procurement_doctype": doc.doctype, "procurement_name": doc.name}
	)
	return doc


def raise_renewal_request(policy, procurement=None):
	if policy.docstatus != 1 or policy.status not in ("Active", "Expiring", "Expired"):
		frappe.throw(_("Renewal can be raised for submitted Active / Expiring / Expired policies only."))
	if policy.renewed_to and frappe.db.get_value("Insurance Policy", policy.renewed_to, "docstatus") != 2:
		frappe.throw(_("Already renewed by {0}.").format(get_link_to_form("Insurance Policy", policy.renewed_to)))
	existing = frappe.db.get_value("Insurance Policy", {"renewed_from": policy.name, "docstatus": 0}, "name")
	if existing:
		frappe.throw(_("Draft renewal {0} already exists.").format(get_link_to_form("Insurance Policy", existing)))
	mode = _resolve_procurement(procurement)

	start, end = next_period(policy.end_date)
	new = frappe.new_doc("Insurance Policy")
	for f in COPY_FIELDS:
		new.set(f, policy.get(f))
	new.update({"start_date": start, "end_date": end, "renewed_from": policy.name, "is_opening": "No"})
	excluded = []
	if (policy.get("insurance_for") or "Assets") != "Assets":
		new.total_sum_insured = policy.total_sum_insured
	for r in policy.insured_assets:
		if r.row_status != "Active":
			continue
		a = frappe.db.get_value("Asset", r.asset, ["status", "insurance_required", "docstatus"], as_dict=True)
		if not a or a.docstatus != 1 or a.status in ("Scrapped", "Sold", "Cancelled") or not a.insurance_required:
			excluded.append(r.asset)
			continue
		new.append("insured_assets", {
			"asset": r.asset,
			"sum_insured": r.sum_insured,
			"book_value_at_inception": get_asset_book_value(r.asset),
			"row_status": "Active",
		})
	if (policy.get("insurance_for") or "Assets") == "Assets" and not new.insured_assets:
		frappe.throw(_("No eligible assets left to renew (all scrapped, sold or no longer flagged)."))
	new.flags.ignore_permissions = True
	new.insert()

	doc = _create_procurement(new, mode, policy.total_premium, policy.end_date)
	new.reload()
	return {
		"policy": new.name,
		"procurement_doctype": doc.doctype,
		"procurement_name": doc.name,
		"excluded": excluded,
	}


def raise_purchase_request(policy, procurement=None):
	"""New cover with no history: draft policy → MR / PO only."""
	if policy.docstatus != 0:
		frappe.throw(_("Purchase requests are raised from draft policies; use Raise Renewal Request on a live policy."))
	if has_open_procurement(policy):
		frappe.throw(_("{0} {1} is already open.").format(policy.procurement_doctype, policy.procurement_name))
	if policy.is_new():
		frappe.throw(_("Save the policy first."))
	mode = _resolve_procurement(procurement)
	rate = flt(policy.total_premium) or flt(policy.basic_premium)
	doc = _create_procurement(policy, mode, rate, add_days(getdate(policy.start_date), -1))
	return {"policy": policy.name, "procurement_doctype": doc.doctype, "procurement_name": doc.name}
