# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Material Requests for ERPNext Asset Repair (Asset Settings → Request Repair Materials).

ERPNext's Asset Repair issues its Stock Items straight from the warehouse when the
repair is submitted. Workshops that do not control their own stock need approval:
with the setting on, the repair lists what it needs in a Materials table and the
workshop raises Material Requests, either from stores (Material Issue) or to
purchase. Each request row carries `asset_repair`, which ERPNext's mappers copy
onto the Stock Entry, Purchase Order and Purchase Invoice made from it:

Rows, statuses and the documents themselves: see materials.py.

- stock issued against the repair → Issued Materials Cost (actual valuation)
- a supplier invoice for a service line → a row in the repair's Purchase Invoices table

Both feed Total Repair Cost. With the setting off nothing here runs.
"""

import frappe
from frappe import _
from frappe.utils import flt

from upande_asset.upande_asset.insurance.utils import get_settings



def enabled():
	return bool(get_settings().get("repair_material_requests"))


def default_parts_warehouse(company):
	"""The company's Workshop parts warehouse (Asset Settings → Defaults)."""
	from upande_asset.upande_asset.insurance.utils import get_company_defaults

	return get_company_defaults(company).parts_warehouse or None


# ── Asset Repair doc events ───────────────────────────────────────────────────


def get_dashboard_data(data):
	"""Connections on Asset Repair (override_doctype_dashboards). Requests, orders, invoices
	and issues carry asset_repair on their item rows (Stock Entry also in its header)."""
	data = data or {}
	data.update({
		"fieldname": "asset_repair",
		"non_standard_fieldnames": {**(data.get("non_standard_fieldnames") or {}), "Asset Check Sheet": "linked_asset_repair"},
		# Invoices in the repair's own table (also those added by hand, not through a request).
		"internal_links": {**(data.get("internal_links") or {}), "Purchase Invoice": ["invoices", "purchase_invoice"]},
		"transactions": [
			{"label": _("Materials"), "items": ["Material Request", "Stock Entry"]},
			{"label": _("Purchasing"), "items": ["Purchase Order", "Purchase Invoice"]},
			{"label": _("Analysis & Insurance"), "items": ["Asset Check Sheet", "Insurance Claim"]},
		],
	})
	return data


def asset_repair_onload(doc, method=None):
	settings = get_settings()
	from upande_asset.upande_asset.materials import client_options

	doc.set_onload("repair_materials", {"enabled": enabled(), "allow_direct_issue": bool(settings.allow_direct_material_issue),
		**client_options()})
	if doc.get("repair_materials"):
		from upande_asset.upande_asset.materials import refresh_rows

		refresh_rows(doc)  # live stock and status on the Materials rows


def asset_repair_validate(doc, method=None):
	validate_locked_rows(doc, "repair_materials")
	if not enabled() and not doc.get("repair_materials"):
		return
	from upande_asset.upande_asset.materials import refresh_rows

	refresh_rows(doc)
	doc.requested_materials_cost = issued_value(doc.name) if not doc.is_new() else 0
	# ERPNext has already summed invoices + Stock Items; add what was issued through requests.
	doc.total_repair_cost = flt(doc.repair_cost) + flt(doc.consumed_items_cost) + flt(doc.requested_materials_cost)


def asset_repair_before_submit(doc, method=None):
	if not doc.get("repair_materials") and not frappe.db.exists("Stock Entry Detail", {"asset_repair": doc.name}):
		return
	waiting = []
	for mr in {r.material_request for r in doc.repair_materials if r.material_request}:
		m = frappe.db.get_value("Material Request", mr, ["material_request_type", "per_ordered", "docstatus", "status"], as_dict=True)
		if m and m.material_request_type == "Material Issue" and m.docstatus < 2 and flt(m.per_ordered) < 100:
			waiting.append(f"{mr} ({_(m.status or 'Draft')})")
	if waiting:
		frappe.throw(_("Stores have not issued everything requested for this repair yet:<br>{0}<br>Complete the repair once the materials are issued, or cancel the request.").format(
			"<br>".join(waiting)), title=_("Materials Still Requested"))
	# ERPNext finds "the" repair Stock Entry by its header link when capitalising; ours carry that
	# link too (even once cancelled), so any request-issued stock rules capitalisation out.
	if doc.get("capitalize_repair_cost") and (flt(doc.get("requested_materials_cost"))
			or frappe.db.exists("Stock Entry Detail", {"asset_repair": doc.name})):
		frappe.throw(_("ERPNext only capitalises Stock Items issued by the repair itself. Untick Capitalize Repair Cost, "
			"or record these materials in Stock Items instead of requesting them."), title=_("Cannot Capitalise Requested Materials"))


# ── Rows ─────────────────────────────────────────────────────────────────────

# What a Material Request / Stock Entry was raised for; frozen once the row has one.
LOCKED_FIELDS = ("item_code", "qty", "uom", "warehouse", "material_request", "purchase_request", "purchase_order", "stock_entry")


