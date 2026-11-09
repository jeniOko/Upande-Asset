# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Scheduled jobs for Asset Insurance (wired in hooks.scheduler_events)."""

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	add_months,
	date_diff,
	escape_html,
	flt,
	fmt_money,
	get_link_to_form,
	get_url_to_form,
	getdate,
	today,
)

from upande_asset.upande_asset.insurance.asset import notify_disposed_asset, sync_policy_assets
from upande_asset.upande_asset.insurance.utils import (
	LIVE_POLICY_STATUSES,
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
		sweep_disposed_assets,
		send_digest_if_due,
	):
		_safe(job)


# 1 ─────────────────────────────────────────────────────────────────────────────
def update_policy_statuses():
	from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import compute_status

	for p in frappe.get_all(
		"Insurance Policy",
		filters={"docstatus": 1, "status": ["in", [*LIVE_POLICY_STATUSES, "Expired"]]},
		fields=["name", "docstatus", "status", "end_date", "start_date", "renewed_to", "renewed_from",
			"purchase_invoice", "is_opening", "total_premium"],
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
	from upande_asset.upande_asset.emails import Batch

	bands = get_alert_days()
	settings = get_settings()
	managers = users_for_setting("renewal_reminder_roles")
	# One email per person a day, listing every policy due a reminder.
	batch = Batch("Insurance Policy", single=lambda i: renewal_email(i["p"], i["days_left"]), grouped=renewal_group_email)
	for p in frappe.get_all(
		"Insurance Policy",
		filters={"docstatus": 1, "status": ["in", ["Active", "Expiring", "Awaiting Premium"]]},  # not once renewal started
		fields=["name", "policy_number", "policy_type", "insurer_name", "insurer", "broker", "end_date", "owner",
			"last_alert_days", "renewed_to", "total_sum_insured", "total_premium", "company", "insurance_for", "cover_description"],
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
		emails = [e for e in [supplier_email(p.broker)] if e] if settings.notify_broker else []
		users = [*managers, *([p.owner] if settings.notify_policy_owner else [])]
		if not users and not emails:
			continue
		batch.add(p.name, {"p": p, "days_left": days_left}, users=users, emails=emails)
		frappe.db.set_value(
			"Insurance Policy", p.name, {"last_alert_days": band, "last_alert_on": today()}, update_modified=False
		)
	batch.send()


def renewal_group_email(items):
	from frappe.utils import get_url_to_list

	from upande_asset.upande_asset import emails as em

	items = sorted(items, key=lambda i: i["days_left"])
	soonest = items[0]["days_left"]
	subject = _("{0} insurance policies expire soon (first in {1} days)").format(len(items), soonest)
	rows = [[
		em.form_link("Insurance Policy", i["p"].name, i["p"].policy_number or i["p"].name),
		escape_html(i["p"].policy_type), escape_html(i["p"].insurer_name or i["p"].insurer or ""), em.date(i["p"].end_date),
		em.pill(_("Today") if i["days_left"] == 0 else _("{0} days").format(i["days_left"]), "danger" if i["days_left"] <= 7 else "warning"),
	] for i in items]
	message = em.grouped(
		area=_("Asset Insurance"), tone="danger" if soonest <= 7 else "warning",
		title=_("{0} policies expire soon").format(len(items)), company=items[0]["p"].company,
		status=_("First expires today") if soonest == 0 else _("First expires in {0} days").format(soonest),
		intro=escape_html(_("Raise the renewals so cover continues without a gap. Procurement then gets the premium requests.")),
		headers=[_("Policy"), _("Type"), _("Insurer"), _("Cover ends"), _("Expires in")], rows=rows,
		button=(_("Open Insurance Policies"), get_url_to_list("Insurance Policy")),
	)
	return subject, message


def renewal_email(p, days_left, band=None):
	from upande_asset.upande_asset import emails as em

	ref = p.policy_number or p.name
	assets = frappe.db.count("Insurance Policy Asset", {"parent": p.name, "row_status": "Active"})
	when = _("today") if days_left == 0 else _("in {0} days").format(days_left)
	subject = _("Policy {0} ({1}) expires {2}").format(ref, p.policy_type, when)
	facts = [
		(_("Policy"), em.form_link("Insurance Policy", p.name, ref)),
		(_("Type"), escape_html(p.policy_type)),
		(_("Insurer"), escape_html(p.insurer_name or p.insurer)),
		(_("Cover ends"), em.date(p.end_date)),
		(_("Insured"), escape_html(p.cover_description or "") if (p.insurance_for or "Assets") != "Assets"
			else _("{0} asset(s)").format(assets)),
		(_("Sum insured"), em.money(p.total_sum_insured, p.company)),
		(_("Last premium"), em.money(p.total_premium, p.company) if p.get("total_premium") else None),
	]
	if p.renewed_to:
		facts.append((_("Renewal"), em.form_link("Insurance Policy", p.renewed_to) + " " + _("(draft, not submitted yet)")))
	message = em.card(
		area=_("Asset Insurance"), tone="danger" if days_left <= 7 else "warning",
		status=_("Expires {0}").format(when), title=_("Policy {0} ends on {1}").format(ref, em.date(p.end_date)),
		intro=_("Raise the renewal so cover continues without a gap. Procurement then gets the premium request."),
		facts=facts, button=(_("Open policy and raise renewal"), get_url_to_form("Insurance Policy", p.name)), company=p.company,
	)
	return subject, message


# 3 ─────────────────────────────────────────────────────────────────────────────
def send_claim_followups():
	from upande_asset.upande_asset.emails import Batch

	days = int(get_settings().claim_followup_days or 14)
	batch = Batch("Insurance Claim", single=lambda i: claim_followup_email(i["c"], i["age"], days),
		grouped=lambda items: claim_group_email(items, days))
	cutoff = add_days(today(), -days)
	settings = get_settings()
	mm = users_for_setting("claim_followup_roles")
	for c in frappe.get_all(
		"Insurance Claim",
		filters={"docstatus": 0, "status": ["in", ["Recorded", "Under Assessment"]], "lodged_on": ["<=", cutoff]},
		fields=["name", "asset", "asset_name", "status", "lodged_on", "owner", "insurer", "last_followup_on", "amount_claimed",
			"company", "policy", "incident_type", "incident_date"],
	):
		if c.last_followup_on and getdate(c.last_followup_on) > getdate(cutoff):
			continue
		age = date_diff(today(), c.lodged_on)
		item = {"c": c, "age": age}
		users = [*mm, *([c.owner] if settings.notify_claim_owner else [])]
		if not users:
			continue
		batch.add(c.name, item, users=users)
		frappe.db.set_value("Insurance Claim", c.name, "last_followup_on", today(), update_modified=False)
	batch.send()


def claim_group_email(items, every):
	from frappe.utils import get_url_to_list

	from upande_asset.upande_asset import emails as em

	items = sorted(items, key=lambda i: -i["age"])
	subject = _("{0} insurance claims are waiting on the insurer").format(len(items))
	rows = [[
		em.form_link("Insurance Claim", i["c"].name),
		em.form_link("Asset", i["c"].asset, i["c"].asset_name or i["c"].asset),
		escape_html(frappe.db.get_value("Supplier", i["c"].insurer, "supplier_name") or i["c"].insurer or ""),
		escape_html(_(i["c"].status)),
		em.pill(_("{0} days").format(i["age"]), "danger" if i["age"] >= 2 * every else "warning"),
	] for i in items]
	message = em.grouped(
		area=_("Insurance Claims"), tone="danger" if items[0]["age"] >= 2 * every else "warning",
		title=_("{0} claims to follow up").format(len(items)), company=items[0]["c"].get("company"),
		status=_("Longest wait {0} days").format(items[0]["age"]),
		intro=escape_html(_("These claims have had no decision from the insurer. Ask for an update, or record their decision on each claim.")),
		headers=[_("Claim"), _("Asset"), _("Insurer"), _("Status"), _("Waiting")], rows=rows,
		button=(_("Open Insurance Claims"), get_url_to_list("Insurance Claim")),
	)
	return subject, message


def claim_followup_email(c, age, every):
	from upande_asset.upande_asset import emails as em

	insurer = frappe.db.get_value("Supplier", c.insurer, "supplier_name") if c.insurer else None
	subject = _("Claim {0} has waited {1} days on {2}").format(c.name, age, insurer or _("the insurer"))
	message = em.card(
		area=_("Insurance Claims"), tone="warning" if age < 2 * every else "danger",
		status=_("{0} · {1} days").format(_(c.status), age), title=_("Follow up claim {0} with the insurer").format(c.name),
		intro=_("This claim has had no decision for {0} days. Ask the insurer for an update, or record their decision on the claim.").format(age),
		facts=[
			(_("Claim"), em.form_link("Insurance Claim", c.name)),
			(_("Asset"), em.form_link("Asset", c.asset, f"{c.asset_name or c.asset} ({c.asset})")),
			(_("Incident"), " · ".join(filter(None, [escape_html(c.get("incident_type") or ""),
				em.date(c.get("incident_date")) if c.get("incident_date") else ""]))),
			(_("Insurer"), escape_html(insurer or c.insurer or "")),
			(_("Policy"), em.form_link("Insurance Policy", c.policy) if c.get("policy") else None),
			(_("Amount claimed"), em.money(c.amount_claimed, c.company) if c.get("company") else None),
			(_("Recorded on"), em.date(c.lodged_on)),
		],
		button=(_("Open claim"), get_url_to_form("Insurance Claim", c.name)), company=c.get("company"),
	)
	return subject, message


# 4 ─────────────────────────────────────────────────────────────────────────────
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
		.where(pol.status.isin(LIVE_POLICY_STATUSES))
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
		.where(pol.status.isin(LIVE_POLICY_STATUSES))
		.run(as_dict=True)
	):
		bv = get_asset_book_value(r.asset)
		if flt(r.sum_insured) < bv:
			r.book_value = bv
			under.append(r)

	if not uninsured and not under:
		return
	subject, message = digest_email(uninsured, under, frequency)
	notify(users_for_setting("digest_roles"), subject, message)


def digest_email(uninsured, under, frequency=None):
	from frappe.utils import get_url_to_report

	from upande_asset.upande_asset import emails as em

	limit = 15
	period = _(frequency) if frequency and frequency != "Custom" else _("Periodic")
	subject = _("Insurance digest: {0} uninsured, {1} under-insured").format(len(uninsured), len(under))
	body = em.stats([
		(len(uninsured), _("Assets that need cover but have none"), "danger" if uninsured else "success"),
		(len(under), _("Assets insured below book value"), "warning" if under else "success"),
	])
	if uninsured:
		body += em.heading(_("Uninsured assets"))
		body += em.table([_("Asset"), _("Category"), _("Company")], [
			[em.form_link("Asset", a.name, f"{a.asset_name} ({a.name})"), escape_html(a.asset_category or ""), escape_html(a.company)]
			for a in uninsured[:limit]])
	if under:
		body += em.heading(_("Under-insured assets"))
		body += em.table([_("Asset"), _("Policy"), _("Sum insured"), _("Book value"), _("Short by")], [
			[em.form_link("Asset", r.asset, r.asset_name or r.asset), em.form_link("Insurance Policy", r.policy),
				em.money(r.sum_insured, r.company), em.money(r.book_value, r.company), em.money(flt(r.book_value) - flt(r.sum_insured), r.company)]
			for r in sorted(under, key=lambda r: flt(r.sum_insured) - flt(r.book_value))[:limit]], numeric=(2, 3, 4))
	more = max(len(uninsured) - limit, 0) + max(len(under) - limit, 0)
	message = em.card(
		area=_("Asset Insurance"), tone="warning" if (uninsured or under) else "success", status=_("{0} digest").format(period),
		title=_("Insurance cover check"),
		intro=_("Assets flagged Insurance Required that have no cover, and insured assets whose sum insured is below their book value.")
			+ (" " + _("The {0} largest gaps are shown; {1} more are in the reports.").format(limit, more) if more else ""),
		body=body,
		button=(_("Open Uninsured Assets report"), get_url_to_report("Uninsured Assets")),
		company=frappe.defaults.get_global_default("company") if len({a.company for a in uninsured} | {r.company for r in under}) <= 1 else None,
		links=[(_("Insurance Register"), get_url_to_report("Insurance Register"))],
	)
	return subject, message
