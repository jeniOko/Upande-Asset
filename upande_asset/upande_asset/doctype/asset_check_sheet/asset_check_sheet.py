# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document

FAULT_STATUSES = {"Fault / Defect", "Affected"}
SECTIONS = ["a", "b", "c", "d", "e", "f"]
TERMINAL_STATUSES = {"Under Repair", "Repaired", "Maintained", "Cancelled"}


class AssetCheckSheet(Document):

    def validate(self):
        self._validate_no_future_date()
        self._validate_one_check_per_day()
        self._block_if_asset_under_repair()
        self._validate_fault_rows()
        self._calculate_thresholds()
        self._auto_set_status()

    def on_submit(self):
        self._enforce_proposed_actions_on_submit()

    # ── Private helpers ──────────────────────────────────────────────────

    def _validate_no_future_date(self):
        from frappe.utils import getdate, today
        if self.check_date and getdate(self.check_date) > getdate(today()):
            frappe.throw(
                _("Check Date cannot be in the future. Checks must be recorded on or before today."),
                title=_("Invalid Date"),
            )

    def _validate_one_check_per_day(self):
        """One submitted/draft check sheet per asset per template per day.
        The same asset may be checked twice in a day only if a different template is used."""
        if not self.asset or not self.check_date or not self.check_sheet_template:
            return
        filters = {
            "asset": self.asset,
            "check_date": self.check_date,
            "check_sheet_template": self.check_sheet_template,
            "docstatus": ["!=", 2],
        }
        if not self.is_new():
            filters["name"] = ["!=", self.name]
        existing = frappe.get_all("Asset Check Sheet", filters=filters, fields=["name"], limit=1)
        if existing:
            frappe.throw(
                _(
                    "A check sheet already exists for asset {0} using template \"{1}\" on {2} ({3}). "
                    "Cancel the existing one or use a different template."
                ).format(
                    self.asset,
                    self.check_sheet_template,
                    frappe.format(self.check_date, "Date"),
                    existing[0]["name"],
                ),
                title=_("Duplicate Check Sheet"),
            )

    def _block_if_asset_under_repair(self):
        """Prevent new check sheets while an open repair exists for the asset."""
        if not self.asset or not self.is_new():
            return
        open_repair = frappe.get_all(
            "Asset Repair",
            filters={
                "asset": self.asset,
                "repair_status": ["in", ["Pending", "In Progress"]],
            },
            fields=["name"],
            limit=1,
        )
        if open_repair:
            frappe.throw(
                _(
                    "Asset {0} has an open repair ({1}). "
                    "New check sheets cannot be recorded until the repair is completed."
                ).format(self.asset, open_repair[0]["name"]),
                title=_("Asset Under Repair"),
            )

    def _validate_fault_rows(self):
        require_incident = int(self.get("require_incident_percent") or 0)
        require_action = int(self.get("require_proposed_action") or 0)

        for sec in SECTIONS:
            label = self.get(f"section_{sec}_label") or f"Section {sec.upper()}"
            for row in self.get(f"section_{sec}_items") or []:
                if row.status not in FAULT_STATUSES:
                    continue
                if not row.severity:
                    frappe.throw(
                        _(
                            "Row {0} in {1}: Severity is mandatory when status is '{2}'."
                        ).format(row.idx, label, row.status)
                    )
                if not row.fault_description:
                    frappe.throw(
                        _(
                            "Row {0} in {1}: Fault / Issue Description is mandatory when status is '{2}'."
                        ).format(row.idx, label, row.status)
                    )
                if require_incident and row.incident_percent is None:
                    frappe.throw(
                        _(
                            "Row {0} in {1}: Incident % is mandatory for this template."
                        ).format(row.idx, label)
                    )
                if require_action and not row.proposed_action:
                    frappe.throw(
                        _(
                            "Row {0} in {1}: Proposed Action is mandatory for this template."
                        ).format(row.idx, label)
                    )

    def _calculate_thresholds(self):
        for sec in SECTIONS:
            for row in self.get(f"section_{sec}_items") or []:
                if (
                    row.status in FAULT_STATUSES
                    and row.threshold_percent
                    and row.incident_percent is not None
                ):
                    above = (row.incident_percent or 0) - (row.threshold_percent or 0)
                    row.percent_above_threshold = above if above > 0 else 0
                else:
                    row.percent_above_threshold = None

                if row.status not in FAULT_STATUSES:
                    row.incident_percent = None
                    row.percent_above_threshold = None
                    row.proposed_action = None

    def _auto_set_status(self):
        """Re-evaluate Passed/Needs Attention on every save.
        Terminal statuses (Under Repair, Repaired, Maintained, Cancelled) are preserved."""
        if self.overall_status in TERMINAL_STATUSES:
            return
        has_fault = any(
            row.status in FAULT_STATUSES
            for sec in SECTIONS
            for row in (self.get(f"section_{sec}_items") or [])
        )
        self.overall_status = "Needs Attention" if has_fault else "Passed"

    def _enforce_proposed_actions_on_submit(self):
        """On submit, block if template requires proposed actions and any are missing."""
        if self.overall_status != "Needs Attention":
            return
        if not int(self.get("require_proposed_action") or 0):
            return
        missing = []
        for sec in SECTIONS:
            label = self.get(f"section_{sec}_label") or f"Section {sec.upper()}"
            for row in self.get(f"section_{sec}_items") or []:
                if row.status in FAULT_STATUSES and not row.proposed_action:
                    missing.append(f"{label} — {row.check_factor}")
        if missing:
            rows_html = "<br>".join(f"• {m}" for m in missing)
            frappe.throw(
                _("The following fault rows are missing a Proposed Action:<br>{0}").format(
                    rows_html
                ),
                title=_("Proposed Action Required"),
            )


