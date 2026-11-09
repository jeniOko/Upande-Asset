# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Filters shared by every Upande Asset report: Company, Location and the accounting
dimensions (Cost Center plus the instance's own dimensions; Project is left out, as on
the Asset Dashboard). The JS side is public/js/report_filters.js.

Group values (a parent cost center, a parent location) include everything under them.
"""

from collections import defaultdict

import frappe
from frappe.utils import flt


def dimensions():
	from upande_asset.upande_asset.dashboard import _dimensions

	return _dimensions()


def _values(value):
	"""MultiSelectList filters arrive as a list (or a JSON / comma separated string)."""
	if not value:
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value) if value.strip().startswith("[") else [v.strip() for v in value.split(",")]
	return [v for v in value if v]


def _with_children(doctype, values):
	meta = frappe.get_meta(doctype)
	if not meta.is_tree or not values:
		return values
	out = set()
	for v in values:
		lft, rgt = frappe.db.get_value(doctype, v, ["lft", "rgt"]) or (None, None)
		if lft is None:
			out.add(v)
			continue
		out.update(frappe.get_all(doctype, filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name"))
	return sorted(out)


def dimension_values(filters):
	"""{fieldname: [values incl. children]} for every dimension filter that is set."""
	out = {}
	for d in dimensions():
		vals = _values((filters or {}).get(d["fieldname"]))
		if vals:
			out[d["fieldname"]] = _with_children(d["doctype"], vals)
	return out


def locations(filters):
	loc = (filters or {}).get("location")
	return _with_children("Location", _values(loc) if isinstance(loc, list) else ([loc] if loc else []))


def asset_scope(filters):
	"""Asset IDs matching Company, Location and the dimension filters, or None when none
	of them is set (no restriction)."""
	filters = filters or {}
	conds = {}
	if filters.get("company"):
		conds["company"] = filters.get("company")
	if locs := locations(filters):
		conds["location"] = ["in", locs]
	for field, vals in dimension_values(filters).items():
		conds[field] = ["in", vals]
	if not conds:
		return None
	return set(frappe.get_all("Asset", filters=conds, pluck="name"))


def asset_condition(filters, column, params, key="scope_assets"):
	"""SQL fragment limiting `column` (an Asset link) to the scope; "" when unrestricted."""
	scope = asset_scope(filters)
	if scope is None:
		return ""
	if not scope:
		return "1 = 0"
	params[key] = tuple(scope)
	return f"{column} IN %({key})s"


# ── Premium lines (Purchase Invoice Items linked to a policy) ──────────────────


def premium_line_share(lines, filters):
	"""Share (0..1) of each premium line that falls inside the filters.

	Dimensions: premium lines carry the cost center (and any other dimension) of the
	assets they were split for, so they match directly.
	Location: a premium has no location; a line counts for the share of its policy's
	sum insured (for that line's cost center) held by assets at the location.
	Non-asset cover (staff, liability) has no location and drops out when one is set."""
	dims = dimension_values(filters)
	locs = set(locations(filters))
	shares = []
	weights = _location_weights({l.insurance_policy for l in lines}, locs) if locs else {}
	for line in lines:
		share = 1.0
		for field, vals in dims.items():
			if line.get(field) not in vals:
				share = 0.0
				break
		if share and locs:
			share = weights.get((line.insurance_policy, line.cost_center), weights.get((line.insurance_policy, None), 0.0))
		shares.append(share)
	return shares


def _location_weights(policies, locs):
	if not policies:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT row.parent AS policy, IFNULL(row.cost_center, a.cost_center) AS cost_center, a.location, row.sum_insured
		FROM `tabInsurance Policy Asset` row
		LEFT JOIN `tabAsset` a ON a.name = row.asset
		WHERE row.parenttype = 'Insurance Policy' AND row.parent IN %(p)s
		""",
		{"p": tuple(policies)},
		as_dict=True,
	)
	total, inside = defaultdict(float), defaultdict(float)
	for r in rows:
		for key in ((r.policy, r.cost_center), (r.policy, None)):
			total[key] += flt(r.sum_insured)
			if r.location in locs:
				inside[key] += flt(r.sum_insured)
	return {k: (inside[k] / total[k] if total[k] else 0.0) for k in total}


def premium_dimension_fields():
	"""Dimension fields present on Purchase Invoice Item (to select with premium lines)."""
	meta = frappe.get_meta("Purchase Invoice Item")
	return [d["fieldname"] for d in dimensions() if meta.has_field(d["fieldname"])]
