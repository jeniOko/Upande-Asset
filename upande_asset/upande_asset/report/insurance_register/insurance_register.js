// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Insurance Register"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "as_on_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "insurer",
			label: __("Insurer"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "policy_type",
			label: __("Policy Type"),
			fieldtype: "Link",
			options: "Insurance Policy Type",
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		// positive gap = book value exceeds sum insured, i.e. under-insured
		if (column.fieldname === "gap" && flt(data.gap) > 0) {
			value = `<span style="color:var(--red-500);font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "days_to_expiry" && data.days_to_expiry != null) {
			if (data.days_to_expiry <= 30) {
				value = `<span style="color:var(--orange-500);font-weight:600">${value}</span>`;
			}
		}
		return value;
	},
};

upande_asset.reports.add_scope_filters("Insurance Register");
