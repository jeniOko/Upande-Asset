from frappe import _


def get_data():
	# MR / PO / PI / Stock Entry carry asset_service_record on their item rows; the
	# maintenance logs sit in this record's own Maintenance Tasks table.
	return {
		"fieldname": "asset_service_record",
		"internal_links": {"Asset Maintenance Log": ["tasks", "asset_maintenance_log"]},
		"transactions": [
			{"label": _("Materials"), "items": ["Material Request", "Stock Entry"]},
			{"label": _("Purchasing"), "items": ["Purchase Order", "Purchase Invoice"]},
			{"label": _("Maintenance"), "items": ["Asset Maintenance Log"]},
		],
	}
