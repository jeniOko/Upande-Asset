# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Mid-term endorsements on a submitted Insurance Policy.

  request  → logged as Requested (no insurer number needed); the policy is unchanged.
             Extra premium: procurement gets a Material Request / PO. Removals: a ToDo.
  confirm  → the insurer's endorsement number, effective date and premium; the change is
             applied (Active). Returned premium: a Journal Entry (make_refund_entry).
  withdraw → a request dropped before the insurer confirms it.
  reverse  → an applied endorsement undone.
  A Total Loss claim, once approved, removes the asset itself (remove_for_claim).

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
from upande_asset.upande_asset.insurance.premium import endorsement_documents
from upande_asset.upande_asset.insurance.utils import policy_days, remaining_days, split_amount

ACTIONS = ("Add Assets", "Remove Assets", "Change Sum Insured", "Cancel Policy")


def _is_asset_policy(policy):
	return (policy.get("insurance_for") or "Assets") == "Assets"


def _policy_cost_center(policy):
	return policy.get("cost_center") or frappe.get_cached_value("Company", policy.company, "cost_center")


def _validate_common(policy, action, payload):
	from upande_asset.upande_asset.insurance.utils import LIVE_POLICY_STATUSES

	if policy.docstatus != 1 or policy.status not in LIVE_POLICY_STATUSES:
		frappe.throw(_("Only submitted policies whose cover is running can be endorsed."))
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


def _apply_asset_premiums(lines, payload):
	"""A premium suggested per asset in the request (blank = keep the pro-rata figure)."""
	suggested = {a.get("asset"): a.get("premium") for a in payload.get("assets") or []
		if a.get("asset") and a.get("premium") not in (None, "")}
	for ln in lines:
		if ln["asset"] in suggested:
			ln["amount"] = flt(suggested[ln["asset"]], 2)
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


def _apply(policy, action, eff, lines, endorsement_no, remarks, group, reference=None):
	"""Change the policy rows for confirmed endorsement lines and log them as Active."""
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
			"requested_on": ln.get("requested_on"),
			"endorsement_type": action,
			"asset": ln["asset"],
			"old_sum_insured": ln["old"],
			"new_sum_insured": ln["new"],
			"premium_adjustment": ln["amount"],
			"status": "Active",
			"remarks": remarks,
			"endorsement_group": group,
			"policy_row": row.name if row else None,
			"old_row_status": old_status,
			"old_removed_on": old_removed,
			"reference_doctype": (reference or (None, None))[0],
			"reference_name": (reference or (None, None))[1],
		})
	if action == "Cancel Policy":
		policy.status = "Cancelled"


def _save(policy):
	policy.flags.ignore_permissions = True
	policy.save()


def request(policy, action, payload):
	"""Endorsement request: what the manager wants changed, with the pro-rata premium as an
	estimate. The policy is not changed until the insurer confirms (confirm). Extra premium is
	then requested from procurement (premium.make_endorsement_document); removals go to the
	insurance role as a ToDo."""
	eff, lines = compute_lines(policy, action, payload)
	lines = _apply_override(lines, payload.get("premium_adjustment"))
	lines = _apply_asset_premiums(lines, payload)
	pending = {e.asset for e in policy.endorsements if e.status == "Requested" and e.asset}
	clash = [ln["asset"] for ln in lines if ln["asset"] and ln["asset"] in pending]
	if clash:
		frappe.throw(_("{0} already has an endorsement request waiting for the insurer. Confirm or withdraw it first.").format(
			", ".join(clash)), title=_("Request Pending"))
	if action == "Add Assets":
		_validate_new_assets(policy, eff, [ln["asset"] for ln in lines])
	if payload.get("create_premium_document", 1) and flt(sum(ln["amount"] for ln in lines), 2) > 0:
		# Fail now, not after recording, if procurement could not be asked for the premium.
		from upande_asset.upande_asset.insurance.premium import PREMIUM_ACCOUNTS
		from upande_asset.upande_asset.insurance.utils import get_company_accounts, get_premium_item

		get_premium_item()
		get_company_accounts(policy.company, PREMIUM_ACCOUNTS)
	group = frappe.generate_hash(length=10)
	for ln in lines:
		policy.append("endorsements", {
			"effective_date": eff, "requested_on": nowdate(), "endorsement_type": action, "asset": ln["asset"],
			"old_sum_insured": ln["old"], "new_sum_insured": ln["new"], "premium_adjustment": ln["amount"],
			"status": "Requested", "remarks": payload.get("remarks"), "endorsement_group": group,
		})
	_save(policy)
	_request_todo(policy, action, group, lines)
	return {"endorsement_group": group, "total": flt(sum(ln["amount"] for ln in lines), 2), "status": "Requested"}


