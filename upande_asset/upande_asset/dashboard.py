# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Data for the Asset Dashboard web page (/asset-dashboard).

Everything is read with frappe.get_list, so each user only sees what their
permissions allow; a doctype the user cannot read simply comes back empty.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, fmt_money, get_first_day, get_last_day, getdate, nowdate, today

INACTIVE_ASSET = ("Scrapped", "Sold", "Cancelled", "Draft")
OPEN_CLAIM = ("Draft", "Recorded", "Under Assessment", "Approved", "Partly Settled")


def _list(doctype, **kw):
	"""get_list that returns [] when the user lacks read access. Frappe lets a user with
	only "select" rights list names but drops every other field, which would make the
	dashboard count rows with empty statuses and values, so that counts as no access."""
	if not frappe.has_permission(doctype, "read"):
		return []
	try:
		return frappe.get_list(doctype, limit_page_length=0, **kw)
	except frappe.PermissionError:
		return []


def _companies():
	return _list("Company", pluck="name", order_by="name asc")


def _resolve_company(company):
	companies = _companies()
	if company and company in companies:
		return company, companies
	default = frappe.defaults.get_user_default("company")
	return (default if default in companies else (companies[0] if companies else None)), companies


WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _dimensions():
	"""Cost Center + the instance's own accounting dimensions (Project is left out:
	it is an ERPNext default that does not really apply to assets)."""
	dims = [{"label": _("Cost Center"), "fieldname": "cost_center", "doctype": "Cost Center"}]
	if frappe.db.exists("DocType", "Accounting Dimension"):
		for d in frappe.get_all("Accounting Dimension", filters={"disabled": 0}, fields=["label", "fieldname", "document_type"]):
			if d.document_type == "Project" or d.fieldname == "project":
				continue
			if frappe.get_meta("Asset").has_field(d.fieldname):
				dims.append({"label": _(d.label or d.document_type), "fieldname": d.fieldname, "doctype": d.document_type})
	return dims


def _dimension_filter(dimension, value):
	"""{asset fieldname: [value + children]} for the chosen dimension, or {}."""
	if not dimension or not value:
		return {}
	dim = next((d for d in _dimensions() if d["fieldname"] == dimension), None)
	if not dim:
		return {}
	values = [value]
	if frappe.get_meta(dim["doctype"]).is_tree:
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_dimension_with_children

		values = get_dimension_with_children(dim["doctype"], value) or [value]
	return {dim["fieldname"]: ["in", values]}


def _period(from_date, to_date):
	td = getdate(today())
	start = getdate(from_date) if from_date else getdate(f"{td.year}-01-01")
	end = getdate(to_date) if to_date else td
	return (start, end) if start <= end else (end, start)


@frappe.whitelist()
def get_filter_options(company=None):
	"""Dimensions for the filter bar, and the instance's first day of the week."""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	first = frappe.db.get_single_value("System Settings", "first_day_of_the_week") or "Sunday"
	return {"dimensions": _dimensions(), "first_day_of_week": WEEKDAYS.index(first) if first in WEEKDAYS else 0}


@frappe.whitelist()
def get_dimension_values(dimension, company=None):
	dim = next((d for d in _dimensions() if d["fieldname"] == dimension), None)
	if not dim:
		return []
	company, _companies_ = _resolve_company(company)
	meta = frappe.get_meta(dim["doctype"])
	filters = {"company": company} if company and meta.has_field("company") else {}
	if meta.has_field("disabled"):
		filters["disabled"] = 0
	fields = ["name"] + (["is_group"] if meta.has_field("is_group") else [])
	order = "lft asc" if meta.is_tree else "name asc"
	return _list(dim["doctype"], filters=filters, fields=fields, order_by=order)


def _group(rows, key, value=None):
	out = defaultdict(lambda: {"count": 0, "value": 0.0})
	for r in rows:
		k = r.get(key) or _("Not set")
		out[k]["count"] += 1
		if value:
			out[k]["value"] += flt(r.get(value))
	return sorted(({"name": k, **v} for k, v in out.items()), key=lambda x: (-x["value"], -x["count"]))


