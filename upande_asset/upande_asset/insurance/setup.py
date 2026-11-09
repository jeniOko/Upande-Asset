# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt
"""Idempotent install / migrate steps for Asset Insurance.

Kept in code (rather than fixtures) to match this app's existing pattern in
install.py and so that records are created only when their dependencies exist."""

import json

import frappe

WORKSPACE = "Asset Insurance"
WORKFLOW = "Insurance Claim Approval"


def ensure_supplier_group():
	if not frappe.db.exists("DocType", "Supplier Group") or frappe.db.exists("Supplier Group", "Insurers"):
		return
	parent = frappe.db.get_value("Supplier Group", {"is_group": 1, "parent_supplier_group": ["in", ["", None]]}, "name")
	if not parent:
		# ERPNext setup wizard not completed yet; created on the next migrate.
		return
	frappe.get_doc({
		"doctype": "Supplier Group",
		"supplier_group_name": "Insurers",
		"parent_supplier_group": parent,
		"is_group": 0,
	}).insert(ignore_permissions=True)


# ── Workflow ──────────────────────────────────────────────────────────────────

STATES = [
	# state, doc_status, roles allowed to edit, style
	("Draft", 0, ("Maintenance Manager", "Maintenance User", "System Manager"), "Danger"),
	("Recorded", 0, ("Accounts User", "Maintenance Manager", "System Manager"), "Warning"),
	("Under Assessment", 0, ("Accounts User", "System Manager"), "Warning"),
	("Approved", 1, ("Accounts User", "System Manager"), "Success"),
	("Rejected", 1, ("Accounts User", "System Manager"), "Danger"),
	("Partly Settled", 1, ("Accounts User", "System Manager"), "Info"),
	("Settled", 1, ("Accounts User", "System Manager"), "Success"),
	("Closed", 1, ("Accounts Manager", "System Manager"), "Inverse"),
	("Cancelled", 2, ("Accounts Manager", "System Manager"), "Danger"),
]

TRANSITIONS = [
	# state, action, next_state, roles
	("Draft", "Record", "Recorded", ("Maintenance Manager", "System Manager")),
	("Recorded", "Start Assessment", "Under Assessment", ("Accounts User", "System Manager")),
	("Recorded", "Return to Draft", "Draft", ("Accounts User", "Maintenance Manager", "System Manager")),
	("Under Assessment", "Approve", "Approved", ("Accounts User", "System Manager")),
	("Under Assessment", "Reject", "Rejected", ("Accounts User", "System Manager")),
	("Under Assessment", "Return to Draft", "Draft", ("Accounts User", "System Manager")),
	("Approved", "Cancel", "Cancelled", ("Accounts Manager", "System Manager")),
	("Rejected", "Cancel", "Cancelled", ("Accounts Manager", "System Manager")),
]


def ensure_workflow():
	for state, _ds, _roles, style in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(ignore_permissions=True)
	actions = {t[1] for t in TRANSITIONS}
	for action in actions:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(ignore_permissions=True)

	wf = frappe.get_doc("Workflow", WORKFLOW) if frappe.db.exists("Workflow", WORKFLOW) else frappe.new_doc("Workflow")
	wf.update({
		"workflow_name": WORKFLOW,
		"document_type": "Insurance Claim",
		"workflow_state_field": "status",
		"is_active": 1,
		"send_email_alert": 0,
		"override_status": 1,
	})
	if wf.is_new():
		# Only seed states/transitions on first install – later edits in the UI are respected.
		for state, ds, roles, _style in STATES:
			for role in roles:
				if frappe.db.exists("Role", role):
					wf.append("states", {"state": state, "doc_status": str(ds), "allow_edit": role,
						"update_field": None})
		for state, action, nxt, roles in TRANSITIONS:
			for role in roles:
				if frappe.db.exists("Role", role):
					wf.append("transitions", {"state": state, "action": action, "next_state": nxt, "allowed": role,
						"allow_self_approval": 1})
		wf.insert(ignore_permissions=True)
	else:
		wf.save(ignore_permissions=True)


