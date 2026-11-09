# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Premium documents: premium Purchase Invoice, endorsement invoices / debit notes,
renewal Material Requests / Purchase Orders, and the PI / PO doc_events."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from upande_asset.upande_asset.insurance.utils import (
	cost_center_weights,
	get_company_accounts,
	get_premium_item,
	get_settings,
	split_amount,
)

PREMIUM_ACCOUNTS = ("prepaid_insurance_account", "insurance_expense_account")
ITEM_TABLES = {
	"Purchase Invoice": "Purchase Invoice Item",
	"Purchase Order": "Purchase Order Item",
	"Material Request": "Material Request Item",
}


def policies_on(doc):
	"""Insurance Policies referenced by the item rows of a PI / PO / MR (in row order)."""
	return list(dict.fromkeys(d.insurance_policy for d in doc.get("items") or [] if d.get("insurance_policy")))


def linked_documents(doctype, policy, **filters):
	"""Names of `doctype` documents with at least one item row linked to `policy`."""
	parent = frappe.qb.DocType(doctype)
	child = frappe.qb.DocType(ITEM_TABLES[doctype])
	q = (
		frappe.qb.from_(parent)
		.join(child)
		.on(child.parent == parent.name)
		.select(parent.name)
		.distinct()
		.where(child.parenttype == doctype)
		.where(child.insurance_policy == policy)
		.orderby(parent.creation)
	)
	for field, value in filters.items():
		if isinstance(value, (list, tuple)):
			q = q.where(parent[field].isin(list(value)))
		else:
			q = q.where(parent[field] == value)
	return q.run(pluck=True)


def _item_defaults(item_code):
	return frappe.get_cached_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True)


def _premium_line(item_code, rate, accounts, start, end, cost_center, description, qty=1):
	item = _item_defaults(item_code)
	return {
		"item_code": item_code,
		"item_name": item.item_name,
		"description": description,
		"qty": qty,
		"uom": item.stock_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": 1,
		"rate": flt(rate, 2),
		"price_list_rate": flt(rate, 2),
		"expense_account": accounts.insurance_expense_account,
		"cost_center": cost_center,
		"enable_deferred_expense": 1,
		"deferred_expense_account": accounts.prepaid_insurance_account,
		"service_start_date": start,
		"service_end_date": end,
	}


def _new_purchase_invoice(policy, lines, is_return=0, return_against=None):
	# Cover that has already ended (e.g. last year's policy captured late) cannot be
	# posted today: ERPNext needs the service end date on or after the posting date.
	posting_date = getdate(nowdate())
	ends = [getdate(line["service_end_date"]) for line in lines if line.get("service_end_date")]
	if ends and min(ends) < posting_date:
		posting_date = min(getdate(line["service_start_date"]) for line in lines if line.get("service_start_date"))
	pi = frappe.new_doc("Purchase Invoice")
	pi.update(
		{
			"supplier": policy.insurer,
			"company": policy.company,
			"posting_date": posting_date,
			"set_posting_time": 1 if posting_date != getdate(nowdate()) else 0,
			"currency": frappe.get_cached_value("Company", policy.company, "default_currency"),
			"ignore_pricing_rule": 1,
			"is_return": is_return,
			"return_against": return_against,
			"update_stock": 0,
		}
	)
	for line in lines:
		line["insurance_policy"] = policy.name
		pi.append("items", line)
	pi.set_missing_values()
	# set_missing_values may refill accounts/rates from item defaults – reassert ours.
	for row, line in zip(pi.items, lines, strict=True):
		row.update(line)
	pi.calculate_taxes_and_totals()
	pi.flags.ignore_permissions = True
	pi.insert()
	return pi


