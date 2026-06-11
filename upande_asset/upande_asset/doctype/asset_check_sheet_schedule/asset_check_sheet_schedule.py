import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, today

FREQ_DAYS = {
    "Daily": 1,
    "Weekly": 7,
    "Monthly": 30,
    "Quarterly": 90,
    "Yearly": 365,
}


class AssetCheckSheetSchedule(Document):

    def validate(self):
        self._validate_unique()
        self._refresh_status()

    def before_insert(self):
        # Second guard: catches race conditions / double-submits that slip past validate
        self._validate_unique()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _validate_unique(self):
        """One schedule record per asset + template combination."""
        if not self.asset or not self.check_sheet_template:
            return
        filters = {
            "asset": self.asset,
            "check_sheet_template": self.check_sheet_template,
        }
        if not self.is_new():
            filters["name"] = ["!=", self.name]
        existing = frappe.get_all("Asset Check Sheet Schedule", filters=filters, fields=["name"], limit=1)
        if existing:
            frappe.throw(
                _("A schedule already exists for asset {0} with template {1} ({2}).").format(
                    self.asset, self.check_sheet_template, existing[0]["name"]
                ),
                title=_("Duplicate Schedule"),
            )

    def _refresh_status(self):
        """Compute last_check_date, next_due_date, check_status, days_overdue."""
        if not self.check_sheet_template:
            return

        tmpl = frappe.get_cached_doc("Asset Check Sheet Template", self.check_sheet_template)
        freq_days = (
            int(tmpl.custom_frequency_days or 0)
            if tmpl.frequency == "Custom"
            else FREQ_DAYS.get(tmpl.frequency, 0)
        )
        if not freq_days:
            return

        last = frappe.get_all(
            "Asset Check Sheet",
            filters={
                "asset": self.asset,
                "check_sheet_template": self.check_sheet_template,
                "docstatus": 1,
            },
            fields=["check_date"],
            order_by="check_date desc",
            limit=1,
        )

        if last:
            self.last_check_date = last[0]["check_date"]
            due = add_days(getdate(last[0]["check_date"]), freq_days)
        else:
            self.last_check_date = None
            due = getdate(today())

        self.next_due_date = due

        if not self.enabled:
            self.check_status = "Inactive"
            self.days_overdue = 0
        elif getdate(today()) < due:
            self.check_status = "Up to Date"
            self.days_overdue = 0
        else:
            self.check_status = "Overdue"
            self.days_overdue = (getdate(today()) - getdate(due)).days
