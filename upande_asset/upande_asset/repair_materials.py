# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Material Requests for ERPNext Asset Repair (Asset Settings → Request Repair Materials).

ERPNext's Asset Repair issues its Stock Items straight from the warehouse when the
repair is submitted. Workshops that do not control their own stock need approval:
with the setting on, the repair lists what it needs in a Materials table and the
workshop raises Material Requests, either from stores (Material Issue) or to
purchase. Each request row carries `asset_repair`, which ERPNext's mappers copy
onto the Stock Entry, Purchase Order and Purchase Invoice made from it:

- stock issued against the repair → Issued Materials Cost (actual valuation)
- a supplier invoice for a service line → a row in the repair's Purchase Invoices table

Both feed Total Repair Cost. With the setting off nothing here runs.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from upande_asset.upande_asset.insurance.utils import get_settings

PURPOSES = ("Material Issue", "Purchase")


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
	doc.set_onload("repair_materials", {"enabled": enabled(), "allow_direct_issue": bool(settings.allow_direct_material_issue)})


def asset_repair_validate(doc, method=None):
	validate_locked_rows(doc, "repair_materials")
	if not enabled() and not doc.get("repair_materials"):
		return
	set_material_rows(doc, doc.get("repair_materials") or [])
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
LOCKED_FIELDS = ("item_code", "qty", "uom", "warehouse", "estimated_rate", "material_request", "stock_entry")


def validate_locked_rows(doc, table):
	"""Rows already requested or issued cannot be edited or removed. Cancel the
	Material Request / Stock Entry first: that frees the row again. Used by both
	Asset Repair (repair_materials) and Asset Service Record (materials)."""
	before = doc.get_doc_before_save()
	if not before:
		return
	locked = {r.name: r for r in before.get(table) or [] if r.material_request or r.stock_entry}
	if not locked:
		return
	current = {r.name: r for r in doc.get(table) or []}
	for name, old in locked.items():
		ref = old.stock_entry or old.material_request
		row = current.get(name)
		if not row:
			frappe.throw(_("Row {0} ({1}) is on {2} and cannot be removed. Cancel {2} first.").format(old.idx, old.item_code, ref),
				title=_("Material Already Requested"))
		changed = [f for f in LOCKED_FIELDS
			if (flt(old.get(f)) != flt(row.get(f)) if f in ("qty", "estimated_rate") else (old.get(f) or "") != (row.get(f) or ""))]
		if changed:
			if {"material_request", "stock_entry"} & set(changed):
				frappe.throw(_("Row {0} ({1}) was requested or issued while this form was open. Reload the form and try again.").format(
					old.idx, old.item_code), title=_("Reload Needed"))
			labels = ", ".join(_(frappe.get_meta(row.doctype).get_label(f)) for f in changed)
			frappe.throw(_("Row {0} ({1}) is on {2}, so its {3} cannot change. Cancel {2} to change it, or add a new row for extra material.").format(
				old.idx, old.item_code, ref, labels), title=_("Material Already Requested"))



def set_material_rows(doc, rows):
	"""Default warehouse (Asset Settings) and current Actual Qty on each Materials row."""
	from erpnext.stock.get_item_details import _get_bin_details

	default_wh = default_parts_warehouse(doc.company)
	for row in rows:
		if not row.item_code:
			continue
		is_stock = frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		row.is_stock_item = is_stock
		row.item_name = row.item_name or frappe.get_cached_value("Item", row.item_code, "item_name")
		row.uom = row.uom or frappe.get_cached_value("Item", row.item_code, "stock_uom")
		if is_stock and not row.warehouse and default_wh:
			row.warehouse = default_wh
		row.actual_qty = flt((_get_bin_details(row.item_code, row.warehouse, doc.company) or {}).get("actual_qty")) \
			if is_stock and row.warehouse else 0


def _conversion(item_code, uom):
	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	uom = uom or stock_uom
	factor = 1
	if uom != stock_uom:
		from erpnext.stock.get_item_details import get_conversion_factor

		factor = flt((get_conversion_factor(item_code, uom) or {}).get("conversion_factor")) or 1
	return uom, stock_uom, factor


def _repair(name, perm="write"):
	if not enabled():
		frappe.throw(_("Turn on Asset Settings → Maintenance → Request Repair Materials first."))
	doc = frappe.get_doc("Asset Repair", name)
	doc.check_permission(perm if doc.docstatus == 0 else "read")
	if doc.docstatus == 2:
		frappe.throw(_("The repair is cancelled."))
	return doc


def _open_rows(doc, rows):
	names = set(frappe.parse_json(rows) or []) if rows else None
	return [r for r in doc.get("repair_materials") or [] if r.item_code and not r.material_request and not r.stock_entry
		and (names is None or r.name in names)]


