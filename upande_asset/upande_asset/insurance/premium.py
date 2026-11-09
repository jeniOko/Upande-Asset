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
	return _unsaved(pi)


def _unsaved(doc):
	"""Documents are handed to the user unsaved (like ERPNext's "Create →" buttons) so that
	the site's own mandatory fields and workflows apply when they save it."""
	doc.set_onload("load_after_mapping", True)
	return doc


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
		if not _is_endorsement_doc("Purchase Invoice", name, policy) and _premium_rows(
			frappe.get_doc("Purchase Invoice", name), policy.name
		):
			return name
	return None


def _premium_rows(doc, policy_name):
	"""Item rows that pay the policy premium itself (not an endorsement adjustment)."""
	return [d for d in doc.get("items") or [] if d.get("insurance_policy") == policy_name and not d.get("insurance_endorsement")]


def has_open_procurement(policy):
	if not policy.procurement_doctype or not policy.procurement_name:
		return False
	return frappe.db.get_value(policy.procurement_doctype, policy.procurement_name, "docstatus") in (0, 1)


def resolve_procurement(procurement=None):
	mode = procurement or get_settings().renewal_procurement or "Material Request"
	if mode == "Ask":
		frappe.throw(_("Choose Material Request or Purchase Order."))
	if mode not in ("Material Request", "Purchase Order"):
		frappe.throw(_("Invalid procurement type {0}.").format(mode))
	return mode


def _rows_query(doctype, policy, child_filters=None, **filters):
	parent = frappe.qb.DocType(doctype)
	child = frappe.qb.DocType(ITEM_TABLES[doctype])
	q = (
		frappe.qb.from_(parent).join(child).on(child.parent == parent.name)
		.select(parent.name).distinct()
		.where(child.parenttype == doctype).where(child.insurance_policy == policy)
		.orderby(parent.creation)
	)
	for field, value in (child_filters or {}).items():
		q = q.where(child[field] == value)
	for field, value in filters.items():
		q = q.where(parent[field].isin(list(value)) if isinstance(value, (list, tuple)) else (parent[field] == value))
	return q.run(pluck=True)


def endorsement_documents(policy, endorsement_group, docstatus=(0, 1)):
	"""[(doctype, name)] of the MRs / POs / invoices raised for an endorsement."""
	out = []
	for doctype in ("Material Request", "Purchase Order", "Purchase Invoice"):
		for name in _rows_query(doctype, policy, {"insurance_endorsement": endorsement_group}, docstatus=docstatus):
			out.append((doctype, name))
	return out


def _endorsement_logs(policy, endorsement_group):
	logs = [e for e in policy.get("endorsements") or []
		if e.endorsement_group == endorsement_group and e.status in ("Requested", "Active")]
	if not logs:
		frappe.throw(_("Endorsement not found, withdrawn or reversed."))
	return logs


def endorsement_has_document(policy, endorsement_group):
	from upande_asset.upande_asset.insurance.endorsement import refund_entries

	if endorsement_documents(policy.name, endorsement_group) or refund_entries(policy.name, endorsement_group):
		return True
	# Endorsements recorded before documents carried the endorsement on their rows.
	for e in _endorsement_logs(policy, endorsement_group):
		if e.reference_doctype and e.reference_name and frappe.db.get_value(e.reference_doctype, e.reference_name, "docstatus") in (0, 1):
			return True
	return False


def _endorsement_line_description(policy, e, start, end):
	"""What procurement asks the insurer for, per asset: e.g. "Add Tractor (ACC-ASS-…) to cover:
	sum insured 400,000 from 09-11-2026 to 09-10-2027 – endorsement request on policy POL-1"."""
	currency = frappe.get_cached_value("Company", policy.company, "default_currency")
	money = lambda v: frappe.utils.fmt_money(v, currency=currency)  # noqa: E731
	what = f"{frappe.db.get_value('Asset', e.asset, 'asset_name') or e.asset} ({e.asset})" if e.asset else (
		policy.get("cover_description") or _("the cover"))
	if e.endorsement_type == "Add Assets":
		change = _("Add {0} to cover: sum insured {1}").format(what, money(e.new_sum_insured))
	elif e.endorsement_type == "Change Sum Insured":
		change = _("Change sum insured of {0} from {1} to {2}").format(what, money(e.old_sum_insured), money(e.new_sum_insured))
	else:
		change = _("{0}: {1}").format(_(e.endorsement_type), what)
	ref = _("endorsement {0}").format(e.endorsement_no) if e.endorsement_no else _("endorsement request")
	return _("{0}, {1} to {2} – {3} on policy {4} ({5})").format(change, frappe.format(start, "Date"), frappe.format(end, "Date"),
		ref, policy.policy_number or policy.name, policy.policy_type)


