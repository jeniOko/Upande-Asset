# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Materials rows (child "Asset Service Material") on Asset Service Record (`materials`)
and Asset Repair (`repair_materials`).

How a row moves, decided by stock in the row's warehouse:

  in stock          → Request from Stores (Material Request, Material Issue)
                      or Issue Used Materials (Stock Entry, when Asset Settings allow it)
  out of stock      → Request Purchase (Material Request, Purchase; received into the row's
                      warehouse), Create Purchase Order (buying without a request), or
                      Request Transfer (Material Request, Material Transfer)
  received          → "To Issue": issue it as above (stores, or the workshop itself)
  service items     → Request Purchase; the supplier invoice feeds the record's costs

Documents open unsaved for the user to complete and save. They carry the parent's Cost Center
and accounting dimensions on every row and, where the document has them, in its header. Every document row carries the
parent link (asset_service_record / asset_repair) and `asset_material_row` (the materials
row), which ERPNext's own mappers copy along MR → PO → Purchase Receipt → Stock Entry, so
an issue made from any of them is recognised and costed.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

ROW_LINK = "asset_material_row"
ISSUE, TRANSFER, PURCHASE = "Material Issue", "Material Transfer", "Purchase"
LINK_FIELDS = ("material_request", "purchase_request", "purchase_order", "stock_entry")
PURPOSES = (ISSUE, TRANSFER, PURCHASE)
ISSUABLE = ("In Stock", "To Issue")
PARENTS = {
	"Asset Service Record": {"table": "materials", "link": "asset_service_record"},
	"Asset Repair": {"table": "repair_materials", "link": "asset_repair"},
}


def conf(doctype):
	return PARENTS[doctype]


def purchase_documents():
	"""Documents this user may purchase through: Asset Settings → Purchase parts and services through
	(Material Request / Purchase Order / both), limited by their create rights."""
	from upande_asset.upande_asset.insurance.utils import get_settings

	setting = get_settings().get("parts_purchase_document") or "Material Request"
	both = setting in ("Material Request and Purchase Order", "Both")
	allowed = ["Material Request", "Purchase Order"] if both else [setting]
	return [dt for dt in allowed if frappe.has_permission(dt, "create")]


def client_options():
	"""What the Materials pop-up may offer this user (sent with the form)."""
	return {
		"purchase_documents": purchase_documents(),
		"can_request": bool(frappe.has_permission("Material Request", "create")),
		"can_issue": bool(frappe.has_permission("Stock Entry", "create")),
	}


def _check_purchase_document(doctype):
	if doctype not in purchase_documents():
		from upande_asset.upande_asset.insurance.utils import get_settings

		setting = get_settings().get("parts_purchase_document") or "Material Request"
		if setting not in ("Material Request and Purchase Order", "Both") and doctype != setting:
			frappe.throw(_("Parts and services are purchased through a {0} (Asset Settings → Maintenance).").format(_(setting)),
				title=_("Not Allowed"))
		frappe.throw(_("You are not allowed to create a {0}.").format(_(doctype)), frappe.PermissionError)


def default_parts_warehouse(company):
	from upande_asset.upande_asset.insurance.utils import get_company_defaults

	return get_company_defaults(company).parts_warehouse or None


def conversion(item_code, uom):
	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	uom = uom or stock_uom
	factor = 1
	if uom != stock_uom:
		from erpnext.stock.get_item_details import get_conversion_factor

		factor = flt((get_conversion_factor(item_code, uom) or {}).get("conversion_factor")) or 1
	return uom, stock_uom, factor


def needed_qty(row):
	"""Row qty in the item's stock UOM (what Actual Qty is measured in)."""
	return flt(row.qty) * conversion(row.item_code, row.uom)[2]


# ── Row state ────────────────────────────────────────────────────────────────


