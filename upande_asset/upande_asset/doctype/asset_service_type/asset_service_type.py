import frappe
from frappe import _
from frappe.model.document import Document


class AssetServiceType(Document):
	def validate(self):
		if self.default_service_item and frappe.db.get_value("Item", self.default_service_item, "is_stock_item"):
			frappe.throw(_("Service Item must be a non-stock item."))