# ── Incident types ────────────────────────────────────────────────────────────

# Classes of general insurance business recognised by the Insurance Regulatory Authority
# (IRA) of Kenya. Seeded as standard Insurance Policy Types: they can be disabled, not deleted.
IRA_CLASSES = {
	"Aviation": "Aircraft hull and aviation liability.",
	"Engineering": "Machinery breakdown, contractors' and erection all risks, electronic equipment, boilers.",
	"Fire Domestic": "Fire and allied perils on homes and their contents.",
	"Fire Industrial": "Fire and allied perils on business premises, stock, plant and greenhouses.",
	"Liability": "Public, products and professional liability.",
	"Marine": "Marine cargo, goods in transit and marine hull.",
	"Motor Private": "Private cars and motorcycles, comprehensive or third party.",
	"Motor Commercial": "Commercial vehicles, tractors and other self-propelled machinery.",
	"Personal Accident": "Group and individual personal accident.",
	"Theft": "Burglary, theft and money.",
	"Workmen's Compensation": "Work injury benefits (WIBA) and employer's liability.",
	"Medical": "Group and individual medical cover.",
	"Miscellaneous": "Everything else, including agricultural (crop and livestock) cover, fidelity guarantee and all risks.",
}

# Policy types this app offered before Insurance Policy Type became a list, with their IRA
# class. Sites that used them keep them as their own (non-standard) types.
LEGACY_POLICY_TYPES = {
	"Comprehensive": "Motor Commercial",
	"Motor": "Motor Commercial",
	"Fire & Perils": "Fire Industrial",
	"All Risks": "Miscellaneous",
	"Machinery Breakdown": "Engineering",
	"Livestock": "Miscellaneous",
	"Poultry": "Miscellaneous",
	"Public Liability": "Liability",
	"Employer's Liability / WIBA": "Workmen's Compensation",
	"Medical": "Medical",
	"Group Life": None,  # life business, outside the general classes
	"Goods in Transit": "Marine",
	"Fidelity Guarantee": "Miscellaneous",
	"Money": "Theft",
	"Professional Indemnity": "Liability",
	"Other": "Miscellaneous",
}


def ensure_policy_types():
	"""Seed the IRA classes as standard types, and make sure every type already used on a
	policy or an incident preference exists. Existing records are never changed, so users
	can disable types freely."""
	for name, desc in IRA_CLASSES.items():
		if not frappe.db.exists("Insurance Policy Type", name):
			frappe.get_doc({"doctype": "Insurance Policy Type", "policy_type_name": name, "is_standard": 1, "enabled": 1,
				"description": desc}).insert(ignore_permissions=True)
	used = set()
	for dt in ("Insurance Policy", "Insurance Incident Policy Type"):
		if frappe.db.table_exists(dt) and frappe.db.has_column(dt, "policy_type"):
			used.update(v for v in frappe.get_all(dt, pluck="policy_type", distinct=True) if v)
	for name in sorted(used):
		ensure_policy_type(name)


def ensure_policy_type(name):
	"""Create `name` as a user type (mapped to its IRA class when known) if it does not exist."""
	if not name or frappe.db.exists("Insurance Policy Type", name):
		return name
	ira = LEGACY_POLICY_TYPES.get(name)
	frappe.get_doc({"doctype": "Insurance Policy Type", "policy_type_name": name, "enabled": 1,
		"ira_class": ira if ira and frappe.db.exists("Insurance Policy Type", ira) else None,
		"comprehensive_cover": 1 if name == "Comprehensive" else 0}).insert(ignore_permissions=True)
	return name


def _preferred_types(names):
	"""Incident preferences use the legacy type when the site has it, else its IRA class."""
	out = []
	for n in names:
		t = n if frappe.db.exists("Insurance Policy Type", n) else LEGACY_POLICY_TYPES.get(n)
		if t and frappe.db.exists("Insurance Policy Type", t) and t not in out:
			out.append(t)
	return out


