# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Mid-term endorsements on a submitted Insurance Policy.

Pro-rata default:
    rate        = basic_premium / sum_insured_at_inception
    adjustment  = rate × Δsum_insured × remaining_days / policy_days
    remaining   = effective_date → end_date (inclusive)
Cancel Policy returns the unexpired share of total_premium.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from upande_asset.upande_asset.insurance.asset import sync_policy_assets
from upande_asset.upande_asset.insurance.premium import make_endorsement_document
from upande_asset.upande_asset.insurance.utils import policy_days, remaining_days, split_amount

ACTIONS = ("Add Assets", "Remove Assets", "Change Sum Insured", "Cancel Policy")


def _is_asset_policy(policy):
	return (policy.get("insurance_for") or "Assets") == "Assets"


def _policy_cost_center(policy):
	return policy.get("cost_center") or frappe.get_cached_value("Company", policy.company, "cost_center")


def _validate_common(policy, action, payload):
	if policy.docstatus != 1 or policy.status not in ("Active", "Expiring"):
		frappe.throw(_("Only submitted Active / Expiring policies can be endorsed."))
	if action not in ACTIONS:
		frappe.throw(_("Unknown endorsement action {0}.").format(action))
	if not _is_asset_policy(policy) and action in ("Add Assets", "Remove Assets"):
		frappe.throw(_("{0} applies to asset policies only.").format(_(action)))
	eff = getdate(payload.get("effective_date") or nowdate())
	if not (getdate(policy.start_date) <= eff <= getdate(policy.end_date)):
		frappe.throw(_("Effective date must fall within the policy period."))
	return eff


def _rate(policy):
	base = flt(policy.sum_insured_at_inception) or flt(policy.total_sum_insured)
	return flt(policy.basic_premium) / base if base else 0


def _active_row(policy, asset):
	row = next((r for r in policy.insured_assets if r.asset == asset and r.row_status == "Active"), None)
	if not row:
		frappe.throw(_("Asset {0} is not an active row on this policy.").format(asset))
	return row


def compute_lines(policy, action, payload):
	"""[{asset, old, new, amount, cost_center}] with the pro-rata default amounts."""
	eff = _validate_common(policy, action, payload)
	factor = remaining_days(eff, policy.end_date) / policy_days(policy.start_date, policy.end_date)
	rate = _rate(policy)
	lines = []
	if action == "Add Assets":
		for a in payload.get("assets") or []:
			if not a.get("asset"):
				continue
			cc = frappe.db.get_value("Asset", a["asset"], "cost_center")
			new = flt(a.get("sum_insured"))
			lines.append({"asset": a["asset"], "old": 0, "new": new, "cost_center": cc, "amount": flt(rate * new * factor, 2)})
	elif action == "Remove Assets":
		for a in payload.get("assets") or []:
			row = _active_row(policy, a.get("asset"))
			lines.append({"asset": row.asset, "old": flt(row.sum_insured), "new": 0, "cost_center": row.cost_center,
				"amount": flt(-rate * flt(row.sum_insured) * factor, 2)})
	elif action == "Change Sum Insured" and not _is_asset_policy(policy):
		old = flt(policy.total_sum_insured)
		new = flt(payload.get("new_sum_insured"))
		lines.append({"asset": None, "old": old, "new": new, "cost_center": _policy_cost_center(policy),
			"amount": flt(rate * (new - old) * factor, 2)})
	elif action == "Change Sum Insured":
		for a in payload.get("assets") or []:
			row = _active_row(policy, a.get("asset"))
			new = flt(a.get("new_sum_insured"))
			delta = new - flt(row.sum_insured)
			lines.append({"asset": row.asset, "old": flt(row.sum_insured), "new": new, "cost_center": row.cost_center,
				"amount": flt(rate * delta * factor, 2)})
	elif action == "Cancel Policy":
		active = [r for r in policy.insured_assets if r.row_status == "Active"]
		total_return = flt(-flt(policy.total_premium) * factor, 2)
		if active:
			parts = split_amount(total_return, [flt(r.sum_insured) for r in active])
			for r, amt in zip(active, parts, strict=True):
				lines.append({"asset": r.asset, "old": flt(r.sum_insured), "new": 0, "cost_center": r.cost_center, "amount": amt})
		else:
			lines.append({"asset": None, "old": flt(policy.total_sum_insured), "new": 0,
				"cost_center": _policy_cost_center(policy), "amount": total_return})
	if not lines:
		frappe.throw(_("Select at least one asset."))
	return eff, lines


def _apply_override(lines, override):
	if override is None or override == "":
		return lines
	override = flt(override, 2)
	computed = flt(sum(ln["amount"] for ln in lines), 2)
	if computed:
		parts = split_amount(override, [abs(ln["amount"]) or 0 for ln in lines])
	else:
		parts = [override] + [0] * (len(lines) - 1)
	for ln, p in zip(lines, parts, strict=True):
		ln["amount"] = p
	return lines


def preview(policy, action, payload):
	eff, lines = compute_lines(policy, action, payload)
	return {
		"effective_date": eff,
		"remaining_days": remaining_days(eff, policy.end_date),
		"policy_days": policy_days(policy.start_date, policy.end_date),
		"lines": lines,
		"total": flt(sum(ln["amount"] for ln in lines), 2),
	}


