// Copyright (c) 2026, jeniffer@upande.com,wycliffe@upande.com and contributors
// For license information, please see license.txt

// Frappe's built-in states feature maps to doc.status; our field is
// overall_status, so we drive the indicator via get_indicator instead.
// This is read by both the list view row and the form header indicator.

frappe.listview_settings["Asset Check Sheet"] = {
	// Fetch overall_status for every row even though it is not a list-view
	// column — get_indicator needs it to pick the right colour.
	add_fields: ["overall_status"],

	// Allow our get_indicator to run for draft docs too (overrides the
	// default early-return that shows all drafts as red).
	has_indicator_for_draft: true,

	get_indicator: function (doc) {
		const map = {
			"Draft":            ["Draft",            "gray"],
			"In Review":        ["In Review",        "blue"],
			"Passed":           ["Passed",           "green"],
			"Needs Attention":  ["Needs Attention",  "red"],
			"Under Repair":     ["Under Repair",     "orange"],
			"Maintained":       ["Maintained",       "purple"],
			"Repaired":         ["Repaired",         "green"],
			"Cancelled":        ["Cancelled",        "gray"],
		};
		const entry = map[doc.overall_status];
		if (entry) {
			return [__(entry[0]), entry[1], "overall_status,=," + doc.overall_status];
		}
	},
};