LIVESTOCK = ["Livestock", "Poultry", "All Risks"]
DEFAULT_INCIDENT_TYPES = {
	"Breakdown": ["Machinery Breakdown", "Comprehensive", "All Risks"],
	"Fire": ["Fire & Perils", "Comprehensive", "All Risks", "Livestock", "Poultry"],
	"Lightning": ["Fire & Perils", "Livestock", "Poultry", "All Risks", "Comprehensive"],
	"Flood": ["Fire & Perils", "All Risks", "Livestock", "Poultry", "Comprehensive"],
	"Weather": ["All Risks", "Comprehensive", "Fire & Perils", "Livestock", "Poultry"],
	"Accident": ["Motor", "Comprehensive", "All Risks", "Livestock"],
	"Theft": ["Comprehensive", "All Risks", "Motor", "Livestock", "Poultry"],
	"Malicious Damage": ["Comprehensive", "All Risks", "Fire & Perils"],
	"Riot & Strike": ["Comprehensive", "All Risks", "Fire & Perils"],
	"Death / Mortality": LIVESTOCK,
	"Disease Outbreak": LIVESTOCK,
	"Injury": LIVESTOCK,
	"Predation": LIVESTOCK,
	"Poisoning": LIVESTOCK,
	"Electrocution": ["Livestock", "Poultry", "Fire & Perils", "All Risks"],
	"Emergency Slaughter": LIVESTOCK,
	"Heat Stress": ["Poultry", "Livestock", "All Risks"],
	"Transit Loss": ["Goods in Transit", "Livestock", "Poultry", "All Risks"],
}
INCIDENT_ORDER = ["Accident", "Theft", "Fire", "Lightning", "Flood", "Weather", "Breakdown", "Malicious Damage", "Riot & Strike",
	"Death / Mortality", "Disease Outbreak", "Injury", "Predation", "Poisoning", "Electrocution", "Emergency Slaughter",
	"Transit Loss", "Heat Stress", "Other"]


def ensure_incident_types():
	"""Seed the standard incident types once. Existing records (and whether they
	are enabled) are never touched, so users can disable or rename freely."""
	for name in INCIDENT_ORDER:
		if frappe.db.exists("Insurance Incident Type", name):
			continue
		frappe.get_doc({
			"doctype": "Insurance Incident Type",
			"incident_type_name": name,
			"enabled": 1,
			"preferred_policy_types": [{"policy_type": t} for t in _preferred_types(DEFAULT_INCIDENT_TYPES.get(name, []))],
		}).insert(ignore_permissions=True)


# ── Settings defaults ─────────────────────────────────────────────────────────


def ensure_settings_defaults():
	s = frappe.get_single("Asset Settings")
	changed = False
	# New Check fields default to 0 on an existing Single; switch this one on once.
	# Reminder recipients: seed once with what the app used to hard-code.
	if not frappe.db.sql("select 1 from `tabSingles` where doctype='Asset Settings' and field='notify_policy_owner'"):
		for table, role in (("renewal_reminder_roles", "Accounts Manager"), ("digest_roles", "Accounts Manager"),
				("claim_followup_roles", "Maintenance Manager")):
			if not s.get(table) and frappe.db.exists("Role", role):
				s.append(table, {"role": role})
		s.endorsement_todo_role = s.endorsement_todo_role or "Accounts Manager"
	# The digest used to be weekly; keep that until someone changes it.
	if not frappe.db.sql("select 1 from `tabSingles` where doctype='Asset Settings' and field='digest_frequency'"):
		s.digest_frequency = "Weekly"
		changed = True
	# New Check fields default to 0 on an existing Single; switch these on once.
	for field in ("auto_create_service_records", "add_to_assets_sidebar", "add_to_assets_landing_page", "show_why_why_guide",
			"notify_policy_owner", "notify_broker", "notify_claim_owner"):
		if not frappe.db.sql("select 1 from `tabSingles` where doctype='Asset Settings' and field=%s", field):
			s.set(field, 1)
			changed = True
	for field, value in (("renewal_alert_days", "60,30,7"), ("expiring_threshold_days", 30),
			("claim_followup_days", 14), ("renewal_procurement", "Material Request")):
		if not s.get(field):
			s.set(field, value)
			changed = True
	if not s.claim_required_documents:
		for incident_type, doc, mandatory in (
			("", "Claim form (signed)", 1),
			("", "Photographs of damage", 1),
			("", "Repair estimate / quotation", 0),
			("Accident", "Police abstract", 1),
			("Accident", "Driver's licence copy", 1),
			("Theft", "Police abstract", 1),
			("Fire", "Fire brigade report", 0),
			("Death / Mortality", "Veterinary / post-mortem report", 1),
			("Death / Mortality", "Animal identification (ear tag / photo)", 1),
			("Disease Outbreak", "Veterinary report", 1),
			("Emergency Slaughter", "Veterinary slaughter certificate", 1),
			("Predation", "Photographs / KWS or police report", 0),
		):
			if incident_type and not frappe.db.exists("Insurance Incident Type", incident_type):
				continue
			s.append("claim_required_documents", {"incident_type": incident_type, "document_name": doc, "mandatory": mandatory})
		changed = True
	if changed:
		s.flags.ignore_permissions = True
		s.flags.ignore_mandatory = True
		s.save()