def _validate_new_assets(policy, eff, assets):
	"""Fail at request time for assets the policy could never take (the full check runs on confirm)."""
	for asset in assets:
		if any(r.asset == asset and r.row_status == "Active" for r in policy.insured_assets):
			frappe.throw(_("Asset {0} is already on this policy.").format(asset))


def _request_todo(policy, action, group, lines):
	"""One ToDo for the insurance role per request: send it to the insurer, then confirm it."""
	from frappe.utils import fmt_money

	from upande_asset.upande_asset.insurance.utils import get_settings

	role = get_settings().endorsement_todo_role or "Accounts Manager"
	assets = [ln["asset"] for ln in lines if ln["asset"]]
	total = flt(sum(ln["amount"] for ln in lines), 2)
	currency = frappe.get_cached_value("Company", policy.company, "default_currency")
	todo = frappe.get_doc({
		"doctype": "ToDo", "status": "Open", "priority": "Medium", "role": role, "date": nowdate(),
		"reference_type": "Insurance Policy", "reference_name": policy.name,
		"description": _("Endorsement request [req:{0}] {1} on {2} ({3}){4}. Estimated premium {5}. "
			"Send it to the insurer, then use Endorse → Confirm Endorsement with their endorsement number.").format(
			group, _(action), policy.policy_number or policy.name, policy.policy_type,
			(": " + ", ".join(f"[{a}]" for a in assets)) if assets else "", fmt_money(total, currency=currency)),
	})
	todo.flags.ignore_permissions = True
	todo.insert()


def _close_request_todo(policy, group):
	for name in frappe.get_all("ToDo", filters={"reference_type": "Insurance Policy", "reference_name": policy,
			"status": "Open", "description": ["like", f"%[req:{group}]%"]}, pluck="name"):
		frappe.db.set_value("ToDo", name, "status", "Closed")


def _requested(policy, group):
	logs = [e for e in policy.endorsements if e.endorsement_group == group and e.status == "Requested"]
	if not logs:
		frappe.throw(_("No endorsement request waiting for the insurer."))
	return logs


def _payload_from(logs, eff):
	action = logs[0].endorsement_type
	payload = {"effective_date": eff}
	if action == "Add Assets":
		payload["assets"] = [{"asset": e.asset, "sum_insured": e.new_sum_insured} for e in logs]
	elif action == "Remove Assets":
		payload["assets"] = [{"asset": e.asset} for e in logs]
	elif action == "Change Sum Insured":
		payload["assets"] = [{"asset": e.asset, "new_sum_insured": e.new_sum_insured} for e in logs if e.asset]
		payload["new_sum_insured"] = logs[0].new_sum_insured
	return payload


def confirm(policy, group, payload):
	"""The insurer has endorsed the policy: record their number, effective date and premium,
	and apply the change to the policy."""
	logs = _requested(policy, group)
	endorsement_no = (payload.get("endorsement_no") or "").strip()
	if not endorsement_no:
		frappe.throw(_("Enter the insurer's Endorsement No."))
	if any(e.endorsement_no == endorsement_no and e.status == "Active" for e in policy.endorsements):
		frappe.throw(_("Endorsement {0} is already recorded on this policy.").format(endorsement_no))
	action = logs[0].endorsement_type
	eff = getdate(payload.get("effective_date") or logs[0].effective_date)
	eff, lines = compute_lines(policy, action, _payload_from(logs, eff))
	premium = payload.get("premium_adjustment")
	requested_total = flt(sum(flt(e.premium_adjustment) for e in logs), 2)
	if premium in (None, "") or flt(premium, 2) == requested_total:
		if eff == getdate(logs[0].effective_date):
			# The request's own figures, asset by asset.
			by_asset = {e.asset: flt(e.premium_adjustment) for e in logs}
			for ln in lines:
				ln["amount"] = by_asset.get(ln["asset"], ln["amount"])
	else:
		lines = _apply_override(lines, premium)  # the insurer's total, shared out
	remarks = "\n".join(filter(None, {e.remarks for e in logs} | {payload.get("remarks")}))
	reference = (logs[0].reference_doctype, logs[0].reference_name) if logs[0].reference_name else None
	for ln in lines:
		ln["requested_on"] = logs[0].requested_on

	frappe.db.savepoint("insurance_endorse")
	try:
		for e in logs:
			policy.remove(e)
		_apply(policy, action, eff, lines, endorsement_no, remarks, group, reference)
		_save(policy)
	except Exception:
		frappe.db.rollback(save_point="insurance_endorse")
		raise
	_close_request_todo(policy.name, group)
	if action in ("Remove Assets", "Cancel Policy"):
		close_disposal_todos(policy.name, [ln["asset"] for ln in lines if ln["asset"]])
	return {"endorsement_group": group, "total": flt(sum(ln["amount"] for ln in lines), 2), "status": "Active"}


