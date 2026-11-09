# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Release schedule for opening policies (prepaid balance captured at go-live).

One Journal Entry per remaining month (Dr insurance expense split by cost
center, Cr prepaid insurance). Months already due are submitted immediately;
future months are kept as drafts and submitted by the daily scheduler once
their posting date arrives. ERPNext does not block future-dated JEs, but
posting them early would show expense in future periods on today's reports
and make corrections (endorsements, cancellation) painful."""

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, get_last_day, getdate, today

from upande_asset.upande_asset.insurance.utils import cost_center_weights, get_company_accounts, split_amount, split_evenly

REMARK_TAG = "Insurance release"


def _month_ends(start, end):
	dates = []
	cur = get_last_day(start)
	end = getdate(end)
	while cur < end:
		dates.append(cur)
		cur = get_last_day(frappe.utils.add_months(get_first_day(cur), 1))
	dates.append(end)
	return dates


def generate(policy):
	if policy.docstatus != 1 or policy.is_opening != "Yes":
		frappe.throw(_("Release schedules are generated for submitted opening policies only."))
	if flt(policy.prepaid_balance_at_go_live) <= 0:
		frappe.throw(_("Set Prepaid Balance at Go-Live first."))
	if policy.release_schedule_generated or frappe.db.exists(
		"Journal Entry", {"insurance_policy": policy.name, "docstatus": ["!=", 2], "user_remark": ["like", f"{REMARK_TAG}%"]}
	):
		frappe.throw(_("A release schedule already exists for this policy."))
	accounts = get_company_accounts(policy.company, ("prepaid_insurance_account", "insurance_expense_account"))
	go_live = getdate(policy.go_live_date or today())
	if go_live > getdate(policy.end_date):
		frappe.throw(_("Go-live date is after the policy end date."))
	dates = _month_ends(max(go_live, getdate(policy.start_date)), policy.end_date)
	amounts = split_evenly(policy.prepaid_balance_at_go_live, len(dates))
	weights = cost_center_weights(policy)
	default_cc = frappe.get_cached_value("Company", policy.company, "cost_center")

	created = []
	frappe.db.savepoint("insurance_release")
	try:
		for i, (posting_date, amount) in enumerate(zip(dates, amounts, strict=True), start=1):
			je = frappe.new_doc("Journal Entry")
			je.update({
				"voucher_type": "Journal Entry",
				"company": policy.company,
				"posting_date": posting_date,
				"insurance_policy": policy.name,
				"user_remark": f"{REMARK_TAG} {i}/{len(dates)} – {policy.name} ({policy.policy_number or ''})",
			})
			for (cc, _w), part in zip(weights, split_amount(amount, [w for _c, w in weights]), strict=True):
				if flt(part):
					je.append("accounts", {"account": accounts.insurance_expense_account, "debit_in_account_currency": part, "cost_center": cc})
			je.append("accounts", {"account": accounts.prepaid_insurance_account, "credit_in_account_currency": amount, "cost_center": default_cc})
			je.flags.ignore_permissions = True
			je.insert()
			if getdate(posting_date) <= getdate(today()):
				je.submit()
			created.append(je.name)
		policy.db_set("release_schedule_generated", 1)
	except Exception:
		frappe.db.rollback(save_point="insurance_release")
		raise
	return created


def submit_due_entries():
	"""Scheduler: submit draft release JEs whose posting date has arrived."""
	for name in frappe.get_all(
		"Journal Entry",
		filters={
			"docstatus": 0,
			"insurance_policy": ["is", "set"],
			"user_remark": ["like", f"{REMARK_TAG}%"],
			"posting_date": ["<=", today()],
		},
		pluck="name",
	):
		try:
			je = frappe.get_doc("Journal Entry", name)
			je.flags.ignore_permissions = True
			je.submit()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Insurance release JE {name} failed")