# ── Workspace & number cards ──────────────────────────────────────────────────

NUMBER_CARDS = [
	{
		"label": "Active Policies",
		"document_type": "Insurance Policy",
		"function": "Count",
		"filters_json": [["Insurance Policy", "docstatus", "=", 1], ["Insurance Policy", "status", "in", ["Active", "Expiring"]]],
		"color": "#10b981",
	},
	{
		"label": "Policies Expiring",
		"document_type": "Insurance Policy",
		"function": "Count",
		"filters_json": [["Insurance Policy", "docstatus", "=", 1], ["Insurance Policy", "status", "=", "Expiring"]],
		"color": "#f97316",
	},
	{
		"label": "Open Claims",
		"document_type": "Insurance Claim",
		"function": "Count",
		"filters_json": [["Insurance Claim", "status", "in", ["Recorded", "Under Assessment", "Approved", "Partly Settled"]]],
		"color": "#2563eb",
	},
	{
		"label": "Claim Recoverable Outstanding",
		"document_type": "Insurance Claim",
		"function": "Sum",
		"aggregate_function_based_on": "outstanding_amount",
		"filters_json": [["Insurance Claim", "docstatus", "=", 1], ["Insurance Claim", "outstanding_amount", ">", 0]],
		"color": "#ef4444",
	},
]

REPORTS = ["Insurance Register", "Uninsured Assets", "Insurance Claims Register", "Premium Analysis", "Asset Incident History"]


def _number_cards():
	names = {}
	for card in NUMBER_CARDS:
		existing = frappe.db.get_value("Number Card", {"label": card["label"], "document_type": card["document_type"]})
		doc = frappe.get_doc("Number Card", existing) if existing else frappe.new_doc("Number Card")
		doc.update({**card, "filters_json": json.dumps(card["filters_json"]), "is_public": 1, "module": "Upande Asset", "type": "Document Type"})
		doc.save(ignore_permissions=True) if existing else doc.insert(ignore_permissions=True)
		names[card["label"]] = doc.name
	return names