def make_endorsement_document(policy, endorsement_group, procurement=None):
	"""Unsaved premium document for an endorsement: extra premium starts procurement
	(Material Request or Purchase Order, as for renewals), already at the request stage so
	procurement can take it to the insurer; returned premium is a Journal Entry once the
	insurer has confirmed the endorsement."""
	logs = _endorsement_logs(policy, endorsement_group)
	total = flt(sum(flt(e.premium_adjustment) for e in logs), 2)
	label = logs[0].endorsement_no or _(logs[0].endorsement_type)
	if not total:
		frappe.throw(_("Endorsement {0} has no premium adjustment.").format(label))
	if total < 0:
		from upande_asset.upande_asset.insurance.endorsement import make_refund_entry

		return make_refund_entry(policy, endorsement_group)
	if endorsement_has_document(policy, endorsement_group):
		frappe.throw(_("A premium document for endorsement {0} already exists.").format(label))
	accounts = get_company_accounts(policy.company, PREMIUM_ACCOUNTS)
	item_code = get_premium_item()
	default_cc = policy.get("cost_center") or frappe.get_cached_value("Company", policy.company, "cost_center")
	rows = {r.name: r for r in policy.get("insured_assets") or []}
	start, end = getdate(logs[0].effective_date), getdate(policy.end_date)
	sign = 1 if total > 0 else -1
	lines = []
	for e in logs:
		if not flt(e.premium_adjustment):
			continue
		row = rows.get(e.policy_row)
		cc = (row.cost_center if row else None) or (e.asset and frappe.db.get_value("Asset", e.asset, "cost_center")) or default_cc
		desc = _endorsement_line_description(policy, e, start, end)
		line = _premium_line(item_code, abs(flt(e.premium_adjustment)), accounts, start, end, cc, desc, qty=sign)
		line["insurance_endorsement"] = endorsement_group
		lines.append(line)

	if resolve_procurement(procurement) == "Purchase Order":
		return make_purchase_order(policy, lines, schedule_date=start)
	return make_material_request(policy, lines=lines, schedule_date=start)


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


def _lines_to_rows(item_code, lines, schedule_date):
	rows = []
	for ln in lines:
		row = _procurement_line(item_code, ln["rate"], ln["description"], schedule_date, ln.get("cost_center"), ln.get("expense_account"))
		if ln.get("insurance_endorsement"):
			row["insurance_endorsement"] = ln["insurance_endorsement"]
		rows.append(row)
	return rows


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
		for row in _lines_to_rows(item_code, lines, schedule_date):
			po.append("items", row)
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
	po.calculate_taxes_and_totals()
	return _unsaved(po)


def _reassert_rates(doc, wanted):
	"""A default buying price list (and the Item Price ERPNext auto-creates on
	purchase) would otherwise replace the policy premium with the last price paid."""
	for d, rate in zip(doc.items, wanted, strict=True):
		if flt(d.rate, 2) != rate:
			d.rate, d.amount = rate, flt(rate * flt(d.qty), 2)


def make_material_request(policy, rate=None, description=None, schedule_date=None, lines=None):
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
	rows = _lines_to_rows(item_code, lines, schedule_date) if lines else [_procurement_line(item_code, rate, description, schedule_date)]
	# Not required on v16 for non-stock items; only applied when configured.
	from upande_asset.upande_asset.insurance.utils import get_company_defaults

	warehouse = get_company_defaults(policy.company).insurance_request_warehouse
	for row in rows:
		row["insurance_policy"] = policy.name
		if warehouse:
			row["warehouse"] = warehouse
		row["amount"] = flt(flt(row["rate"]) * flt(row["qty"]), 2)
		mr.append("items", row)
	return _unsaved(mr)


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
		effective = {e.endorsement_group: e.effective_date for e in policy.get("endorsements") or [] if e.endorsement_group}
		for d in rows:
			if not d.service_start_date or not d.service_end_date:
				row_start = effective.get(d.get("insurance_endorsement")) if d.get("insurance_endorsement") else None
				d.service_start_date, d.service_end_date = getdate(row_start) if row_start else start, end
			if not d.enable_deferred_expense:
				d.enable_deferred_expense = 1
			if not d.deferred_expense_account and accounts.prepaid_insurance_account:
				d.deferred_expense_account = accounts.prepaid_insurance_account
			# Settings accounts are defaults: applied when the invoice is first created (a PI made
			# from a PO carries the item's default expense account), then a user's change is kept.
			if accounts.insurance_expense_account and (doc.is_new() or not d.expense_account):
				d.expense_account = accounts.insurance_expense_account


def refresh_policies_of_invoices(invoices):
	"""Renewal status depends on the premium invoice being submitted / paid."""
	from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import refresh_renewal_chain

	invoices = [i for i in set(invoices or []) if i]
	if not invoices:
		return
	for policy in frappe.get_all("Purchase Invoice Item", filters={"parent": ["in", invoices], "insurance_policy": ["is", "set"]},
			pluck="insurance_policy", distinct=True):
		refresh_renewal_chain(policy)


def purchase_invoice_on_submit(doc, method=None):
	_link_premium_invoice(doc)
	refresh_policies_of_invoices([doc.name, doc.get("return_against")])