def _issued(doc, rows):
	"""{row name: stock qty issued} from submitted issues (source warehouse, no target)
	linked to the parent. Issues without a row reference (made before rows carried one)
	are shared out over rows of the same item, in order."""
	link = conf(doc.doctype)["link"]
	sed = frappe.qb.DocType("Stock Entry Detail")
	se = frappe.qb.DocType("Stock Entry")
	data = (
		frappe.qb.from_(sed).join(se).on(se.name == sed.parent)
		.select(sed.item_code, sed.transfer_qty, sed[ROW_LINK], sed.material_request, sed.parent)
		.where(sed[link] == doc.name)
		.where(se.docstatus == 1)
		.where(sed.s_warehouse.isnotnull() & (sed.s_warehouse != ""))
		.where(sed.t_warehouse.isnull() | (sed.t_warehouse == ""))
		.run(as_dict=True)
	) if doc.name and not doc.is_new() else []
	names = {r.name for r in rows}
	out = {r.name: 0.0 for r in rows}
	loose = {}
	for d in data:
		own = next((r.name for r in rows if r.item_code == d.item_code and (
			(d.material_request and r.material_request == d.material_request) or r.stock_entry == d.parent)), None)
		if d.get(ROW_LINK) in names:
			out[d.get(ROW_LINK)] += flt(d.transfer_qty)
		elif own:  # issued before rows carried a reference: match its request / entry
			out[own] += flt(d.transfer_qty)
		else:
			loose[d.item_code] = loose.get(d.item_code, 0) + flt(d.transfer_qty)
	for item_code, qty in loose.items():
		same = [r for r in rows if r.item_code == item_code]
		for i, r in enumerate(same):
			take = qty if i == len(same) - 1 else min(qty, max(needed_qty(r) - out[r.name], 0))
			out[r.name] += take
			qty -= take
	return out


def _received(row):
	"""Stock qty brought into the row's warehouse for it: Purchase Receipts and transfers carrying
	the row reference (they inherit it from the request / order)."""
	pri = frappe.qb.DocType("Purchase Receipt Item")
	pr = frappe.qb.DocType("Purchase Receipt")
	received = flt((
		frappe.qb.from_(pri).join(pr).on(pr.name == pri.parent)
		.select(frappe.query_builder.functions.Sum(pri.stock_qty))
		.where(pri[ROW_LINK] == row.name).where(pr.docstatus == 1).where(pr.is_return == 0)
		.run()
	)[0][0])
	sed = frappe.qb.DocType("Stock Entry Detail")
	se = frappe.qb.DocType("Stock Entry")
	received += flt((
		frappe.qb.from_(sed).join(se).on(se.name == sed.parent)
		.select(frappe.query_builder.functions.Sum(sed.transfer_qty))
		.where(sed[ROW_LINK] == row.name).where(se.docstatus == 1)
		.where(sed.s_warehouse.isnotnull() & (sed.s_warehouse != "")).where(sed.t_warehouse.isnotnull() & (sed.t_warehouse != ""))
		.run()
	)[0][0])
	if received or not row.get("purchase_request"):
		return received
	# Requests raised before their lines carried the row reference.
	mr_type = frappe.db.get_value("Material Request", row.purchase_request, "material_request_type")
	items = frappe.get_all("Material Request Item", filters={"parent": row.purchase_request},
		fields=["item_code", "received_qty", "ordered_qty", ROW_LINK])
	mine = [i for i in items if i.item_code == row.item_code and not i.get(ROW_LINK)]
	return sum(flt(i.received_qty if mr_type == PURCHASE else i.ordered_qty) for i in mine)


def row_status(row, issued, received):
	ordered = bool(row.get("purchase_order")) or bool(row.get("purchase_request") and
		flt(frappe.db.get_value("Material Request", row.purchase_request, "per_ordered")) >= 100)
	if not row.is_stock_item:
		if row.get("purchase_order") or row.get("purchase_request") or row.material_request:
			if not ordered and row.material_request and not row.get("purchase_request"):
				ordered = flt(frappe.db.get_value("Material Request", row.material_request, "per_ordered")) >= 100
			return "Ordered" if ordered else "Purchase Requested"
		return "Not Requested"
	needed = needed_qty(row)
	if needed and issued >= needed:
		return "Issued"
	if issued > 0:
		return "Partly Issued"
	if row.material_request or row.stock_entry:
		return "Issue Requested"
	if row.get("purchase_request") or row.get("purchase_order"):
		if needed and received >= needed:
			# Received: issue it while the stock is still there (it may have gone to another job).
			return "To Issue" if row.warehouse and flt(row.actual_qty) >= needed - issued else "Received, Out of Stock"
		if row.get("purchase_request") and frappe.db.get_value("Material Request", row.purchase_request, "material_request_type") == TRANSFER:
			return "Transfer Requested"
		return "Ordered" if ordered else "Purchase Requested"
	return "In Stock" if row.warehouse and flt(row.actual_qty) >= needed else "Out of Stock"