def ensure_workspace():
	nc = _number_cards()
	reports = [r for r in REPORTS if frappe.db.exists("Report", r)]
	content = [
		{"id": "ins-h1", "type": "header", "data": {"text": '<span class="h4"><b>Asset Insurance</b></span>', "col": 12}},
		*[
			{"id": f"ins-nc-{i}", "type": "number_card", "data": {"number_card_name": nc[label], "col": 3}}
			for i, label in enumerate(nc, start=1)
		],
		{"id": "ins-sp-1", "type": "spacer", "data": {"col": 12}},
		{"id": "ins-sc-1", "type": "shortcut", "data": {"shortcut_name": "Insurance Policy", "col": 3}},
		{"id": "ins-sc-2", "type": "shortcut", "data": {"shortcut_name": "Insurance Claim", "col": 3}},
		{"id": "ins-sc-3", "type": "shortcut", "data": {"shortcut_name": "Insurance Register", "col": 3}},
		{"id": "ins-sc-4", "type": "shortcut", "data": {"shortcut_name": "Settings", "col": 3}},
		{"id": "ins-sp-2", "type": "spacer", "data": {"col": 12}},
		{"id": "ins-card-1", "type": "card", "data": {"card_name": "Insurance", "col": 4}},
		{"id": "ins-card-2", "type": "card", "data": {"card_name": "Reports", "col": 4}},
	]
	links = [
		{"type": "Card Break", "label": "Insurance"},
		{"type": "Link", "label": "Insurance Policy", "link_to": "Insurance Policy", "link_type": "DocType", "onboard": 1},
		{"type": "Link", "label": "Insurance Claim", "link_to": "Insurance Claim", "link_type": "DocType", "onboard": 1},
		{"type": "Link", "label": "Insurance Policy Type", "link_to": "Insurance Policy Type", "link_type": "DocType"},
		{"type": "Link", "label": "Insurance Incident Type", "link_to": "Insurance Incident Type", "link_type": "DocType"},
		{"type": "Link", "label": "Asset Settings", "link_to": "Asset Settings", "link_type": "DocType"},
		{"type": "Card Break", "label": "Reports"},
		*[{"type": "Link", "label": r, "link_to": r, "link_type": "Report", "is_query_report": 1} for r in reports],
	]
	shortcuts = [
		{"type": "DocType", "label": "Insurance Policy", "link_to": "Insurance Policy", "doc_view": "List", "color": "Green"},
		{"type": "DocType", "label": "Insurance Claim", "link_to": "Insurance Claim", "doc_view": "List", "color": "Blue"},
		{"type": "DocType", "label": "Settings", "link_to": "Asset Settings", "color": "Grey"},
	]
	if "Insurance Register" in reports:
		shortcuts.append({"type": "Report", "label": "Insurance Register", "link_to": "Insurance Register", "color": "Orange"})
	else:
		content = [c for c in content if c.get("data", {}).get("shortcut_name") != "Insurance Register"]

	ws = frappe.get_doc("Workspace", WORKSPACE) if frappe.db.exists("Workspace", WORKSPACE) else frappe.new_doc("Workspace")
	if ws.is_new():
		ws.update({"name": WORKSPACE, "label": WORKSPACE, "title": WORKSPACE, "module": "Upande Asset",
			"parent_page": "Assets", "public": 1, "type": "Workspace", "icon": "shield"})
	ws.content = json.dumps(content)
	ws.links, ws.shortcuts = [], []
	for lnk in links:
		ws.append("links", lnk)
	for sc in shortcuts:
		ws.append("shortcuts", sc)
	ws.save(ignore_permissions=True) if not ws.is_new() else ws.insert(ignore_permissions=True)


# ── ERPNext "Assets" sidebar ──────────────────────────────────────────────────

REPORTS_ALL = ["Check Sheet Compliance", "Check Sheet Fault Analysis", "Check Sheet History", "Asset Incident History",
	"Insurance Register", "Uninsured Assets", "Insurance Claims Register", "Premium Analysis"]

# Bump when the arrangement below changes; the next migrate re-applies it.
ASSETS_SIDEBAR_VERSION = "4"
ASSETS_SIDEBAR_VERSION_KEY = "upande_asset_assets_sidebar_version"