COLLECTED = ("td", "currency", "assets", "active", "required", "insured", "uninsured", "under", "records", "open_records",
	"done_year", "done_month", "logs", "overdue_logs", "repairs", "months", "month_cost", "sheets", "open_issues", "policies",
	"live", "expiring", "premium_by_insurer", "claims", "open_claims", "period_claims", "followup", "stale_claims", "p_start", "p_end")


def _collect(company, from_date=None, to_date=None, dimension=None, dimension_value=None):
	"""Every record set the dashboard summarises; shared by get_dashboard and get_drilldown
	so a pop-up always lists exactly what was counted."""
	td = getdate(today())
	p_start, p_end = _period(from_date, to_date)
	in_period = lambda d: bool(d) and p_start <= getdate(d) <= p_end
	currency = frappe.get_cached_value("Company", company, "default_currency")
	dim_filter = _dimension_filter(dimension, dimension_value)

	# ── Assets ──────────────────────────────────────────────────────────────
	assets = _list("Asset", filters={"company": company, "docstatus": 1, **dim_filter},
		fields=["name", "asset_name", "asset_category", "status", "cost_center", "location", "net_purchase_amount",
			"value_after_depreciation", "insurance_required", "insurance_policy", "insurance_end_date", "insured_value"])
	asset_set = {a.name for a in assets}
	scoped = lambda rows, key="asset": [r for r in rows if r.get(key) in asset_set] if dim_filter else rows
	active = [a for a in assets if a.status not in INACTIVE_ASSET]
	required = [a for a in active if a.insurance_required]
	insured = [a for a in required if a.insurance_policy and a.insurance_end_date and getdate(a.insurance_end_date) >= td]
	uninsured = [a for a in required if a not in insured]
	under = sorted(
		[{**a, "gap": flt(a.value_after_depreciation) - flt(a.insured_value)} for a in insured
			if flt(a.insured_value) and flt(a.insured_value) < flt(a.value_after_depreciation)],
		key=lambda x: -x["gap"])

	# ── Service records & maintenance ──────────────────────────────────────
	records = _list("Asset Service Record", filters={"company": company, "docstatus": ["<", 2]},
		fields=["name", "title", "asset", "asset_name", "status", "docstatus", "service_by", "service_date", "service_type",
			"assigned_to", "materials_cost", "services_cost", "total_service_cost"], order_by="service_date desc")
	records = scoped(records)
	open_records = [r for r in records if r.docstatus == 0]
	task_due = defaultdict(list)
	if open_records:
		for t in frappe.get_all("Asset Service Record Task", filters={"parent": ["in", [r.name for r in open_records]]},
				fields=["parent", "due_date", "maintenance_task"]):
			task_due[t.parent].append(t)
	for r in open_records:
		dues = [getdate(t.due_date) for t in task_due.get(r.name, []) if t.due_date]
		r["due"] = min(dues) if dues else (getdate(r.service_date) if r.service_date else None)
		r["tasks"] = [t.maintenance_task for t in task_due.get(r.name, []) if t.maintenance_task]
		r["overdue"] = bool(r["due"] and r["due"] < td)
	done_year = [r for r in records if r.docstatus == 1 and in_period(r.service_date)]  # completed in the period
	done_month = done_year
	logs = _list("Asset Maintenance Log", filters={"docstatus": 0, "maintenance_status": ["in", ["Planned", "Overdue"]]},
		fields=["name", "asset_name", "task_name", "due_date", "maintenance_status"])
	logs = [lg for lg in logs if lg.asset_name in asset_set]
	overdue_logs = [lg for lg in logs if lg.maintenance_status == "Overdue" or (lg.due_date and getdate(lg.due_date) < td)]
	repairs = _list("Asset Repair", filters={"company": company, "docstatus": ["<", 2], "repair_status": ["in", ["Pending", "In Progress"]]},
		fields=["name", "asset", "asset_name", "repair_status", "failure_date"])
	repairs = scoped(repairs)
	# Month buckets across the period (at most the last 24 months of it)
	months, cur_m = [], get_first_day(p_start)
	while cur_m <= p_end:
		months.append((cur_m.year, cur_m.month))
		cur_m = getdate(frappe.utils.add_months(cur_m, 1))
	months = months[-24:]
	month_cost = {m: {"materials": 0.0, "services": 0.0} for m in months}
	for r in done_year:
		key = (getdate(r.service_date).year, getdate(r.service_date).month)
		if key in month_cost:
			month_cost[key]["materials"] += flt(r.materials_cost)
			month_cost[key]["services"] += flt(r.services_cost)

	# ── Check sheets ───────────────────────────────────────────────────────
	sheets = scoped(_list("Asset Check Sheet", filters={"company": company, "docstatus": 1, "check_date": ["between", [p_start, p_end]]},
		fields=["name", "asset", "asset_name", "overall_status", "check_date"]))
	open_issues = scoped(_list("Asset Check Sheet", filters={"company": company, "docstatus": 1, "overall_status": "Needs Attention"},
		fields=["name", "asset", "asset_name", "check_date"], order_by="check_date desc"))

	# ── Insurance ──────────────────────────────────────────────────────────
	policies = _list("Insurance Policy", filters={"company": company, "docstatus": 1},
		fields=["name", "policy_number", "policy_type", "insurer_name", "insurer", "start_date", "end_date", "status",
			"total_sum_insured", "total_premium", "renewed_to", "insurance_for", "cost_center"])
	if dim_filter:
		# Only policies covering a matching asset; sum insured counts those assets' rows only.
		rows = frappe.get_all("Insurance Policy Asset", filters={"parent": ["in", [p.name for p in policies] or [""]],
			"asset": ["in", list(asset_set) or [""]], "row_status": "Active"}, fields=["parent", "sum_insured"])
		by_policy = defaultdict(float)
		for r in rows:
			by_policy[r.parent] += flt(r.sum_insured)
		cc = dim_filter.get("cost_center", [None, []])[1]
		policies = [p for p in policies if p.name in by_policy or (p.insurance_for == "Other" and p.cost_center in cc)]
		for p in policies:
			if p.name in by_policy:
				p.total_sum_insured = by_policy[p.name]
	live = [p for p in policies if p.status in ("Active", "Expiring")]
	expiring = sorted([p for p in live if date_diff(p.end_date, td) <= 60], key=lambda p: p.end_date)
	for p in expiring:
		p["days"] = date_diff(p.end_date, td)
	premium_by_insurer = _group([p for p in policies if in_period(p.start_date)], "insurer_name", "total_premium")
	claims = scoped(_list("Insurance Claim", filters={"company": company, "docstatus": ["<", 2]},
		fields=["name", "asset", "asset_name", "status", "incident_type", "incident_date", "amount_claimed", "amount_approved",
			"amount_received", "outstanding_amount", "lodged_on"], order_by="incident_date desc"))
	open_claims = [c for c in claims if c.status in OPEN_CLAIM]
	period_claims = [c for c in claims if in_period(c.incident_date)]
	followup = cint(frappe.db.get_single_value("Asset Settings", "claim_followup_days") or 14)
	stale_claims = [c for c in claims if c.status in ("Recorded", "Under Assessment") and c.lodged_on
		and date_diff(td, c.lodged_on) > followup]

	return frappe._dict({k: v for k, v in locals().items() if k in COLLECTED})


