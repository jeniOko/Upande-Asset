# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Shared helpers for the Asset Insurance feature.

ERPNext v16 facts this module relies on (verified against erpnext 16.53):
  * Asset native insurance fields: policy_number (Data), insurer (Data),
    insured_value (Data), insurance_start_date, insurance_end_date,
    comprehensive_insurance (Data) – all inside `insurance_section`.
  * Asset book value: `value_after_depreciation`; for depreciating assets the
    finance-book aware value comes from asset.get_value_after_depreciation().
  * Asset Repair cost fields: `repair_cost` (service cost) and
    `total_repair_cost` (repair_cost + consumed stock items).
  * Material Request Item.warehouse is NOT mandatory (reqd=0, no
    mandatory_depends_on) – non-stock MR lines can be saved without one.
  * Scrapping (erpnext.assets.doctype.asset.depreciation.scrap_asset) sets the
    status with db_set and books a Journal Entry with voucher_type
    "Asset Disposal" whose rows reference the Asset.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today

ACCOUNT_FIELDS = (
	"prepaid_insurance_account",
	"insurance_expense_account",
	"claim_recoverable_account",
	"claim_recovery_income_account",
	"claim_shortfall_account",
)

LIVE_POLICY_STATUSES = ("Active", "Expiring")
SETTINGS = "Asset Settings"


def get_settings():
	return frappe.get_cached_doc(SETTINGS)


def get_company_defaults(company):
	"""The company's row in Asset Settings → Defaults (warehouses and insurance accounts), or {}."""
	row = next((r for r in get_settings().get("company_defaults") or [] if r.company == company), None)
	return frappe._dict(row.as_dict() if row else {})


def get_company_accounts(company, required=()):
	"""Return a dict of the five insurance accounts for `company`.
	Throws a helpful message listing any `required` account that is missing."""
	row = get_company_defaults(company) or None
	accounts = {f: (row.get(f) if row else None) for f in ACCOUNT_FIELDS}
	missing = [f for f in required if not accounts.get(f)]
	if missing:
		labels = ", ".join(frappe.unscrub(f) for f in missing)
		frappe.throw(
			_("Set {0} for company {1} in {2}.").format(
				labels, frappe.bold(company), frappe.utils.get_link_to_form(SETTINGS, SETTINGS)
			),
			title=_("Insurance Accounts Missing"),
		)
	return frappe._dict(accounts)


def get_premium_item():
	item = get_settings().premium_item
	if not item:
		frappe.throw(
			_("Set the Premium Item in {0}.").format(frappe.utils.get_link_to_form(SETTINGS, SETTINGS)),
			title=_("Premium Item Missing"),
		)
	return item


def get_alert_days():
	raw = get_settings().renewal_alert_days or "60,30,7"
	days = sorted({cint(d.strip()) for d in raw.split(",") if d.strip().isdigit() and cint(d.strip()) > 0}, reverse=True)
	return days or [60, 30, 7]


def get_expiring_threshold():
	return cint(get_settings().expiring_threshold_days) or 30


def get_asset_book_value(asset):
	"""Current value after depreciation (finance-book aware where possible)."""
	try:
		from erpnext.assets.doctype.asset.asset import _get_asset_value_after_depreciation

		return flt(_get_asset_value_after_depreciation(asset))
	except Exception:
		return flt(frappe.db.get_value("Asset", asset, "value_after_depreciation"))


def policy_days(start_date, end_date):
	return date_diff(end_date, start_date) + 1


def remaining_days(effective_date, end_date):
	return max(0, date_diff(end_date, effective_date) + 1)


def split_amount(total, weights, precision=2):
	"""Split `total` across `weights` pro-rata; rounding remainder goes on the last part
	so the parts always sum exactly to `total`."""
	total = flt(total, precision)
	if not weights:
		return []
	weight_sum = sum(flt(w) for w in weights)
	if not weight_sum:
		weights = [1] * len(weights)
		weight_sum = len(weights)
	parts = [flt(total * flt(w) / weight_sum, precision) for w in weights[:-1]]
	parts.append(flt(total - sum(parts), precision))
	return parts


def split_evenly(total, n, precision=2):
	return split_amount(total, [1] * n, precision)