def build_premium_lines(policy, accounts, item_code):
	"""Basic premium (single line, or one per cost center pro-rata by active sum
	insured) + one levies line. Lines always sum exactly to total_premium."""
	start, end = policy.start_date, policy.end_date
	# Non-asset policies carry their own cost center; asset policies fall back to the company's.
	default_cc = policy.get("cost_center") or frappe.get_cached_value("Company", policy.company, "cost_center")
	ref = f"{policy.policy_number or policy.name} ({frappe.format(start, 'Date')} – {frappe.format(end, 'Date')})"
	lines = []
	if get_settings().split_premium_by_cost_center:
		weights = cost_center_weights(policy)
		parts = split_amount(policy.basic_premium, [w for _cc, w in weights])
		for (cc, _w), amount in zip(weights, parts, strict=True):
			lines.append(_premium_line(item_code, amount, accounts, start, end, cc, _("Basic premium {0} – {1}").format(ref, cc)))
	else:
		lines.append(_premium_line(item_code, policy.basic_premium, accounts, start, end, default_cc, _("Basic premium {0}").format(ref)))
	if flt(policy.levies):
		lines.append(_premium_line(item_code, policy.levies, accounts, start, end, default_cc, _("Levies & stamp duty {0}").format(ref)))
	return [ln for ln in lines if flt(ln["rate"])]


def make_premium_invoice(policy):
	if policy.docstatus != 1 or policy.is_opening == "Yes":
		frappe.throw(_("Premium invoices are only raised for submitted, non-opening policies."))
	if policy.purchase_invoice and frappe.db.get_value("Purchase Invoice", policy.purchase_invoice, "docstatus") != 2:
		frappe.throw(_("Premium invoice {0} already exists.").format(policy.purchase_invoice))
	open_pi = draft_premium_invoice(policy)
	if open_pi:
		frappe.throw(_("Draft premium invoice {0} already exists.").format(open_pi))
	if has_open_procurement(policy):
		frappe.throw(
			_("Premium is being procured via {0} {1}; raise the invoice from it.").format(
				policy.procurement_doctype, policy.procurement_name
			)
		)
	if not policy.insurer:
		frappe.throw(_("Set the Insurer first."))
	accounts = get_company_accounts(policy.company, PREMIUM_ACCOUNTS)
	item_code = get_premium_item()
	lines = build_premium_lines(policy, accounts, item_code)
	if not lines:
		frappe.throw(_("Total premium is zero."))
	return _new_purchase_invoice(policy, lines)


def draft_premium_invoice(policy):
	"""Draft, non-return PI for the policy that is not an endorsement document."""
	for name in linked_documents("Purchase Invoice", policy.name, docstatus=0, is_return=0):
		if not _is_endorsement_doc("Purchase Invoice", name, policy):
			return name
	return None


def has_open_procurement(policy):
	if not policy.procurement_doctype or not policy.procurement_name:
		return False
	return frappe.db.get_value(policy.procurement_doctype, policy.procurement_name, "docstatus") in (0, 1)


def make_endorsement_document(policy, endorsement_type, effective_date, asset_lines, endorsement_no):
	"""asset_lines: [(asset, cost_center, amount)] – positive → PI/PO, negative → debit note.
	Returns (doctype, name) or (None, None) when the net adjustment is zero."""
	total = flt(sum(flt(a) for _x, _cc, a in asset_lines), 2)
	if not total:
		return None, None
	accounts = get_company_accounts(policy.company, PREMIUM_ACCOUNTS)
	item_code = get_premium_item()
	default_cc = frappe.get_cached_value("Company", policy.company, "cost_center")
	start, end = getdate(effective_date), getdate(policy.end_date)
	sign = 1 if total > 0 else -1
	lines = []
	for asset, cc, amount in asset_lines:
		if not flt(amount):
			continue
		desc = _("Endorsement {0} ({1}) on {2}{3}, {4} – {5}").format(
			endorsement_no,
			_(endorsement_type),
			policy.policy_number or policy.name,
			f" – {asset}" if asset else "",
			frappe.format(start, "Date"),
			frappe.format(end, "Date"),
		)
		line = _premium_line(item_code, abs(flt(amount)), accounts, start, end, cc or default_cc, desc, qty=sign)
		lines.append(line)

	settings = get_settings()
	if sign > 0 and settings.renewal_procurement == "Purchase Order" and settings.endorsements_via_po:
		po = make_purchase_order(policy, lines, schedule_date=start)
		return "Purchase Order", po.name
	if sign < 0:
		return_against = None
		if policy.purchase_invoice and frappe.db.get_value("Purchase Invoice", policy.purchase_invoice, "docstatus") == 1:
			return_against = policy.purchase_invoice
		pi = _new_purchase_invoice(policy, lines, is_return=1, return_against=return_against)
		return "Purchase Invoice", pi.name
	pi = _new_purchase_invoice(policy, lines)
	return "Purchase Invoice", pi.name


