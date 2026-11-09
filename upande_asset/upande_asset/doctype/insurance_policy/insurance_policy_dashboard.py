from frappe import _


def get_data():
	# Purchase Invoice / Order / Material Request carry insurance_policy on their
	# item rows (References section); Frappe resolves the fieldname to the child
	# table when counting, as ERPNext does for Item → Purchase Order.
	# Renewals point both ways (renewed from / renewed to), so they are read from
	# the hidden renewal_links table on this policy instead of a single field.
	return {
		"fieldname": "insurance_policy",
		"non_standard_fieldnames": {"Insurance Claim": "policy"},
		"internal_links": {"Insurance Policy": ["renewal_links", "policy"]},
		"transactions": [
			{"label": _("Premium"), "items": ["Purchase Invoice", "Purchase Order", "Material Request"]},
			{"label": _("Claims"), "items": ["Insurance Claim"]},
			{"label": _("Accounting"), "items": ["Journal Entry"]},
			{"label": _("Renewal"), "items": ["Insurance Policy"]},
		],
	}