def cost_center_weights(policy):
	"""[(cost_center, active sum insured)] for the policy, falling back to the company default."""
	default_cc = frappe.get_cached_value("Company", policy.company, "cost_center")
	if (policy.get("insurance_for") or "Assets") != "Assets":
		return [(policy.get("cost_center") or default_cc, 1)]
	weights = {}
	for row in policy.get("insured_assets") or []:
		if row.row_status != "Active":
			continue
		cc = row.cost_center or default_cc
		weights[cc] = weights.get(cc, 0) + flt(row.sum_insured)
	if not weights:
		weights[default_cc] = 1
	return list(weights.items())


def get_covering_rows(asset, on_date=None, policy_type=None, exclude_policy=None):
	"""Policy rows covering `asset` on `on_date`.

	Coverage uses the row's own window (added_on .. removed_on) clipped to the
	policy period, so an incident before an asset's removal is still covered."""
	on_date = getdate(on_date or today())
	pol = frappe.qb.DocType("Insurance Policy")
	row = frappe.qb.DocType("Insurance Policy Asset")
	q = (
		frappe.qb.from_(row)
		.join(pol)
		.on(pol.name == row.parent)
		.select(
			pol.name.as_("policy"),
			pol.policy_type,
			pol.policy_number,
			pol.insurer,
			pol.insurer_name,
			pol.start_date,
			pol.end_date,
			pol.status,
			row.name.as_("row_name"),
			row.sum_insured,
			row.row_status,
			row.added_on,
			row.removed_on,
		)
		.where(row.parenttype == "Insurance Policy")
		.where(row.asset == asset)
		.where(pol.docstatus == 1)
		.where(pol.start_date <= on_date)
		.where(pol.end_date >= on_date)
		.orderby(pol.end_date, order=frappe.qb.desc)
	)
	if policy_type:
		q = q.where(pol.policy_type == policy_type)
	if exclude_policy:
		q = q.where(pol.name != exclude_policy)
	out = []
	for r in q.run(as_dict=True):
		if r.added_on and getdate(r.added_on) > on_date:
			continue
		if r.row_status == "Removed" and (not r.removed_on or getdate(r.removed_on) <= on_date):
			continue
		out.append(r)
	return out


def users_with_role(*roles):
	if not roles:
		return []
	has_role = frappe.qb.DocType("Has Role")
	user = frappe.qb.DocType("User")
	rows = (
		frappe.qb.from_(has_role)
		.join(user)
		.on(user.name == has_role.parent)
		.select(user.name)
		.distinct()
		.where(has_role.parenttype == "User")
		.where(has_role.role.isin(list(roles)))
		.where(user.enabled == 1)
		.where(user.user_type == "System User")
		.where(user.name.notin(["Administrator", "Guest"]))
		.run(pluck=True)
	)
	return rows


def setting_roles(fieldname):
	"""Roles picked in an Asset Settings role table (Table MultiSelect of Asset Settings Role)."""
	return [r.role for r in (get_settings().get(fieldname) or []) if r.role]


def users_for_setting(fieldname):
	return users_with_role(*setting_roles(fieldname))


def notify(users, subject, message, doctype=None, name=None, emails=None):
	"""System notification + email. Never raises (scheduler-safe)."""
	users = sorted({u for u in (users or []) if u and u not in ("Guest",)})
	recipients = sorted(set(users) | set(emails or []))
	try:
		if users:
			from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

			enqueue_create_notification(
				users,
				{
					"type": "Alert",
					"subject": subject,
					"email_content": message,
					"document_type": doctype,
					"document_name": name,
				},
			)
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=frappe.utils.strip_html(subject),
				message=message,
				reference_doctype=doctype,
				reference_name=name,
				now=False,
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Insurance notification failed")


def supplier_email(supplier):
	if not supplier:
		return None
	email = frappe.db.get_value("Supplier", supplier, "email_id")
	if email:
		return email
	contact = frappe.db.get_value("Supplier", supplier, "supplier_primary_contact")
	if contact:
		return frappe.db.get_value("Contact", contact, "email_id")
	dl = frappe.qb.DocType("Dynamic Link")
	c = frappe.qb.DocType("Contact")
	res = (
		frappe.qb.from_(dl)
		.join(c)
		.on(c.name == dl.parent)
		.select(c.email_id)
		.where(dl.parenttype == "Contact")
		.where(dl.link_doctype == "Supplier")
		.where(dl.link_name == supplier)
		.where(c.email_id.isnotnull())
		.limit(1)
		.run(pluck=True)
	)
	return res[0] if res else None


def next_period(end_date):
	"""(start, end) for a 1-year renewal starting the day after `end_date`."""
	start = add_days(getdate(end_date), 1)
	end = add_days(frappe.utils.add_years(start, 1), -1)
	return start, end