def refresh_rows(doc):
	"""Defaults plus live Actual Qty, Issued Qty and Status on every materials row.
	Runs on load as well as on save, so the form always shows current stock."""
	from erpnext.stock.get_item_details import _get_bin_details

	rows = [r for r in doc.get(conf(doc.doctype)["table"]) or [] if r.item_code]
	default_wh = default_parts_warehouse(doc.company)
	for row in rows:
		is_stock = frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		row.is_stock_item = is_stock
		row.item_name = row.item_name or frappe.get_cached_value("Item", row.item_code, "item_name")
		row.uom = row.uom or frappe.get_cached_value("Item", row.item_code, "stock_uom")
		if is_stock and not row.warehouse and default_wh:
			row.warehouse = default_wh
		row.actual_qty = flt((_get_bin_details(row.item_code, row.warehouse, doc.company) or {}).get("actual_qty")) \
			if is_stock and row.warehouse else 0
	issued = _issued(doc, rows)
	for row in rows:
		row.issued_qty = issued.get(row.name, 0)
		row.material_status = row_status(row, row.issued_qty, _received(row))


# ── Building the documents ───────────────────────────────────────────────────


def _parent(doctype, name):
	if doctype not in PARENTS:
		frappe.throw(_("Materials are not used on {0}.").format(doctype))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write" if doc.docstatus == 0 else "read")
	if doc.docstatus == 2:
		frappe.throw(_("{0} {1} is cancelled.").format(_(doctype), name))
	from upande_asset.upande_asset.repair_materials import enabled

	if not enabled():
		frappe.throw(_("Turn on Asset Settings → Maintenance → Use Material Requests for parts first."))
	refresh_rows(doc)
	return doc


def _eligible(row, purpose):
	"""None when the row can go on a `purpose` document, else the reason it cannot."""
	status = row.material_status
	if purpose == "Purchase Order":
		purpose = PURCHASE
	if purpose in (ISSUE, "Direct Issue"):
		if not row.is_stock_item:
			return _("services are purchased, not issued")
		if status not in ISSUABLE:
			return _("it is {0}").format(_(status)) if status != "Out of Stock" else \
				_("only {0} in {1}; request a purchase or a transfer").format(flt(row.actual_qty), row.warehouse or _("no warehouse"))
		return None
	if purpose == TRANSFER and not row.is_stock_item:
		return _("services are purchased, not transferred")
	if status not in ("Out of Stock", "Not Requested"):
		return _("it is {0}").format(_(status)) if status != "In Stock" else \
			_("{0} in stock in {1}; request it from stores").format(flt(row.actual_qty), row.warehouse)
	return None


def _pick_rows(doc, purpose, rows):
	names = set(frappe.parse_json(rows) or []) if rows else None
	picked, skipped = [], []
	for row in doc.get(conf(doc.doctype)["table"]) or []:
		if not row.item_code or (names is not None and row.name not in names):
			continue
		reason = _eligible(row, purpose)
		(skipped if reason else picked).append((row, reason))
	if not picked:
		detail = "<br>".join(_("Row {0} ({1}): {2}").format(r.idx, r.item_code, why) for r, why in skipped)
		frappe.throw(_("No rows can go on this document.") + (f"<br>{detail}" if detail else ""), title=_("Nothing to Request"))
	if skipped and names is not None:
		frappe.msgprint("<br>".join(_("Row {0} ({1}) left out: {2}").format(r.idx, r.item_code, why) for r, why in skipped),
			indicator="orange", alert=True)
	return [r for r, _x in picked]


