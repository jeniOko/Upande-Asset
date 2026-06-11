// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Check Sheet Compliance"] = {
	filters: [
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
		},
		{
			fieldname: "check_status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nOverdue\nUp to Date",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "check_status") {
			const color = { "Overdue": "red", "Up to Date": "green" }[data.check_status] || "gray";
			value = `<span class="indicator-pill ${color}">${data.check_status || ""}</span>`;
		}
		if (column.fieldname === "days_overdue" && data.days_overdue > 0) {
			value = `<b style="color:#ef4444">${data.days_overdue}</b>`;
		}
		return value;
	},
};