def _procurement_line(item_code, rate, description, schedule_date, cost_center=None, expense_account=None):
	item = _item_defaults(item_code)
	line = {
		"item_code": item_code,
		"item_name": item.item_name,
		"description": description,
		"qty": 1,
		"uom": item.stock_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": 1,
		"rate": flt(rate, 2),
		"schedule_date": schedule_date,
	}
	if cost_center:
		line["cost_center"] = cost_center
	if expense_account:
		line["expense_account"] = expense_account
	return line


def make_purchase_order(policy, lines=None, schedule_date=None, rate=None, description=None):
	if not policy.insurer:
		frappe.throw(_("A Purchase Order needs a supplier – set the Insurer on {0} or use a Material Request.").format(policy.name))
	accounts = get_company_accounts(policy.company)
	item_code = get_premium_item()
	schedule_date = getdate(schedule_date or nowdate())
	if schedule_date < getdate(nowdate()):
		schedule_date = getdate(nowdate())
	po = frappe.new_doc("Purchase Order")
	po.update(
		{
			"supplier": policy.insurer,
			"company": policy.company,
			"transaction_date": nowdate(),
			"schedule_date": schedule_date,
			"currency": frappe.get_cached_value("Company", policy.company, "default_currency"),
			"ignore_pricing_rule": 1,
		}
	)
	if lines:
		for ln in lines:
			po.append(
				"items",
				_procurement_line(item_code, ln["rate"], ln["description"], schedule_date, ln.get("cost_center"), ln.get("expense_account")),
			)
	else:
		po.append(
			"items",
			_procurement_line(item_code, rate, description, schedule_date, expense_account=accounts.insurance_expense_account),
		)
	for d in po.items:
		d.insurance_policy = policy.name
	wanted = [flt(d.rate, 2) for d in po.items]
	po.set_missing_values()
	_reassert_rates(po, wanted)
	po.flags.ignore_permissions = True
	po.insert()
	_finalise(po, "purchase_order_status")
	return po


def _finalise(doc, setting):
	"""Submit the new MR / PO when Settings say so – unless an approval Workflow
	owns that doctype, in which case the workflow decides and it stays Draft."""
	if (get_settings().get(setting) or "Draft") != "Submitted":
		return
	from frappe.model.workflow import get_workflow_name

	if get_workflow_name(doc.doctype):
		frappe.msgprint(
			_("{0} {1} was left in Draft: its approval workflow controls submission.").format(_(doc.doctype), doc.name),
			alert=True,
		)
		return
	doc.flags.ignore_permissions = True
	doc.submit()


def _reassert_rates(doc, wanted, persist=False):
	"""A default buying price list (and the Item Price ERPNext auto-creates on
	purchase) would otherwise replace the policy premium with the last price paid."""
	changed = False
	for d, rate in zip(doc.items, wanted, strict=True):
		if flt(d.rate, 2) != rate:
			d.rate, d.amount, changed = rate, flt(rate * flt(d.qty), 2), True
	if not changed:
		return
	if persist:
		# Material Request only: it carries no document totals to recompute.
		for d in doc.items:
			d.db_set({"rate": d.rate, "amount": d.amount}, update_modified=False)