# Lucide icons (frappe/public/icons/lucide). Child rows in ERPNext's sections ship without one,
# which renders a generic list icon; they get one here so ERPNext's rows and ours match.
SIDEBAR_ICONS = {
	# ERPNext
	"Asset Depreciation Ledger": "file-chart-column",
	"Asset Depreciations and Balances": "file-chart-column",
	"Asset Activity": "file-chart-column",
	"Asset Capitalization": "sprout",
	"Asset Value Adjustment": "trending-down",
	"Asset Maintenance": "calendar-check",
	"Asset Maintenance Log": "notebook-pen",
	"Asset Repair": "wrench",
	"Asset Maintenance Team": "users",
	"Asset Category": "layout-grid",
	"Location": "map-pin",
	"Accounts Settings": "cog",
	# Upande Asset
	"Asset Checksheet": "layout-dashboard",
	"Asset Check Sheet": "clipboard-check",
	"Asset Check Sheet Schedule": "calendar-clock",
	"Asset Service Record": "clipboard-list",
	"Asset Insurance": "gauge",
	"Insurance Policy": "shield-check",
	"Insurance Claim": "hand-coins",
	"Asset Check Sheet Template": "list-checks",
	"Asset Service Type": "tags",
	"Insurance Incident Type": "triangle-alert",
	"Insurance Policy Type": "shapes",
	"Asset Settings": "settings",
	**{r: "file-chart-column" for r in REPORTS_ALL},
}


def _row(label, link_type, link_to, child=1, icon=None):
	return {"type": "Link", "label": label, "link_type": link_type, "link_to": link_to, "child": child,
		"icon": icon or SIDEBAR_ICONS.get(link_to), "added": 1, "hidden": 0, "collapsible": 1, "indent": 0, "keep_closed": 0}


def _section(label, icon):
	# Same shape as ERPNext's own sections: indented, collapsible, closed by default.
	return {"type": "Section Break", "label": label, "link_type": "DocType", "link_to": None, "child": 0,
		"icon": icon, "added": 1, "hidden": 0, "collapsible": 1, "indent": 1, "keep_closed": 1}


def ensure_assets_sidebar():
	"""Add this app's doctypes and reports into ERPNext's standard Assets sidebar.

	ERPNext ships that sidebar, so it is extended through the site layer (`Custom Sidebar`),
	the same place the desk's sidebar editor saves to; that survives ERPNext updates. Rows are
	placed inside ERPNext's own sections, which needs the whole arrangement, so it is written
	once per ASSETS_SIDEBAR_VERSION. Between version bumps, rearranging done on the site is kept."""
	from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_customization, layer_arrangement, save_site_sidebar

	if not frappe.db.exists("Module Def", "Assets"):
		return
	if not display_setting("add_to_assets_sidebar"):
		remove_assets_sidebar()
		return
	if frappe.db.get_default(ASSETS_SIDEBAR_VERSION_KEY) == ASSETS_SIDEBAR_VERSION:
		return
	existing = get_customization("Assets", None)
	if existing:
		# Start from ERPNext's own arrangement again, so the new layout applies cleanly.
		frappe.delete_doc("Custom Sidebar", existing.name, ignore_permissions=True, force=True)

	rows = [dict(r) for r in layer_arrangement("Assets", None)]
	# Asset Dashboard right under ERPNext's Home / Dashboard links
	at = next((i + 1 for i, r in enumerate(rows) if r.get("link_type") == "Dashboard"), 1)
	rows.insert(at, {"type": "Link", "label": "Asset Dashboard", "link_type": "URL", "url": "/asset-dashboard", "link_to": None,
		"child": 0, "icon": "chart-pie", "added": 1, "hidden": 0, "collapsible": 1, "indent": 0, "keep_closed": 0})
	for r in rows:
		# Icon overrides on ERPNext's child rows (a site layer may override label and icon).
		if r.get("child") and not r.get("icon") and SIDEBAR_ICONS.get(r.get("link_to")):
			r["icon"] = SIDEBAR_ICONS[r["link_to"]]

	def section_end(label):
		"""Index just after the last child row of the section `label`, or None."""
		for i, r in enumerate(rows):
			if r.get("type") == "Section Break" and (r.get("label") or "").lower() == label.lower():
				j = i + 1
				while j < len(rows) and rows[j].get("child"):
					j += 1
				return j
		return None

	def insert_into(label, new_rows, fallback_icon):
		at = section_end(label)
		if at is None:
			rows.extend([_section(label, fallback_icon), *new_rows])
		else:
			rows[at:at] = new_rows

	insert_into("Maintenance", [
		_row("Check Sheet Overview", "Workspace", "Asset Checksheet"),
		_row("Asset Check Sheet", "DocType", "Asset Check Sheet"),
		_row("Check Sheet Schedule", "DocType", "Asset Check Sheet Schedule"),
		_row("Asset Service Record", "DocType", "Asset Service Record"),
	], "wrench")
	at = section_end("Maintenance")
	rows[at:at] = [
		_section("Insurance", "shield"),
		_row("Insurance Overview", "Workspace", "Asset Insurance"),
		_row("Insurance Policy", "DocType", "Insurance Policy"),
		_row("Insurance Claim", "DocType", "Insurance Claim"),
	]
	insert_into("Reports", [_row(r, "Report", r) for r in REPORTS_ALL if frappe.db.exists("Report", r)], "sheet")
	insert_into("Setup", [
		_row("Check Sheet Template", "DocType", "Asset Check Sheet Template"),
		_row("Asset Service Type", "DocType", "Asset Service Type"),
		_row("Insurance Policy Type", "DocType", "Insurance Policy Type"),
		_row("Insurance Incident Type", "DocType", "Insurance Incident Type"),
	], "database")
	rows.append(_row("Asset Settings", "DocType", "Asset Settings", child=0))
	save_site_sidebar("Assets", items=rows)
	frappe.db.set_default(ASSETS_SIDEBAR_VERSION_KEY, ASSETS_SIDEBAR_VERSION)


