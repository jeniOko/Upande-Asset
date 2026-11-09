# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""One layout for every Upande Asset reminder email (and its in-app notification).

Email-safe HTML: tables and inline styles only (Outlook, Gmail, Apple Mail), a 600px card,
links absolute so they open from any mail program. ERPNext's own email wrapper goes around it.

    card(area="Insurance", tone="warning", status="Expires in 7 days",
         title="Policy GUIHJLKKL expires on 16 Nov 2026",
         intro="Raise the renewal so cover continues without a gap.",
         facts=[("Policy", link), ("Insurer", "Equator"), ...],
         button=("Open policy", url), company=policy.company)
"""

import re
from html import escape

import frappe
from frappe import _

# Neutral greys that sit with any company logo or letterhead. Colour is kept to the
# small status pill, softly, so "Overdue" still stands out.
INK = "#1f2328"
MUTED = "#636c76"
LINE = "#e4e7eb"
PANEL = "#f6f7f9"
BRAND = "#2b3137"  # buttons and links: charcoal, not a brand colour
TONES = {  # (pill text, pill background, number colour in stats)
	"info": ("#35506b", "#eef2f6", "#35506b"),
	"warning": ("#7a5310", "#f7f0e1", "#7a5310"),
	"danger": ("#8a2c2c", "#f7e9e8", "#8a2c2c"),
	"success": ("#2f5d46", "#eaf2ec", "#2f5d46"),
	"neutral": ("#4a525a", "#eef0f2", "#4a525a"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def link(label, url):
	# !important: ERPNext's email stylesheet (.email-body a) would otherwise turn links black and underlined.
	return (f'<a href="{escape(url, quote=True)}" style="color:{BRAND} !important;text-decoration:none !important;'
		f'font-weight:600 !important">{escape(str(label))}</a>')


def form_link(doctype, name, label=None):
	return link(label or name, frappe.utils.get_url_to_form(doctype, name))


def money(value, company):
	amount = frappe.utils.fmt_money(value, currency=frappe.get_cached_value("Company", company, "default_currency"))
	return f'<span style="white-space:nowrap">{escape(amount)}</span>'


def date(value):
	return f'<span style="white-space:nowrap">{frappe.format(value, "Date")}</span>' if value else _("Not set")


def _facts(rows):
	cells = "".join(
		f"""<tr>
			<td style="padding:9px 14px;border-bottom:1px solid {LINE};color:{MUTED};font-size:13px;width:38%;vertical-align:top">{escape(str(label))}</td>
			<td style="padding:9px 14px;border-bottom:1px solid {LINE};color:{INK};font-size:14px;vertical-align:top">{value}</td>
		</tr>"""
		for label, value in rows if value not in (None, "")
	)
	return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
		style="border-collapse:collapse;border:1px solid {LINE};border-radius:8px;background:#ffffff;margin:0 0 20px">{cells}</table>"""


def table(headers, rows, numeric=()):
	"""A compact data table (digest lists). `numeric`: column indexes aligned right."""
	head = "".join(
		f'<th style="padding:8px 10px;text-align:{"right" if i in numeric else "left"};font-size:11px;letter-spacing:.06em;'
		f'text-transform:uppercase;color:{MUTED};border-bottom:1px solid {LINE};background:{PANEL}">{escape(h)}</th>'
		for i, h in enumerate(headers)
	)
	body = "".join(
		"<tr>" + "".join(
			f'<td style="padding:8px 10px;border-bottom:1px solid {LINE};font-size:13px;color:{INK};'
			f'text-align:{"right" if i in numeric else "left"};vertical-align:top">{cell}</td>'
			for i, cell in enumerate(row)
		) + "</tr>"
		for row in rows
	)
	return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
		style="border-collapse:collapse;border:1px solid {LINE};margin:0 0 20px"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"""


def stats(items):
	"""Two or three headline numbers side by side: [(value, label, tone)]."""
	width = int(100 / max(len(items), 1))
	cells = "".join(
		f"""<td width="{width}%" style="padding:14px 16px;border:1px solid {LINE};background:#ffffff;vertical-align:top">
			<div style="font-size:26px;font-weight:700;color:{TONES[tone][2]};line-height:1.1">{escape(str(value))}</div>
			<div style="font-size:13px;color:{MUTED};margin-top:4px">{escape(label)}</div></td>"""
		for value, label, tone in items
	)
	return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
		style="border-collapse:collapse;margin:0 0 20px"><tr>{cells}</tr></table>"""


def heading(text):
	return f'<div style="font-size:15px;font-weight:700;color:{INK};margin:4px 0 10px">{escape(text)}</div>'


def letterhead(company):
	"""The letterhead chosen for the company in Asset Settings → Defaults, as email-safe HTML
	(absolute image URLs), or "" when none is set."""
	if not company:
		return ""
	from upande_asset.upande_asset.insurance.utils import get_company_defaults

	name = get_company_defaults(company).get("letter_head")
	if not name:
		return ""
	lh = frappe.get_cached_doc("Letter Head", name)
	if lh.disabled:
		return ""
	align = (lh.align or "Left").lower()
	if (lh.source or "Image") == "Image" and lh.image:
		height = f"max-height:{int(lh.image_height)}px;" if lh.image_height else "max-height:80px;"
		html = (f'<img src="{escape(frappe.utils.get_url(lh.image), quote=True)}" alt="{escape(company)}" '
			f'style="{height}max-width:100%;height:auto;border:0;display:inline-block">')
	elif lh.content:
		# Letterheads are written for print formats and may use {{ doc.company }} and the like.
		try:
			context = {"doc": frappe._dict(doctype="Company", name=company, company=company), "company": company, "letter_head": lh}
			html = frappe.utils.expand_relative_urls(frappe.render_template(lh.content, context))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Letterhead {name} could not be shown in a reminder email")
			return ""
	else:
		return ""
	return f'<div style="text-align:{align};padding:20px 28px 16px;border-bottom:1px solid {LINE}">{html}</div>'