def _source_warehouse(doc, row, needed):
	"""Another warehouse of the company holding enough of the item, most stock first."""
	b = frappe.qb.DocType("Bin")
	w = frappe.qb.DocType("Warehouse")
	return (
		frappe.qb.from_(b).join(w).on(w.name == b.warehouse)
		.select(b.warehouse)
		.where(b.item_code == row.item_code).where(w.company == doc.company).where(w.is_group == 0)
		.where(b.warehouse != (row.warehouse or "")).where(b.actual_qty >= needed)
		.orderby(b.actual_qty, order=frappe.qb.desc).limit(1)
		.run(pluck=True) or [None]
	)[0]


def _schedule_date(doc):
	if doc.doctype == "Asset Service Record":
		due = min([getdate(t.due_date) for t in doc.get("tasks") or [] if t.due_date] or [getdate(doc.service_date or nowdate())])
	else:
		due = getdate(doc.get("failure_date") or nowdate())
	return max(due, getdate(nowdate()))


def _label(doc):
	if doc.doctype == "Asset Service Record":
		return f"{doc.title} ({doc.name})"
	return _("repair of {0} ({1})").format(doc.get("asset_name") or doc.asset, doc.name)


def _service_rate(doc, item_code):
	"""The Service Type's usual price for its service item, as a guide on the request."""
	if doc.doctype != "Asset Service Record" or not doc.get("service_type"):
		return None
	st = frappe.db.get_value("Asset Service Type", doc.service_type, ["default_service_item", "default_service_rate"], as_dict=True)
	return flt(st.default_service_rate) if st and st.default_service_item == item_code and flt(st.default_service_rate) else None


def _unsaved(new, parent=None):
	"""As ERPNext's "Create →" buttons do. A Material Request has no header dimensions, so
	the parent's are added to the form data: ERPNext's client keeps mapped row dimensions only
	when the header already holds one (dimension_tree_filter.js update_dimension)."""
	new.set_onload("load_after_mapping", True)
	out = new.as_dict()
	if parent is not None:
		from upande_asset.upande_asset.dimensions import get_dimensions

		for field, value in get_dimensions(parent).items():
			if out.get(field) is None:
				out[field] = value
	return out


def _header_dimensions(new, parent):
	from upande_asset.upande_asset.dimensions import get_dimensions

	new.update(get_dimensions(parent, new.doctype))


@frappe.whitelist()
def make_material_request(doctype, name, purpose, rows=None):
	"""Unsaved Material Request for the parent's materials rows: Material Issue (in stock),
	Purchase or Material Transfer (out of stock; services are always purchased)."""
	if purpose not in PURPOSES:
		frappe.throw(_("Choose Material Issue, Material Transfer or Purchase."))
	frappe.has_permission("Material Request", "create", throw=True)
	if purpose == PURCHASE:
		_check_purchase_document("Material Request")
	doc = _parent(doctype, name)
	lines = _pick_rows(doc, purpose, rows)

	from upande_asset.upande_asset.dimensions import get_dimensions

	link = conf(doctype)["link"]
	schedule = _schedule_date(doc)
	dims = get_dimensions(doc, "Material Request Item")
	mr = frappe.new_doc("Material Request")
	mr.update({"material_request_type": purpose, "company": doc.company, "transaction_date": nowdate(), "schedule_date": schedule})
	for r in lines:
		uom, stock_uom, factor = conversion(r.item_code, r.uom)
		row = {
			"item_code": r.item_code, "item_name": r.item_name, "qty": r.qty, "uom": uom, "stock_uom": stock_uom,
			"conversion_factor": factor, "stock_qty": flt(r.qty) * factor, "schedule_date": schedule,
			"warehouse": r.warehouse or default_parts_warehouse(doc.company),
			"description": _("{0} for {1}").format(r.item_name or r.item_code, _label(doc)),
			link: doc.name, ROW_LINK: r.name, **dims,
		}
		if purpose in (ISSUE, TRANSFER) and not row["warehouse"]:
			frappe.throw(_("Row {0}: set the Warehouse for {1}.").format(r.idx, r.item_code))
		if purpose == TRANSFER:
			row["from_warehouse"] = _source_warehouse(doc, r, needed_qty(r))
			if not row["from_warehouse"]:
				frappe.throw(_("Row {0}: no other warehouse holds {1} {2}. Request a purchase instead.").format(
					r.idx, flt(needed_qty(r)), r.item_code), title=_("Not Available to Transfer"))
		if rate := _service_rate(doc, r.item_code):
			row["rate"] = rate
		mr.append("items", row)
	return _unsaved(mr, doc)


