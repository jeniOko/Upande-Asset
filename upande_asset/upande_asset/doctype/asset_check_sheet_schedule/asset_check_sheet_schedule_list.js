// Copyright (c) 2026, jeniffer@upande.com,wycliffe@upande.com and contributors
// For license information, please see license.txt

frappe.listview_settings["Asset Check Sheet Schedule"] = {
	add_fields: ["check_status"],
	has_indicator_for_draft: true,

	get_indicator: function (doc) {
		const map = {
			"Up to Date": ["Up to Date", "green"],
			"Overdue":    ["Overdue",    "red"],
			"Inactive":   ["Inactive",   "gray"],
		};
		const entry = map[doc.check_status];
		if (entry) {
			return [__(entry[0]), entry[1], "check_status,=," + doc.check_status];
		}
	},
};