def endorse(policy, action, payload):
	endorsement_no = (payload.get("endorsement_no") or "").strip()
	if not endorsement_no:
		frappe.throw(_("Insurer's Endorsement No is required."))
	if any(e.endorsement_no == endorsement_no and e.status == "Active" for e in policy.endorsements):
		frappe.throw(_("Endorsement {0} is already recorded on this policy.").format(endorsement_no))

	eff, lines = compute_lines(policy, action, payload)
	lines = _apply_override(lines, payload.get("premium_adjustment"))
	group = frappe.generate_hash(length=10)
	remarks = payload.get("remarks")

	frappe.db.savepoint("insurance_endorse")
	try:
		ref_dt, ref_name = (None, None)
		if payload.get("create_premium_document", 1):
			ref_dt, ref_name = make_endorsement_document(
				policy, action, eff, [(ln["asset"], ln["cost_center"], ln["amount"]) for ln in lines], endorsement_no
			)

		for ln in lines:
			if action == "Add Assets":
				row = policy.append("insured_assets", {
					"asset": ln["asset"], "sum_insured": ln["new"], "added_on": eff, "row_status": "Active",
				})
				row.name = frappe.generate_hash(length=10)  # stable id for the log before save
				old_status, old_removed = None, None
			elif not ln["asset"]:
				# Policy-level line (non-asset policy): no insured row to touch.
				row, old_status, old_removed = None, None, None
				if action == "Change Sum Insured":
					policy.total_sum_insured = ln["new"]
			else:
				row = _active_row(policy, ln["asset"])
				old_status, old_removed = row.row_status, row.removed_on
				if action in ("Remove Assets", "Cancel Policy"):
					row.row_status, row.removed_on = "Removed", eff
				else:
					row.sum_insured = ln["new"]
			policy.append("endorsements", {
				"endorsement_no": endorsement_no,
				"effective_date": eff,
				"endorsement_type": action,
				"asset": ln["asset"],
				"old_sum_insured": ln["old"],
				"new_sum_insured": ln["new"],
				"premium_adjustment": ln["amount"],
				"reference_doctype": ref_dt,
				"reference_name": ref_name,
				"status": "Active",
				"remarks": remarks,
				"endorsement_group": group,
				"policy_row": row.name if row else None,
				"old_row_status": old_status,
				"old_removed_on": old_removed,
			})
		if action == "Cancel Policy":
			policy.status = "Cancelled"
		policy.flags.ignore_permissions = True
		policy.save()
	except Exception:
		frappe.db.rollback(save_point="insurance_endorse")
		raise

	if action in ("Remove Assets", "Cancel Policy"):
		close_disposal_todos(policy.name, [ln["asset"] for ln in lines if ln["asset"]])
	return {"reference_doctype": ref_dt, "reference_name": ref_name, "endorsement_group": group}


def close_disposal_todos(policy, assets):
	"""The "asset was scrapped / sold, endorse the policy" reminder (marked "[<asset>]")
	is done once the asset has been endorsed off the policy."""
	for asset in assets:
		for name in frappe.get_all("ToDo", filters={"reference_type": "Insurance Policy", "reference_name": policy, "status": "Open",
				"description": ["like", f"%[{asset}]%"]}, pluck="name"):
			frappe.db.set_value("ToDo", name, "status", "Closed")


def reverse(policy, endorsement_group):
	logs = [e for e in policy.endorsements if e.endorsement_group == endorsement_group and e.status == "Active"]
	if not logs:
		frappe.throw(_("Nothing to reverse."))
	ref_dt, ref_name = logs[0].reference_doctype, logs[0].reference_name
	if ref_dt and ref_name and frappe.db.exists(ref_dt, ref_name):
		ds = frappe.db.get_value(ref_dt, ref_name, "docstatus")
		if ds == 1:
			frappe.throw(
				_("{0} {1} is submitted. Cancel it before reversing this endorsement.").format(ref_dt, ref_name)
			)
	# Later endorsements on the same assets would be silently undone – block.
	later = [
		e for e in policy.endorsements
		if e.status == "Active" and e.endorsement_group != endorsement_group and e.idx > max(lg.idx for lg in logs)
		and e.asset in {lg.asset for lg in logs}
	]
	if later:
		frappe.throw(_("Reverse the later endorsement {0} first.").format(later[0].endorsement_no))

	frappe.db.savepoint("insurance_reverse")
	try:
		was_cancel = any(lg.endorsement_type == "Cancel Policy" for lg in logs)
		for lg in logs:
			row = next((r for r in policy.insured_assets if r.name == lg.policy_row), None)
			if not lg.asset and lg.endorsement_type == "Change Sum Insured":
				policy.total_sum_insured = lg.old_sum_insured
			elif lg.endorsement_type == "Add Assets":
				if row:
					policy.remove(row)
			elif row:
				if lg.endorsement_type == "Change Sum Insured":
					row.sum_insured = lg.old_sum_insured
				else:
					row.row_status = lg.old_row_status or "Active"
					row.removed_on = lg.old_removed_on
			lg.status = "Reversed"
		delete_ref = bool(ref_dt and ref_name and frappe.db.get_value(ref_dt, ref_name, "docstatus") == 0)
		if delete_ref:
			# The draft is deleted below; keep its number in remarks instead of a dangling link.
			for lg in logs:
				lg.remarks = "\n".join(filter(None, [lg.remarks, _("Reversed; draft {0} {1} deleted.").format(ref_dt, ref_name)]))
				lg.reference_name = None
		if was_cancel:
			from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import compute_status

			policy.status = None
			policy.status = compute_status(policy)
		policy.flags.ignore_permissions = True
		policy.save()
		if delete_ref:
			frappe.delete_doc(ref_dt, ref_name, ignore_permissions=True)
		sync_policy_assets(policy)
	except Exception:
		frappe.db.rollback(save_point="insurance_reverse")
		raise
	return True