@frappe.whitelist()
def make_material_issue(doctype, name, rows=None):
	"""Unsaved Material Issue Stock Entry for parts the workshop already holds
	(Asset Settings → Allow recording parts already used)."""
	from upande_asset.upande_asset.insurance.utils import get_settings

	if not get_settings().allow_direct_material_issue:
		frappe.throw(_("Recording used materials directly is switched off in Asset Settings → Maintenance."))
	frappe.has_permission("Stock Entry", "create", throw=True)
	doc = _parent(doctype, name)
	lines = _pick_rows(doc, "Direct Issue", rows)

	from upande_asset.upande_asset.dimensions import get_dimensions

	link = conf(doctype)["link"]
	dims = get_dimensions(doc, "Stock Entry Detail")
	se = frappe.new_doc("Stock Entry")
	se.update({"stock_entry_type": "Material Issue", "purpose": "Material Issue", "company": doc.company,
		"remarks": _("Materials used on {0}").format(_label(doc))})
	if doctype == "Asset Repair":
		se.asset_repair = doc.name
	_header_dimensions(se, doc)
	for r in lines:
		uom, stock_uom, factor = conversion(r.item_code, r.uom)
		se.append("items", {"item_code": r.item_code, "qty": r.qty, "uom": uom, "stock_uom": stock_uom,
			"conversion_factor": factor, "transfer_qty": flt(r.qty) * factor, "s_warehouse": r.warehouse,
			link: doc.name, ROW_LINK: r.name, **dims})
	se.set_missing_values()
	return _unsaved(se)


@frappe.whitelist()
def make_purchase_order(doctype, name, rows=None):
	"""Unsaved Purchase Order for rows to buy, for workshops that order without a Material
	Request. The supplier is chosen on the order; the parent's dimensions are on it already."""
	frappe.has_permission("Purchase Order", "create", throw=True)
	_check_purchase_document("Purchase Order")
	doc = _parent(doctype, name)
	lines = _pick_rows(doc, "Purchase Order", rows)

	from upande_asset.upande_asset.dimensions import get_dimensions

	link = conf(doctype)["link"]
	schedule = _schedule_date(doc)
	dims = get_dimensions(doc, "Purchase Order Item")
	po = frappe.new_doc("Purchase Order")
	po.update({"company": doc.company, "transaction_date": nowdate(), "schedule_date": schedule,
		"supplier": doc.get("supplier") if doctype == "Asset Service Record" else None})
	_header_dimensions(po, doc)
	for r in lines:
		uom, stock_uom, factor = conversion(r.item_code, r.uom)
		row = {
			"item_code": r.item_code, "item_name": r.item_name, "qty": r.qty, "uom": uom, "stock_uom": stock_uom,
			"conversion_factor": factor, "stock_qty": flt(r.qty) * factor, "schedule_date": schedule,
			"description": _("{0} for {1}").format(r.item_name or r.item_code, _label(doc)),
			link: doc.name, ROW_LINK: r.name, **dims,
		}
		if r.is_stock_item:
			row["warehouse"] = r.warehouse or default_parts_warehouse(doc.company)
		if rate := _service_rate(doc, r.item_code):
			row["rate"] = rate
		po.append("items", row)
	return _unsaved(po)


# ── doc_events: link rows to the documents the user saved ─────────────────────


