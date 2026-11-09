# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class InsurancePolicyType(Document):
	def validate(self):
		if self.is_standard:
			# A standard IRA class is its own class (set after insert: the link must exist first).
			self.ira_class = None if self.is_new() else self.name
		elif self.ira_class and not frappe.db.get_value("Insurance Policy Type", self.ira_class, "is_standard"):
			frappe.throw(_("IRA Class must be one of the standard IRA classes."))

	def after_insert(self):
		if self.is_standard:
			self.db_set("ira_class", self.name, update_modified=False)

	def on_trash(self):
		if self.is_standard and not frappe.flags.in_uninstall:
			frappe.throw(_("{0} is a standard IRA class. Disable it instead of deleting it.").format(frappe.bold(self.name)))
