# ================================================================
# File: upande_asset/overrides/asset_repair.py
#
# Ties Material Requests to an Asset Repair so that:
#   1. A repair may raise any number of Material Requests
#   2. Stores issues the parts the ordinary way - Material Request ->
#      Material Issue - and THAT is what relieves stock
#   3. The repair's "Consumed Stock Items" table is a record of what was
#      fitted, not a stock movement. Submitting a repair moves nothing,
#      so a part can never leave the store twice
#   4. Issuing against a repair's request copies the issued lines onto
#      that repair, so the record writes itself
#   5. Cancelled requests are ignored everywhere; deleted ones
#      simply cease to exist, so nothing is left dangling
#
# hooks.py entries:
#   override_doctype_class = {
#       "Asset Repair": "upande_asset.overrides.asset_repair.CustomAssetRepair"
#   }
#   doctype_js = {"Asset Repair": "public/js/asset_repair.js"}
#   doc_events = {"Stock Entry": {
#       "before_validate": "upande_asset.overrides.asset_repair.stamp_material_request",
#       "on_submit": "upande_asset.overrides.asset_repair.record_issue_on_repair"}}
# ================================================================

import frappe
from frappe import _
from frappe.utils import flt, nowdate, nowtime

from erpnext.assets.doctype.asset_repair.asset_repair import AssetRepair

LINK_FIELD = "custom_repair_reference"
ISSUE = "Material Issue"
PURCHASE = "Purchase"
TOLERANCE = 0.00001


class CustomAssetRepair(AssetRepair):
	def validate(self):
		super().validate()
		self.warn_consumption_against_requests()

	def decrease_stock_quantity(self):
		"""Do nothing, on purpose.

		ERPNext relieves stock here, out of the warehouse on each consumed row.
		On this bench stores has already issued those parts against the repair's
		material request, so moving them again would take them out of the store
		twice. The table stays as the record of what was fitted.
		"""
		return

	def before_submit(self):
		# Those parts were issued by stores, and that issue already posted its
		# own expense. Capitalising them onto the asset as well books them twice,
		# and ERPNext's credit side reads the stock entry this repair no longer
		# makes, so the entry would not even balance.
		if self.get("capitalize_repair_cost") and self.get("stock_items"):
			frappe.throw(
				_("This repair capitalises its cost, but its consumed items were "
				  "issued by stores and are already expensed there. Either clear "
				  "Capitalize Repair Cost, or take the items off and put the "
				  "amount in Repair Cost instead."),
				title=_("That would count the parts twice"),
			)

		over = self.warn_consumption_against_requests()
		if over:
			self.add_comment(
				"Comment",
				_("Submitted consuming more than the material request allowed - {0}").format(
					"; ".join(over)
				),
			)

	def warn_consumption_against_requests(self):
		"""Say when a repair goes past what its requests allow, and let it through.

		A hard stop was the first cut and it was wrong: a repair that genuinely
		needs a third bolt would reach the last approver and die there with no
		way forward. The form asks the operator to confirm instead, and the
		overage is written onto the repair so it is on the record."""
		if not self.get("stock_items"):
			return []

		allowance = get_allowance(self.name)
		if not allowance:
			return []

		used = {}
		for row in self.stock_items:
			used[row.item_code] = flt(used.get(row.item_code)) + flt(row.consumed_quantity)

		over = []
		for item_code, qty in used.items():
			allowed = allowance.get(item_code)
			if not allowed:
				# not covered by any request; left to the storekeeper's judgement
				continue
			if qty > flt(allowed["left"]) + TOLERANCE:
				over.append(
					_("{0}: {1} used against {2} allowed by the request").format(
						item_code, qty, allowed["left"])
				)

		return over


# ── shared helpers ─────────────────────────────────────────────────────────

def get_requests(asset_repair, only_live=True):
	"""Requests raised for this repair. Cancelled ones are dropped by default."""
	filters = {LINK_FIELD: asset_repair}
	if only_live:
		filters["docstatus"] = ["!=", 2]
	return frappe.get_all(
		"Material Request",
		filters=filters,
		fields=["name", "material_request_type", "status", "docstatus",
				"transaction_date", "workflow_state"],
		order_by="creation asc",
	)