def _parents_of(doc):
	out = set()
	for dt, c in PARENTS.items():
		out |= {(dt, d.get(c["link"])) for d in doc.get("items") or [] if d.get(c["link"])}
	return out


def sync_rows(parents):
	"""Store each row's current Actual Qty / Issued Qty / Status (also shown live on load)."""
	for dt, name in parents:
		if not frappe.db.exists(dt, name):
			continue
		doc = frappe.get_doc(dt, name)
		refresh_rows(doc)
		for r in doc.get(conf(dt)["table"]) or []:
			if r.item_code:
				frappe.db.set_value("Asset Service Material", r.name, {"actual_qty": r.actual_qty, "issued_qty": r.issued_qty,
					"material_status": r.material_status, "is_stock_item": r.is_stock_item}, update_modified=False)


def on_change(doc, method=None):
	"""PO / Purchase Receipt / Stock Entry submitted or cancelled: rows move on."""
	sync_rows(_parents_of(doc))


def _rows_of(doc):
	return {d.get(ROW_LINK) for d in doc.get("items") or [] if d.get(ROW_LINK)}


def _mark_started(row_names):
	"""A Pending Service Record moves to In Progress once its first material document exists."""
	for parent in set(frappe.get_all("Asset Service Material", filters={"name": ["in", list(row_names)],
			"parenttype": "Asset Service Record"}, pluck="parent")):
		if frappe.db.get_value("Asset Service Record", parent, ["docstatus", "status"]) == (0, "Pending"):
			frappe.db.set_value("Asset Service Record", parent, "status", "In Progress", update_modified=False)


def _relink(doc, field, rows):
	"""Point the materials rows on `doc` at it, and free rows taken off it (or a type change).
	Runs on every save, so removing a line from the request and saving clears the row."""
	for f in LINK_FIELDS:
		for name in frappe.get_all("Asset Service Material", filters={f: doc.name}, pluck="name"):
			if f != field or name not in rows:
				frappe.db.set_value("Asset Service Material", name, f, None, update_modified=False)
	for row in rows if field else ():
		current = frappe.db.get_value("Asset Service Material", row, field)
		if current is None and not frappe.db.exists("Asset Service Material", row):
			continue
		if not current:
			frappe.db.set_value("Asset Service Material", row, field, doc.name, update_modified=False)
	_mark_started(rows)
	on_change(doc)


def material_request_on_update(doc, method=None):
	"""Saving a request made from the Materials rows links them (Material Issue → Issue
	Request; Purchase / Transfer → Purchase / Transfer Request)."""
	field = "material_request" if doc.material_request_type == ISSUE else (
		"purchase_request" if doc.material_request_type in (PURCHASE, TRANSFER) else None)
	_relink(doc, field, _rows_of(doc))


def purchase_order_on_update(doc, method=None):
	"""A Purchase Order started from the Materials rows (not from their request) links them."""
	rows = {d.get(ROW_LINK) for d in doc.get("items") or [] if d.get(ROW_LINK) and not d.get("material_request")}
	if not rows and not frappe.db.exists("Asset Service Material", {"purchase_order": doc.name}):
		return
	_relink(doc, "purchase_order", rows)


def stock_entry_on_update(doc, method=None):
	"""A direct issue (not made from an issue request) is recorded on its rows."""
	rows = {d.get(ROW_LINK) for d in doc.get("items") or [] if d.get(ROW_LINK) and not d.get("material_request")}
	if not rows and not frappe.db.exists("Asset Service Material", {"stock_entry": doc.name}):
		return
	_relink(doc, "stock_entry" if doc.purpose == ISSUE else None, rows)


def material_request_released(doc, method=None):
	"""A cancelled / deleted request frees its rows to be requested again."""
	for field in ("material_request", "purchase_request"):
		frappe.db.sql(f"update `tabAsset Service Material` set `{field}` = NULL where `{field}` = %s", doc.name)
	on_change(doc)


def purchase_order_released(doc, method=None):
	frappe.db.sql("update `tabAsset Service Material` set purchase_order = NULL where purchase_order = %s", doc.name)
	on_change(doc)


