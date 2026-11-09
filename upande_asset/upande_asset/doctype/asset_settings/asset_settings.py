# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# field → (expected root_type, expected account_type or None)
ACCOUNT_RULES = {
	"prepaid_insurance_account": ("Asset", None),
	"insurance_expense_account": ("Expense", None),
	"claim_recoverable_account": ("Liability", "Payable"),
	"claim_recovery_income_account": ("Income", None),
	"claim_shortfall_account": ("Expense", None),
}


class AssetSettings(Document):
	def validate(self):
		self._validate_premium_item()
		self._validate_accounts()
		self._validate_alert_days()

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)
		self._apply_display_settings()
		if self.has_value_changed("renewal_complete_on"):
			self._refresh_renewal_statuses()

	def _refresh_renewal_statuses(self):
		"""Renewed / Renewal In Progress / Awaiting Premium depend on this setting."""
		from upande_asset.upande_asset.doctype.insurance_policy.insurance_policy import refresh_status

		for name in frappe.get_all("Insurance Policy", filters={"docstatus": 1,
				"status": ["in", ["Renewed", "Renewal In Progress", "Awaiting Premium", "Active", "Expiring"]]}, pluck="name"):
			refresh_status(name)

	def _apply_display_settings(self):
		"""Apply the Display switches straight away instead of waiting for the next migrate."""
		from upande_asset.upande_asset.insurance import setup

		if self.has_value_changed("add_to_assets_sidebar"):
			setup.remove_assets_sidebar()  # rebuilt below when switched on
			if self.add_to_assets_sidebar:
				setup.ensure_assets_sidebar()
		if self.has_value_changed("add_to_assets_landing_page"):
			if self.add_to_assets_landing_page:
				setup.ensure_assets_workspace()
			else:
				setup.remove_assets_workspace_cards()

	def _validate_premium_item(self):
		if not self.premium_item:
			return
		item = frappe.db.get_value("Item", self.premium_item, ["is_stock_item", "enable_deferred_expense", "is_fixed_asset"], as_dict=True)
		if item.is_stock_item or item.is_fixed_asset:
			frappe.throw(_("Premium Item must be a non-stock, non-asset service item."))
		if not item.enable_deferred_expense:
			frappe.msgprint(
				_("Tip: enable Deferred Expense on item {0}. Premium lines set it explicitly anyway.").format(self.premium_item),
				indicator="blue",
				alert=True,
			)

	def _validate_accounts(self):
		seen = set()
		for row in self.company_defaults:
			if row.company in seen:
				frappe.throw(_("Row {0}: company {1} is listed twice.").format(row.idx, row.company))
			seen.add(row.company)
			for field in ("parts_warehouse", "insurance_request_warehouse"):
				wh = row.get(field)
				if not wh:
					continue
				w = frappe.db.get_value("Warehouse", wh, ["company", "is_group"], as_dict=True)
				label = _(frappe.get_meta(row.doctype).get_label(field))
				if w.company != row.company:
					frappe.throw(_("Row {0}: {1} {2} does not belong to {3}.").format(row.idx, label, wh, row.company))
				if w.is_group:
					frappe.throw(_("Row {0}: {1} cannot be a group warehouse.").format(row.idx, label))
			for field, (root_type, account_type) in ACCOUNT_RULES.items():
				account = row.get(field)
				if not account:
					continue
				acc = frappe.db.get_value("Account", account, ["company", "root_type", "account_type", "is_group"], as_dict=True)
				label = frappe.unscrub(field)
				if acc.company != row.company:
					frappe.throw(_("Row {0}: {1} {2} does not belong to {3}.").format(row.idx, label, account, row.company))
				if acc.is_group:
					frappe.throw(_("Row {0}: {1} cannot be a group account.").format(row.idx, label))
				if acc.root_type != root_type:
					frappe.throw(_("Row {0}: {1} must be an {2} account.").format(row.idx, label, root_type))
				if account_type and acc.account_type != account_type:
					frappe.throw(
						_("Row {0}: {1} must have Account Type {2} so the insurer (a Supplier) can be the party.").format(
							row.idx, label, account_type
						)
					)

	def _validate_alert_days(self):
		bad = [d for d in (self.renewal_alert_days or "").split(",") if d.strip() and not d.strip().isdigit()]
		if bad:
			frappe.throw(_("Renewal Alert Days must be a comma separated list of numbers."))
