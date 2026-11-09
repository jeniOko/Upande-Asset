# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Asset-side integration: native insurance field sync, cover status, dashboard,
and disposal (scrap / sale) notifications."""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, fmt_money, get_link_to_form, getdate, today

from upande_asset.upande_asset.insurance.utils import (
	LIVE_POLICY_STATUSES,
	get_covering_rows,
	get_expiring_threshold,
	users_with_role,
)

NATIVE_FIELDS = (
	"insurance_policy",
	"policy_number",
	"insurer",
	"insured_value",
	"insurance_start_date",
	"insurance_end_date",
	"comprehensive_insurance",
)



def _is_comprehensive(policy_type):
	"""Insurance Policy Type → Comprehensive cover."""
	return bool(policy_type and frappe.get_cached_value("Insurance Policy Type", policy_type, "comprehensive_cover"))


def _primary_cover(asset):
	"""The policy row that should be mirrored onto the Asset: covering today
	(Comprehensive first, then latest end date), else the nearest upcoming one."""
	rows = [r for r in get_covering_rows(asset) if r.status != "Cancelled"]
	if not rows:
		pol = frappe.qb.DocType("Insurance Policy")
		row = frappe.qb.DocType("Insurance Policy Asset")
		rows = (
			frappe.qb.from_(row)
			.join(pol)
			.on(pol.name == row.parent)
			.select(
				pol.name.as_("policy"), pol.policy_type, pol.policy_number, pol.insurer, pol.insurer_name,
				pol.start_date, pol.end_date, pol.status, row.sum_insured,
			)
			.where(row.parenttype == "Insurance Policy")
			.where(row.asset == asset)
			.where(row.row_status == "Active")
			.where(pol.docstatus == 1)
			.where(pol.status.notin(["Cancelled"]))
			.where(pol.start_date > today())
			.orderby(pol.start_date)
			.limit(1)
			.run(as_dict=True)
		)
		return rows[0] if rows else None
	# Comprehensive cover first, then the cover that runs longest.
	rows.sort(key=lambda r: (0 if _is_comprehensive(r.policy_type) else 1, -getdate(r.end_date).toordinal()))
	return rows[0]


def sync_asset_insurance(asset):
	"""Recompute the Asset's native insurance fields from submitted policies.

	Fields are only cleared when they were previously written by this app
	(i.e. `insurance_policy` is set) – manually entered data on assets that were
	never on an Insurance Policy is left untouched."""
	if not asset or not frappe.db.exists("Asset", asset):
		return
	current = frappe.db.get_value("Asset", asset, NATIVE_FIELDS, as_dict=True)
	cover = _primary_cover(asset)
	if cover:
		values = {
			"insurance_policy": cover.policy,
			"policy_number": cover.policy_number,
			"insurer": cover.insurer_name or cover.insurer,
			"insured_value": str(flt(cover.sum_insured, 2)),
			"insurance_start_date": cover.start_date,
			"insurance_end_date": cover.end_date,
			"comprehensive_insurance": "Yes" if _is_comprehensive(cover.policy_type) else "No",
		}
	elif current.insurance_policy:
		values = {f: None for f in NATIVE_FIELDS}
	else:
		return
	if any(str(current.get(k) or "") != str(v or "") for k, v in values.items()):
		frappe.db.set_value("Asset", asset, values, update_modified=False)


def sync_policy_assets(policy_doc):
	for asset in {r.asset for r in policy_doc.get("insured_assets") or [] if r.asset}:
		sync_asset_insurance(asset)


@frappe.whitelist()
def get_asset_insurance_status(asset):
	"""Used by the Asset form headline/indicator."""
	frappe.has_permission("Asset", "read", asset, throw=True)
	if not frappe.db.get_value("Asset", asset, "insurance_required"):
		return {"required": 0}
	rows = [r for r in get_covering_rows(asset) if r.status != "Cancelled"]
	if not rows:
		return {"required": 1, "state": "Uninsured", "policies": []}
	threshold = get_expiring_threshold()
	latest_end = max(getdate(r.end_date) for r in rows)
	days_left = date_diff(latest_end, today())
	# A submitted renewal that starts the day after means cover is not really expiring.
	renewed = frappe.db.exists(
		"Insurance Policy",
		{"renewed_from": ["in", [r.policy for r in rows]], "docstatus": 1},
	)
	state = "Cover expiring" if (days_left <= threshold and not renewed) else "Insured"
	return {
		"required": 1,
		"state": state,
		"days_left": days_left,
		"policies": [
			{"policy": r.policy, "policy_type": r.policy_type, "end_date": r.end_date, "sum_insured": r.sum_insured}
			for r in rows
		],
	}


def get_asset_dashboard_data(data):
	"""override_doctype_dashboards hook for Asset."""
	data = data or {}
	data.setdefault("non_standard_fieldnames", {})
	data.setdefault("transactions", [])
	data["non_standard_fieldnames"].update(
		{"Insurance Policy": "asset", "Insurance Claim": "asset", "Asset Check Sheet": "asset"}
	)
	items = ["Insurance Policy", "Insurance Claim"]
	data["transactions"].append({"label": _("Insurance"), "items": items})
	ops = ["Asset Check Sheet"]
	if frappe.db.exists("DocType", "Asset Task"):
		data["non_standard_fieldnames"]["Asset Task"] = "tractor"
		ops.append("Asset Task")
	data["transactions"].append({"label": _("Operations"), "items": ops})
	return data


# ── doc_events ────────────────────────────────────────────────────────────────


def asset_on_update_after_submit(doc, method=None):
	before = doc.get_doc_before_save()
	if not before or not before.get("insurance_required") or doc.get("insurance_required"):
		return
	rows = [r for r in get_covering_rows(doc.name) if r.status in LIVE_POLICY_STATUSES and r.row_status == "Active"]
	if not rows:
		return
	links = "".join(
		"<li>{0} ({1})</li>".format(get_link_to_form("Insurance Policy", r.policy), r.policy_type) for r in rows
	)
	frappe.msgprint(
		_(
			"Asset {0} is no longer flagged as requiring insurance but is still an active row on:<ul>{1}</ul>"
			"Endorse the policy to remove it so the premium is adjusted."
		).format(frappe.bold(doc.name), links),
		title=_("Asset still on an active policy"),
		indicator="orange",
		primary_action={
			"label": _("Endorse → Remove Assets"),
			"client_action": "upande_asset.insurance.open_endorse",
			"args": {"policy": rows[0].policy, "action": "Remove Assets", "asset": doc.name},
		},
	)


def notify_disposed_asset(asset, reason):
	"""Create one open ToDo (role: Accounts Manager) per policy that still carries
	the disposed asset as an active row. Never modifies the policy itself."""
	from upande_asset.upande_asset.insurance.utils import get_settings

	endorsement_role = get_settings().endorsement_todo_role or "Accounts Manager"
	rows = [
		r
		for r in get_covering_rows(asset)
		if r.status in LIVE_POLICY_STATUSES and r.row_status == "Active"
	]
	for r in rows:
		marker = f"[{asset}]"
		if frappe.db.exists(
			"ToDo",
			{
				"reference_type": "Insurance Policy",
				"reference_name": r.policy,
				"status": "Open",
				"description": ["like", f"%{marker}%"],
			},
		):
			continue
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"status": "Open",
				"priority": "High",
				"role": endorsement_role,
				"date": today(),
				"reference_type": "Insurance Policy",
				"reference_name": r.policy,
				"description": _(
					"Asset {0} {1} was {2}. It is still an active row on policy {3} ({4}, sum insured {5}). "
					"Endorse the policy (Endorse → Remove Assets) to claim the return premium."
				).format(
					marker,
					frappe.db.get_value("Asset", asset, "asset_name") or "",
					reason,
					r.policy,
					r.policy_type,
					fmt_money(r.sum_insured),
				),
			}
		)
		todo.flags.ignore_permissions = True
		todo.insert()
		for user in users_with_role(endorsement_role):
			try:
				from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

				enqueue_create_notification(
					user,
					{
						"type": "Alert",
						"subject": _("Endorse policy {0}: asset {1} {2}").format(r.policy, asset, reason),
						"document_type": "Insurance Policy",
						"document_name": r.policy,
					},
				)
			except Exception:
				pass


def journal_entry_on_submit(doc, method=None):
	"""Scrap hook: ERPNext books scrapping as a JE with voucher_type 'Asset Disposal'."""
	if doc.voucher_type != "Asset Disposal":
		return
	for asset in {a.reference_name for a in doc.accounts if a.reference_type == "Asset" and a.reference_name}:
		if frappe.db.get_value("Asset", asset, "insurance_required"):
			notify_disposed_asset(asset, _("scrapped"))


def sales_invoice_on_submit(doc, method=None):
	for item in doc.items:
		if item.get("is_fixed_asset") and item.get("asset") and frappe.db.get_value("Asset", item.asset, "insurance_required"):
			notify_disposed_asset(item.asset, _("sold ({0})").format(doc.name))