@frappe.whitelist()
def make_asset_repair(check_sheet):
    """Create and return an unsaved Asset Repair pre-populated from the check sheet.
    Mirrors ERPNext's create_asset_repair pattern so frappe.model.sync works on the client."""
    cs = frappe.get_doc("Asset Check Sheet", check_sheet)

    fault_lines = []
    for sec in SECTIONS:
        label = cs.get(f"section_{sec}_label") or f"Section {sec.upper()}"
        for row in cs.get(f"section_{sec}_items") or []:
            if row.status not in FAULT_STATUSES:
                continue
            line = f"[{label}] {row.check_factor}: {row.status}"
            if row.severity:
                line += f" (Severity: {row.severity})"
            if row.fault_description:
                line += f" — {row.fault_description}"
            if row.proposed_action:
                line += f"\n  Proposed Action: {row.proposed_action}"
            fault_lines.append(line)

    repair = frappe.new_doc("Asset Repair")
    repair.update(
        {
            "asset": cs.asset,
            "asset_name": cs.asset_name,
            "company": cs.company,
            "failure_date": cs.check_date,
            "check_sheet": check_sheet,
            "description": "\n\n".join(fault_lines),
        }
    )
    return repair


@frappe.whitelist()
def make_asset_maintenance(check_sheet):
    """Find an existing Asset Maintenance for the check sheet's asset and return its name,
    or create and return an unsaved Asset Maintenance pre-populated from the check sheet."""
    cs = frappe.get_doc("Asset Check Sheet", check_sheet)

    existing = frappe.get_all(
        "Asset Maintenance",
        filters={"asset_name": cs.asset},
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )
    if existing:
        return {"action": "open", "name": existing[0]["name"]}

    asset_doc = frappe.get_doc("Asset", cs.asset)
    maintenance = frappe.new_doc("Asset Maintenance")
    maintenance.update(
        {
            "asset_name": cs.asset,
            "company": cs.company,
            "item_code": asset_doc.item_code,
            "item_name": asset_doc.item_name,
            "asset_category": asset_doc.asset_category,
            "asset_maintenance_team": cs.asset_maintenance_team,
        }
    )
    return {"action": "new", "doc": maintenance}


@frappe.whitelist()
def check_duplicate_check_sheet(asset, check_date, template):
    """Return the name of an existing non-cancelled check sheet for this
    asset + template + date combination, or None if the slot is free."""
    existing = frappe.get_all(
        "Asset Check Sheet",
        filters={
            "asset": asset,
            "check_date": check_date,
            "check_sheet_template": template,
            "docstatus": ["!=", 2],
        },
        fields=["name"],
        limit=1,
    )
    return existing[0]["name"] if existing else None


@frappe.whitelist()
def create_check_sheet_from_mobile(payload):
    """Create and submit an Asset Check Sheet from the mobile wizard.

    `payload` is a JSON string with keys:
        asset, check_date, template, company, additional_notes,
        sections  –  dict keyed by section letter ("a"–"f"), each a list of
                     {check_factor, threshold_percent, status, severity,
                      fault_description, incident_percent, proposed_action}
    """
    import json

    if isinstance(payload, str):
        payload = json.loads(payload)

    template = payload.get("template") or ""
    tmpl_doc = frappe.get_doc("Asset Check Sheet Template", template) if template else None

    cs = frappe.new_doc("Asset Check Sheet")
    cs.asset = payload["asset"]
    cs.asset_category = frappe.db.get_value("Asset", payload["asset"], "asset_category")
    cs.check_sheet_template = template
    cs.check_date = payload.get("check_date")
    cs.company = payload.get("company")
    cs.additional_notes = payload.get("additional_notes") or ""

    for sec in SECTIONS:
        label = ""
        if tmpl_doc:
            label = tmpl_doc.get(f"section_{sec}_name") or ""
        cs.set(f"section_{sec}_label", label)

        for item in (payload.get("sections") or {}).get(sec) or []:
            cs.append(
                f"section_{sec}_items",
                {
                    "check_factor":      item.get("check_factor", ""),
                    "threshold_percent": item.get("threshold_percent") or None,
                    "status":            item.get("status", "OK"),
                    "severity":          item.get("severity", ""),
                    "fault_description": item.get("fault_description", ""),
                    "incident_percent":  item.get("incident_percent") or None,
                    "proposed_action":   item.get("proposed_action", ""),
                },
            )

    cs.insert()

    try:
        cs.submit()
        return {"name": cs.name, "submitted": True}
    except frappe.ValidationError as e:
        return {"name": cs.name, "submitted": False, "error": str(e)}