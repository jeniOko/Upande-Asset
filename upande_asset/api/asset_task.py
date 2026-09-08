# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
"""Endpoints behind the scan-an-asset page.

Nothing here knows what a tractor is. Which asset categories carry a day plan,
and whether they read an hour meter or an odometer, is configured per site on
Asset Category:

    custom_track_tasks  (Check)   - this category gets a daily work plan
    custom_meter_type   (Select)  - "Hour Meter" or "Odometer"
"""

import json

import frappe
from frappe.utils import add_days, flt, now, nowdate

MOVING_METER = "Odometer"
DEFAULT_METER = "Hour Meter"


def _meter_for(asset_category: str) -> str:
	return (
		frappe.db.get_value("Asset Category", asset_category, "custom_meter_type")
		or DEFAULT_METER
	)


def _tracks_tasks(asset_category: str) -> bool:
	return bool(frappe.db.get_value("Asset Category", asset_category, "custom_track_tasks"))


@frappe.whitelist()
def lookup(asset: str | None = None, date: str | None = None, qr: str | None = None):
	"""Everything the page needs for one asset on one day."""
	asset = (asset or "").strip()
	if not asset and qr:
		asset = (qr.replace("\r", "\n").split("\n")[0] or "").strip()
	if not asset:
		return {"status": "error", "message": "Scan a code or type an asset number."}

	a = frappe.db.get_value(
		"Asset",
		asset,
		["name", "asset_name", "asset_category", "location", "company", "status"],
		as_dict=True,
	)
	if not a:
		return {"status": "error", "message": f"No asset with the number {asset}"}

	day = (date or "").strip() or nowdate()
	tasks = frappe.get_all(
		"Asset Task",
		filters={"tractor": asset, "work_date": day},
		fields=[
			"name", "work_date", "task", "task_subject", "work_description", "block",
			"location", "operator", "operator_name", "implement", "implement_name",
			"planned_hours", "planned_qty", "uom", "status", "hour_meter_start",
			"hour_meter_end", "actual_hours", "actual_qty", "fuel_litres", "breakdown",
			"notes", "meter_type", "started_at", "ended_at",
		],
		order_by="creation asc",
	)
	for t in tasks:
		t["blocks"] = frappe.get_all(
			"Asset Task Block",
			filters={"parent": t["name"], "parenttype": "Asset Task"},
			fields=["block", "location", "planned_qty", "actual_qty", "remarks"],
			order_by="idx asc",
		)

	last = frappe.get_all(
		"Asset Task",
		filters={"tractor": asset, "hour_meter_end": [">", 0]},
		fields=["hour_meter_end", "work_date"],
		order_by="ended_at desc, modified desc",
		limit=1,
	)
	week = frappe.get_all(
		"Asset Task",
		filters={"tractor": asset, "work_date": [">=", add_days(day, -6)], "status": "Completed"},
		fields=["actual_hours", "fuel_litres"],
	)

	return {
		"status": "success",
		"asset": a,
		"date": day,
		"tracks_tasks": _tracks_tasks(a.asset_category),
		"meter_type": _meter_for(a.asset_category),
		"tasks": tasks,
		"last_reading": (last[0]["hour_meter_end"] if last else 0),
		"last_reading_date": (str(last[0]["work_date"]) if last else None),
		"week_hours": round(sum(flt(w.actual_hours) for w in week), 2),
		"week_fuel": round(sum(flt(w.fuel_litres) for w in week), 2),
		"qr_url": f"/files/qr-{a.name}.png",
	}


