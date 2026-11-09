# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Scheduled jobs for Asset Insurance (wired in hooks.scheduler_events)."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, date_diff, flt, fmt_money, get_link_to_form, get_url_to_form, getdate, today

from upande_asset.upande_asset.insurance.asset import notify_disposed_asset, sync_policy_assets
from upande_asset.upande_asset.insurance.utils import (
	get_alert_days,
	get_asset_book_value,
	get_covering_rows,
	get_settings,
	notify,
	supplier_email,
	users_for_setting,
)


def _cur(company):
	return frappe.get_cached_value("Company", company, "default_currency")


def _safe(fn):
	try:
		fn()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), f"Asset Insurance job failed: {fn.__name__}")


def daily():
	for job in (
		update_policy_statuses,
		send_renewal_alerts,
		send_claim_followups,
		submit_due_release_entries,
		sweep_disposed_assets,
		send_digest_if_due,
	):
		_safe(job)


# 1 ─────────────────────────────────────────────────────────────────────────────
def update_policy_statuses():
	from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import compute_status

	for p in frappe.get_all(
		"Insurance Policy",
		filters={"docstatus": 1, "status": ["in", ["Active", "Expiring", "Expired"]]},
		fields=["name", "docstatus", "status", "end_date", "start_date", "renewed_to"],
	):
		new = compute_status(frappe._dict(p))
		if new != p.status:
			frappe.db.set_value("Insurance Policy", p.name, "status", new, update_modified=False)
		# Re-mirror on the day a policy starts or the day after it ends.
		if new != p.status or getdate(p.start_date) == getdate(today()) or getdate(p.end_date) == getdate(add_days(today(), -1)):
			sync_policy_assets(frappe.get_doc("Insurance Policy", p.name))


# 2 ─────────────────────────────────────────────────────────────────────────────
def send_renewal_alerts():
	"""Idempotent: alerts once per configured band (e.g. 60/30/7 days). The band
	last alerted is stored on the policy, so a missed scheduler day still alerts
	at the next run, and the same band is never sent twice."""
	bands = get_alert_days()
	settings = get_settings()
	managers = users_for_setting("renewal_reminder_roles")
	for p in frappe.get_all(
		"Insurance Policy",
		filters={"docstatus": 1, "status": ["in", ["Active", "Expiring"]]},
		fields=["name", "policy_number", "policy_type", "insurer_name", "insurer", "broker", "end_date", "owner",
			"last_alert_days", "renewed_to", "total_sum_insured", "company"],
	):
		days_left = date_diff(p.end_date, today())
		if days_left < 0:
			continue
		hit = [b for b in bands if days_left <= b]
		if not hit:
			continue
		band = min(hit)
		if p.last_alert_days and band >= p.last_alert_days:
			continue
		renewal_note = ""
		if p.renewed_to:
			renewal_note = _("<p>Renewal {0} is in progress but not yet submitted.</p>").format(
				get_link_to_form("Insurance Policy", p.renewed_to)
			)
		subject = _("Insurance policy {0} ({1}) expires in {2} days").format(p.policy_number or p.name, p.policy_type, days_left)
		message = _(
			"<p>Policy <a href='{0}'>{1}</a> with {2} for {3} sum insured ends on {4}.</p>{5}"
			"<p>Open the policy and use <b>Raise Renewal Request</b>.</p>"
		).format(
			get_url_to_form("Insurance Policy", p.name),
			p.policy_number or p.name,
			p.insurer_name or p.insurer,
			fmt_money(p.total_sum_insured, currency=frappe.get_cached_value("Company", p.company, "default_currency")),
			frappe.format(p.end_date, "Date"),
			renewal_note,
		)
		emails = [e for e in [supplier_email(p.broker)] if e] if settings.notify_broker else []
		users = [*managers, *([p.owner] if settings.notify_policy_owner else [])]
		if not users and not emails:
			continue
		notify(users, subject, message, "Insurance Policy", p.name, emails=emails)
		frappe.db.set_value(
			"Insurance Policy", p.name, {"last_alert_days": band, "last_alert_on": today()}, update_modified=False
		)


# 3 ─────────────────────────────────────────────────────────────────────────────
def send_claim_followups():
	days = int(get_settings().claim_followup_days or 14)
	cutoff = add_days(today(), -days)
	settings = get_settings()
	mm = users_for_setting("claim_followup_roles")
	for c in frappe.get_all(
		"Insurance Claim",
		filters={"docstatus": 0, "status": ["in", ["Recorded", "Under Assessment"]], "lodged_on": ["<=", cutoff]},
		fields=["name", "asset", "asset_name", "status", "lodged_on", "owner", "insurer", "last_followup_on", "amount_claimed"],
	):
		if c.last_followup_on and getdate(c.last_followup_on) > getdate(cutoff):
			continue
		age = date_diff(today(), c.lodged_on)
		subject = _("Insurance claim {0} has been {1} for {2} days").format(c.name, c.status, age)
		message = _("<p>Claim <a href='{0}'>{1}</a> for {2} ({3}) was recorded on {4} with {5}. Please follow up with the insurer.</p>").format(
			get_url_to_form("Insurance Claim", c.name), c.name, c.asset, c.asset_name, frappe.format(c.lodged_on, "Date"), c.insurer
		)
		users = [*mm, *([c.owner] if settings.notify_claim_owner else [])]
		if not users:
			continue
		notify(users, subject, message, "Insurance Claim", c.name)
		frappe.db.set_value("Insurance Claim", c.name, "last_followup_on", today(), update_modified=False)