def validate_locked_rows(doc, table):
	"""Rows already requested or issued cannot be edited or removed. Cancel the
	Material Request / Stock Entry first: that frees the row again. Used by both
	Asset Repair (repair_materials) and Asset Service Record (materials)."""
	before = doc.get_doc_before_save()
	if not before:
		return
	locked = {r.name: r for r in before.get(table) or [] if r.material_request or r.stock_entry or r.get("purchase_request") or r.get("purchase_order")}
	if not locked:
		return
	current = {r.name: r for r in doc.get(table) or []}
	for name, old in locked.items():
		ref = old.stock_entry or old.material_request or old.get("purchase_request") or old.get("purchase_order")
		row = current.get(name)
		if not row:
			frappe.throw(_("Row {0} ({1}) is on {2} and cannot be removed. Cancel {2} first.").format(old.idx, old.item_code, ref),
				title=_("Material Already Requested"))
		changed = [f for f in LOCKED_FIELDS
			if (flt(old.get(f)) != flt(row.get(f)) if f == "qty" else (old.get(f) or "") != (row.get(f) or ""))]
		if changed:
			if {"material_request", "purchase_request", "purchase_order", "stock_entry"} & set(changed):
				frappe.throw(_("Row {0} ({1}) was requested or issued while this form was open. Reload the form and try again.").format(
					old.idx, old.item_code), title=_("Reload Needed"))
			labels = ", ".join(_(frappe.get_meta(row.doctype).get_label(f)) for f in changed)
			frappe.throw(_("Row {0} ({1}) is on {2}, so its {3} cannot change. Cancel {2} to change it, or add a new row for extra material.").format(
				old.idx, old.item_code, ref, labels), title=_("Material Already Requested"))



@frappe.whitelist()
def make_material_request(asset_repair, purpose, rows=None):
	"""Unsaved Material Request for the repair's Materials rows (see materials.py)."""
	from upande_asset.upande_asset.materials import make_material_request as _make

	return _make("Asset Repair", asset_repair, purpose, rows)


@frappe.whitelist()
def make_material_issue(asset_repair, rows=None):
	"""Unsaved Material Issue for parts the workshop already holds (see materials.py)."""
	from upande_asset.upande_asset.materials import make_material_issue as _make

	return _make("Asset Repair", asset_repair, rows)


# ── Costs from real transactions ─────────────────────────────────────────────


def issued_value(repair):
	"""Value of stock issued against the repair through request / direct issue rows."""
	if not repair:
		return 0
	sed = frappe.qb.DocType("Stock Entry Detail")
	se = frappe.qb.DocType("Stock Entry")
	value = (
		frappe.qb.from_(sed).join(se).on(se.name == sed.parent)
		.select(frappe.query_builder.functions.Sum(sed.amount))
		.where(sed.asset_repair == repair)  # row level: ERPNext's own issue on submit has no row link
		.where(se.docstatus == 1)
		.where(sed.s_warehouse.isnotnull() & (sed.s_warehouse != ""))
		.where(sed.t_warehouse.isnull() | (sed.t_warehouse == ""))
		.run()
	)[0][0]
	return flt(value)


def recompute(repair):
	r = frappe.db.get_value("Asset Repair", repair, ["docstatus", "repair_cost", "consumed_items_cost"], as_dict=True)
	if not r or r.docstatus == 2:
		return
	issued = issued_value(repair)
	frappe.db.set_value("Asset Repair", repair, {"requested_materials_cost": issued,
		"total_repair_cost": flt(r.repair_cost) + flt(r.consumed_items_cost) + issued}, update_modified=False)


def stock_entry_validate(doc, method=None):
	"""A Stock Entry issuing materials for one repair (from its Material Request or a direct
	issue) also carries the repair in its header, so it shows under the repair's Connections."""
	repairs = {d.get("asset_repair") for d in doc.items if d.get("asset_repair")}
	if len(repairs) == 1 and not doc.get("asset_repair"):
		doc.asset_repair = repairs.pop()


def stock_entry_on_change(doc, method=None):
	for name in {d.get("asset_repair") for d in doc.items if d.get("asset_repair")}:
		recompute(name)


def stock_entry_released(doc, method=None):
	"""Row links are freed by materials.stock_entry_released; recompute the repair's cost."""
	stock_entry_on_change(doc)


def purchase_invoice_on_submit(doc, method=None):
	_sync_invoices(doc, add=True)


def purchase_invoice_on_cancel(doc, method=None):
	_sync_invoices(doc, add=False)


def _sync_invoices(pi, add):
	"""Service lines of a supplier invoice raised for a repair land in the repair's
	(draft) Purchase Invoices table; stock parts are costed when issued."""
	from upande_asset.upande_asset.doctype.asset_service_record.asset_service_record import available_amount

	by_key = {}
	for d in pi.items:
		if not d.get("asset_repair") or d.get("is_fixed_asset") or frappe.get_cached_value("Item", d.item_code, "is_stock_item"):
			continue
		key = (d.asset_repair, d.expense_account)
		by_key[key] = by_key.get(key, 0) + flt(d.base_net_amount)
	for (repair, account), amount in by_key.items():
		doc = frappe.get_doc("Asset Repair", repair)
		if doc.docstatus != 0:
			continue
		rows = [r for r in doc.invoices if r.purchase_invoice == pi.name and r.expense_account == account]
		if add and not rows:
			doc.append("invoices", {"purchase_invoice": pi.name, "expense_account": account,
				"repair_cost": min(flt(amount), available_amount(pi.name, account, exclude=doc.name))})
		elif not add and rows:
			for r in rows:
				doc.remove(r)
		else:
			continue
		doc.flags.ignore_permissions = True
		doc.save()
