# Copyright (c) 2026, jeniffer@upande.com,wycliffe@upande.com and contributors
# For license information, please see license.txt


# Ensures only one default template exists per asset category.

import frappe
from frappe import _
from frappe.model.document import Document


class AssetCheckSheetTemplate(Document):

    def validate(self):
        self._enforce_single_default()

    def _enforce_single_default(self):
        """If this template is marked as default, clear the flag on all others
        in the same asset category so there is always at most one default."""
        if not self.is_default:
            return
        existing = frappe.get_all(
            "Asset Check Sheet Template",
            filters={
                "asset_category": self.asset_category,
                "is_default": 1,
                "name": ["!=", self.name],
            },
            fields=["name"],
        )
        for tmpl in existing:
            frappe.db.set_value(
                "Asset Check Sheet Template", tmpl["name"], "is_default", 0
            )
            frappe.msgprint(
                _(
                    "Default flag removed from template {0} — "
                    "only one default is allowed per asset category."
                ).format(tmpl["name"]),
                alert=True,
            )