def endorse(policy, action, payload):
	"""Request and confirm at once: the insurer has already endorsed the policy."""
	res = request(policy, action, payload)
	return confirm(frappe.get_doc("Insurance Policy", policy.name), res["endorsement_group"], payload)


def withdraw(policy, group, reason=None):
	"""Drop a request the insurer has not confirmed. Draft premium requests made for it are deleted."""
	from upande_asset.upande_asset.insurance.premium import endorsement_documents

	logs = _requested(policy, group)
	documents = endorsement_documents(policy.name, group)
	submitted = [(dt, dn) for dt, dn in documents if frappe.db.get_value(dt, dn, "docstatus") == 1]
	if submitted:
		frappe.throw(_("{0} {1} is submitted. Cancel it before withdrawing this request.").format(_(submitted[0][0]), submitted[0][1]))
	note = reason or _("Withdrawn.")
	if documents:
		note += " " + _("Draft {0} deleted.").format(", ".join(f"{_(dt)} {dn}" for dt, dn in documents))
	for e in logs:
		e.status = "Withdrawn"
		e.remarks = "\n".join(filter(None, [e.remarks, note]))
		e.reference_doctype = e.reference_name = None
	_save(policy)
	for dt, dn in sorted(documents, key=lambda d: ("Journal Entry", "Purchase Invoice", "Purchase Order", "Material Request").index(d[0])):
		frappe.delete_doc(dt, dn, ignore_permissions=True)
	_close_request_todo(policy.name, group)
	return True


# ── Returned premium: a Journal Entry ─────────────────────────────────────────


def refund_entries(policy, group, docstatus=(0, 1)):
	return frappe.get_all("Journal Entry", filters={"insurance_policy": policy, "insurance_endorsement": group,
		"docstatus": ["in", list(docstatus)]}, pluck="name")


def make_refund_entry(policy, group):
	"""Unsaved Journal Entry for premium the insurer returns on a confirmed endorsement:
	Dr insurer (their payable account) / Cr insurance expense.

	Crediting expense, not prepaid: the premium invoice keeps releasing its prepaid balance to
	expense every month (ERPNext deferred expense), so by the end of the cover period expense
	is net of the refund and prepaid ends at zero."""
	from erpnext.accounts.party import get_party_account

	from upande_asset.upande_asset.insurance.utils import get_company_accounts

	logs = [e for e in policy.endorsements if e.endorsement_group == group and e.status == "Active"]
	if not logs:
		frappe.throw(_("Confirm the endorsement with the insurer before recording its refund."))
	total = flt(sum(flt(e.premium_adjustment) for e in logs), 2)
	if total >= 0:
		frappe.throw(_("Endorsement {0} returns no premium.").format(logs[0].endorsement_no))
	if refund_entries(policy.name, group):
		frappe.throw(_("The refund for endorsement {0} is already recorded.").format(logs[0].endorsement_no))
	expense = get_company_accounts(policy.company, ("insurance_expense_account",)).insurance_expense_account
	payable = get_party_account("Supplier", policy.insurer, policy.company)
	rows = {r.name: r for r in policy.get("insured_assets") or []}
	default_cc = policy.get("cost_center") or frappe.get_cached_value("Company", policy.company, "cost_center")
	je = frappe.new_doc("Journal Entry")
	je.update({
		"voucher_type": "Journal Entry", "company": policy.company, "posting_date": nowdate(),
		"insurance_policy": policy.name, "insurance_endorsement": group,
		"user_remark": _("Premium returned by {0} on endorsement {1} ({2}) of policy {3}.").format(
			policy.insurer_name or policy.insurer, logs[0].endorsement_no, _(logs[0].endorsement_type), policy.policy_number or policy.name),
	})
	je.append("accounts", {"account": payable, "party_type": "Supplier", "party": policy.insurer,
		"debit_in_account_currency": -total, "cost_center": default_cc})
	for e in logs:
		if not flt(e.premium_adjustment):
			continue
		row = rows.get(e.policy_row)
		cc = (row.cost_center if row else None) or (e.asset and frappe.db.get_value("Asset", e.asset, "cost_center")) or default_cc
		je.append("accounts", {"account": expense, "credit_in_account_currency": -flt(e.premium_adjustment), "cost_center": cc,
			"user_remark": e.asset or ""})
	je.set_onload("load_after_mapping", True)
	return je


def journal_entry_on_update(doc, method=None):
	"""A saved refund entry is the endorsement's document."""
	if doc.get("insurance_endorsement") and doc.get("insurance_policy"):
		for name in frappe.get_all("Insurance Policy Endorsement Log", pluck="name", filters={"parenttype": "Insurance Policy",
				"parent": doc.insurance_policy, "endorsement_group": doc.insurance_endorsement}):
			frappe.db.set_value("Insurance Policy Endorsement Log", name,
				{"reference_doctype": "Journal Entry", "reference_name": doc.name}, update_modified=False)