@frappe.whitelist()
def get_dashboard(company=None, from_date=None, to_date=None, dimension=None, dimension_value=None):
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	company, companies = _resolve_company(company)
	if not company:
		return {"companies": [], "company": None}
	c = _collect(company, from_date, to_date, dimension, dimension_value)
	(td, currency, assets, active, required, insured, uninsured, under, records, open_records, done_year, done_month, logs,
		overdue_logs, repairs, months, month_cost, sheets, open_issues, policies, live, expiring, premium_by_insurer, claims,
		open_claims, period_claims, followup, stale_claims, p_start, p_end) = (c[k] for k in COLLECTED)

	# ── Attention flags ────────────────────────────────────────────────────
	flags = []
	def flag(key, sev, label, value, desc, route):
		if value:
			flags.append({"key": key, "sev": sev, "label": label, "value": value, "desc": desc, "route": route})
	flag("overdue", "high", _("Overdue maintenance"), len(overdue_logs) + len([r for r in open_records if r["overdue"]]),
		_("Service jobs or maintenance tasks past their due date"), "/app/asset-maintenance-log?maintenance_status=Overdue")
	flag("uninsured", "high", _("Uninsured assets"), len(uninsured), _("Flagged Insurance Required with no active cover"),
		"/app/query-report/Uninsured Assets")
	flag("expiring30", "med", _("Policies expiring ≤ 30 days"), len([p for p in expiring if p["days"] <= 30]),
		_("Raise the renewal request from the policy"), "/app/insurance-policy?status=Expiring")
	flag("stale_claims", "med", _("Claims waiting on insurer"), len(stale_claims), _("Recorded or under assessment longer than {0} days").format(followup),
		"/app/insurance-claim?status=Under Assessment")
	flag("under", "med", _("Under-insured assets"), len(under), _("Sum insured below current book value"),
		"/app/query-report/Insurance Register")
	flag("open_issues", "low", _("Check sheet issues"), len(open_issues), _("Submitted check sheets still marked Needs Attention"),
		"/app/asset-check-sheet?overall_status=Needs Attention&docstatus=1")
	flag("repairs", "low", _("Open repairs"), len(repairs), _("Asset repairs pending or in progress"), "/app/asset-repair?repair_status=Pending")

	return {
		"company": company,
		"companies": companies,
		"currency": currency,
		"today": str(td),
		"period": {"from": str(p_start), "to": str(p_end)},
		"kpis": {
			"assets": len(active),
			"disposed": len(assets) - len(active),
			"gross_value": sum(flt(a.net_purchase_amount) for a in active),
			"book_value": sum(flt(a.value_after_depreciation) for a in active),
			"insurance_required": len(required),
			"insured": len(insured),
			"uninsured": len(uninsured),
			"open_service": len(open_records),
			"overdue_service": len([r for r in open_records if r["overdue"]]) + len(overdue_logs),
			"service_cost_year": sum(flt(r.total_service_cost) for r in done_year),
			"materials_cost_year": sum(flt(r.materials_cost) for r in done_year),
			"services_cost_year": sum(flt(r.services_cost) for r in done_year),
			"completed_month": len(done_month),
			"open_repairs": len(repairs),
			"active_policies": len(live),
			# Asset cover only, so it compares with book value; staff / liability cover is shown apart.
			"sum_insured": sum(flt(p.total_sum_insured) for p in live if p.insurance_for != "Other"),
			"other_sum_insured": sum(flt(p.total_sum_insured) for p in live if p.insurance_for == "Other"),
			"premium_year": sum(x["value"] for x in premium_by_insurer),
			"expiring_policies": len([p for p in expiring if p["days"] <= 30]),
			"open_claims": len(open_claims),
			"recoverable": sum(flt(c.outstanding_amount) for c in claims if c.status in ("Approved", "Partly Settled")),
			"checks_30d": len(sheets),
			"checks_passed_30d": len([s for s in sheets if s.overall_status == "Passed"]),
			"open_issues": len(open_issues),
			"claims_period": len(period_claims),
			"claimed_period": sum(flt(c.amount_claimed) for c in period_claims),
		},
		"by_category": _group(active, "asset_category", "value_after_depreciation"),
		"by_status": _group(active, "status"),
		"by_cost_center": _group(active, "cost_center", "value_after_depreciation")[:8],
		"by_location": _group(active, "location", "value_after_depreciation")[:8],
		"flags": flags,
		"service": {
			"open": sorted(open_records, key=lambda r: (r["due"] or getdate("2999-12-31")))[:12],
			"by_status": _group(open_records, "status"),
			"by_type": _group(done_year, "service_type", "total_service_cost")[:8],
			"by_month": [{"year": y, "month": m, **month_cost[(y, m)]} for (y, m) in months],
			"overdue_logs": sorted(overdue_logs, key=lambda x: str(x.due_date))[:10],
			"repairs": repairs[:10],
			"check_status": _group(sheets, "overall_status"),
			"open_issues": open_issues[:10],
		},
		"insurance": {
			"expiring": expiring[:12],
			"claims_by_status": _group(period_claims, "status", "amount_claimed"),
			"open_claims": open_claims[:10],
			"premium_by_insurer": premium_by_insurer[:8],
			"under": under[:10],
			"uninsured": uninsured[:12],
			"by_type": _group(live, "policy_type", "total_sum_insured"),
		},
	}


