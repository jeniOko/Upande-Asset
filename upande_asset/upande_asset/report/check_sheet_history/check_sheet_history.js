// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Check Sheet History"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
		},
		{
			fieldname: "asset",
			label: __("Asset"),
			fieldtype: "Link",
			options: "Asset",
		},
		{
			fieldname: "overall_status",
			label: __("Overall Status"),
			fieldtype: "Select",
			options: "\nPassed\nNeeds Attention\nUnder Repair\nRepaired\nMaintained\nCancelled",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "overall_status") {
			const colors = {
				"Passed":          "green",
				"Needs Attention": "red",
				"Under Repair":    "orange",
				"Repaired":        "green",
				"Maintained":      "purple",
				"Cancelled":       "gray",
			};
			const color = colors[data.overall_status] || "gray";
			value = `<span class="indicator-pill ${color}">${data.overall_status || ""}</span>`;
		}
		if (column.fieldname === "fault_count" && data.fault_count > 0) {
			value = `<b style="color:#ef4444">${data.fault_count}</b>`;
		}
		return value;
	},
};