def journal_entry_on_trash(doc, method=None):
	frappe.db.sql("""UPDATE `tabInsurance Policy Endorsement Log` SET reference_doctype = NULL, reference_name = NULL
		WHERE reference_doctype = 'Journal Entry' AND reference_name = %s""", doc.name)


# ── Total loss: an approved claim takes the asset off cover ───────────────────


def remove_for_claim(claim):
	"""Approving a Total Loss claim (e.g. an animal died) removes the asset from every live policy
	from the incident date, with no refund: the claim pays out instead. Pending requests for the
	asset are withdrawn."""
	from upande_asset.upande_asset.insurance.utils import LIVE_POLICY_STATUSES

	row = frappe.qb.DocType("Insurance Policy Asset")
	pol = frappe.qb.DocType("Insurance Policy")
	policies = (
		frappe.qb.from_(row).join(pol).on(pol.name == row.parent).select(pol.name).distinct()
		.where(row.parenttype == "Insurance Policy").where(row.asset == claim.asset).where(row.row_status == "Active")
		.where(pol.docstatus == 1).where(pol.status.isin(LIVE_POLICY_STATUSES))
		.run(pluck=True)
	)
	for name in policies:
		policy = frappe.get_doc("Insurance Policy", name)
		r = _active_row(policy, claim.asset)
		eff = min(max(getdate(claim.incident_date or nowdate()), getdate(policy.start_date)), getdate(policy.end_date))
		withdrawn = {e.endorsement_group for e in policy.endorsements if e.status == "Requested" and e.asset == claim.asset}
		for e in policy.endorsements:
			if e.endorsement_group in withdrawn and e.status == "Requested":
				e.status = "Withdrawn"
				e.remarks = "\n".join(filter(None, [e.remarks, _("Withdrawn: asset removed by Insurance Claim {0}.").format(claim.name)]))
		_apply(policy, "Remove Assets", eff,
			[{"asset": claim.asset, "old": flt(r.sum_insured), "new": 0, "amount": 0, "cost_center": r.cost_center}],
			None, _("Total loss: removed by Insurance Claim {0}.").format(claim.name), frappe.generate_hash(length=10),
			("Insurance Claim", claim.name))
		_save(policy)
		for group in withdrawn:
			_close_request_todo(policy.name, group)
		close_disposal_todos(policy.name, [claim.asset])
		sync_policy_assets(policy)


def restore_for_claim(claim):
	"""Cancelling the claim puts the asset back on cover."""
	for name, group in frappe.get_all("Insurance Policy Endorsement Log", fields=["parent", "endorsement_group"],
			filters={"parenttype": "Insurance Policy", "reference_doctype": "Insurance Claim", "reference_name": claim.name,
				"status": "Active"}, as_list=True):
		reverse(frappe.get_doc("Insurance Policy", name), group)


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
	documents = endorsement_documents(policy.name, endorsement_group)
	documents += [("Journal Entry", n) for n in refund_entries(policy.name, endorsement_group)]
	ref_dt, ref_name = logs[0].reference_doctype, logs[0].reference_name
	if ref_dt and ref_name and ref_dt != "Insurance Claim" and (ref_dt, ref_name) not in documents \
			and frappe.db.get_value(ref_dt, ref_name, "docstatus") in (0, 1):
		documents.append((ref_dt, ref_name))  # recorded before rows carried the endorsement
	submitted = [(dt, dn) for dt, dn in documents if frappe.db.get_value(dt, dn, "docstatus") == 1]
	if submitted:
		frappe.throw(
			_("{0} {1} is submitted. Cancel it before reversing this endorsement.").format(_(submitted[0][0]), submitted[0][1])
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
		if documents:
			# The drafts are deleted below; keep their numbers in remarks instead of dangling links.
			note = _("Reversed; draft {0} deleted.").format(", ".join(f"{_(dt)} {dn}" for dt, dn in documents))
			for lg in logs:
				lg.remarks = "\n".join(filter(None, [lg.remarks, note]))
				if lg.reference_doctype != "Insurance Claim":
					lg.reference_doctype = lg.reference_name = None
		if was_cancel:
			from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import compute_status

			policy.status = None
			policy.status = compute_status(policy)
		policy.flags.ignore_permissions = True
		policy.save()
		# Invoices first, then the POs and MRs they were made from.
		for dt, dn in sorted(documents, key=lambda d: ("Journal Entry", "Purchase Invoice", "Purchase Order", "Material Request").index(d[0])):
			frappe.delete_doc(dt, dn, ignore_permissions=True)
		sync_policy_assets(policy)
	except Exception:
		frappe.db.rollback(save_point="insurance_reverse")
		raise
	return True