TEAM_VIEW_ROLES = {"System Manager", "Maintenance Manager", "Accounts Manager", "Quality Manager"}
ASSET_TODO_DOCTYPES = ("Asset", "Asset Service Record", "Asset Maintenance Log", "Asset Maintenance", "Asset Repair", "Asset Check Sheet",
	"Insurance Policy", "Insurance Claim")


def _todo_in_scope(t, company, asset_names, dim_filter):
	"""ToDos carry no company: judge them by the record they point to. Personal ToDos
	without a reference always show."""
	if not t.reference_type or not t.reference_name or not frappe.db.exists("DocType", t.reference_type):
		return True
	if t.reference_type == "Asset":
		return t.reference_name in asset_names
	meta = frappe.get_meta(t.reference_type)
	fields = [f for f in ("company", "asset", "asset_name") if meta.has_field(f)]
	if not fields:
		return True
	row = frappe.db.get_value(t.reference_type, t.reference_name, fields, as_dict=True)
	if not row:
		return False
	if row.get("company") and row.company != company:
		return False
	asset = row.get("asset") or (row.get("asset_name") if t.reference_type in ("Asset Maintenance Log", "Asset Maintenance") else None)
	if asset and (dim_filter or not row.get("company")):
		return asset in asset_names
	return True


@frappe.whitelist()
def get_events(company=None, start=None, end=None, dimension=None, dimension_value=None):
	"""Calendar items between start and end (inclusive): policy renewals, service
	jobs coming due, maintenance tasks without a service record, and the user's ToDos."""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	company, _companies_ = _resolve_company(company)
	start = getdate(start or get_first_day(today()))
	end = getdate(end or get_last_day(today()))
	td = getdate(today())
	events = []
	dim_filter = _dimension_filter(dimension, dimension_value)
	asset_names = set(_list("Asset", filters={"company": company, **dim_filter}, pluck="name"))

	def add(date, kind, title, sub, doctype, name, overdue=False, status=None, assignee=None, asset=None):
		if date:
			events.append({"date": str(getdate(date)), "type": kind, "title": title, "sub": sub, "doctype": doctype,
				"name": name, "overdue": overdue, "status": status, "assignee": assignee or None, "asset": asset})

	def assigned(row):
		"""Frappe assignment (Assign To) if any, else the record's owner."""
		try:
			users = frappe.parse_json(row.get("_assign") or "[]") or []
		except Exception:
			users = []
		return users[0] if users else row.get("owner")

	for p in _list("Insurance Policy", filters={"company": company, "docstatus": 1, "status": ["in", ["Active", "Expiring", "Expired"]],
			"end_date": ["between", [start, end]]},
			fields=["name", "policy_number", "policy_type", "insurer_name", "end_date", "status", "renewed_to", "_assign", "owner"]):
		if dim_filter and not frappe.db.exists("Insurance Policy Asset", {"parent": p.name, "asset": ["in", list(asset_names) or [""]]}):
			continue
		add(p.end_date, "renewal", _("Renewal: {0}").format(p.policy_number or p.name),
			f"{p.policy_type} · {p.insurer_name or ''}", "Insurance Policy", p.name, getdate(p.end_date) < td, p.status, assigned(p))

	job_assets = set()  # assets with a service job or maintenance task on the calendar
	records = [r for r in _list("Asset Service Record", filters={"company": company, "docstatus": 0},
		fields=["name", "title", "asset", "asset_name", "status", "service_date", "assigned_to", "_assign", "owner"]) if r.asset in asset_names]
	in_records = set()
	if records:
		tasks = frappe.get_all("Asset Service Record Task", filters={"parent": ["in", [r.name for r in records]]},
			fields=["parent", "due_date", "maintenance_task", "asset_maintenance_log"])
		by_parent = defaultdict(list)
		for t in tasks:
			by_parent[t.parent].append(t)
			in_records.add(t.asset_maintenance_log)
		for r in records:
			dues = [getdate(t.due_date) for t in by_parent.get(r.name, []) if t.due_date]
			due = min(dues) if dues else (getdate(r.service_date) if r.service_date else None)
			if due and (start <= due <= end or (due < td and start <= td <= end)):
				# Overdue jobs also show on today so they are not lost in past months.
				shown = due if due >= start else td
				job_assets.add(r.asset)
				add(shown, "service", r.title or r.name, f"{r.asset_name or ''} · {r.status}", "Asset Service Record", r.name,
					due < td, r.status, r.assigned_to or assigned(r), r.asset_name)

	for lg in _list("Asset Maintenance Log", filters={"docstatus": 0, "maintenance_status": ["in", ["Planned", "Overdue"]],
			"due_date": ["between", [start, end]]}, fields=["name", "asset_name", "task_name", "due_date", "maintenance_status",
			"task_assignee_email", "_assign", "owner"]):
		if lg.asset_name in asset_names and lg.name not in in_records:
			job_assets.add(lg.asset_name)
			add(lg.due_date, "maintenance", lg.task_name or lg.name, lg.asset_name, "Asset Maintenance Log", lg.name,
				getdate(lg.due_date) < td, lg.maintenance_status, lg.task_assignee_email or assigned(lg), lg.asset_name)

	roles = frappe.get_roles()
	todo = frappe.qb.DocType("ToDo")
	mine = (todo.allocated_to == frappe.session.user) | (todo.role.isin(roles or [""]))
	# Managers plan for their team: they also see others' ToDos on asset work.
	if set(roles) & TEAM_VIEW_ROLES:
		mine = mine | todo.reference_type.isin(ASSET_TODO_DOCTYPES)
	q = (frappe.qb.from_(todo)
		.select(todo.name, todo.date, todo.description, todo.reference_type, todo.reference_name, todo.priority, todo.allocated_to,
			todo.role)
		.where(todo.status == "Open")
		.where(todo.date.between(start, end))
		.where(mine))
	shown = {(e["doctype"], e["name"]) for e in events} | {("Asset Maintenance Log", n) for n in in_records}
	# ERPNext assigns each Asset Maintenance task with a ToDo on the parent record; the job itself is already shown.
	shown |= {("Asset Maintenance", am) for am in frappe.get_all("Asset Maintenance",
		filters={"asset_name": ["in", list(job_assets) or [""]]}, pluck="name")}
	role_names = {}
	for t in q.run(as_dict=True):
		# ERPNext's assignment ToDo for a job already on the calendar would only repeat it.
		if (t.reference_type, t.reference_name) in shown:
			continue
		if not _todo_in_scope(t, company, asset_names, dim_filter):
			continue
		title = frappe.utils.strip_html(t.description or "")[:90] or _("ToDo")
		assignee = t.allocated_to
		if not assignee and t.role:  # a role ToDo (e.g. endorse a policy) is filterable by its role
			assignee = f"role:{t.role}"
			role_names[assignee] = _("{0} (role)").format(_(t.role))
		add(t.date, "todo", title, f"{t.reference_type or ''} {t.reference_name or ''}".strip(), "ToDo", t.name,
			getdate(t.date) < td, t.priority, assignee)

	names = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list({e["assignee"] for e in events if e["assignee"]}) or [""]]},
		fields=["name", "full_name"])}
	names.update(role_names)
	for e in events:
		e["assignee_name"] = names.get(e["assignee"]) or e["assignee"]

	return sorted(events, key=lambda e: (e["date"], e["type"]))