# 4 ─────────────────────────────────────────────────────────────────────────────
def submit_due_release_entries():
	from upande_asset.upande_asset.insurance.release_schedule import submit_due_entries

	submit_due_entries()


def sweep_disposed_assets():
	"""Safety net for scrap/sale paths that bypass the JE / Sales Invoice hooks."""
	row = frappe.qb.DocType("Insurance Policy Asset")
	pol = frappe.qb.DocType("Insurance Policy")
	asset = frappe.qb.DocType("Asset")
	rows = (
		frappe.qb.from_(row)
		.join(pol).on(pol.name == row.parent)
		.join(asset).on(asset.name == row.asset)
		.select(row.asset, asset.status)
		.distinct()
		.where(row.parenttype == "Insurance Policy")
		.where(row.row_status == "Active")
		.where(pol.docstatus == 1)
		.where(pol.status.isin(["Active", "Expiring"]))
		.where(asset.status.isin(["Scrapped", "Sold"]))
		.run(as_dict=True)
	)
	for r in rows:
		notify_disposed_asset(r.asset, _(r.status).lower())


# digest ────────────────────────────────────────────────────────────────────────
DIGEST_MONTHS = {"Monthly": 1, "Quarterly": 3, "Half-yearly": 6, "Yearly": 12}
DIGEST_DAYS = {"Daily": 1, "Weekly": 7}


def digest_next_due(frequency, last_sent, every_days=0):
	"""Date the next digest is due, or None when the digest is switched off."""
	if not frequency:
		return None
	if not last_sent:
		return getdate(today())
	last_sent = getdate(last_sent)
	if frequency in DIGEST_MONTHS:
		return getdate(add_months(last_sent, DIGEST_MONTHS[frequency]))
	days = DIGEST_DAYS.get(frequency) or (int(every_days or 0) if frequency == "Custom" else 0)
	return add_days(last_sent, days) if days > 0 else None


def send_digest_if_due():
	"""Runs daily; sends at the frequency set in Asset Settings (blank = off)."""
	s = get_settings()
	due = digest_next_due(s.digest_frequency, s.digest_last_sent, s.digest_every_days)
	if not due or getdate(today()) < due:
		return
	send_digest(s.digest_frequency)
	frappe.db.set_single_value("Asset Settings", "digest_last_sent", today())


def send_digest(frequency=None):
	uninsured = []
	for a in frappe.get_all(
		"Asset",
		filters={"docstatus": 1, "insurance_required": 1, "status": ["not in", ["Scrapped", "Sold", "Cancelled"]]},
		fields=["name", "asset_name", "asset_category", "company"],
	):
		if not [r for r in get_covering_rows(a.name) if r.status != "Cancelled"]:
			uninsured.append(a)

	under = []
	row = frappe.qb.DocType("Insurance Policy Asset")
	pol = frappe.qb.DocType("Insurance Policy")
	for r in (
		frappe.qb.from_(row).join(pol).on(pol.name == row.parent)
		.select(row.asset, row.asset_name, row.sum_insured, pol.name.as_("policy"), pol.company)
		.where(row.parenttype == "Insurance Policy")
		.where(row.row_status == "Active")
		.where(pol.docstatus == 1)
		.where(pol.status.isin(["Active", "Expiring"]))
		.run(as_dict=True)
	):
		bv = get_asset_book_value(r.asset)
		if flt(r.sum_insured) < bv:
			r.book_value = bv
			under.append(r)

	if not uninsured and not under:
		return
	period = _(frequency).lower() if frequency and frequency != "Custom" else ""
	parts = [f"<h3>{_('Asset Insurance {0} digest').format(period).replace('  ', ' ')}</h3>"]
	if uninsured:
		parts.append(f"<p><b>{_('Uninsured assets flagged Insurance Required')}: {len(uninsured)}</b></p><ul>")
		parts += [f"<li><a href='{get_url_to_form('Asset', a.name)}'>{a.name}</a> – {a.asset_name} ({a.asset_category})</li>" for a in uninsured[:100]]
		parts.append("</ul>")
	if under:
		parts.append(f"<p><b>{_('Under-insured rows (sum insured below book value)')}: {len(under)}</b></p><ul>")
		parts += [
			f"<li>{r.asset} – {r.asset_name} on <a href='{get_url_to_form('Insurance Policy', r.policy)}'>{r.policy}</a>: "
			f"{fmt_money(r.sum_insured, currency=_cur(r.company))} vs {fmt_money(r.book_value, currency=_cur(r.company))}</li>"
			for r in under[:100]
		]
		parts.append("</ul>")
	notify(
		users_for_setting("digest_roles"),
		_("Asset Insurance digest: {0} uninsured, {1} under-insured").format(len(uninsured), len(under)),
		"".join(parts),
	)