def singular(text):
	"""'1 days' → '1 day' (the day counts in titles, pills and subjects)."""
	return re.sub(r"(?<![\d.])1 days\b", "1 day", text) if text else text


def card(area, title, intro=None, tone="info", status=None, facts=None, body="", button=None, links=None, company=None):
	"""The whole email body. `button`: (label, url); `links`: [(label, url)] shown under the button.
	With a letterhead set for `company` (Asset Settings → Defaults) it heads the email."""
	pill_fg, pill_bg, _x = TONES.get(tone, TONES["info"])
	title, status, intro, body = singular(title), singular(status), singular(intro), singular(body)
	pill = (f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;background:{pill_bg};color:{pill_fg};'
		f'font-size:12px;font-weight:600;letter-spacing:.02em">{escape(status)}</span>') if status else ""
	btn = ""
	if button:
		btn = f"""<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 16px"><tr>
			<td style="border-radius:6px;background:{BRAND}">
				<a class="btn" href="{escape(button[1], quote=True)}" style="display:inline-block;padding:11px 22px;font-size:14px;
					font-weight:600;color:#ffffff !important;background:{BRAND} !important;text-decoration:none !important;border:0 !important;
					border-radius:6px;margin:0 !important;line-height:20px">{escape(button[0])}</a></td></tr></table>"""
	extra = ""
	if links:
		extra = '<div style="font-size:13px;color:{0};margin:0 0 18px">{1}</div>'.format(
			MUTED, " &nbsp;·&nbsp; ".join(link(label, url) for label, url in links))
	head = letterhead(company)
	company_label = "" if head else (company or "")
	return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PANEL};font-family:{FONT}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
	style="width:100%;max-width:600px;background:#ffffff;border:1px solid {LINE};border-radius:10px;border-collapse:separate;overflow:hidden">
	{f'<tr><td>{head}</td></tr>' if head else ''}
	<tr><td style="padding:18px 28px 0">
		<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
			<td style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{MUTED}">{escape(area)}</td>
			<td align="right" style="font-size:12px;color:{MUTED}">{escape(company_label)}</td>
		</tr></table>
	</td></tr>
	<tr><td style="padding:14px 28px 6px">
		{pill}
		<h1 style="margin:10px 0 8px;font-size:21px;line-height:1.3;font-weight:700;color:{INK}">{escape(title)}</h1>
		{f'<p style="margin:0 0 18px;font-size:15px;line-height:1.55;color:{INK}">{intro}</p>' if intro else ''}
		{_facts(facts) if facts else ''}
		{body}
		{btn}
		{extra}
	</td></tr>
	<tr><td style="padding:0 28px 22px"></td></tr>
</table>
</td></tr></table>"""


def grouped(area, title, intro, headers, rows, tone="info", status=None, button=None, numeric=(), company=None):
	"""Several reminders of one kind in one email: a table, one row per document.
	`status`: the most urgent fact (e.g. "2 overdue"), shown in the pill above the title."""
	return card(area=area, tone=tone, status=status, title=title, intro=intro,
		body=table(headers, rows, numeric=numeric), button=button, company=company)


def pill(text, tone):
	fg, bg, _bar = TONES.get(tone, TONES["neutral"])
	return (f'<span style="display:inline-block;padding:1px 8px;border-radius:999px;background:{bg};color:{fg};'
		f'font-size:12px;font-weight:600;white-space:nowrap">{escape(text)}</span>')


class Batch:
	"""A day's reminders of one kind: each person gets one email (and one bell notification)
	listing all their items, instead of one per document. One item keeps its detailed email.

	    batch = Batch("Insurance Policy", single=renewal_email, grouped=renewal_group_email)
	    batch.add(policy.name, item, users=[...], emails=[...])   # inside the job's loop
	    batch.send()                                               # once, at the end
	`single(item)` / `grouped(items)` return (subject, html). `send_email` replaces the default
	notify (in-app + email) for email-only reminders such as overdue check sheets."""

	def __init__(self, doctype, single, grouped, send_email=None):
		self.doctype, self.single, self.grouped, self.send_email = doctype, single, grouped, send_email
		self.by_user, self.by_email, self.order = {}, {}, []

	def add(self, key, item, users=(), emails=()):
		if key not in self.order:
			self.order.append(key)
		for u in {u for u in users if u and u != "Guest"}:
			self.by_user.setdefault(u, {})[key] = item
		for e in {e for e in emails if e}:
			self.by_email.setdefault(e, {})[key] = item

	def _content(self, items):
		items = sorted(items.items(), key=lambda kv: self.order.index(kv[0]))
		if len(items) == 1:
			key, item = items[0]
			subject, message = self.single(item)
		else:
			key = None
			subject, message = self.grouped([i for _k, i in items])
		return singular(subject), message, key

	def send(self):
		from upande_asset.upande_asset.insurance.utils import notify

		for user, items in self.by_user.items():
			subject, message, name = self._content(items)
			if self.send_email:
				self.send_email([frappe.db.get_value("User", user, "email") or user], subject, message)
			else:
				notify([user], subject, message, self.doctype, name)
		for email, items in self.by_email.items():
			subject, message, name = self._content(items)
			if self.send_email:
				self.send_email([email], subject, message)
			else:
				notify([], subject, message, self.doctype, name, emails=[email])
		return len(self.by_user) + len(self.by_email)