def make_material_request(policy, rate, description, schedule_date):
	item_code = get_premium_item()
	schedule_date = getdate(schedule_date or nowdate())
	if schedule_date < getdate(nowdate()):
		schedule_date = getdate(nowdate())
	mr = frappe.new_doc("Material Request")
	mr.update(
		{
			"material_request_type": "Purchase",
			"company": policy.company,
			"transaction_date": nowdate(),
			"schedule_date": schedule_date,
		}
	)
	line = _procurement_line(item_code, rate, description, schedule_date)
	line["insurance_policy"] = policy.name
	# Not required on v16 for non-stock items; only applied when configured.
	from upande_asset.upande_asset.insurance.utils import get_company_defaults

	if warehouse := get_company_defaults(policy.company).insurance_request_warehouse:
		line["warehouse"] = warehouse
	mr.append("items", line)
	mr.flags.ignore_permissions = True
	mr.insert()
	_reassert_rates(mr, [flt(rate, 2)], persist=True)
	_finalise(mr, "material_request_status")
	return mr


def _is_endorsement_doc(doctype, name, policy):
	return any(e.reference_doctype == doctype and e.reference_name == name for e in policy.get("endorsements") or [])


def _endorsement_for_po(policy, po_names):
	for e in policy.get("endorsements") or []:
		if e.status == "Active" and e.reference_doctype == "Purchase Order" and e.reference_name in po_names:
			return e
	return None


# ── doc_events ────────────────────────────────────────────────────────────────


def purchase_invoice_validate(doc, method=None):
	"""Stamp service dates / deferral on premium lines that lack them (e.g. PI made from a PO)."""
	premium_item = get_settings().premium_item
	for policy_name in policies_on(doc):
		policy = frappe.get_doc("Insurance Policy", policy_name)
		accounts = get_company_accounts(policy.company)
		rows = [d for d in doc.items if d.get("insurance_policy") == policy_name and d.item_code == premium_item]
		po_names = {d.purchase_order for d in rows if d.get("purchase_order")}
		endorsement = _endorsement_for_po(policy, po_names) if po_names else None
		start = getdate(endorsement.effective_date) if endorsement else getdate(policy.start_date)
		end = getdate(policy.end_date)
		for d in rows:
			if not d.service_start_date or not d.service_end_date:
				d.service_start_date, d.service_end_date = start, end
			if not d.enable_deferred_expense:
				d.enable_deferred_expense = 1
			if not d.deferred_expense_account and accounts.prepaid_insurance_account:
				d.deferred_expense_account = accounts.prepaid_insurance_account
			# Settings accounts are defaults: applied when the invoice is first created (a PI made
			# from a PO carries the item's default expense account), then a user's change is kept.
			if accounts.insurance_expense_account and (doc.is_new() or not d.expense_account):
				d.expense_account = accounts.insurance_expense_account


def purchase_invoice_on_submit(doc, method=None):
	if doc.is_return:
		return
	for policy_name in policies_on(doc):
		policy = frappe.get_doc("Insurance Policy", policy_name)
		if _is_endorsement_doc("Purchase Invoice", doc.name, policy):
			continue
		po_names = {d.purchase_order for d in doc.items if d.get("insurance_policy") == policy_name and d.get("purchase_order")}
		if po_names and _endorsement_for_po(policy, po_names):
			continue
		if not policy.purchase_invoice or frappe.db.get_value("Purchase Invoice", policy.purchase_invoice, "docstatus") == 2:
			policy.db_set("purchase_invoice", doc.name)


def purchase_invoice_on_cancel(doc, method=None):
	for policy_name in policies_on(doc):
		if frappe.db.get_value("Insurance Policy", policy_name, "purchase_invoice") == doc.name:
			frappe.db.set_value("Insurance Policy", policy_name, "purchase_invoice", None)


def purchase_order_on_submit(doc, method=None):
	for policy_name in policies_on(doc):
		policy = frappe.db.get_value(
			"Insurance Policy", policy_name, ["insurer", "docstatus", "procurement_doctype"], as_dict=True
		)
		if policy.docstatus != 0:
			# Endorsement POs on a live policy – nothing to propagate.
			continue
		updates = {}
		if not policy.insurer:
			updates["insurer"] = doc.supplier
		if policy.procurement_doctype in (None, "", "Material Request"):
			# Track the PO that superseded the MR so "open procurement" checks follow it.
			updates.update({"procurement_doctype": "Purchase Order", "procurement_name": doc.name})
		if updates:
			frappe.db.set_value("Insurance Policy", policy_name, updates)