# ── Drill-down (pop-ups) ──────────────────────────────────────────────────────


def _row(doctype, name, title, sub="", date=None, status=None, amount=None):
	return {"doctype": doctype, "name": name, "title": title or name, "sub": sub or "", "date": str(date) if date else None,
		"status": status, "amount": amount}


def _asset_row(a, amount_key="value_after_depreciation"):
	return _row("Asset", a.name, a.asset_name or a.name, " · ".join(filter(None, [a.name, a.asset_category, a.cost_center])),
		status=a.status, amount=flt(a.get(amount_key)))


def _record_row(r):
	return _row("Asset Service Record", r.name, r.title or r.name, " · ".join(filter(None, [r.asset_name, r.service_type, r.service_by])),
		r.get("due") or r.service_date, "Completed" if r.docstatus == 1 else r.status, flt(r.total_service_cost) or None)


def _policy_row(p, amount_key="total_sum_insured"):
	return _row("Insurance Policy", p.name, p.policy_number or p.name, " · ".join(filter(None, [p.policy_type, p.insurer_name])),
		p.end_date, p.status, flt(p.get(amount_key)))


def _claim_row(c):
	return _row("Insurance Claim", c.name, c.asset_name or c.asset or c.name, " · ".join(filter(None, [c.name, c.incident_type])),
		c.incident_date, c.status, flt(c.outstanding_amount or c.amount_approved or c.amount_claimed))