def get_allowance(asset_repair):
	"""Per item: what was requested, what stores already issued, what is left."""
	names = [r.name for r in get_requests(asset_repair)
			 if r.material_request_type == ISSUE and r.docstatus == 1]
	out = {}
	if not names:
		return out

	for row in frappe.get_all(
		"Material Request Item",
		filters={"parent": ["in", names]},
		fields=["item_code", "item_name", "warehouse", "uom", "qty", "ordered_qty"],
		order_by="parent asc, idx asc",
	):
		entry = out.setdefault(row.item_code, {
			"item_code": row.item_code, "item_name": row.item_name,
			"warehouse": row.warehouse, "uom": row.uom,
			"requested": 0.0, "issued": 0.0, "left": 0.0,
		})
		entry["requested"] += flt(row.qty)
		entry["issued"] += flt(row.ordered_qty)
		if row.warehouse and not entry["warehouse"]:
			entry["warehouse"] = row.warehouse

	for entry in out.values():
		entry["left"] = max(entry["requested"] - entry["issued"], 0.0)
	return out


def get_valuation_rate(item_code, warehouse, qty, company):
	"""Value a consumed row the way the form values it, with fallbacks so a
	thin stock ledger does not silently leave the cost at zero."""
	from erpnext.stock.utils import get_incoming_rate

	try:
		rate = flt(get_incoming_rate({
			"item_code": item_code,
			"warehouse": warehouse,
			"qty": flt(qty),
			"company": company,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
		}, raise_error_if_no_rate=False))
	except Exception:
		rate = 0.0

	if not rate and warehouse:
		rate = flt(frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"))
	if not rate:
		rate = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
	if not rate:
		# last resort: what the item is worth in whatever store still holds it,
		# so a repair is not booked at zero cost just because this particular
		# warehouse has never carried the part
		elsewhere = frappe.get_all(
			"Bin",
			filters={"item_code": item_code, "valuation_rate": [">", 0]},
			fields=["valuation_rate"],
			order_by="actual_qty desc",
			limit=1,
		)
		if elsewhere:
			rate = flt(elsewhere[0].valuation_rate)
	return rate


# ── called from the form ───────────────────────────────────────────────────

@frappe.whitelist()
def get_repair_materials(asset_repair):
	"""Everything the Materials dialog shows."""
	doc = frappe.get_doc("Asset Repair", asset_repair)
	doc.check_permission("read")

	requests = get_requests(asset_repair)
	allowance = get_allowance(asset_repair)

	consumed = {}
	for row in (doc.get("stock_items") or []):
		consumed[row.item_code] = flt(consumed.get(row.item_code)) + flt(row.consumed_quantity)

	to_pull = []
	for item_code, entry in allowance.items():
		outstanding = flt(entry["left"]) - flt(consumed.get(item_code))
		if outstanding > TOLERANCE:
			to_pull.append({
				"item_code": item_code,
				"item_name": entry["item_name"],
				"warehouse": entry["warehouse"],
				"uom": entry["uom"],
				"qty": outstanding,
			})

	return {
		"requests": requests,
		"to_pull": to_pull,
		"allowance": list(allowance.values()),
		"is_draft": doc.docstatus == 0,
	}


@frappe.whitelist()
def make_material_request(source_name):
	"""A fresh request, already pointed at this repair."""
	repair = frappe.get_doc("Asset Repair", source_name)
	repair.check_permission("read")

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = ISSUE
	mr.company = repair.company
	mr.transaction_date = nowdate()
	mr.schedule_date = nowdate()
	mr.set(LINK_FIELD, repair.name)

	# carry the farm across when both doctypes track one
	for source_field in ("farm", "custom_farm"):
		value = repair.get(source_field)
		if value and mr.meta.get_field("custom_farm"):
			mr.custom_farm = value
			break

	return mr.as_dict()


@frappe.whitelist()
def pull_materials(asset_repair):
	"""Backfill Consumed Stock Items from the approved issue requests."""
	doc = frappe.get_doc("Asset Repair", asset_repair)
	doc.check_permission("write")

	if doc.docstatus != 0:
		frappe.throw(_("This repair is no longer a draft, so its consumed items cannot be changed."))

	data = get_repair_materials(asset_repair)
	added = []
	for row in data["to_pull"]:
		child = doc.append("stock_items", {
			"item_code": row["item_code"],
			"warehouse": row["warehouse"],
			"consumed_quantity": flt(row["qty"]),
		})
		child.valuation_rate = get_valuation_rate(
			row["item_code"], row["warehouse"], row["qty"], doc.company)
		child.total_value = flt(child.consumed_quantity) * flt(child.valuation_rate)
		added.append(row["item_code"])

	if added:
		doc.save()

	return {"added": added, "count": len(added)}


# ── called from hooks on Stock Entry ───────────────────────────────────────

def stamp_material_request(doc, method=None):
	"""The repair makes one Material Issue on submit. Point its rows back at the
	approved requests so ERPNext closes them without anyone touching them."""
	if doc.doctype != "Stock Entry" or not doc.get("asset_repair"):
		return

	names = [r.name for r in get_requests(doc.asset_repair)
			 if r.material_request_type == ISSUE and r.docstatus == 1]
	if not names:
		return

	pool = {}
	for row in frappe.get_all(
		"Material Request Item",
		filters={"parent": ["in", names]},
		fields=["name", "parent", "item_code", "qty", "ordered_qty"],
		order_by="parent asc, idx asc",
	):
		outstanding = flt(row.qty) - flt(row.ordered_qty)
		if outstanding <= 0:
			continue
		pool.setdefault(row.item_code, []).append(
			{"request": row.parent, "row": row.name, "left": outstanding})

	for item in doc.get("items") or []:
		if item.get("material_request"):
			continue
		for candidate in pool.get(item.item_code, []):
			if candidate["left"] <= 0:
				continue
			# ERPNext refuses an issue that exceeds the requested quantity, and
			# that refusal cannot be overridden. A row using more than was asked
			# for is left unstamped rather than made unsubmittable.
			if flt(item.qty) > candidate["left"] + TOLERANCE:
				continue
			item.material_request = candidate["request"]
			item.material_request_item = candidate["row"]
			candidate["left"] -= flt(item.qty)
			break


def record_issue_on_repair(doc, method=None):
	"""When stores issues parts against a repair's request, write them onto
	that repair's Consumed Stock Items.

	Stores relieves the stock; the repair only records what was fitted. Doing
	the copy here means the record keeps itself, and the storekeeper never has
	to open the repair.
	"""
	if doc.doctype != "Stock Entry":
		return
	if doc.get("asset_repair"):
		return                       # the repair's own entry, if one ever exists

	wanted = {}
	for item in doc.get("items") or []:
		request = item.get("material_request")
		if not request or not item.get("s_warehouse") or item.get("t_warehouse"):
			continue
		repair = frappe.db.get_value("Material Request", request, LINK_FIELD)
		if repair:
			wanted.setdefault(repair, []).append(item)

	for repair_name, items in wanted.items():
		repair = frappe.get_doc("Asset Repair", repair_name)
		if repair.docstatus != 0:
			frappe.msgprint(
				_("Asset repair {0} is no longer a draft, so {1} was not added to its "
				  "consumed items. Add it there by hand if it belongs on the record.").format(
					frappe.bold(repair_name), frappe.bold(doc.name)),
				indicator="orange",
			)
			continue

		added = []
		for item in items:
			child = repair.append("stock_items", {
				"item_code": item.item_code,
				"warehouse": item.s_warehouse,
				"consumed_quantity": flt(item.qty),
			})
			child.valuation_rate = flt(item.basic_rate) or get_valuation_rate(
				item.item_code, item.s_warehouse, item.qty, repair.company)
			child.total_value = flt(child.consumed_quantity) * flt(child.valuation_rate)
			added.append(item.item_code)

		if added:
			repair.flags.ignore_permissions = True
			repair.save()
			frappe.msgprint(
				_("Recorded on asset repair {0}: {1}").format(
					frappe.bold(repair_name), ", ".join(added)),
				indicator="green",
			)



# ── raising a request without leaving the repair ───────────────────────────

# Set by the request itself or by us, never by the operator's dialog.
REQUEST_OWN_FIELDS = {
	"naming_series", "company", "transaction_date", "schedule_date", "set_warehouse",
	"material_request_type", "items", "workflow_state", "amended_from", "status",
	"per_ordered", "per_received", "title", "docstatus", "name", LINK_FIELD,
}

ASKABLE = ("Link", "Select", "Data", "Date", "Small Text")


@frappe.whitelist()
def get_request_spec(asset_repair):
	"""What the dialog has to ask for on this site.

	Sites bolt their own mandatory fields onto Material Request — a farm here,
	a business unit and a request type there — so the dialog is built from the
	meta rather than from a fixed list, and prefilled from the asset wherever
	the asset carries the same information.
	"""
	repair = frappe.get_doc("Asset Repair", asset_repair)
	request_meta = frappe.get_meta("Material Request")
	row_meta = frappe.get_meta("Material Request Item")
	asset_meta = frappe.get_meta("Asset")
	asset = frappe.get_doc("Asset", repair.asset) if repair.asset else None

	extra_fields = []
	for df in request_meta.fields:
		if df.fieldname in REQUEST_OWN_FIELDS or df.fieldtype not in ASKABLE:
			continue
		if not (df.reqd or df.mandatory_depends_on):
			continue
		default = None
		if asset:
			for candidate in (df.fieldname, df.fieldname.replace("custom_", "", 1)):
				if asset_meta.has_field(candidate) and asset.get(candidate):
					default = asset.get(candidate)
					break
		extra_fields.append({
			"fieldname": df.fieldname,
			"label": df.label or df.fieldname,
			"fieldtype": df.fieldtype,
			"options": df.options,
			"default": default,
			"depends_on": df.depends_on,
			"mandatory_depends_on": df.mandatory_depends_on,
			"reqd": 1 if df.reqd else 0,
		})

	employee_field = None
	if row_meta.has_field("employee"):
		employee_field = "row"
	elif request_meta.has_field("custom_employee"):
		employee_field = "parent"

	return {
		"company": repair.company,
		"asset": repair.asset,
		"asset_name": repair.asset_name,
		"docstatus": repair.docstatus,
		"extra_fields": extra_fields,
		"employee_field": employee_field,
		"has_purpose": 1 if row_meta.has_field("custom_purpose") else 0,
	}


@frappe.whitelist()
def create_material_request(asset_repair, material_request_type=None, schedule_date=None,
							items=None, extras=None, employee=None):
	"""Build the request from the dialog. The operator never opens the form."""
	repair = frappe.get_doc("Asset Repair", asset_repair)
	if repair.docstatus == 2:
		frappe.throw(_("This repair is cancelled, so materials cannot be requested for it."))

	rows = frappe.parse_json(items) if isinstance(items, str) else (items or [])
	rows = [r for r in rows if r.get("item_code") and flt(r.get("qty")) > 0]
	if not rows:
		frappe.throw(_("Add at least one item with a quantity."))

	extras = frappe.parse_json(extras) if isinstance(extras, str) else (extras or {})
	request_meta = frappe.get_meta("Material Request")
	row_meta = frappe.get_meta("Material Request Item")

	schedule_date = schedule_date or frappe.utils.add_days(nowdate(), 1)

	request = frappe.new_doc("Material Request")
	request.material_request_type = material_request_type or ISSUE
	request.company = repair.company
	request.transaction_date = nowdate()
	request.schedule_date = schedule_date
	request.set(LINK_FIELD, repair.name)

	for fieldname, value in extras.items():
		if not value or fieldname in REQUEST_OWN_FIELDS:
			continue
		if request_meta.has_field(fieldname):
			request.set(fieldname, value)

	if employee and request_meta.has_field("custom_employee"):
		request.custom_employee = employee

	for row in rows:
		line = {
			"item_code": row.get("item_code"),
			"qty": flt(row.get("qty")),
			"schedule_date": schedule_date,
		}
		if row.get("warehouse"):
			line["warehouse"] = row.get("warehouse")
		if row_meta.has_field("custom_purpose"):
			line["custom_purpose"] = row.get("purpose") or _("For asset repair {0}").format(repair.name)
		if row_meta.has_field("custom_asset"):
			line["custom_asset"] = repair.asset
		if employee and row_meta.has_field("employee"):
			line["employee"] = employee
		# the same dimensions the parent carries, where rows track them too
		for fieldname, value in extras.items():
			plain = fieldname.replace("custom_", "", 1)
			if value and row_meta.has_field(plain) and plain not in line:
				line[plain] = value
		request.append("items", line)

	request.flags.ignore_permissions = True
	request.insert()

	return {"name": request.name, "rows": len(rows),
			"material_request_type": request.material_request_type}


@frappe.whitelist()
def issue_to_repair(material_request):
	"""Stores issues a repair request by putting its parts into the repair's
	Consumed Stock Items, which is where they belong. The stock itself leaves
	when the repair is submitted, so nothing is counted twice.

	Clicking twice is harmless: whatever is already sitting in the consumed
	table is netted off before anything is added.
	"""
	request = frappe.get_doc("Material Request", material_request)
	repair_name = request.get(LINK_FIELD)

	if not repair_name:
		frappe.throw(_("{0} was not raised for an asset repair.").format(request.name))
	if request.docstatus != 1:
		frappe.throw(
			_("{0} has not been approved yet, so nothing can be issued against it.").format(request.name)
		)
	if request.material_request_type != ISSUE:
		frappe.throw(
			_("{0} is a {1} request. Those bring the part into store; they are not issued from here.").format(
				request.name, request.material_request_type)
		)

	repair = frappe.get_doc("Asset Repair", repair_name)
	repair.check_permission("write")
	if repair.docstatus != 0:
		frappe.throw(
			_("Asset repair {0} is already submitted, so its consumed items can no longer be changed.").format(
				repair.name)
		)

	credit = {}
	for row in repair.get("stock_items") or []:
		credit[row.item_code] = flt(credit.get(row.item_code)) + flt(row.consumed_quantity)

	added = []
	for row in request.items:
		outstanding = flt(row.qty) - flt(row.ordered_qty)
		if outstanding <= TOLERANCE:
			continue

		# already in the consumed table counts as issued
		covered = min(flt(credit.get(row.item_code)), outstanding)
		credit[row.item_code] = flt(credit.get(row.item_code)) - covered
		outstanding -= covered
		if outstanding <= TOLERANCE:
			continue

		warehouse = row.warehouse or repair.get("warehouse")
		child = repair.append("stock_items", {
			"item_code": row.item_code,
			"warehouse": warehouse,
			"consumed_quantity": outstanding,
		})
		child.valuation_rate = get_valuation_rate(
			row.item_code, warehouse, outstanding, repair.company)
		child.total_value = flt(child.consumed_quantity) * flt(child.valuation_rate)
		added.append({"item_code": row.item_code, "qty": outstanding, "warehouse": warehouse})

	if added:
		repair.save()

	return {
		"repair": repair.name,
		"added": added,
		"count": len(added),
		"asset": repair.asset,
		"short": check_stock_on_hand(repair),
	}


def check_stock_on_hand(repair):
	"""Where the repair asks for more than the store holds.

	ERPNext only says so at submit, which on a repair behind an approval chain
	means the last approver discovers it — too late and in the wrong hands.
	Stores sees it at the moment of issuing instead."""
	needed = {}
	for row in repair.get("stock_items") or []:
		if not row.warehouse:
			continue
		key = (row.item_code, row.warehouse)
		needed[key] = flt(needed.get(key)) + flt(row.consumed_quantity)

	short = []
	for key, qty in needed.items():
		item_code, warehouse = key
		on_hand = flt(frappe.db.get_value(
			"Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
		if on_hand + TOLERANCE >= qty:
			continue
		elsewhere = frappe.get_all(
			"Bin",
			filters={"item_code": item_code, "actual_qty": [">", 0]},
			fields=["warehouse", "actual_qty"],
			order_by="actual_qty desc",
			limit=3,
		)
		short.append({
			"item_code": item_code,
			"warehouse": warehouse,
			"needed": qty,
			"on_hand": on_hand,
			"elsewhere": [{"warehouse": e.warehouse, "qty": flt(e.actual_qty)} for e in elsewhere],
		})
	return short


@frappe.whitelist()
def preflight(asset_repair):
	"""Everything that would stop this repair going through, gathered before
	anyone presses the button.

	Two different things, and they are not the same weight: consuming more than
	was requested is a judgement call the operator can make, while consuming
	stock the store does not hold is simply impossible — ERPNext raises
	NegativeStockError and the repair dies at whichever approver happened to be
	last. Both are shown up front."""
	repair = frappe.get_doc("Asset Repair", asset_repair)

	over = []
	allowance = get_allowance(repair.name)
	if allowance and repair.get("stock_items"):
		used = {}
		for row in repair.stock_items:
			used[row.item_code] = flt(used.get(row.item_code)) + flt(row.consumed_quantity)
		for item_code, qty in used.items():
			allowed = allowance.get(item_code)
			if not allowed:
				continue
			if qty > flt(allowed["left"]) + TOLERANCE:
				over.append({
					"item_code": item_code,
					"used": qty,
					"allowed": flt(allowed["left"]),
					"requested": flt(allowed["requested"]),
					"already_issued": flt(allowed["issued"]),
				})

	# warehouse is mandatory on the consumed row, so there is no "no warehouse"
	# case to guard against here.
	# Submitting a repair no longer moves stock, so what the store holds cannot
	# stop it. The shortfall is still worth seeing when parts are being issued,
	# which is where check_stock_on_hand is used.
	return {"over": over, "short": []}