@frappe.whitelist()
def save(data):
	"""Create or update one Asset Task.

	`action` is "save", "start" or "stop". Start and stop both require a meter
	reading, and neither may move the meter backwards.
	"""
	if isinstance(data, str):
		data = json.loads(data)
	if not data:
		frappe.throw("Nothing was sent.")

	action = (data.get("action") or "save").strip()
	name = (data.get("name") or "").strip()

	if name:
		doc = frappe.get_doc("Asset Task", name)
	else:
		asset = (data.get("tractor") or data.get("asset") or "").strip()
		if not asset:
			frappe.throw("An asset is required.")
		doc = frappe.new_doc("Asset Task")
		doc.tractor = asset
		doc.work_date = data.get("work_date") or nowdate()

	for f in (
		"task", "operator", "implement", "work_description", "planned_hours",
		"planned_qty", "uom", "notes", "farm", "location", "block", "meter_type",
	):
		if f in data:
			doc.set(f, data.get(f) or None)

	if "blocks" in data:
		doc.set("blocks", [])
		for b in data.get("blocks") or []:
			block = (b.get("block") if isinstance(b, dict) else str(b) or "").strip()
			if not block:
				continue
			doc.append(
				"blocks",
				{
					"block": block,
					"location": (b.get("location") if isinstance(b, dict) else None) or None,
					"planned_qty": (b.get("planned_qty") if isinstance(b, dict) else 0) or 0,
					"actual_qty": (b.get("actual_qty") if isinstance(b, dict) else 0) or 0,
					"remarks": (b.get("remarks") if isinstance(b, dict) else "") or "",
				},
			)

	label = "Odometer/machine hours reading"

	if action in ("start", "stop"):
		reading = data.get("reading")
		if reading in (None, ""):
			frappe.throw(f"{label} is required before you can {action}.")
		reading = flt(reading)

	if action == "start":
		previous = frappe.get_all(
			"Asset Task",
			filters={"tractor": doc.tractor, "hour_meter_end": [">", 0]},
			fields=["hour_meter_end"],
			order_by="ended_at desc, modified desc",
			limit=1,
		)
		if previous and reading < flt(previous[0]["hour_meter_end"]):
			frappe.throw(
				f"{label} {reading} is below the last recorded reading "
				f"{previous[0]['hour_meter_end']}."
			)
		doc.hour_meter_start = reading
		doc.started_at = now()
		doc.status = "In Progress"
	elif action == "stop":
		if not flt(doc.hour_meter_start):
			frappe.throw("This job was never started, so it cannot be stopped.")
		if reading < flt(doc.hour_meter_start):
			frappe.throw(f"{label} {reading} is below the start reading {doc.hour_meter_start}.")
		doc.hour_meter_end = reading
		doc.ended_at = now()
		doc.status = "Completed"
		for f in ("fuel_litres", "actual_qty"):
			if data.get(f) not in (None, ""):
				doc.set(f, data.get(f))
		if data.get("breakdown") is not None:
			doc.breakdown = 1 if data.get("breakdown") else 0
	else:
		for f in (
			"status", "hour_meter_start", "hour_meter_end", "actual_qty",
			"fuel_litres", "breakdown",
		):
			if f in data:
				doc.set(f, data.get(f) or None)

	if not doc.status:
		doc.status = "Planned"

	doc.save(ignore_permissions=True)
	frappe.db.commit()

	verb = {"start": "Started", "stop": "Stopped"}.get(action, "Updated" if name else "Recorded")
	return {
		"status": "success",
		"name": doc.name,
		"doc_status": doc.status,
		"actual_hours": flt(doc.actual_hours),
		"hour_meter_start": flt(doc.hour_meter_start),
		"hour_meter_end": flt(doc.hour_meter_end),
		"meter_type": doc.meter_type,
		"message": f"{verb} {doc.name}",
	}


@frappe.whitelist()
def options(kind: str, q: str | None = None):
	"""Type-ahead lists for the plan form."""
	q = (q or "").strip()
	out = []

	if kind == "task":
		filters = [["status", "in", ["Open", "Working", "Pending Review"]]]
		if q:
			filters.append(["subject", "like", f"%{q}%"])
		for r in frappe.get_all(
			"Task", filters=filters, fields=["name", "subject", "status"],
			order_by="modified desc", limit_page_length=25
		):
			out.append({"value": r.name, "label": r.subject or r.name, "hint": r.status})

	elif kind == "block":
		filters = [["is_group", "=", 0], ["disabled", "=", 0]]
		if q:
			filters.append(["name", "like", f"%{q}%"])
		for r in frappe.get_all(
			"Warehouse", filters=filters, fields=["name"],
			order_by="name asc", limit_page_length=25
		):
			out.append({"value": r.name, "label": r.name, "hint": ""})

	elif kind == "operator":
		filters = [["status", "=", "Active"]]
		if q:
			filters.append(["employee_name", "like", f"%{q}%"])
		for r in frappe.get_all(
			"Employee", filters=filters, fields=["name", "employee_name", "designation"],
			order_by="employee_name asc", limit_page_length=25
		):
			out.append(
				{"value": r.name, "label": r.employee_name or r.name, "hint": r.designation or ""}
			)

	elif kind == "implement":
		cats = [
			c.name
			for c in frappe.get_all(
				"Asset Category", filters={"custom_track_tasks": 1}, fields=["name"]
			)
		]
		filters = [["docstatus", "<", 2]]
		if cats:
			filters.append(["asset_category", "in", cats])
		if q:
			filters.append(["asset_name", "like", f"%{q}%"])
		for r in frappe.get_all(
			"Asset", filters=filters,
			fields=["name", "asset_name", "asset_category", "location"],
			order_by="asset_name asc", limit_page_length=25
		):
			hint = r.asset_category or ""
			if r.location:
				hint = f"{hint} · {r.location}"
			out.append({"value": r.name, "label": r.asset_name or r.name, "hint": hint})

	elif kind == "uom":
		filters = [["name", "like", f"%{q}%"]] if q else []
		for r in frappe.get_all(
			"UOM", filters=filters, fields=["name"], order_by="name asc", limit_page_length=25
		):
			out.append({"value": r.name, "label": r.name, "hint": ""})

	else:
		frappe.throw(f"Unknown list: {kind}")

	return {"status": "success", "options": out}