def _sheet_row(s):
	return _row("Asset Check Sheet", s.name, s.asset_name or s.asset, s.name, s.check_date, s.get("overall_status") or "Needs Attention")


@frappe.whitelist()
def get_drilldown(key, group=None, value=None, company=None, from_date=None, to_date=None, dimension=None, dimension_value=None):
	"""Records behind one dashboard figure, chart segment or bar. `group`/`value`
	narrow the set to one segment (e.g. group=status, value=Pending)."""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	company, _companies_ = _resolve_company(company)
	c = _collect(company, from_date, to_date, dimension, dimension_value)
	pick = lambda rows: [r for r in rows if (r.get(group) or _("Not set")) == value] if group else rows

	if key == "assets":
		rows = [_asset_row(a) for a in pick(c.active)]
	elif key == "insured":
		rows = [_asset_row(a, "insured_value") for a in c.insured] + [_asset_row(a, "insured_value") for a in c.uninsured]
		for r in rows[len(c.insured):]:
			r["status"] = _("No cover")
	elif key == "uninsured":
		rows = [{**_asset_row(a), "status": _("No cover")} for a in c.uninsured]
	elif key == "under":
		rows = [{**_asset_row(frappe._dict(a)), "status": _("Gap {0}").format(fmt_money(a["gap"], currency=c.currency)),
			"amount": flt(a.get("insured_value"))} for a in c.under]
	elif key == "open_service":
		rows = [_record_row(r) for r in pick(c.open_records)]
	elif key == "overdue":
		rows = [_record_row(r) for r in c.open_records if r["overdue"]] + [
			_row("Asset Maintenance Log", lg.name, lg.task_name or lg.name, lg.asset_name, lg.due_date, _("Overdue")) for lg in c.overdue_logs]
	elif key == "done":
		done = c.done_year
		if group == "month":
			done = [r for r in done if getdate(r.service_date).strftime("%Y-%m") == value]
		else:
			done = pick(done)
		rows = [_record_row(r) for r in done]
	elif key == "repairs":
		rows = [_row("Asset Repair", r.name, r.asset_name or r.asset, r.name, r.failure_date, r.repair_status) for r in c.repairs]
	elif key == "sheets":
		rows = [_sheet_row(s) for s in pick(c.sheets)]
	elif key == "open_issues":
		rows = [_sheet_row(s) for s in c.open_issues]
	elif key == "policies":
		rows = [_policy_row(p) for p in pick(c.live)]
	elif key == "asset_policies":
		rows = [_policy_row(p) for p in c.live if p.insurance_for != "Other"]
	elif key == "expiring30":
		rows = [_policy_row(p) for p in c.expiring if p["days"] <= 30]
	elif key == "premium":
		rows = [_policy_row(p, "total_premium") for p in pick([p for p in c.policies
			if p.start_date and c.p_start <= getdate(p.start_date) <= c.p_end])]
	elif key == "open_claims":
		rows = [_claim_row(x) for x in c.open_claims]
	elif key == "claims":
		rows = [_claim_row(x) for x in pick(c.period_claims)]
	elif key == "stale_claims":
		rows = [_claim_row(x) for x in c.stale_claims]
	else:
		frappe.throw(_("Unknown drill-down {0}").format(key))
	return {"rows": rows[:200], "count": len(rows), "total": sum(flt(r.get("amount")) for r in rows), "currency": c.currency}
