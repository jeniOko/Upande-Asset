# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Accounting dimensions (Cost Center plus the instance's own dimensions) on documents
tied to an asset: Insurance Claim, Asset Service Record, Asset Repair.

Documents raised for a repair / service record (Material Request, Purchase Order, Purchase
Receipt, Purchase Invoice, Stock Entry) carry the dimensions on every row; the header of those
that have one is filled from the rows too (fill_header_from_rows).

Values default from the asset and are copied onto every accounting row the document
creates (Journal Entry, Payment Entry, Material Request, Stock Entry rows).
"""

import frappe


def dimension_fields(doctype):
	"""Cost Center plus the enabled accounting dimensions that exist on `doctype`."""
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions

	meta = frappe.get_meta(doctype)
	return [f for f in ["cost_center", *get_accounting_dimensions()] if meta.has_field(f)]


def default_from_asset(doc):
	"""Fill the document's dimensions from its asset: all of them when the asset changes,
	otherwise only the empty ones (so a value set by hand stays)."""
	if not doc.get("asset"):
		return
	asset_changed = doc.is_new() or doc.has_value_changed("asset")
	fields = [f for f in dimension_fields(doc.doctype) if frappe.get_meta("Asset").has_field(f)]
	values = frappe.db.get_value("Asset", doc.asset, fields, as_dict=True) or {}
	for field, value in values.items():
		if value and (asset_changed or not doc.get(field)):
			doc.set(field, value)


def get_dimensions(doc, target_doctype=None):
	"""{fieldname: value} for the document's dimensions, limited to the fields `target_doctype`
	has. An empty value falls back to the asset's; Cost Center last to the company's."""
	fields = dimension_fields(doc.doctype)
	out = {f: doc.get(f) for f in fields if doc.get(f)}
	missing = [f for f in fields if f not in out]
	if missing and doc.get("asset"):
		asset_meta = frappe.get_meta("Asset")
		from_asset = [f for f in missing if asset_meta.has_field(f)]
		if from_asset:
			values = frappe.db.get_value("Asset", doc.asset, from_asset, as_dict=True) or {}
			out.update({f: v for f, v in values.items() if v})
	out["cost_center"] = out.get("cost_center") or frappe.get_cached_value("Company", doc.company, "cost_center")
	if target_doctype:
		meta = frappe.get_meta(target_doctype)
		out = {f: v for f, v in out.items() if meta.has_field(f)}
	return out


LINKED_ROW_FIELDS = ("asset_repair", "asset_service_record")


def fill_header_from_rows(doc, method=None, *args, **kwargs):
	"""after_mapping / validate of a PO, Purchase Receipt, Purchase Invoice or Stock Entry made
	for a repair or service record: an empty header dimension takes the value all its rows share.

	The header is what the user sees in the form's Accounting Dimensions section and what ERPNext
	gives new rows; a header value also stops ERPNext's client from replacing mapped row values
	with the company's default dimensions when the new form opens (dimension_tree_filter.js)."""
	rows = doc.get("items") or []
	if not rows or not any(d.get(f) for d in rows for f in LINKED_ROW_FIELDS):
		return
	for field in dimension_fields(doc.doctype):
		if doc.get(field):
			continue
		values = {d.get(field) for d in rows}
		if len(values) == 1 and (value := values.pop()):
			doc.set(field, value)


def header_values(doc, target_doctype):
	"""{fieldname: value} of `doc`'s dimensions that `target_doctype` has in its header."""
	return get_dimensions(doc, target_doctype)
