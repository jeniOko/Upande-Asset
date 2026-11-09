// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Asset Incident History"] = {
	filters: [
		{
			fieldname: "asset",
			label: __("Asset"),
			fieldtype: "Link",
			options: "Asset",
			reqd: 1,
			// The company's assets; Location / dimensions are checked on the server (groups include children).
			get_query: () => {
				const company = frappe.query_report.get_filter_value("company");
				return { filters: { docstatus: ["<", 2], ...(company ? { company } : {}) } };
			},
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "type") {
			const colors = {
				"Breakdown Task": "red",
				"Check Sheet": "blue",
				Repair: "orange",
				"Insurance Claim": "purple",
			};
			value = `<span class="indicator-pill ${colors[data.type] || "gray"}">${frappe.utils.escape_html(data.type || "")}</span>`;
		}
		return value;
	},
};

upande_asset.reports.add_scope_filters("Asset Incident History");