def display_setting(fieldname):
	"""Asset Settings → Display switches; on unless explicitly turned off."""
	value = frappe.db.get_single_value("Asset Settings", fieldname)
	return value is None or bool(value)


def remove_assets_sidebar():
	"""Back to ERPNext's own Assets sidebar (drops the site layer this app wrote)."""
	from frappe.desk.doctype.custom_sidebar.custom_sidebar import get_customization

	existing = get_customization("Assets", None)
	if existing:
		frappe.delete_doc("Custom Sidebar", existing.name, ignore_permissions=True, force=True)
	frappe.db.set_default(ASSETS_SIDEBAR_VERSION_KEY, "")


def remove_assets_workspace_cards():
	"""Back to ERPNext's own Assets landing page, re-imported from ERPNext's file."""
	if frappe.db.exists("Workspace", "Assets"):
		frappe.reload_doc("assets", "workspace", "assets", force=True)
		frappe.clear_cache()


# ── ERPNext "Assets" landing page ─────────────────────────────────────────────

# Link cards on the Assets workspace, mirroring the sidebar's sections so nothing has
# to be expanded. ERPNext's own cards ("Asset Reports", "Setup") keep their links and get
# ours appended; the other cards are this app's and are rebuilt on every migrate.
ASSETS_PAGE_CARDS = [
	("Asset Reports", [("Report", r) for r in REPORTS_ALL]),
	("Value Changes", [("DocType", "Asset Movement"), ("DocType", "Asset Capitalization"), ("DocType", "Asset Value Adjustment")]),
	("Maintenance & Service", [
		("DocType", "Asset Maintenance"), ("DocType", "Asset Maintenance Log"), ("DocType", "Asset Repair"),
		("DocType", "Asset Check Sheet"), ("DocType", "Asset Check Sheet Schedule"), ("DocType", "Asset Service Record"),
		("DocType", "Asset Maintenance Team"),
	]),
	("Insurance", [("DocType", "Insurance Policy"), ("DocType", "Insurance Claim")]),
	("Setup", [
		("DocType", "Asset Category"), ("DocType", "Location"), ("DocType", "Asset Check Sheet Template"),
		("DocType", "Asset Service Type"), ("DocType", "Insurance Incident Type"), ("DocType", "Asset Settings"),
	]),
]
LINK_LABELS = {"Asset Check Sheet Schedule": "Check Sheet Schedule", "Asset Check Sheet Template": "Check Sheet Template"}