def stock_entry_released(doc, method=None):
	frappe.db.sql("update `tabAsset Service Material` set stock_entry = NULL where stock_entry = %s", doc.name)
	on_change(doc)


def purchase_receipt_on_submit(doc, method=None):
	"""Parts bought for a job have arrived: tell whoever runs the job they can be issued."""
	from frappe.utils import get_url_to_form

	from upande_asset.upande_asset.insurance.utils import get_settings, notify

	on_change(doc)
	for parent_doctype, c in PARENTS.items():
		for name in {d.get(c["link"]) for d in doc.items if d.get(c["link"])}:
			parent = frappe.db.get_value(parent_doctype, name, ["docstatus", "owner",
				"assigned_to" if parent_doctype == "Asset Service Record" else "owner"], as_dict=True)
			if not parent or parent.docstatus != 0:
				continue
			user = parent.get("assigned_to") or parent.owner
			from upande_asset.upande_asset import emails as em

			lines = [d for d in doc.items if d.get(c["link"]) == name]
			info = frappe.db.get_value(parent_doctype, name, ["asset", "company"], as_dict=True) or frappe._dict()
			asset_name = frappe.db.get_value("Asset", info.asset, "asset_name") if info.asset else None
			body = em.heading(_("Received")) + em.table([_("Item"), _("Qty"), _("Warehouse")], [
				[frappe.utils.escape_html(d.get("item_name") or d.item_code), frappe.utils.escape_html(
					f"{frappe.format(d.get('qty') or 0, 'Float')} {d.get('uom') or ''}".strip()),
				 frappe.utils.escape_html(d.get("warehouse") or "")] for d in lines], numeric=(1,))
			message = em.card(
				area=_("Workshop Parts"), tone="success", status=_("Ready to issue"),
				title=_("Parts received for {0} {1}").format(_(parent_doctype), name),
				intro=frappe.utils.escape_html(_("The parts bought for this job are in the warehouse. Issue them to the job from its "
					"Materials table: Material Request → Request from Stores, or Issue Used Materials.")),
				facts=[
					(_(parent_doctype), em.form_link(parent_doctype, name)),
					(_("Asset"), em.form_link("Asset", info.asset, f"{asset_name or info.asset} ({info.asset})") if info.asset else None),
					(_("Purchase Receipt"), em.form_link("Purchase Receipt", doc.name) if not doc.is_new() else frappe.utils.escape_html(doc.name)),
				],
				body=body, button=(_("Open {0}").format(_(parent_doctype)), get_url_to_form(parent_doctype, name)), company=info.company,
			)
			# Asset Settings → Maintenance: email, SMS, both, or the bell notification only.
			mode = get_settings().get("parts_received_notification") or "Email"
			subject = _("Parts received for {0} {1}").format(_(parent_doctype), name)
			notify([user], subject, message, parent_doctype, name, send_email=mode in ("Email", "Email and SMS"))
			if mode in ("SMS", "Email and SMS"):
				from upande_asset.upande_asset.insurance.utils import send_sms

				items_text = ", ".join(f"{d.get('item_name') or d.item_code} x{frappe.format(d.get('qty') or 0, 'Float')}" for d in lines)
				send_sms([user], _("Parts received for {0} {1}{2}: {3}. Issue them from the job's Materials table.").format(
					_(parent_doctype), name, f" ({asset_name})" if asset_name else "", items_text)[:300])


def migrate_purchase_requests():
	"""Before rows had their own Purchase Request field, purchase / transfer requests sat in
	Material Request. Move them so the row can still get its issue request."""
	frappe.db.sql(
		"""UPDATE `tabAsset Service Material` r JOIN `tabMaterial Request` m ON m.name = r.material_request
		SET r.purchase_request = r.material_request, r.material_request = NULL
		WHERE m.material_request_type IN ('Purchase', 'Material Transfer') AND IFNULL(r.purchase_request, '') = ''"""
	)
	return cint(frappe.db.sql("select row_count()")[0][0])
