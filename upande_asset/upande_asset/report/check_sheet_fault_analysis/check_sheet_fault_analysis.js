// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Check Sheet Fault Analysis"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
		},
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: "\nLow\nMedium\nHigh\nCritical",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "critical" && data.critical > 0) {
			value = `<b style="color:#7c3aed">${data.critical}</b>`;
		}
		if (column.fieldname === "high" && data.high > 0) {
			value = `<b style="color:#ef4444">${data.high}</b>`;
		}
		return value;
	},
};