def ensure_assets_workspace():
	"""Add link cards for this app (and ERPNext's sidebar-only doctypes) to the Assets page.

	Rows are written with db_insert / set_value rather than doc.save(): saving a standard
	Workspace in developer mode exports it into ERPNext's own source files. `modified` is left
	alone, so when ERPNext ships a newer version of the page it still applies, and the next
	migrate adds these cards again. Idempotent."""
	import json

	if not frappe.db.exists("Workspace", "Assets"):
		return
	if not display_setting("add_to_assets_landing_page"):
		remove_assets_workspace_cards()
		return
	ws = frappe.get_doc("Workspace", "Assets")

	# Current cards → {label: [rows]}, in order.
	cards, order, current = {}, [], None
	for link in ws.links:
		if link.type == "Card Break":
			current = link.label
			cards.setdefault(current, [])
			if current not in order:
				order.append(current)
		elif current:
			cards[current].append(link)

	ours = {label for label, _ in ASSETS_PAGE_CARDS}
	for label, wanted in ASSETS_PAGE_CARDS:
		rows = cards.setdefault(label, [])
		if label not in order:
			order.append(label)
		present = {(r.link_type, r.link_to) for r in rows}
		for link_type, link_to in wanted:
			exists = frappe.db.exists("Report" if link_type == "Report" else "DocType", link_to)
			if exists and (link_type, link_to) not in present:
				rows.append(frappe._dict({
					"type": "Link", "label": LINK_LABELS.get(link_to, link_to), "link_type": link_type, "link_to": link_to,
					"is_query_report": 1 if link_type == "Report" else 0, "onboard": 0, "hidden": 0, "link_count": 0,
				}))

	# Card order: ERPNext's reports first, then the sidebar's order, setup last.
	wanted_order = [label for label, _ in ASSETS_PAGE_CARDS]
	order = [c for c in wanted_order if c in order] + [c for c in order if c not in wanted_order]

	frappe.db.delete("Workspace Link", {"parent": ws.name, "parenttype": "Workspace", "parentfield": "links"})
	idx = 0
	for label in order:
		rows = [r for r in cards[label] if r.link_to]
		idx += 1
		frappe.get_doc({"doctype": "Workspace Link", "parent": ws.name, "parenttype": "Workspace", "parentfield": "links",
			"idx": idx, "type": "Card Break", "label": label, "link_count": len(rows), "hidden": 0}).db_insert()
		for r in rows:
			idx += 1
			frappe.get_doc({"doctype": "Workspace Link", "parent": ws.name, "parenttype": "Workspace", "parentfield": "links",
				"idx": idx, "type": "Link", "label": r.label, "link_type": r.link_type, "link_to": r.link_to,
				"is_query_report": r.is_query_report or 0, "onboard": r.onboard or 0, "hidden": r.hidden or 0,
				"dependencies": r.get("dependencies"), "only_for": r.get("only_for"), "link_count": 0,
				"description": r.get("description"), "report_ref_doctype": r.get("report_ref_doctype")}).db_insert()

	# Page content: keep every non-card block where it is, lay the cards out in `order`.
	blocks = json.loads(ws.content or "[]")
	first_card = next((i for i, b in enumerate(blocks) if b.get("type") == "card"), len(blocks))
	others = [b for b in blocks if b.get("type") != "card"]
	card_blocks = [{"id": f"ua-card-{i}", "type": "card", "data": {"card_name": label, "col": 4}} for i, label in enumerate(order)]
	head = [b for b in others if blocks.index(b) < first_card]
	tail = [b for b in others if blocks.index(b) >= first_card]
	frappe.db.set_value("Workspace", ws.name, "content", json.dumps(head + card_blocks + tail), update_modified=False)
	frappe.clear_cache()


def run_all():
	from upande_asset.upande_asset.maintenance import ensure_maintenance_data

	steps = (ensure_supplier_group, ensure_workflow, ensure_policy_types, ensure_incident_types, ensure_settings_defaults,
		ensure_workspace, ensure_assets_sidebar, ensure_assets_workspace, ensure_maintenance_data)
	for step in steps:
		try:
			step()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Asset Insurance setup step failed: {step.__name__}")
			print(f"Asset Insurance setup step failed: {step.__name__}\n{frappe.get_traceback()}")