def _link_premium_invoice(doc):
	if doc.is_return:
		return
	for policy_name in policies_on(doc):
		policy = frappe.get_doc("Insurance Policy", policy_name)
		if _is_endorsement_doc("Purchase Invoice", doc.name, policy):
			continue
		rows = _premium_rows(doc, policy_name)
		po_names = {d.purchase_order for d in rows if d.get("purchase_order")}
		if not rows or (po_names and _endorsement_for_po(policy, po_names)):
			continue
		if not policy.purchase_invoice or frappe.db.get_value("Purchase Invoice", policy.purchase_invoice, "docstatus") == 2:
			policy.db_set("purchase_invoice", doc.name)
			_premium_follows_invoice(policy, doc, rows)


def _premium_follows_invoice(policy, invoice, rows):
	"""The insurer's invoice is the premium actually paid: when it differs from the
	policy (e.g. a renewal quoted at last year's premium), the policy takes the invoiced
	amount. Levies stay as entered; the difference goes to the basic premium."""
	invoiced = flt(sum(flt(d.base_net_amount) for d in rows), 2)
	if not invoiced or invoiced == flt(policy.total_premium, 2):
		return
	levies = min(flt(policy.levies), invoiced)
	old_total = flt(policy.total_premium, 2)
	policy.db_set({"basic_premium": flt(invoiced - levies, 2), "levies": levies, "total_premium": invoiced})
	currency = frappe.get_cached_value("Company", policy.company, "default_currency")
	policy.add_comment(
		"Info",
		_("Premium changed from {0} to {1} to match Purchase Invoice {2}.").format(
			frappe.utils.fmt_money(old_total, currency=currency),
			frappe.utils.fmt_money(invoiced, currency=currency),
			frappe.utils.get_link_to_form("Purchase Invoice", invoice.name),
		),
	)


def purchase_invoice_on_cancel(doc, method=None):
	for policy_name in policies_on(doc):
		if frappe.db.get_value("Insurance Policy", policy_name, "purchase_invoice") == doc.name:
			frappe.db.set_value("Insurance Policy", policy_name, "purchase_invoice", None)
	refresh_policies_of_invoices([doc.name, doc.get("return_against")])


def payment_on_change(doc, method=None):
	"""Payment Entry / Journal Entry against a premium invoice (Asset Settings may count a
	renewal complete only once the premium is paid)."""
	if doc.doctype == "Payment Entry":
		invoices = [r.reference_name for r in doc.get("references") or [] if r.reference_doctype == "Purchase Invoice"]
	else:
		invoices = [r.reference_name for r in doc.get("accounts") or [] if r.get("reference_type") == "Purchase Invoice"]
	refresh_policies_of_invoices(invoices)


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


def material_request_validate(doc, method=None):
	"""ERPNext re-prices every row of a new Material Request from the buying price list
	(MaterialRequest.on_update → update_item_rates). Keep the premium as saved."""
	doc.flags.premium_rates = [(d, flt(d.rate, 2)) for d in doc.get("items") or [] if d.get("insurance_policy")]


def material_request_on_update(doc, method=None):
	for d, rate in doc.flags.premium_rates or []:
		if flt(d.rate, 2) != rate:
			d.db_set({"rate": rate, "amount": flt(rate * flt(d.qty), 2)}, update_modified=False)


def link_procurement(doc, method=None):
	"""after_insert of a premium MR / PO / PI the user saved: record it on the policy
	(open procurement of a draft policy, or the endorsement it pays)."""
	for policy_name in policies_on(doc):
		groups = {d.insurance_endorsement for d in doc.items if d.get("insurance_policy") == policy_name and d.get("insurance_endorsement")}
		for group in groups:
			for log in frappe.get_all("Insurance Policy Endorsement Log", pluck="name",
					filters={"parenttype": "Insurance Policy", "parent": policy_name, "endorsement_group": group}):
				frappe.db.set_value("Insurance Policy Endorsement Log", log,
					{"reference_doctype": doc.doctype, "reference_name": doc.name}, update_modified=False)
		if doc.doctype == "Purchase Invoice" or not _premium_rows(doc, policy_name):
			continue
		policy = frappe.db.get_value("Insurance Policy", policy_name,
			["name", "docstatus", "procurement_doctype", "procurement_name"], as_dict=True)
		if policy.docstatus != 0:
			continue
		# A PO made from the policy's MR takes over as the open procurement.
		if not has_open_procurement(policy) or (policy.procurement_doctype == "Material Request" and doc.doctype == "Purchase Order"):
			frappe.db.set_value("Insurance Policy", policy_name,
				{"procurement_doctype": doc.doctype, "procurement_name": doc.name}, update_modified=False)


def unlink_procurement(doc, method=None):
	"""on_trash of an MR / PO / PI: drop the policy's links to it so the policy can still be saved."""
	if not policies_on(doc):
		return
	frappe.db.sql(
		"""UPDATE `tabInsurance Policy` SET procurement_doctype = NULL, procurement_name = NULL
		WHERE procurement_doctype = %s AND procurement_name = %s""", (doc.doctype, doc.name))
	frappe.db.sql(
		"""UPDATE `tabInsurance Policy Endorsement Log` SET reference_doctype = NULL, reference_name = NULL
		WHERE reference_doctype = %s AND reference_name = %s""", (doc.doctype, doc.name))