def _label(doc):
	return f"{doc.asset_name or doc.asset} ({doc.name})"


# ── Actions ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def make_material_request(asset_repair, purpose, rows=None):
	"""Material Request for the repair's open Materials rows: Material Issue (from stores)
	or Purchase. Submitted at once or left as a draft per Asset Settings."""
	doc = _repair(asset_repair)
	frappe.has_permission("Material Request", "create", throw=True)
	if purpose not in PURPOSES:
		frappe.throw(_("Choose Material Issue or Purchase."))
	lines = _open_rows(doc, rows)
	if purpose == "Material Issue":
		lines = [r for r in lines if frappe.get_cached_value("Item", r.item_code, "is_stock_item")]
	if not lines:
		frappe.throw(_("No Materials rows left to request. Rows already requested or issued are skipped; services are purchased, not issued."))
	settings = get_settings()
	schedule = max(getdate(nowdate()), getdate(doc.failure_date or nowdate()))
	mr = frappe.new_doc("Material Request")
	mr.update({"material_request_type": purpose, "company": doc.company, "transaction_date": nowdate(), "schedule_date": schedule})
	for r in lines:
		warehouse = r.warehouse or default_parts_warehouse(doc.company)
		if purpose == "Material Issue" and not warehouse:
			frappe.throw(_("Row {0}: set the Warehouse to issue {1} from.").format(r.idx, r.item_code))
		uom, stock_uom, factor = _conversion(r.item_code, r.uom)
		mr.append("items", {
			"item_code": r.item_code, "qty": r.qty, "uom": uom, "stock_uom": stock_uom, "conversion_factor": factor,
			"warehouse": warehouse, "schedule_date": schedule, "rate": r.estimated_rate,
			"cost_center": doc.cost_center or frappe.get_cached_value("Company", doc.company, "cost_center"),
			"description": _("{0} for repair of {1}").format(r.item_name or r.item_code, _label(doc)),
			"asset_repair": doc.name,
		})
	mr.insert()
	if (settings.maintenance_mr_status or "Draft") == "Submitted":
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Material Request"):
			mr.submit()
	for r in lines:
		frappe.db.set_value("Asset Service Material", r.name, "material_request", mr.name, update_modified=False)
	return mr.name


@frappe.whitelist()
def make_material_issue(asset_repair, rows=None):
	"""Record parts the workshop already holds: a Material Issue straight from the rows
	(Asset Settings → Record Used Materials Directly)."""
	doc = _repair(asset_repair)
	settings = get_settings()
	if not settings.allow_direct_material_issue:
		frappe.throw(_("Recording used materials directly is switched off in Asset Settings → Maintenance."))
	frappe.has_permission("Stock Entry", "create", throw=True)
	lines = [r for r in _open_rows(doc, rows) if frappe.get_cached_value("Item", r.item_code, "is_stock_item")]
	if not lines:
		frappe.throw(_("No stock items left to issue. Services are purchased, not issued; rows already requested or issued are skipped."))
	cost_center = doc.cost_center or frappe.db.get_value("Asset", doc.asset, "cost_center") \
		or frappe.get_cached_value("Company", doc.company, "cost_center")
	se = frappe.new_doc("Stock Entry")
	se.update({"stock_entry_type": "Material Issue", "purpose": "Material Issue", "company": doc.company,
		"remarks": _("Materials used on Asset Repair {0}").format(_label(doc))})
	for r in lines:
		warehouse = r.warehouse or default_parts_warehouse(doc.company)
		if not warehouse:
			frappe.throw(_("Row {0}: set the Warehouse the {1} came from.").format(r.idx, r.item_code))
		uom, stock_uom, factor = _conversion(r.item_code, r.uom)
		se.append("items", {"item_code": r.item_code, "qty": r.qty, "uom": uom, "stock_uom": stock_uom, "conversion_factor": factor,
			"s_warehouse": warehouse, "cost_center": cost_center, "asset_repair": doc.name})
	se.insert()
	if (settings.direct_issue_status or "Draft") == "Submitted":
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Stock Entry"):
			se.submit()
	for r in lines:
		frappe.db.set_value("Asset Service Material", r.name, "stock_entry", se.name, update_modified=False)
	return se.name


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
	frappe.db.sql("update `tabAsset Service Material` set stock_entry = NULL where stock_entry = %s and parenttype = 'Asset Repair'", doc.name)
	stock_entry_on_change(doc)


def material_request_released(doc, method=None):
	frappe.db.sql("update `tabAsset Service Material` set material_request = NULL where material_request = %s and parenttype = 'Asset Repair'",
		doc.name)


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
